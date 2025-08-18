import sys
import os
import time
from os import getcwd, path
from os.path import dirname, join
from functools import lru_cache
import logging

import numpy as np
from PyQt5 import uic
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QMainWindow, QMessageBox,
                             QMdiSubWindow, QWidget)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from collections import deque

from .image_processing import (HistogramStretcher, ImageFlipper,
                               ImageProcessingPipeline, ImageRotator)
from neucams.udp_socket import UDPSocket
from neucams.utils import display
from neucams.camera_handler import CameraHandler

# Re-use the existing CamWidget implementation (and its helpers) from the legacy GUI.
from neucams.view.components import DisplaySettingsWidget, ImageProcessingWidget
from neucams.view.base_widgets import BaseCameraWidget, nparray_to_qimg

# -----------------------------------------------------------------------------
# Shared-memory helper (avoid importing AVT driver unless actually needed)
# -----------------------------------------------------------------------------
def frame_from_shm(shm_name, shape, dtype):
    from multiprocessing import shared_memory
    shm = shared_memory.SharedMemory(name=shm_name)
    arr = np.ndarray(shape, dtype=np.dtype(dtype), buffer=shm.buf)
    return arr, shm

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

dirpath = dirname(__file__)
legacy_icon_path = join(dirname(dirpath), 'view', 'icon', 'NeuCams.png')

# -----------------------------------------------------------------------------
# Logging Handler for GUI
# -----------------------------------------------------------------------------
class QtLogHandler(logging.Handler):
    """A custom logging handler that emits a signal for each log record."""
    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def emit(self, record):
        msg = self.format(record)
        self.parent.log_message.emit(msg)

