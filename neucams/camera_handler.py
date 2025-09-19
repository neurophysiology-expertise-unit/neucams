# camera_handler.py

from multiprocessing import Process, Queue, Event
import queue
import time
import datetime
from importlib import import_module
from neucams.file_writer import BinaryWriter, TiffWriter, FFMPEGWriter, OpenCVWriter
from neucams.utils import display

def clear_queue(my_queue):
    while True:
        try:
            my_queue.get_nowait()
        except queue.Empty:
            break

class CameraFactory:
    cameras = {
        'avt':       ('cams.avt_cam',       'AVTCam'),
        'genicam':   ('cams.genicam',       'GenICam'),
        'hamamatsu': ('cams.hamamatsu_cam', 'HamamatsuCam'),
        'opencv':    ('cams.opencvcam',     'OpenCVCam'),
    }

    @staticmethod
    def get_camera(driver, cam_id=None, params=None, serial_number=None):
        if driver not in CameraFactory.cameras:
            raise ValueError(f"Unknown camera driver: {driver}")
        module_name, class_name = CameraFactory.cameras[driver]
        module = import_module(f'neucams.{module_name}')
        cam_cls = getattr(module, class_name)
        return cam_cls(cam_id, params=params, serial_number=serial_number)

class CameraHandler:
    def __init__(self, cam_dict, writer_dict):
        # Camera configuration
        self.driver      = cam_dict['driver']
        self.cam_id      = cam_dict.get('id', None)
        self.serial      = cam_dict.get('serial', None)
        self.params      = cam_dict.get('params', {})
        self.freerun     = cam_dict.get('freerun', False)

        # Connection state
        self.camera_connected = False
        self.cam = None

        # IPC primitives
        self.image_queue    = Queue(maxsize=1000)
        self.start_trigger  = Event()
        self.stop_trigger   = Event()
        self.camera_ready   = Event()
        self.is_running     = Event()

        # Writer setup
        self.writer = self._create_writer(writer_dict)

        # Initialize camera
        if self._init_camera():
            self.camera_connected = True
            if self.freerun:
                self._start_freerun_mode()

        # Launch acquisition process
        self.process = Process(target=self._acquisition_loop, daemon=True)
        self.process.start()

    def _create_writer(self, writer_dict):
        fmt = writer_dict.get('format', 'binary').lower()
        if fmt == 'tiff':
            return TiffWriter(writer_dict)
        if fmt == 'ffmpeg':
            return FFMPEGWriter(writer_dict)
        if fmt == 'opencv':
            return OpenCVWriter(writer_dict)
        return BinaryWriter(writer_dict)

    def _init_camera(self):
        try:
            self.cam = CameraFactory.get_camera(
                driver=self.driver,
                cam_id=self.cam_id,
                params=self.params,
                serial_number=self.serial
            )
            if hasattr(self.cam, 'apply_params'):
                self.cam.apply_params()
            display(f"[{self.cam_id}] Camera initialized", level='info')
            return True
        except Exception as e:
            display(f"[{self.cam_id}] Init error: {e}", level='error')
            return False

    def _start_freerun_mode(self):
        """Start continuous freerun acquisition."""
        try:
            display(f"[{self.cam_id}] Entering freerun mode", level='info')
            if hasattr(self.cam, 'start'):
                self.cam.start()
            self.camera_ready.set()
        except Exception as e:
            display(f"[{self.cam_id}] Freerun error: {e}", level='error')

    def wait_for_trigger(self):
        """Block until master-run or stop. Skip restart if freerun."""
        while not self.start_trigger.is_set() and not self.stop_trigger.is_set():
            time.sleep(0.001)

        if self.stop_trigger.is_set():
            return False

        self.is_running.set()

        if not self.freerun:
            try:
                if hasattr(self.cam, 'stop'):
                    self.cam.stop()
                if hasattr(self.cam, 'start'):
                    self.cam.start()
            except Exception:
                pass

        src = str(self.params.get('trigger_source', '')).lower()
        if src == 'software' and hasattr(self.cam, 'fire_software_trigger'):
            self.cam.fire_software_trigger()

        self.camera_ready.clear()
        return True

    def _acquisition_loop(self):
        """Process loop: wait, acquire frames, write out."""
        while not self.stop_trigger.is_set():
            if not self.wait_for_trigger():
                break

            while self.is_running.is_set() and not self.stop_trigger.is_set():
                try:
                    frame = self.cam.image()
                    ts = datetime.datetime.now().isoformat()
                    self.writer.write(frame, ts)
                except queue.Empty:
                    continue
                except Exception as e:
                    display(f"[{self.cam_id}] Acquire error: {e}", level='error')
                    break

            self.is_running.clear()
            self.writer.close_run()

        if self.cam and hasattr(self.cam, 'stop'):
            self.cam.stop()
        self.writer.close()

    # Public API called by UI:
    def start_recording(self):
        """Arm recording on Record button."""
        self.start_trigger.clear()
        self.stop_trigger.clear()
        self.camera_ready.set()

    def master_run(self):
        """Trigger actual run on Master Run button."""
        self.start_trigger.set()

    def stop(self):
        """Stop acquisition."""
        self.stop_trigger.set()
        self.is_running.clear()
        self.camera_ready.set()
