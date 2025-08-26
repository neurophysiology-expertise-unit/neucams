import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLabel, QFileDialog, QMessageBox, QProgressBar
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from neucams.view.widgets import NeuCamsWindow
from neucams.utils import get_preferences, display, check_preferences, resolve_cam_id_by_serial
from neucams.camera_handler import CameraHandler, CameraFactory
from pathlib import Path
import logging
import platform
import sys
# Set global logging to INFO so neucams info messages show
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Remove global LAST_CONFIG_PATH
# neucams_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

PROJECT_ROOT = str(Path(__file__).parent.parent.parent)

def get_installed_config_dir():
    """Get the jsonfiles directory from the installed location"""
    # Check if we're running from an installed location
    if getattr(sys, 'frozen', False):
        # Running from PyInstaller bundle
        base_path = os.path.dirname(sys.executable)
        installed_config = os.path.join(base_path, 'jsonfiles')
        if os.path.isdir(installed_config):
            return installed_config
    
    # Check if we're running from source and jsonfiles exists
    source_config = os.path.join(PROJECT_ROOT, 'neucams', 'jsonfiles')
    if os.path.isdir(source_config):
        return source_config
    
    return None

def get_default_config_dir():
    """Get the default directory for config files"""
    # First try installed location
    installed_dir = get_installed_config_dir()
    if installed_dir:
        return installed_dir
    
    # Fallback to user's Desktop
    desktop = os.path.expanduser("~/Desktop")
    if os.path.isdir(desktop):
        return desktop
    
    # Final fallback to current working directory
    return os.getcwd()

CONFIG_DIR = get_installed_config_dir() or get_default_config_dir()

