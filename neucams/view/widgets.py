# widgets.py
import sys
import os
import time
from os import getcwd
from os.path import dirname, join
import logging
from pathlib import Path


import numpy as np
from PyQt5 import uic
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import (QAction, QApplication, QMainWindow, QMessageBox,
                             QMdiSubWindow)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
# Delay before starting cameras via Master Start (ms): allows writers to arm
ARM_DELAY_MS = 3000
from collections import deque

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
# Resource helper (works for dev and PyInstaller)
# -----------------------------------------------------------------------------

def resource_path(name: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str((base / name).resolve())

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
    _udp_server_created = False  # one-per-process guard

    def __init__(self, preferences=None, preinit_cam_handlers=None):
        # Keep the same public API expected by the rest of the app
        self.preferences = preferences if preferences is not None else {}

        super().__init__()

        # Load the *new* Qt Designer layout
        uic.loadUi(join(dirpath, 'UI_NeuCams.ui'), self)

        # ---- Subtle styling improvements - closer to original ----
        self.setStyleSheet("""
        QLabel#save_location_label { font-size: 10pt; }
        QLabel#fps_label { font-size: 10pt; font-weight: 600; }
        QLabel#frame_nr_label { font-size: 10pt; }
        QToolButton#start_stop_pushButton { min-height: 34px; font-size: 11pt; }
        QCheckBox#record_checkBox { font-size: 10pt; }
        
        /* Subtle improvements */
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
        }
        QCheckBox::indicator:checked {
            background-color: #0078d4;
            border: 1px solid #0078d4;
        }
        QCheckBox::indicator:disabled {
            background-color: #f3f2f1;
            border: 1px solid #c8c6c4;
        }
        """)

        # Set window icon (works with PyInstaller when icon.ico is bundled)
        try:
            self.setWindowIcon(QIcon(resource_path('icon.ico')))
        except Exception:
            pass

        # Enlarge and style the Master Start/Stop tool button
        try:
            btn = self.mainToolBar.widgetForAction(self.actionMasterStartStop)
            if btn is not None:
                from PyQt5.QtCore import QSize
                btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
                btn.setMinimumWidth(120)
                btn.setMinimumHeight(32)
                self.mainToolBar.setIconSize(QSize(20, 20))
                btn.setStyleSheet(
                    "QToolButton {"
                    "  background-color: #f0f0f0;"
                    "  padding: 6px 14px;"
                    "  border: 1px solid #c0c0c0;"
                    "  border-radius: 4px;"
                    "  font-weight: 600;"
                    "  font-size: 11pt;"
                    "}"
                    "QToolButton:hover {"
                    "  background-color: #e8e8e8;"
                    "  border-color: #a0a0a0;"
                    "}"
                    "QToolButton:checked {"
                    "  background-color: #d4d4d4;"
                    "  border-color: #808080;"
                    "}"
                )
                # Add a small fixed-width spacer right after the Master Start/Stop
                from PyQt5.QtWidgets import QWidget
                spacer_after_master = QWidget(self)
                spacer_after_master.setFixedWidth(12)
                self.mainToolBar.addWidget(spacer_after_master)
        except Exception:
            pass

        # --- Universal Trigger control ---
        from PyQt5.QtWidgets import QCheckBox
        self.trigger_master_check = QCheckBox('Trigger', self)
        self.trigger_master_check.setChecked(False)  # default free-run
        self.trigger_master_check.stateChanged.connect(lambda _: self._broadcast_trigger_setting())
        self.mainToolBar.addWidget(self.trigger_master_check)

        # --- Global Run Name Controls ---
        from PyQt5.QtWidgets import QLineEdit, QPushButton, QLabel, QWidget, QHBoxLayout, QVBoxLayout, QSizePolicy
        # Container widget to allow vertical layout inside the horizontal toolbar
        self.run_container = QWidget(self)
        vbox = QVBoxLayout(self.run_container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)
        top_row = QWidget(self.run_container)
        hbox = QHBoxLayout(top_row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(6)
        self.run_name_edit = QLineEdit(self.run_container)
        self.run_name_edit.setPlaceholderText('session_name/run_name')
        self.run_name_edit.setFixedWidth(350)
        self.set_run_name_btn = QPushButton('Set', self.run_container)
        self.set_run_name_btn.setEnabled(False)
        self.set_run_name_btn.clicked.connect(self._on_set_run_name)

        # Connect text changes to update button state, path preview, and record controls
        self.run_name_edit.textChanged.connect(self._update_runname_controls_enabled)
        self.run_name_edit.textChanged.connect(self._update_global_path_label)
        self.run_name_edit.textChanged.connect(self._update_record_controls_enabled)
        hbox.addWidget(self.run_name_edit)
        hbox.addWidget(self.set_run_name_btn)
        top_row.setLayout(hbox)
        self.global_path_label = QLabel('', self.run_container)
        small_font = self.global_path_label.font()
        small_font.setPointSize(8)
        self.global_path_label.setFont(small_font)
        vbox.addWidget(top_row)
        vbox.addWidget(self.global_path_label)
        self.run_container.setLayout(vbox)
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.mainToolBar.addWidget(spacer)
        self.mainToolBar.addWidget(self.run_container)

        # --- Logging Setup ---
        handler = QtLogHandler(self)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s',
                                      datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        display("neucams started.")

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
        
        # Update initial control states
        self._update_record_controls_enabled()

        # ------------------------------------------------------------------
        # Optional UDP server (one per process) - gated by udp_enable flag
        # ------------------------------------------------------------------
        server_params = self.preferences.get('server_params', None)
        if (server_params and isinstance(server_params, dict)
            and bool(server_params.get('udp_enable', False))
            and not NeuCamsWindow._udp_server_created):
            try:
                self._init_udp_server(server_params)
                NeuCamsWindow._udp_server_created = True
            except Exception as e:
                logging.getLogger().warning(f"UDP server could not be initialized: {e}")

        # Setup periodic UI updates for run-name controls, trigger checkbox, and record controls
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._update_runname_controls_enabled)
        self._ui_timer.timeout.connect(self._update_global_path_label)
        self._ui_timer.timeout.connect(self._update_udp_status_label)
        self._ui_timer.timeout.connect(self._update_trigger_controls_enabled)
        self._ui_timer.timeout.connect(self._update_record_controls_enabled)
        self._ui_timer.start(300)
        self._update_global_path_label()

        # Status bar UDP indicator
        from PyQt5.QtWidgets import QLabel
        self.udp_status_label = QLabel('UDP: OFF', self)
        self.statusbar.addPermanentWidget(self.udp_status_label)

        # Misc UI initialisation
        self.mdiArea.setActivationOrder(1)
        self.menuView.triggered[QAction].connect(self._view_menu_actions)

        # --- Master Start/Stop wiring ---
        self.actionMasterStartStop.toggled.connect(self._master_toggled)
        self._master_ui_timer = QTimer(self)
        self._master_ui_timer.timeout.connect(self._update_master_action_enabled)
        self._master_ui_timer.start(150)
        self._update_master_action_enabled()

        self.show()

    # ------------------------------------------------------------------
    # Legacy helpers copied / simplified from the original widgets.py
    # ------------------------------------------------------------------

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
            self._add_widget(cam_dict.get('description', 'Camera'), widget)
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
    # UDP helpers
    # ------------------------------------------------------------------

    def _init_udp_server(self, server_params: dict):
        ip = str(server_params.get('server_ip') or '0.0.0.0')
        port = int(server_params.get('server_port', 9999))
        refresh = int(server_params.get('server_refresh_time', 30))

        self.server = None
        self._server_timer = None

        try:
            self.server = UDPSocket((ip, port))
            self._server_timer = QTimer(self)
            self._server_timer.timeout.connect(self._process_server_messages)
            self._server_timer.start(refresh)
            display(f"UDP server listening on {ip}:{port}")
        except Exception as e:
            # If UDPSocket() partially succeeded but something else failed, close it.
            if self.server is not None:
                try:
                    self.server.close()
                except Exception:
                    pass
                self.server = None
            logging.getLogger().warning(f"UDP server could not be initialized: {e}")


    def _handle_set_run_via_udp(self, run_spec: str):
        """Stops all cameras and sets the run name (but does NOT auto-start)."""
        run_spec = (run_spec or "").strip()
        if not run_spec:
            return

        if self._any_camera_running():
            QMessageBox.warning(
                self,
                'UDP Command Error',
                'Cannot change run name while cameras are active. Please stop all cameras first.'
            )
            return

        display(f"UDP command received: Set run name to '{run_spec}' (no auto-start).")

        # # 1) Stop any running acquisition (REMOVED: Now handled by preventing the command if running)
        # if self._any_camera_running():
        #     self.actionMasterStartStop.setChecked(False) # This will trigger _master_toggled to stop all

        # # 2) Wait for all cameras to be fully stopped (REMOVED: No longer need to wait if prevented)
        # t0 = time.time()
        # while not self._all_cameras_stopped() and (time.time() - t0 < 5.0):
        #     QApplication.processEvents() # Allow UI to process the stop events
        #     time.sleep(0.05)
        # 
        # if not self._all_cameras_stopped():
        #     display("UDP: Timed out waiting for cameras to stop. Path not changed.", level='warning')
        #     return

        # 3) Update UI textbox and apply the new run name to camera handlers
        self.run_name_edit.setText(run_spec)
        self._apply_run_name_to_cameras(run_spec)
        self._update_global_path_label()

        QMessageBox.information(
            self,
            'UDP Path Set',
            f'New path set to: {run_spec}\n'
            f'Frame counters reset to 0 for new session.\n'
            f'Use \"start\" command to begin acquisition.'
        )


    def _handle_master_start_via_udp(self, address):
        """Handle UDP 'start' command - master start all cameras."""
        if self._all_cameras_running():
            QMessageBox.warning(
                self,
                'UDP Command Error',
                'Cameras are already running. Please stop them before sending a new start command.'
            )
            display(f'UDP start command received but cameras already running [{address}]')
            self.server.send('ok=already_running', address)
            return

        # NEW: don't flip the button if master control is disabled (mixed state, etc.)
        if not self.actionMasterStartStop.isEnabled():
            display(f'UDP start ignored: Master control not available [{address}]')
            self.server.send('error=master_disabled', address)
            return

        display(f'UDP master start command received [{address}]')
        self.actionMasterStartStop.setChecked(True)  # Triggers _master_toggled
        self.server.send('ok=start', address)


    def _handle_master_stop_via_udp(self, address):
        """Handle UDP 'stop' command - master stop all cameras."""
        if self._all_cameras_stopped():
            display(f'UDP stop command received but cameras already stopped [{address}]')
            self.server.send('ok=already_stopped', address)
            return

        # NEW: don't flip the button if master control is disabled (mixed state, etc.)
        if not self.actionMasterStartStop.isEnabled():
            display(f'UDP stop ignored: Master control not available [{address}]')
            self.server.send('error=master_disabled', address)
            return

        display(f'UDP master stop command received [{address}]')
        self.actionMasterStartStop.setChecked(False)  # Triggers _master_toggled
        self.server.send('ok=stop', address)
    # widgets.py

    def _udp_apply_run_and_unlock(self, run_spec: str, address=None):
        """Apply run name from UDP exactly like the GUI 'Set' button, then unlock Record."""
        run_spec = str(run_spec).strip().strip('"').strip("'")

        # show in the UI text box (so the user sees it too)
        try:
            self.run_name_edit.setText(run_spec)
        except Exception:
            pass

        # Call the same handler the Set button uses (validates + applies to cameras)
        try:
            self._on_set_run_name()
        except Exception as e:
            display(f"[UDP] _on_set_run_name() failed: {e}", level="error")
            # still try to continue so we can show diagnostics
            # (you can 'return' here if you prefer to be strict)

        # Make sure the Record checkbox(es) get enabled like the GUI would
        try:
            self._update_record_controls_enabled()
        except Exception:
            pass

        # ---- DIAGNOSTICS: show base folder, run_spec, and each camera's final folder ----
        try:
            base = getattr(self.preferences, "recorder_params", {}).get("data_folder", "")
        except Exception:
            base = ""
        # Collect what each camera will actually use
        per_cam = []
        for w in getattr(self, "cam_widgets", []):
            ch = getattr(w, "cam_handler", None)
            descr = ""
            if ch and hasattr(ch, "cam_dict"):
                descr = ch.cam_dict.get("description", "")
            final_folder = None

            # If your handler exposes a current folder string/array, use it:
            if ch and hasattr(ch, "get_current_save_folder"):
                try:
                    final_folder = ch.get_current_save_folder()
                except Exception:
                    final_folder = None

            # Fallback: expected path based on your app’s convention
            if not final_folder:
                if base and descr and run_spec:
                    final_folder = os.path.normpath(os.path.join(base, descr, run_spec))
                else:
                    final_folder = "<unknown>"

            per_cam.append((descr or "<cam>", final_folder))

        display(f"[UDP] Applied run name: {run_spec!r} (base={base!r})", level="info")
        for descr, folder in per_cam:
            display(f"[UDP]   {descr}: {folder}", level="info")

        # Optional ACK
        try:
            if address and getattr(self, "server", None):
                self.server.send("ok=setrun", address)
        except Exception:
            pass

    def _process_server_messages(self):
        srv = getattr(self, 'server', None)
        if not srv:
            return
        ret, msg, address = srv.receive()
        if not ret:
            return

        raw = (msg or "").strip()
        if not raw:
            return

        # already working:
        if raw.lower() == "start":
            self._handle_master_start_via_udp(address); return
        if raw.lower() == "stop":
            self._handle_master_stop_via_udp(address); return

        # Treat these as RUN NAME (session/run), not absolute folder:
        if raw.upper().startswith("SET_PATH "):
            run_spec = raw[9:].strip()
            self._udp_apply_run_and_unlock(run_spec, address)
            return

        if raw.lower().startswith(("name=", "setrun=", "folder=")):
            _, run_spec = raw.split("=", 1)
            run_spec = run_spec.strip()
            self._udp_apply_run_and_unlock(run_spec, address)
            return

        # Optional: status probe
        if raw.lower() in ("status?", "whoami?"):
            try:
                base = getattr(self.preferences, "recorder_params", {}).get("data_folder", "")
            except Exception:
                base = ""
            rn = ""
            try:
                rn = self.run_name_edit.text().strip()
            except Exception:
                pass
            # quick record-enabled check
            rec_enabled = False
            for attr in ("record_checkbox", "recordCheckBox", "cb_record"):
                cb = getattr(self, attr, None)
                if cb is not None:
                    try:
                        rec_enabled = bool(cb.isEnabled())
                        break
                    except Exception:
                        pass
            msg_out = f"base={base}; run={rn}; record_enabled={rec_enabled}"
            self.server.send(msg_out, address)
            return

        # Unknown
        try:
            self.server.send("err=unknown_cmd", address)
        except Exception:
            pass

        
    # ------------------------------------------------------------------
    # Utility helpers (unchanged)
    # ------------------------------------------------------------------

    def _set_save_path(self, save_path):
        # Accept str | list | tuple; join tokens, strip quotes, expand vars, normalize
        if isinstance(save_path, (list, tuple)):
            save_path = " ".join(str(x) for x in save_path)
        else:
            save_path = str(save_path)

        save_path = save_path.strip().strip('"').strip("'")
        save_path = os.path.expandvars(save_path)
        save_path = save_path.replace('/', os.sep)

        # If relative, anchor it to the current UI folder (if any) or CWD
        try:
            current = self.dir_lineEdit.text().strip()
        except Exception:
            current = ""
        base = current if current else os.getcwd()
        if not os.path.isabs(save_path):
            save_path = os.path.normpath(os.path.join(base, save_path))
        else:
            save_path = os.path.normpath(save_path)

        # Ensure the folder exists
        try:
            os.makedirs(save_path, exist_ok=True)
        except Exception as e:
            display(f"Could not create folder {save_path}: {e}", level="error")

        # Update UI (ignore if widget missing) and propagate to handlers/writers
        try:
            self.dir_lineEdit.setText(save_path)
        except Exception:
            pass

        try:
            for w in getattr(self, "cam_widgets", []):
                ch = getattr(w, "cam_handler", None)
                if ch is None:
                    continue
                if hasattr(ch, "set_save_path"):
                    ch.set_save_path(save_path)
                elif hasattr(ch, "writer") and ch.writer:
                    ch.writer.set_filepath(save_path)
            display(f"Save path set to: {save_path}")
        except Exception as e:
            display(f"Failed to set save path: {e}", level="warning")

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

    # ---- Master action logic (fixed: disabled in mixed states) ----
    def _any_camera_running(self):
        for w in self.cam_widgets:
            ch = w.cam_handler
            if ch.start_trigger.is_set() and not ch.stop_trigger.is_set():
                return True
        return False

    def _all_cameras_running(self):
        if not self.cam_widgets:
            return False
        for w in self.cam_widgets:
            ch = w.cam_handler
            if not (ch.start_trigger.is_set() and not ch.stop_trigger.is_set()):
                return False
        return True

    def _all_cameras_stopped(self):
        if not self.cam_widgets:
            return False
        for w in self.cam_widgets:
            ch = w.cam_handler
            if ch.start_trigger.is_set() and not ch.stop_trigger.is_set():
                return False
        return True

    def _update_master_action_enabled(self):
        all_running = self._all_cameras_running()
        all_stopped = self._all_cameras_stopped()
        mixed = not (all_running or all_stopped)

        # Enable only when uniform; disable when mixed
        self.actionMasterStartStop.setEnabled(not mixed)

        if all_running:
            if not self.actionMasterStartStop.isChecked():
                self.actionMasterStartStop.setChecked(True)
            self.actionMasterStartStop.setText("Master Stop")
        elif all_stopped:
            if self.actionMasterStartStop.isChecked():
                self.actionMasterStartStop.setChecked(False)
            self.actionMasterStartStop.setText("Master Start")
        else:
            self.actionMasterStartStop.setText("Master (mixed)")

    def _master_toggled(self, checked: bool):
        # Safety: if mixed, bail (should already be disabled)
        if not (self._all_cameras_running() or self._all_cameras_stopped()):
            return

        if checked:
            # Broadcast trigger setting first, then start after a short delay
            try:
                self._broadcast_trigger_setting()
            except Exception:
                pass
            from PyQt5.QtCore import QTimer
            def _do_start_all():
                for w in self.cam_widgets:
                    ch = w.cam_handler
                    if ch.camera_ready.is_set():
                        started = ch.start_acquisition()
                        if started:
                            w._set_stop_text()
                self._update_master_action_enabled()
            QTimer.singleShot(60, _do_start_all)
        else:
            # Stop all
            for w in self.cam_widgets:
                ch = w.cam_handler
                is_running = ch.start_trigger.is_set() and not ch.stop_trigger.is_set()
                if is_running:
                    ch.stop_acquisition()
                    w._set_start_text()

        self._update_master_action_enabled()

    def _broadcast_trigger_setting(self):
        """Send the universal trigger mode to all cameras that can accept it.
        Cameras that do not recognize it will safely ignore it in apply_params()."""
        want_on = self.trigger_master_check.isChecked()
        val = 'On' if want_on else 'Off'
        
        # Collect cameras that can't be triggered for popup warning
        unsupported_cameras = []
        
        for w in self.cam_widgets:
            ch = w.cam_handler
            try:
                # Skip Dalsa/GenICam cameras entirely from global trigger control
                driver = ch.cam_dict.get('driver', '').lower()
                cam_description = ch.cam_dict.get('description', 'unknown')
                
                if driver == 'genicam':
                    display(f"Skipping trigger setting for Dalsa camera '{cam_description}' - not supported")
                    unsupported_cameras.append(f"{cam_description} (Dalsa/GenICam)")
                    continue
                
                # Optional guard using capability flag when available
                supported = True
                try:
                    supported = bool(getattr(ch, 'trigger_supported', None).value)
                except Exception:
                    supported = True  # best-effort
                    
                if not supported:
                    unsupported_cameras.append(f"{cam_description} ({driver})")
                    continue
                    
                if supported:
                    ch.set_cam_param('trigger_mode', val)
            except Exception:
                pass
            # reflect on per-camera checkbox if present
            try:
                if hasattr(w, 'trigger_checkBox'):
                    w.trigger_checkBox.setChecked(want_on)
            except Exception:
                pass
        
        if unsupported_cameras and want_on:
            camera_list = ', '.join(unsupported_cameras)
            display(f"Trigger not supported for: {camera_list}. They remain in free-run mode.")
        elif unsupported_cameras and not want_on:
            display(f"Trigger disabled. Note: {len(unsupported_cameras)} camera(s) don't support triggering anyway.")

        # 1) after _set_save_path(save_path) succeeds, emulate pressing Set:
    def _confirm_folder_set(self):
        """
        Call the same code path as the GUI 'Set' folder button so UI state updates
        (enables Record, clears grayed state, etc.).
        """
        # If you have a concrete slot for the Set button, call it here:
        try:
            self.on_setDirBtn_clicked()     # <-- replace with your real slot name
            return
        except AttributeError:
            pass

        # Fallback: enable Record directly if there is no dedicated slot
        for attr in ("record_checkbox", "recordCheckBox", "cb_record"):
            cb = getattr(self, attr, None)
            if cb is not None:
                try:
                    cb.setEnabled(True)
                    cb.setToolTip("")
                except Exception:
                    pass
                break
        # Optional internal flag many apps use:
        setattr(self, "_folder_is_set", True)


    # 2) run-name helper you can call from UDP
    def _apply_run_name(self, run_name: str):
        run_name = str(run_name).strip().strip('"').strip("'")
        # show in UI
        for attr in ("name_lineEdit", "run_lineEdit", "le_run", "le_name"):
            le = getattr(self, attr, None)
            if le is not None:
                try:
                    le.setText(run_name)
                except Exception:
                    pass
                break
        # hit the same slot as the GUI Set-Name button, if present
        for slot_name in ("on_setNameBtn_clicked", "on_btnSetName_clicked", "on_setRunBtn_clicked"):
            slot = getattr(self, slot_name, None)
            if callable(slot):
                try:
                    slot()
                    return
                except Exception:
                    pass
        # Fallback: if your code guards Record on a “name set” flag:
        setattr(self, "_run_name_is_set", True)

    # ------------------------------------------------------------------
    # Global run name helpers
    # ------------------------------------------------------------------
    def _get_data_folder(self):
        # recorder_params are shared across cams
        rec = self.preferences.get('recorder_params', {}) if isinstance(self.preferences, dict) else {}
        return rec.get('data_folder', None)

    def _update_runname_controls_enabled(self):
        # Only allow setting when all cameras are stopped
        enabled = not self._any_camera_running()
        self.set_run_name_btn.setEnabled(enabled and len(self.run_name_edit.text().strip()) > 0)
    
    def _update_trigger_controls_enabled(self):
        # Disable trigger checkbox when any camera is running
        enabled = not self._any_camera_running()
        self.trigger_master_check.setEnabled(enabled)
    
    def _update_record_controls_enabled(self):
        # Disable record checkboxes when no save path is set
        # Check if any camera actually has a folder path set (meaning "Set" was clicked)
        has_valid_path = False
        if self.cam_widgets:
            # Check if at least one camera has a folder path set
            for w in self.cam_widgets:
                ch = w.cam_handler
                if ch.get_folder_path().strip():
                    has_valid_path = True
                    break
        
        for w in self.cam_widgets:
            if hasattr(w, 'record_checkBox'):
                # Only enable if path is set AND camera is not currently running
                ch = w.cam_handler
                is_running = ch.start_trigger.is_set() and not ch.stop_trigger.is_set()
                enabled = has_valid_path and not is_running
                w.record_checkBox.setEnabled(enabled)
                
                # Set helpful tooltip based on why it's disabled
                if not enabled:
                    if not has_valid_path:
                        w.record_checkBox.setToolTip("Set a save path first using the 'Set' button above")
                    elif is_running:
                        w.record_checkBox.setToolTip("Stop camera to change recording state")
                else:
                    w.record_checkBox.setToolTip("Enable/disable recording for this camera")

    def _update_global_path_label(self):
        data_folder = self._get_data_folder()
        name = self.run_name_edit.text().strip()
        camera_segment = 'camera_name'
        if not data_folder:
            self.global_path_label.setText('')
            return
        
        # Show structure: data_folder/camera_name/session_name/run_name
        if name:
            # Convert backslashes to forward slashes for display
            normalized_name = name.replace('\\', '/')
            base = join(data_folder, camera_segment, normalized_name)
        else:
            base = join(data_folder, camera_segment, 'session_name/run_name')
        
        # Convert all path separators to forward slashes for consistent display
        display_path = base.replace('\\', '/')
        self.global_path_label.setText(f'Save path: {display_path}')

    def _update_udp_status_label(self):
        # Reflect udp_enable preference and actual server object state
        enabled = bool((self.preferences.get('server_params') or {}).get('udp_enable', False))
        active = hasattr(self, 'server') and (self.server is not None)
        txt = 'UDP: ON' if (enabled and active) else ('UDP: OFF' if not enabled else 'UDP: ERROR')
        self.udp_status_label.setText(txt)

    def set_global_run_name(self, name: str):
        self.run_name_edit.setText(name)
        self._apply_run_name_to_cameras(name)
        self._update_global_path_label()
        self._update_record_controls_enabled()

    def _on_set_run_name(self):
        name = self.run_name_edit.text().strip()
        if not name:
            return

        # Block while any camera is running
        if self._any_camera_running():
            QMessageBox.warning(self, 'Cameras running', 'Stop all cameras before changing the save name.')
            return

        # Basic validation - disallow Windows-invalid filename chars
        invalid_chars = ['<', '>', ':', '"', '|', '?', '*']
        if any(char in name for char in invalid_chars):
            QMessageBox.warning(
                self,
                'Invalid characters',
                f'Path contains invalid characters: '
                f'{", ".join(char for char in invalid_chars if char in name)}\n'
                'Please use only letters, numbers, underscores, hyphens, and forward slashes.'
            )
            return

        # Apply and refresh
        self._apply_run_name_to_cameras(name)
        self._update_record_controls_enabled()
        self._update_global_path_label()

        # Minimal, accurate popup (no "frames saved" claims)
        QMessageBox.information(
            self,
            'Save Path Updated',
            f'New path set to: {name}\n'
            f'Frame counters reset to 0 for new session.'
        )


    def _apply_run_name_to_cameras(self, name: str):
        data_folder = self._get_data_folder()
        if not data_folder:
            return  # silent in UDP path

        # Expected format: session_name/run_name or session_name\run_name
        # Convert backslashes to forward slashes for consistency
        normalized_name = name.replace('\\', '/')
        
        for w in self.cam_widgets:
            ch = w.cam_handler
            cam_desc = ch.cam_dict.get('description', 'camera')
            # Structure: data_folder/camera_description/session_name/run_name
            new_folder = join(data_folder, cam_desc, normalized_name)
            # Ask handler to apply folder immediately (writer aware)
            try:
                ch.cam_param_InQ.put(('set_folder', new_folder))
            except Exception:
                # Fallback to shared-array update
                ch.set_folder_path(new_folder)
            # Refresh the filepath preview used by UI labels immediately
            new_fp = ch.get_new_filepath()
            if hasattr(w, "save_location_label"):
                # Convert backslashes to forward slashes for consistent display
                display_filepath = new_fp.replace('\\', '/')
                w.save_location_label.setText('Filepath: ' + display_filepath)
            
            # Reset frame counter display for clarity
            ch.total_frames.value = 0
            if hasattr(w, "frame_nr_label"):
                w.frame_nr_label.setText("frame: 0")



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
        # stop UDP polling first
        try:
            if hasattr(self, "_server_timer") and self._server_timer is not None:
                self._server_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "server") and self.server is not None:
                self.server.close()
                self.server = None
        except Exception:
            pass

        for cam_widget in self.cam_widgets:
            h = cam_widget.cam_handler
            try:
                h.close()
                h.join(timeout=2.0)
                if h.is_alive():
                    try:
                        h.terminate()
                    except Exception:
                        pass
            except Exception:
                pass
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

        # Optional: direct font control (instead of stylesheets)
        for _name, _pt, _bold in [
            ("save_location_label", 10, False),
            ("fps_label", 10, True),
            ("frame_nr_label", 10, False),
        ]:
            w = getattr(self, _name, None)
            if w:
                f = w.font()
                f.setPointSize(_pt)
                f.setBold(_bold)
                w.setFont(f)

        # Make the Start/Stop button a bit bigger by default
        if hasattr(self, "start_stop_pushButton"):
            self.start_stop_pushButton.setMinimumHeight(34)
            self.start_stop_pushButton.setMinimumWidth(90)

        # --- Connections ---
        self.start_stop_pushButton.clicked.connect(self._start_stop_toggled)
        self.record_checkBox.stateChanged.connect(self._record)
        self.display_settings_pushButton.clicked.connect(self._toggle_display_settings)
        self.image_processing_pushButton.clicked.connect(self._toggle_img_processing_settings)
        
        # Initialize record checkbox as disabled (will be enabled once save path is set)
        self.record_checkBox.setEnabled(False)

        # --- Child widgets ---
        self.display_settings = DisplaySettingsWidget(self)
        self.img_processing_settings = ImageProcessingWidget(self)

        # --- Image processing pipeline ---
        if hasattr(self.display_settings, 'pipeline'):
            pipeline = self.display_settings.pipeline
            pipeline.stages.insert(0, self.img_processing_settings.bg_subtract_stage)
            pipeline.stages.insert(0, self.img_processing_settings.blur_stage)

        # Reflect initial trigger state if known
        try:
            if hasattr(self, 'trigger_checkBox') and self.cam_handler is not None:
                # read current param (both styles supported)
                p = getattr(self.cam_handler, 'cam_dict', {}).get('params', {})
                trig_val = p.get('trigger_mode', p.get('trigger', p.get('TriggerMode', 'Off')))
                is_on = str(trig_val).lower() in ('on', 'true', '1') if isinstance(trig_val, str) else bool(trig_val)
                self.trigger_checkBox.setChecked(is_on)
        except Exception:
            pass

    def _update(self):
        if self.cam_handler is None:
            return
        dest = self.cam_handler.get_filepath()
        # Convert backslashes to forward slashes for consistent display
        display_dest = dest.replace('\\', '/')
        self.save_location_label.setText('Filepath: ' + display_dest)
        if self.frame_nr != self.cam_handler.total_frames.value:
            img = self.cam_handler.get_image()
            if isinstance(img, tuple) and len(img) == 3 and isinstance(img[0], str):
                shm_name, shape, dtype = img
                img, shm = frame_from_shm(shm_name, shape, dtype)
                img = np.array(img, copy=True)
                shm.close()
            self.original_img = img
            self.frame_nr = self.cam_handler.total_frames.value
            self.is_img_processed = False
            self._update_img()
        self._update_stats()
        self._sync_record_checkbox()

    def _toggle_trigger_checkbox(self, state):
        # Do not apply immediately if camera is running
        if self.cam_handler is None:
            return
        is_running = self.cam_handler.start_trigger.is_set() and not self.cam_handler.stop_trigger.is_set()
        if is_running:
            return
        # Update the camera parameter in the child process on next _process_params
        enabled = bool(state)
        # Canonical param key is 'trigger_mode': 'On'|'Off'
        try:
            val = 'On' if enabled else 'Off'
            self.cam_handler.set_cam_param('trigger_mode', val)
        except Exception:
            pass

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

    def _update_stats(self):
        current_time = time.time()
        current_frame = self.cam_handler.total_frames.value
        dt = current_time - self._prev_time
        df = current_frame - self._prev_frame_nr
        if dt >= 0.2: # Always check after 0.2s
            if df > 0:
                fps = df / dt
                self._fps_deque.append(fps)
                avg_fps = np.mean(self._fps_deque)
                self.fps_label.setText(f"{avg_fps:.1f} fps")
            else:
                # If no frames arrived, set FPS to 0.0
                self.fps_label.setText("0.0 fps")

            self._prev_time = current_time
            self._prev_frame_nr = current_frame
        self.frame_nr_label.setText(f"frame: {current_frame}")

    def _sync_record_checkbox(self):
        """Sync the record checkbox with the actual saving state from camera handler"""
        if self.cam_handler:
            # Check if saving state changed (e.g., turned off due to path error)
            is_saving = self.cam_handler.saving.is_set()
            is_checked = self.record_checkBox.isChecked()
            
            # If saving was turned off but checkbox is checked, uncheck it
            if not is_saving and is_checked:
                self.record_checkBox.blockSignals(True)  # Prevent triggering _record again
                self.record_checkBox.setChecked(False)
                self.record_checkBox.blockSignals(False)

    def _start_stop_toggled(self, checked):
        if checked:
            self.start_cam()
        else:
            self.stop_cam()

    def _set_start_text(self):
        self.start_stop_pushButton.setText("Start")
        self.start_stop_pushButton.setChecked(False)
        # Don't enable record checkbox here - let the path checking logic handle it

    def _set_stop_text(self):
        self.start_stop_pushButton.setText("Stop")
        self.start_stop_pushButton.setChecked(True)
        # Don't disable record checkbox here - let the path checking logic handle it

    def _record(self, state):
        if state:
            self.cam_handler.start_saving()
        else:
            self.cam_handler.stop_saving()

    def _toggle_display_settings(self):
        self.display_settings.setVisible(not self.display_settings.isVisible())

    def _toggle_img_processing_settings(self):
        self.img_processing_settings.setVisible(not self.img_processing_settings.isVisible())