class NeuCamsWindow(QMainWindow):
    """Next-gen wrapper that loads the *new* main-window .ui while keeping the
    proven backend logic from the legacy GUI.
    """
    log_message = pyqtSignal(str)
    _udp_server_created = False  # Re-use the one-per-process server guard

    def __init__(self, preferences=None, preinit_cam_handlers=None):
        # Keep the same public API expected by the rest of the app
        self.preferences = preferences if preferences is not None else {}

        super().__init__()

        # Load the *new* Qt Designer layout
        uic.loadUi(join(dirpath, 'UI_NeuCams.ui'), self)

        # --- Logging Setup ---
        # self.log_message.connect(self.log_textEdit.append)
        handler = QtLogHandler(self)
        # Optional: Add formatting to the handler
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                      datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        display("neucams started.")

        # Reuse the existing icon so we do not need to copy the whole folder
        # self.setWindowIcon(QIcon(legacy_icon_path))

        # ------------------------------------------------------------------
        # Camera widgets setup (logic copied from legacy implementation)
        # ------------------------------------------------------------------
        self.cam_widgets = []
        if preinit_cam_handlers is not None:
            for cam, cam_handler in preinit_cam_handlers:
                cam_handler.start()
                widget = CamWidget(cam_handler)
                widget.is_triggered = self._compute_is_triggered(cam)
                self.cam_widgets.append(widget)
                self._add_widget(cam.get('description'), widget)
        else:
            for cam in self.preferences.get('cams', []):
                if cam.get('driver') in ['avt', 'pco', 'genicam', 'hamamatsu']:
                    self._setup_camera(cam)

        # Arrange the camera windows in a grid
        self.mdiArea.tileSubWindows()

        # ------------------------------------------------------------------
        # Optional UDP server (one per process)
        # ------------------------------------------------------------------
        server_params = self.preferences.get('server_params', None)
        if (server_params is not None and
                not hasattr(NeuCamsWindow, '_udp_server_created')):
            server_type = server_params.get('server', None)
            if server_type == 'udp':
                self.server = UDPSocket((server_params.get('server_ip', '0.0.0.0'),
                                         server_params.get('server_port', 9999)))
                self._timer = QTimer(self)
                self._timer.timeout.connect(self._process_server_messages)
                self._timer.start(server_params.get('server_refresh_time', 100))
                NeuCamsWindow._udp_server_created = True

        # Misc UI initialisation
        self.mdiArea.setActivationOrder(1)
        self.menuView.triggered[QAction].connect(self._view_menu_actions)

        self.show()

    # ------------------------------------------------------------------
    # Legacy helpers copied / simplified from the original widgets.py
    # ------------------------------------------------------------------

    # widgets.py

    def _compute_is_triggered(self, cam_dict):
        p = (cam_dict or {}).get('params', {}) or {}
        trig_mode = str(p.get('TriggerMode', p.get('trigger_mode', 'off'))).lower()
        return bool(p.get('triggered', False)) or trig_mode == 'on'

    def _setup_camera(self, cam_dict):
        if 'settings_file' in cam_dict.get('params', {}):
            cam_dict['params']['settings_file'] = join(dirname(getcwd()), 'configs',
                                                    cam_dict['params']['settings_file'])
        writer_dict = {**self.preferences.get('recorder_params', {})}
        cam_handler = CameraHandler(cam_dict, writer_dict)
        cam_handler.start()  # <-- start the process
        if cam_handler.camera_connected:
            widget = CamWidget(cam_handler)
            widget.is_triggered = self._compute_is_triggered(cam_dict)
            self.cam_widgets.append(widget)
            self._add_widget(cam_dict.get('description', 'Camera'), widget)  # pass widget
        else:
            cam_handler.close()


    def _add_widget(self, name, widget):
        active_subwindows = [e.objectName() for e in self.mdiArea.subWindowList()]
        if name not in active_subwindows:
            subwindow = QMdiSubWindow(self.mdiArea)
            subwindow.setWindowTitle(name)
            subwindow.setObjectName(name)
            subwindow.setWidget(widget)
            subwindow.resize(widget.minimumSize().width() + 40,
                             widget.minimumSize().height() + 40)
            subwindow.show()
            subwindow.setProperty("center", True)
        else:
            widget.show()

    # ------------------------------------------------------------------
    # UDP helper
    # ------------------------------------------------------------------

    def _process_server_messages(self):
        ret, msg, address = self.server.receive()
        if not ret:
            return

        action, *value = [i.lower() for i in msg.split('=')]

        if action == 'ping':
            display(f'Server got pinged [{address}]')
            self.server.send('pong', address)

        elif action == 'folder':
            self._set_save_path(value)
            display(f'Folder changed to {value} [{address}]')
            self.server.send('ok=folder', address)

        elif action == 'start':
            display(f'Starting triggered cameras [{address}]')
            for cam_widget in self.cam_widgets:
                if getattr(cam_widget, 'is_triggered', False):
                    cam_widget.start_cam()
            self.server.send('ok=start', address)

        elif action == 'stop':
            display(f'Stopping triggered cameras [{address}]')
            for cam_widget in self.cam_widgets:
                if getattr(cam_widget, 'is_triggered', False):
                    cam_widget.stop_cam()
            self.server.send('ok=stop', address)

        elif action == 'done?':
            cam_descr = value[0] if value else ''
            for cam_widget in self.cam_widgets:
                if cam_widget.cam_handler.cam_dict.get('description') == cam_descr:
                    status = cam_widget.cam_handler.is_acquisition_done.is_set()
                    self.server.send(f'done?={status}', address)
                    return
            self.server.send('done?=camera not found', address)

        elif action == 'quit':
            display(f'Exiting [{address}]')
            self.server.send('ok=bye', address)
            self.close()

    # ------------------------------------------------------------------
    # Utility helpers (unchanged)
    # ------------------------------------------------------------------

    def _set_save_path(self, save_path):
        if os.path.sep == '/':
            save_path = save_path.replace('\\', os.path.sep)
        save_path = save_path.strip(' ')
        for cam_widget in self.cam_widgets:
            cam_widget.cam_handler.set_folder_path(save_path)

    def _view_menu_actions(self, q):
        if q.text() == 'Subwindow View':
            self.mdiArea.setViewMode(0)
        if q.text() == 'Tabbed View':
            self.mdiArea.setViewMode(1)
        elif q.text() == 'Cascade View':
            self.mdiArea.setViewMode(0)
            self.mdiArea.cascadeSubWindows()
        elif q.text() == 'Tile View':
            self.mdiArea.setViewMode(0)
            self.mdiArea.tileSubWindows()

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        reply = QMessageBox.question(self, 'Window Close',
                                     'Are you sure you want to close the window?',
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            event.accept()
            self.close()
        else:
            event.ignore()

    def close(self):
        for cam_widget in self.cam_widgets:
            cam_widget.cam_handler.close()
        time.sleep(0.5)
        display("NeuCams out, bye!")
        QApplication.quit()
        sys.exit()

class CamWidget(BaseCameraWidget):
    def __init__(self, cam_handler=None):
        super().__init__(cam_handler)
        uic.loadUi(join(dirpath, 'UI_cam.ui'), self)
        self.is_triggered = False

        # --- Initialize state ---
        self.original_img = None
        self.processed_img = None
        self.frame_nr = 0
        self._prev_time = time.time()
        self._prev_frame_nr = 0
        self._fps_deque = deque(maxlen=5)

        # --- Connections ---
        self.start_stop_pushButton.clicked.connect(self._start_stop_toggled)
        self.record_checkBox.stateChanged.connect(self._record)
        self.display_settings_pushButton.clicked.connect(self._toggle_display_settings)
        self.image_processing_pushButton.clicked.connect(self._toggle_img_processing_settings)

        # --- Child widgets ---
        self.display_settings = DisplaySettingsWidget(self)
        self.img_processing_settings = ImageProcessingWidget(self)

        # --- Image processing pipeline ---
        if hasattr(self.display_settings, 'pipeline'):
            pipeline = self.display_settings.pipeline
            pipeline.stages.insert(0, self.img_processing_settings.bg_subtract_stage)
            pipeline.stages.insert(0, self.img_processing_settings.blur_stage)

    def _update(self):
        if self.cam_handler is None:
            return
        dest = self.cam_handler.get_filepath()
        self.save_location_label.setText('Filepath: ' + dest)
        if self.frame_nr != self.cam_handler.total_frames.value:
            img = self.cam_handler.get_image()
            if isinstance(img, tuple) and len(img) == 3 and isinstance(img[0], str):
                shm_name, shape, dtype = img
                img, shm = frame_from_shm(shm_name, shape, dtype)
                img = np.array(img, copy=True)
                shm.close()
                shm.unlink()
            self.original_img = np.copy(img)
            self.is_img_processed = False
            self.frame_nr = self.cam_handler.total_frames.value
        self._update_stats()
        if self.cam_handler.start_trigger.is_set() and not self.cam_handler.stop_trigger.is_set():
            self._set_stop_text()
        else:
            self._set_start_text()
        if self.display_settings.isVisible():
            self.is_img_processed = False
        self._update_img()
        super().update()

    def _update_stats(self):
        current_time = time.time()
        current_frame = self.cam_handler.total_frames.value
        dt = current_time - self._prev_time
        df = current_frame - self._prev_frame_nr
        if dt >= 0.5 and df > 0:
            fps = df / dt
            self._fps_deque.append(fps)
            avg_fps = np.mean(self._fps_deque)
            self.fps_label.setText(f"{avg_fps:.1f} fps")
            self._prev_time = current_time
            self._prev_frame_nr = current_frame
        self.frame_nr_label.setText(f"frame: {current_frame}")

    def _update_img(self):
        if not self.cam_handler or not self.cam_handler.start_trigger.is_set():
            return
            
        if self.original_img is not None and self.original_img.size > 0:
            if not self.is_img_processed:
                self.processed_img = self.display_settings.process_img(self.original_img)
                self.is_img_processed = True
            pixmap = QPixmap(nparray_to_qimg(self.processed_img))
            pixmap = pixmap.scaled(self.img_label.width(), self.img_label.height(),
                                   self.AR_policy, Qt.FastTransformation)
            self.img_label.setPixmap(pixmap)

    def _start_stop_toggled(self, checked):
        if checked:
            self.start_cam()
        else:
            self.stop_cam()

    def _set_start_text(self):
        self.start_stop_pushButton.setText("Start")
        self.start_stop_pushButton.setChecked(False)
        self.record_checkBox.setEnabled(True)

    def _set_stop_text(self):
        self.start_stop_pushButton.setText("Stop")
        self.start_stop_pushButton.setChecked(True)
        self.record_checkBox.setEnabled(False)

    def _record(self, state):
        if state:
            self.cam_handler.start_saving()
        else:
            self.cam_handler.stop_saving()

    def _toggle_display_settings(self):
        self.display_settings.setVisible(not self.display_settings.isVisible())

    def _toggle_img_processing_settings(self):
        self.img_processing_settings.setVisible(not self.img_processing_settings.isVisible()) 