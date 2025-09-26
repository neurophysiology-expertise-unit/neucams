from multiprocessing import Process,Queue,Event,Array,Value
import queue
import numpy as np
import ctypes
import time
import datetime
from os.path import dirname, join, isdir, expanduser
import json
from neucams.file_writer import BinaryWriter, TiffWriter, FFMPEGWriter, OpenCVWriter
from neucams.utils import display, resolve_cam_id_by_serial
from importlib import import_module
import os


def clear_queue(my_queue):
    while True:
        try:
            my_queue.get_nowait()
        except queue.Empty:
            break


class CameraFactory:
    cameras = {
        'avt': ('cams.avt_cam', 'AVTCam'),
        'genicam': ('cams.genicam', 'GenICam'),
        'hamamatsu': ('cams.hamamatsu_cam', 'HamamatsuCam'),
        'opencv': ('cams.opencv_cam', 'OpenCVCam'),
    }

    @staticmethod
    def get_camera(driver, cam_id=None, params=None, serial_number=None):
        if driver not in CameraFactory.cameras:
            raise ValueError(f"Unknown camera driver: {driver}")
        module_name, class_name = CameraFactory.cameras[driver]
        module = import_module(f'neucams.{module_name}')
        cam_class = getattr(module, class_name)

        # Pass serial_number for drivers that can use it (hamamatsu, avt, genicam passes it as cam_id already)
        if driver in ('hamamatsu','avt'):
            return cam_class(cam_id=cam_id, params=params, serial_number=serial_number)
        else:
            return cam_class(cam_id=cam_id, params=params)



