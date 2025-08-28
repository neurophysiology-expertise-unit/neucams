import time
import ctypes

import numpy as np
from neucams.utils import display


class GenericCam:
    """Abstract class for interfacing with the cameras
    Has last frame on multiprocessing array
    """
    def __init__(self, name = '', cam_id = None, params = None, format = None):
        
        self.name = name
        self.cam_id = cam_id
        self.cam_handle = None
        
        self.params = params if params is not None else {}
        self.format = format if format is not None else {}
        
        self.is_recording = False
        
        self.exposed_params = []
    
    def _init_format(self):
        frame, _ = self.image()
        if frame is not None:
            self.format['height'] = frame.shape[0]
            self.format['width'] = frame.shape[1]
            self.format['n_chan'] = frame.shape[2] if frame.ndim == 3 else 1
            display(f"{self.name} - size: {self.format['height']} x {self.format['width']}")
    
    def is_connected(self):
        pass
        
    def __enter__(self):
        return self
        
    def __exit__(self, type, value, traceback):
        self.close()

    def close(self):
        '''close cam - release driver'''
        pass
        
    def apply_params(self):
        pass
    
    def set_param(self, param, val):
        # print(f"Set param {param} : {val}", flush=True)
        self.params[param] = val
        
    def get_param(self, param : str):
        pass
    
    def get_features(self):
        """returns features as formatted string"""
        pass

    def is_triggered(self) -> bool:
        # best-effort default
        v = str(self.params.get("trigger_mode", "off")).lower()
        return v == "on"
        
    def _record(self):
        '''start camera acq'''
        pass

    def stop(self):
        '''stop camera acq'''
        pass
    
    def get_health_status(self):
        pass
    
    def image(self):
        pass

