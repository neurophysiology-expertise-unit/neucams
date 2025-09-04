import time
import cv2
import numpy as np
from .generic_cam import GenericCam
from ..utils import display

class OpenCVCam(GenericCam):
    """OpenCV camera driver for webcams, facecams, and USB cameras
    
    Supports standard USB webcams, built-in laptop cameras, and external USB cameras.
    Limited hardware trigger support - primarily free-run mode.
    """
    
    def __init__(self, cam_id=None, params=None, **kwargs):
        # Default cam_id to 0 if not specified (first available camera)
        if cam_id is None:
            cam_id = 0
            
        # Convert string cam_id to int if needed
        try:
            cam_id = int(cam_id)
        except (ValueError, TypeError):
            display(f"Warning: Invalid cam_id '{cam_id}', defaulting to 0", level='warning')
            cam_id = 0
            
        super().__init__(
            name='OpenCV', 
            cam_id=cam_id, 
            params=params or {}
        )
        
        # Set default parameters
        default_params = {
            'frame_rate': 30.0,
            'width': 640,
            'height': 480,
            'auto_exposure': True,
            'exposure': 0.5,  # 0.0 to 1.0 range
            'brightness': 0.5,
            'contrast': 0.5,
            'saturation': 0.5,
            'hue': 0.5,
            'gain': 0.5
        }
        
        # Merge with user params
        for key, value in default_params.items():
            if key not in self.params:
                self.params[key] = value
                
        # Set exposed parameters that can be controlled from UI
        self.exposed_params = [
            'frame_rate', 'width', 'height', 'auto_exposure', 'exposure',
            'brightness', 'contrast', 'saturation', 'hue', 'gain'
        ]
                
        # Initialize the camera to get format information
        self._init_camera_format()
        
    def _init_camera_format(self):
        """Initialize camera and determine format information"""
        cap = cv2.VideoCapture(self.cam_id)
        if not cap.isOpened():
            display(f"ERROR: Could not open OpenCV camera {self.cam_id}", level='error')
            self.format = {'width': 640, 'height': 480, 'dtype': np.uint8, 'n_chan': 3}
            cap.release()
            return False
            
        # Try to set desired resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.params.get('width', 640))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.params.get('height', 480))
        cap.set(cv2.CAP_PROP_FPS, self.params.get('frame_rate', 30))
        
        # Read a test frame to get actual format
        ret, frame = cap.read()
        if ret and frame is not None:
            # OpenCV typically returns BGR, we'll convert to RGB in image()
            height, width = frame.shape[:2]
            n_chan = 3 if len(frame.shape) == 3 else 1
            
            self.format = {
                'width': width,
                'height': height, 
                'dtype': np.uint8,  # OpenCV typically uses uint8
                'n_chan': n_chan
            }
            
            display(f"[OpenCV {self.cam_id}] Initialized: {width}x{height}, {n_chan} channels")
        else:
            display(f"ERROR: Could not read test frame from OpenCV camera {self.cam_id}", level='error')
            self.format = {'width': 640, 'height': 480, 'dtype': np.uint8, 'n_chan': 3}
            
        cap.release()
        return ret
        
    def is_connected(self):
        """Check if camera is available"""
        cap = cv2.VideoCapture(self.cam_id)
        is_open = cap.isOpened()
        if is_open:
            # Try to read a frame to make sure it's actually working
            ret, _ = cap.read()
            is_open = ret
        cap.release()
        return is_open
        
    def open(self):
        """Open camera for acquisition"""
        self.cap = cv2.VideoCapture(self.cam_id)
        if not self.cap.isOpened():
            display(f"ERROR: Failed to open OpenCV camera {self.cam_id}", level='error')
            return False
            
        # Apply camera settings
        self._apply_settings()
        display(f"[OpenCV {self.cam_id}] Camera opened and configured")
        return True
        
    def close(self):
        """Close camera"""
        if hasattr(self, 'cap') and self.cap is not None:
            self.cap.release()
            self.cap = None
        display(f"[OpenCV {self.cam_id}] Camera closed")
        
    def start(self):
        """Start acquisition - for OpenCV this is mostly a no-op"""
        if not hasattr(self, 'cap') or self.cap is None:
            self.open()
        display(f"[OpenCV {self.cam_id}] Acquisition started")
        
    def stop(self):
        """Stop acquisition"""
        # For OpenCV, we don't need to explicitly stop, just note it
        display(f"[OpenCV {self.cam_id}] Acquisition stopped")
        
    def image(self):
        """Capture and return next frame"""
        if not hasattr(self, 'cap') or self.cap is None:
            return None, "camera not opened"
            
        ret, frame = self.cap.read()
        if not ret or frame is None:
            return None, "failed to capture frame"
            
        # Convert BGR to RGB for consistency with other cameras
        if len(frame.shape) == 3 and frame.shape[2] == 3:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
        # Generate metadata (frame ID and timestamp)
        frame_id = getattr(self, '_frame_counter', 0)
        self._frame_counter = frame_id + 1
        timestamp = time.time()
        
        return frame, (frame_id, timestamp)
        
    def _apply_settings(self):
        """Apply camera parameter settings"""
        if not hasattr(self, 'cap') or self.cap is None:
            return
            
        # Frame rate
        fps = float(self.params.get('frame_rate', 30))
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        
        # Resolution 
        width = int(self.params.get('width', 640))
        height = int(self.params.get('height', 480))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        
        # Exposure settings
        auto_exposure = self.params.get('auto_exposure', True)
        if auto_exposure:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)  # Auto exposure on
        else:
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # Manual exposure
            exposure = float(self.params.get('exposure', 0.5))
            self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
            
        # Other settings (if supported by camera)
        settings_map = {
            'brightness': cv2.CAP_PROP_BRIGHTNESS,
            'contrast': cv2.CAP_PROP_CONTRAST, 
            'saturation': cv2.CAP_PROP_SATURATION,
            'hue': cv2.CAP_PROP_HUE,
            'gain': cv2.CAP_PROP_GAIN
        }
        
        for param_name, cv_prop in settings_map.items():
            if param_name in self.params:
                value = float(self.params[param_name])
                self.cap.set(cv_prop, value)
                
        display(f"[OpenCV {self.cam_id}] Settings applied: {fps}fps, {width}x{height}")
        
    def set_param(self, param_name, value):
        """Set a camera parameter"""
        self.params[param_name] = value
        
        # Apply immediately if camera is open
        if hasattr(self, 'cap') and self.cap is not None:
            if param_name == 'frame_rate':
                self.cap.set(cv2.CAP_PROP_FPS, float(value))
            elif param_name == 'width':
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(value))
            elif param_name == 'height': 
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(value))
            elif param_name == 'auto_exposure':
                if value:
                    self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
                else:
                    self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            elif param_name == 'exposure':
                self.cap.set(cv2.CAP_PROP_EXPOSURE, float(value))
            elif param_name == 'brightness':
                self.cap.set(cv2.CAP_PROP_BRIGHTNESS, float(value))
            elif param_name == 'contrast':
                self.cap.set(cv2.CAP_PROP_CONTRAST, float(value))
            elif param_name == 'saturation':
                self.cap.set(cv2.CAP_PROP_SATURATION, float(value))
            elif param_name == 'hue':
                self.cap.set(cv2.CAP_PROP_HUE, float(value))
            elif param_name == 'gain':
                self.cap.set(cv2.CAP_PROP_GAIN, float(value))
                
    def apply_params(self):
        """Apply all pending parameter changes"""
        if hasattr(self, 'cap') and self.cap is not None:
            self._apply_settings()
            
    def __enter__(self):
        self.open()
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