def get_user_config_dir():
    if platform.system() == "Windows":
        base = os.getenv('LOCALAPPDATA', os.path.expanduser('~'))
        return os.path.join(base, "NeuCams")
    else:
        base = os.getenv('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        return os.path.join(base, "NeuCams")

USER_CONFIG_DIR = get_user_config_dir()
os.makedirs(USER_CONFIG_DIR, exist_ok=True)

# Helpers for saving/loading last config as a relative path

def get_last_config_path():
    return os.path.join(USER_CONFIG_DIR, '.last_config.txt')

def save_last_config(path):
    # Save absolute path instead of relative
    last_config_path = get_last_config_path()
    try:
        with open(last_config_path, 'w') as f:
            f.write(path)
    except Exception as e:
        display(f'Could not save last config: {e}', level='warning')

def load_last_config():
    last_config_path = get_last_config_path()
    try:
        if os.path.isfile(last_config_path):
            with open(last_config_path, 'r') as f:
                path = f.read().strip()
                if os.path.isfile(path):
                    return path
    except Exception as e:
        display(f'Could not load last config: {e}', level='warning')
    return None

# QThread for background loading (heavy camera setup)
class CameraSetupWorker(QThread):
    finished = pyqtSignal(object, object, object, str)  # (ret, prefs, cam_handlers, error_message)
    def __init__(self, config_path):
        super().__init__()
        self.config_path = config_path
    def run(self):
        ret, prefs = get_preferences(self.config_path)
        error_message = ""
        cam_handlers = []
        # If ret is a string, it's an error message from get_preferences
        if isinstance(ret, str):
            self.finished.emit(False, prefs, [], ret)
            return
        if ret:
            valid_drivers = list(CameraFactory.cameras.keys())
            error_message = check_preferences(prefs, valid_drivers=valid_drivers)
            if error_message:
                self.finished.emit(False, prefs, [], error_message)
                return
            for cam in prefs.get('cams', []):
                if cam.get('driver', '').lower() in valid_drivers:
                    writer_dict = prefs.get('recorder_params', {})
                    cam_handler = CameraHandler(cam, writer_dict)
                    if cam_handler.camera_connected:
                        cam_handlers.append((cam, cam_handler))
        self.finished.emit(ret, prefs, cam_handlers, error_message)

# Splash/launcher window
class SplashWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('NeuCams Launcher')
        self.setFixedSize(400, 320)
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        title = QLabel('NeuCams')
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet('font-size: 32px; font-weight: bold; margin-bottom: 30px;')
        layout.addWidget(title)
        self.choose_btn = QPushButton('Choose a JSON configuration file')
        self.choose_btn.setStyleSheet('font-size: 18px; padding: 16px;')
        self.choose_btn.clicked.connect(self.choose_config)
        layout.addWidget(self.choose_btn)
        self.last_btn = QPushButton('Open with last used config')
        self.last_btn.setStyleSheet('font-size: 18px; padding: 16px;')
        self.last_btn.clicked.connect(self.open_last_config)
        layout.addWidget(self.last_btn)
        self.last_config_label = QLabel('')
        self.last_config_label.setAlignment(Qt.AlignCenter)
        self.last_config_label.setStyleSheet('font-size: 12px; color: #555; margin-bottom: 10px;')
        layout.addWidget(self.last_config_label)
        self.loading_label = QLabel('Loading, please wait...')
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet('font-size: 16px; margin-top: 30px;')
        self.loading_label.hide()
        layout.addWidget(self.loading_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.setLayout(layout)
        self.worker_thread = None
        self.main_window = None
        # Track the last config directory (default to installed jsonfiles or fallback)
        self.last_config_dir = get_default_config_dir()
        self.update_last_config_label()

    def update_last_config_label(self):
        last = load_last_config()
        if last and os.path.isfile(last):
            # Show a user-friendly path
            if get_installed_config_dir() and last.startswith(get_installed_config_dir()):
                rel_path = os.path.relpath(last, get_installed_config_dir())
                self.last_config_label.setText(f'Last config: {rel_path}')
            else:
                # Show just the filename for non-installed paths
                filename = os.path.basename(last)
                self.last_config_label.setText(f'Last config: {filename}')
        else:
            self.last_config_label.setText('No last config found.')

    def choose_config(self):
        # Use installed jsonfiles directory as default, then fallback to user's Desktop
        default_dir = get_default_config_dir()
        fname, _ = QFileDialog.getOpenFileName(self, 'Select configuration file', default_dir, 'JSON Files (*.json)')
        if fname:
            self.start_loading()
            self.worker_thread = CameraSetupWorker(fname)
            self.worker_thread.finished.connect(lambda ret, prefs, cam_handlers, error_message: self.on_loaded(ret, prefs, cam_handlers, error_message, fname))
            self.worker_thread.start()
            self.update_last_config_label()

    def open_last_config(self):
        last = load_last_config()
        if last and os.path.isfile(last):
            self.start_loading()
            self.worker_thread = CameraSetupWorker(last)
            self.worker_thread.finished.connect(lambda ret, prefs, cam_handlers, error_message: self.on_loaded(ret, prefs, cam_handlers, error_message, last))
            self.worker_thread.start()
        else:
            QMessageBox.warning(self, 'No config found', 'No previous configuration file found in this folder.')
        self.update_last_config_label()

    def start_loading(self):
        self.choose_btn.setEnabled(False)
        self.last_btn.setEnabled(False)
        self.loading_label.show()
        self.progress_bar.show()

    def stop_loading(self):
        self.choose_btn.setEnabled(True)
        self.last_btn.setEnabled(True)
        self.loading_label.hide()
        self.progress_bar.hide()

    def on_loaded(self, ret, prefs, cam_handlers, error_message, config_path=None):
        if not ret or error_message:
            msg = error_message if error_message else 'Could not load preferences from the selected file.'
            QMessageBox.warning(self, 'Config Error', msg)
            self.loading_label.setText('')
            self.choose_btn.setEnabled(True)
            self.last_btn.setEnabled(True)
            self.stop_loading()
            return
        # Only save last config if it loaded successfully
        if config_path:
            save_last_config(config_path)
        self.update_last_config_label()
        self.main_window = NeuCamsWindow(preferences=prefs, preinit_cam_handlers=cam_handlers)
        self.main_window.show()
        self.hide()
        self.stop_loading() 