class CameraHandler(Process):
    
    def __init__(self, cam_dict, writer_dict):
        super().__init__()
        
        self.cam_dict = cam_dict
        self.writer_dict = writer_dict
        
        self.close_event = Event()
        self.start_trigger = Event()
        self.is_running = Event()
        self.stop_trigger = Event()
        self.camera_ready = Event()
        self.saving = Event()
        
        self.is_acquisition_done = Event()

        self.cam_param_InQ = Queue()
        self.cam_param_OutQ = Queue()
        self.cam_param_get_flag = Event()
        # Capability flags
        self.trigger_supported = Value('b', False)
        
        self.handler_closed = Event()
        
        self.img = None
        self.folder_path_array = Array('u',' ' * 1024) #can set folder
        self.filepath_array = Array('u',' ' * 1024) #filepath is readonly
        
        self.run_nr = 0
        self.frame_nr = 0
        
        self.total_frames = Value('i', 0)
        
        self.lastframeid = -1
        self.last_timestamp = 0
        
        cam = self._open_cam()
        self.camera_connected = cam.is_connected()
        if not self.camera_connected:
            display(f"Camera '{self.cam_dict.get('description', 'unknown')}' (name: '{self.cam_dict.get('name', 'unknown')}') not found or not connected. Please check the connection and close other processes which use the camera.", level='error')
        cam.close()
        
        if self.camera_connected:
            ok = self._init_framebuffer()
            if ok:
                fmt = getattr(self, 'format', {})
                display(f"[{cam.name} {cam.cam_id}] camera ready: {fmt.get('width')}x{fmt.get('height')} dtype={fmt.get('dtype')} n_chan={fmt.get('n_chan')}")
        
    def _open_cam_for_format(self):
        cam = self._open_cam()
        # tell the driver we're only peeking for format (no stream, no full apply)
        setattr(cam, "_open_for_format", True)
        return cam

    def _init_framebuffer(self):
        with self._open_cam_for_format() as cam:
            dtype  = cam.format.get('dtype', None)
            height = cam.format.get('height', None)
            width  = cam.format.get('width', None)
            n_chan = cam.format.get('n_chan', 1)

            dtype = np.dtype(dtype) if dtype is not None else None

            if dtype == np.dtype(np.uint8):
                cdtype = ctypes.c_ubyte
            elif dtype == np.dtype(np.uint16):
                cdtype = ctypes.c_ushort
            else:
                display(f"WARNING: dtype {dtype} not available, defaulting to np.uint16")
                cdtype = ctypes.c_ushort
                dtype = np.dtype(np.uint16)

            if (dtype is None) or (height is None) or (width is None):
                display("ERROR: format (height, width, dtype[,n_chan]) must be set to init the framebuffer")
                return False

            self.frame = Array(cdtype, np.zeros([height, width, n_chan], dtype=dtype).ravel())
            self.format = {'dtype': dtype, 'height': height, 'width': width, 'n_chan': n_chan, 'cdtype': cdtype}
            self._init_buffer()
            # Detect trigger support best-effort once during init
            try:
                supported = False
                # AVT supports trigger; GenICam supports if node exists
                drv = str(self.cam_dict.get('driver', '')).lower()
                if drv == 'avt':
                    supported = True
                elif drv == 'genicam':
                    nm = getattr(cam, 'features', None)
                    supported = bool(nm and hasattr(nm, 'TriggerMode'))
                elif drv == 'hamamatsu':
                    supported = False
                self.trigger_supported.value = bool(supported)
            except Exception:
                self.trigger_supported.value = False
            return True


            
    def _init_buffer(self):
        self.img = np.frombuffer(self.frame.get_obj(), dtype = self.format['cdtype'])\
                        .reshape([self.format['height'], self.format['width'], self.format['n_chan']])
                        
    def run(self):
        try:
            if not hasattr(self, "frame"):
                ok = self._init_framebuffer()
                if not ok:            # Let _init_framebuffer() return True/False
                    display("Camera not ready-handler exiting.", level="error")
                    return

            self._init_buffer()
            with self._open_cam() as cam:
                self.cam = cam
                self.writer = None  # Initialize writer as None - will be created when recording starts
                while not self.close_event.is_set():
                    self._process_queues()
                    self.init_run()

                    display(f'[{cam.name} {cam.cam_id}] waiting for trigger.')
                    self.wait_for_trigger()
                    if self.start_trigger.is_set():
                        display(f'[{cam.name} {cam.cam_id}] start trigger set.')
                        if self.saving.is_set():
                            display(f'[{cam.name} {cam.cam_id}] filepath: {self.get_filepath()}')
                    while not self.stop_trigger.is_set():
                        self._process_queues()
                        frame, metadata = cam.image()
                        # Handle shared memory tuple from AVT with writer-first handoff
                        handled_shm_in_writer = False
                        if isinstance(frame, tuple) and len(frame) == 3 and isinstance(frame[0], str):
                            shm_name, shape, dtype = frame
                            # 1) If recording, hand the SHM tuple to the writer FIRST (writer will manage unlink)
                            if self.saving.is_set():
                                if self.writer is None:
                                    try:
                                        self.writer = self._open_writer()
                                    except ValueError as e:
                                        display(f"Cannot start recording: {e}", level='error')
                                        self.saving.clear()  # Turn off saving since we can't create writer
                                        continue
                                self.writer.save((shm_name, shape, dtype), metadata)
                                handled_shm_in_writer = True
                            # 2) For display, copy from SHM into our shared framebuffer
                            from neucams.file_writer import shm_frame as _shm_frame
                            arr, shm = _shm_frame(shm_name, shape, dtype)
                            try:
                                frame_np = np.array(arr, copy=True)
                            finally:
                                shm.close()
                                # Only unlink here if we did NOT give it to writer
                                if not handled_shm_in_writer:
                                    import contextlib
                                    with contextlib.suppress(FileNotFoundError):
                                        from multiprocessing import shared_memory as _shm
                                        _shm.SharedMemory(name=shm_name).unlink()

                            # Now proceed with local numpy frame
                            frame = frame_np

                        # Non-SHM or post-copy handling
                        if frame is not None:
                            if self.saving.is_set() and not handled_shm_in_writer:
                                if self.writer is None:
                                    try:
                                        self.writer = self._open_writer()
                                    except ValueError as e:
                                        display(f"Cannot start recording: {e}", level='error')
                                        self.saving.clear()  # Turn off saving since we can't create writer
                                        continue
                                self.writer.save(frame, metadata)
                            self._update(frame, metadata)
                        elif metadata == "stop":
                            self.stop_trigger.set()
                    display(f'[{cam.name} {cam.cam_id}] stop trigger set.')
                    self.close_run()
        except Exception as e:
            display(f"Camera handler '{self.cam_dict.get('description', 'unknown')}' crashed: {e}", level='error')
            import traceback
            traceback.print_exc()
        finally:
            display(f"Closing handler for camera '{self.cam_dict.get('description', 'unknown')}'.")
            self.handler_closed.set()
    
    def _open_writer(self):
        writer_type = self.writer_dict.get('recorder', 'opencv')
        writers = {'opencv': OpenCVWriter, 'binary': BinaryWriter, 'tiff': TiffWriter, 'ffmpeg': FFMPEGWriter}
        writer_cls = writers[writer_type]

        cfg = {}
        if writer_type == 'tiff':
            tiff_fpf = (self.writer_dict.get('tiff_size'))
            if isinstance(tiff_fpf, int) and tiff_fpf > 0:
                cfg['frames_per_file'] = tiff_fpf

        # ---- Path logic: UI/UDP is source of truth ----
        folder = self.get_folder_path()
        if not folder:
            raise ValueError("No save folder set. Please set a save path before starting recording.")

        cfg['filepath'] = self.get_new_filepath()

        import inspect
        sig = inspect.signature(writer_cls)
        if 'frame_rate' in sig.parameters:
            cfg['frame_rate'] = self.cam.params.get('frame_rate', None)
        if 'compression' in sig.parameters:
            if 'compress' in self.writer_dict:
                cfg['compression'] = self.writer_dict.get('compress')
            elif 'compression' in self.writer_dict:
                cfg['compression'] = self.writer_dict.get('compression')

        return writer_cls(**cfg)

    
    def get_filepath(self):
        return str(self.filepath_array[:]).strip(' ')
    
    def _update_filepath_array(self, filepath):
        for i in range(len(self.filepath_array)):
            self.filepath_array[i] = ' '
        for i in range(len(filepath)):
            self.filepath_array[i] = filepath[i]
            
    def get_folder_path(self):
        return str(self.folder_path_array[:]).strip(' ')
        
    def set_folder_path(self, folder_path):
        for i in range(len(self.folder_path_array)):
            self.folder_path_array[i] = ' '
        for i in range(len(folder_path)):
            self.folder_path_array[i] = folder_path[i]
    

    def get_current_save_folder(self) -> str:
        """Return the folder path this camera/writer will save into (best effort)."""
        # If you keep a folder_path_array / save_folder attr, use that:
        if hasattr(self, "folder_path_array"):
            try:
                s = str(self.folder_path_array[:]).strip()
                if s:
                    return s
            except Exception:
                pass

        # If there is a writer with a full filepath, return its dirname
        if hasattr(self, "writer") and self.writer:
            try:
                fp = self.writer.get_filepath()
                if fp:
                    return os.path.dirname(fp)
            except Exception:
                pass

        return ""


    def get_new_filename(self):
        # date_run where run is not zero-padded
        return datetime.date.today().strftime('%y%m%d') + '_' + f"{self.run_nr}"
    
    def get_new_filepath(self):
        filepath = join(self.get_folder_path(), self.get_new_filename())
        self._update_filepath_array(filepath)
        return filepath
        
    def _open_cam(self):
        """
        Build and return the actual camera instance based on self.cam_dict.
        This is a CameraHandler helper (not a camera method).
        """
        cam_dict_copy = self.cam_dict.copy()
        cam_type = cam_dict_copy.pop('driver', None)
        if cam_type is None:
            raise ValueError("Camera 'driver' must be specified (avt|genicam|hamamatsu)")
        cam_type = cam_type.lower()

        serial_number = cam_dict_copy.get('serial_number', None)

        # Decide cam_id policy per driver
        if cam_type == 'hamamatsu':
            cam_id = None  # resolved by serial in the driver
        elif cam_type == 'avt':
            # Let the AVT driver resolve by serial itself; keep optional 'id' if provided
            cam_id = cam_dict_copy.get('id', None)
        else:
            # genicam: resolve by serial -> cam_id (or fall back to 'id')
            cam_id = (resolve_cam_id_by_serial(cam_type, serial_number)
                    if serial_number is not None else cam_dict_copy.get('id', None))

        return CameraFactory.get_camera(
            cam_type,
            cam_id=cam_id,
            params=cam_dict_copy.get('params', None),
            serial_number=serial_number
        )

    
    def get_image(self):
        return self.img
    
    def init_run(self):
        self.frame_nr = 0
        self.lastframeid = -1
        # Only set filepath if writer exists (will be set when recording starts)
        if self.writer is not None:
            self.writer.set_filepath(self.get_new_filepath())
        self.camera_ready.set()
    
    def close_run(self):
        # Ensure device acquisition really stops between runs
        try:
            if hasattr(self, "cam") and self.cam:
                self.cam.stop()
        except Exception:
            pass

        # Close writer if it was created
        if hasattr(self, 'writer') and self.writer is not None:
            try:
                self.writer.close()
                self.writer = None
            except Exception:
                pass

        self.start_trigger.clear()
        self.is_acquisition_done.set()
        if self.saving.is_set():
            self.run_nr += 1
        if not self.close_event.is_set():
            self.stop_trigger.clear()
        self.is_running.clear()


    def _update(self, frame, metadata):
        self._update_buffer(frame)
        self.frame_nr += 1
        self.total_frames.value += 1
        frameID,timestamp = metadata[:2]
        self.lastframeid = frameID
        self.last_timestamp = timestamp
    
    def _update_buffer(self,frame):
        self.img[:] = np.reshape(frame,self.img.shape)[:]
            
    def wait_for_trigger(self):
        while not self.start_trigger.is_set() and not self.stop_trigger.is_set():
            self._process_queues()
            time.sleep(0.001)
        self.is_running.set()

        # Apply any pending/staged camera parameters now before starting acquisition
        try:
            if hasattr(self.cam, "apply_params") and callable(self.cam.apply_params):
                self.cam.apply_params()
        except Exception as e:
            display(f"Failed to apply camera params before start: {e}", level='error')

        # --- make sure we're disarmed before arming again ---
        try:
            if hasattr(self.cam, "stop") and callable(self.cam.stop):
                self.cam.stop()
        except Exception:
            pass
        # -----------------------------------------------------

            
        # Ensure writer is ready BEFORE starting the camera if recording is requested
        try:
            if self.saving.is_set() and getattr(self, 'writer', None) is None:
                try:
                    self.writer = self._open_writer()
                    # Give filesystem a tiny moment to finish opening handles
                    time.sleep(0.01)
                except ValueError as e:
                    display(f"Cannot start recording: {e}", level='error')
                    self.saving.clear()  # Abort recording for this run
        except Exception as e:
            display(f"Unexpected error while preparing writer: {e}", level='warning')
        try:    
            if hasattr(self.cam, "start") and callable(self.cam.start):
                self.cam.start()
        except Exception:
            pass

        mode_triggered = getattr(self.cam, "is_triggered", lambda: False)()
        src = str(getattr(self.cam, "params", {}).get("trigger_source", "")).lower()
        if mode_triggered and src == "software" and hasattr(self.cam, "fire_software_trigger"):
            self.cam.fire_software_trigger()
        self.camera_ready.clear()

    def load_cam_settings(self, fpath):
        """Loads camera settings from a JSON file."""
        try:
            with open(fpath, 'r') as f:
                settings = json.load(f)
            for param, value in settings.items():
                self.set_cam_param(param, value)
            display(f"Loaded camera settings from {fpath}")
        except Exception as e:
            display(f"Error loading settings: {e}", level='error')

    def save_cam_settings(self, fpath):
        """Saves current camera settings to a JSON file."""
        # Ensure the filename ends with .json
        if not fpath.lower().endswith('.json'):
            fpath += '.json'

        try:
            # Query the camera for its current parameters
            self.query_cam_params()
            params = self.get_cam_params(timeout=1.0) # Wait up to 1s
            if params is None:
                display("Could not retrieve camera settings to save.", level='error')
                return

            with open(fpath, 'w') as f:
                json.dump(params, f, indent=4)
            display(f"Saved camera settings to {fpath}")
        except Exception as e:
            display(f"Error saving settings: {e}", level='error')
    
    def _process_queues(self):
        self._process_params()

    def _process_params(self):
        # Handle all pending requests in the queue
        params_to_set = False
        while not self.cam_param_InQ.empty():
            try:
                message = self.cam_param_InQ.get_nowait()
                if not isinstance(message, tuple) or not message:
                    continue

                command = message[0]
                if command == 'get':
                    # Always clear previous params from the queue
                    clear_queue(self.cam_param_OutQ)
                    # Send back a copy of all exposed params
                    for param, val in self.cam.params.items():
                        if param in self.cam.exposed_params:
                            self.cam_param_OutQ.put((param, val))
                    self.cam_param_get_flag.set()

                elif command == 'set' and len(message) == 3:
                    _, param, val = message
                    self.cam.set_param(param, val)
                    params_to_set = True

                elif command == 'set_folder' and len(message) == 2:
                    # Update folder path immediately; if writer exists, refresh filepath
                    _, folder_path = message
                    try:
                        self.set_folder_path(folder_path)
                        if hasattr(self, 'writer') and self.writer is not None:
                            self.writer.set_filepath(self.get_new_filepath())
                    except Exception:
                        pass

            except queue.Empty:
                break  # No more messages
        
        # If any 'set' commands were processed, apply them in one batch
        if params_to_set:
            pass
            # self.cam.apply_params() #-- DEFERRED until acquisition start

    def set_cam_param(self, param : str, val):
        """Puts a ('set', param, value) command on the input queue."""
        try:
            # Use a tuple to be consistent with the 'get' command
            self.cam_param_InQ.put(('set', param, val))
        except queue.Full:
            display(f"Warning: could not set cam param {param}, queue is full",
                    level='warning')

    def query_cam_params(self):
        # self.cam_param_OutQ.put(None) # Not needed with clear_queue
        self.cam_param_InQ.put(('get',))

    def get_cam_params(self, timeout=0.2):
        """
        Returns the camera parameters.
        timeout (float): The optional timeout in seconds.
        """
        params = {}
        tstart = time.time()
        while time.time() - tstart < timeout:
            try:
                param,val = self.cam_param_OutQ.get(timeout=0.01)
                params[param] = val
            except (queue.Empty, TypeError):
                break
        self.cam_param_get_flag.clear()
        return params if params else None
        
    def start_saving(self):
        self.saving.set()
        
    def stop_saving(self):
        self.saving.clear()
    
    def start_acquisition(self):
        # Wait briefly for readiness to avoid race with init_run()
        if not self.camera_ready.is_set():
            t0 = time.time()
            while time.time() - t0 < 1.5:
                if self.camera_ready.is_set():
                    break
                time.sleep(0.01)
        if self.camera_ready.is_set():
            self.is_acquisition_done.clear()
            self.start_trigger.set()
            return True
        display(f"Could not start acquisition, camera {self.cam_dict['description']} not ready", level='warning')
        return False
        
    def stop_acquisition(self):
        self.stop_trigger.set()
        self.start_trigger.clear()
        

    def close(self):
        self.close_event.set()
        self.stop_acquisition()
        # Only wait if the process was started
        if self.is_alive():
            self.handler_closed.wait(timeout=2.0)
            # If still alive, terminate forcefully
            if self.is_alive():
                try:
                    self.terminate()
                except Exception:
                    pass