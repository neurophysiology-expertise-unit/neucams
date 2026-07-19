import sys
from os import path, makedirs
from datetime import datetime
import json
import time
import platform
import subprocess
import logging

# Set up a basic logger
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

def display(s, level='info'):
    """
    Prints a string to the console, optionally with a datestring
    level: 'info' (default), 'warning', 'error'
    """
    log_func = getattr(logging, level, logging.info)
    log_func(s)

DEFAULT_SERVER_PARAMS = {
                         'server': 'udp',
                         'server_refresh_time':30, #ms
                         'server_port':9999
                         }
                      
DEFAULT_RECORDER_PARAMS = {
                            'recorder': 'opencv',      # opencv | tiff | ffmpeg | binary
                            'data_folder': path.join(path.expanduser('~'), 'neucams_data'),
                            # 'experiment_folder' is optional; runtime defaults to 'test' if missing
                            'compress': 0
                          }

# Starter template written when no config exists. Uses a real, always-present
# driver (opencv) with valid parameter names so the template runs as-is; copy
# one of the jsonfiles/ presets for AVT/Dalsa/Hamamatsu setups.
DEFAULT_CAM_INFOS = [
                     {'description': 'webcam',
                      'driver': 'opencv',
                      'id': 0,
                      'params': {'frame_rate': 30, 'width': 1280, 'height': 720}},
                      ]

def get_default_folder():
    return path.join(path.expanduser('~'), 'neucams')

def get_default_preferences():
    return {'cams': DEFAULT_CAM_INFOS, 'recorder_params': DEFAULT_RECORDER_PARAMS, 'server_params' : DEFAULT_SERVER_PARAMS}
    
def write_template_to_file(filepath):
    display('Creating editable template.')
    dir_path = path.dirname(filepath)
    if not path.isdir(dir_path):
        makedirs(dir_path)
    with open(filepath, 'w') as outfile:
        json.dump(get_default_preferences(), outfile, sort_keys = True, indent = 4)
    display('Saved editable template to: ' + filepath)

def get_preferences(filepath = None, create_template = True):
    """
    
    """
    filepath = path.join(get_default_folder(),'default.json') if filepath is None else filepath
    pref = {}
    if path.isfile(filepath):
        try:
            with open(filepath, 'r') as infile:
                pref = json.load(infile)
            pref['user_config_path'] = filepath
            return True, pref
        except json.JSONDecodeError as e:
            error_msg = (f"JSON syntax error in file:\n{filepath}\nLine {e.lineno}, Column {e.colno}:\n{e.msg}")
            return error_msg, pref
        except Exception as e:
            return f"Error loading config file {filepath}: {e}", pref
    else:
        if create_template:
            write_template_to_file(filepath)
        return False, pref

def check_preferences(pref, valid_drivers=None):
    error_messages = ""
    
    def check_missing_keys(d, required_keys):
        return [key for key in required_keys if key not in d]
                
    cams = pref.get("cams", [])
    required_cam_keys = ['description', 'driver']
    descriptions = []
    for cam in cams:
        if "description" in cam:
            description =  cam["description"]
            if description in descriptions:
                error_messages += f"ERROR: descriptions have to be unique in your neucams config file at {pref.get('user_config_path', '')}. Those are used to determine the recorder subfolders.\n"
            descriptions.append(description)
        missing_keys = check_missing_keys(cam, required_cam_keys)
        if len(missing_keys) > 0:
            error_messages += f"ERROR: the following required keys are missing from your cam entry: {', '.join(missing_keys)}.\n"
        # Validate driver
        if valid_drivers is not None and 'driver' in cam:
            driver = cam['driver'].lower()
            if driver not in valid_drivers:
                error_messages += (
                    f"ERROR: Invalid driver '{driver}' in camera '{cam.get('description', '?')}'. "
                    f"Valid drivers are: {', '.join(valid_drivers)}.\n"
                )
    required_recorder_keys = ['data_folder']
    if not "recorder_params" in pref:
        error_messages += f"ERROR: there needs to be a recorder_params entry, with at least the following required keys: {', '.join(required_recorder_keys)}.\n"
    else:
        missing_keys = check_missing_keys(pref["recorder_params"], required_recorder_keys)
        if len(missing_keys) > 0:
            error_messages += f"ERROR: the following required keys are missing from your recorder_params entry: {', '.join(missing_keys)}.\n"
    return error_messages

def resolve_cam_id_by_serial(driver, serial_number):
    """
    Given a driver and serial_number, return the correct cam_id for use with the camera class.
    """
    driver = driver.lower()
    if driver == 'genicam':
        # For GenICam, the serial number is used as the ID.
        return serial_number
    elif driver == 'pco':
        # PCO cameras are often opened by index, not ID.
        return None
    elif driver == 'avt':
        try:
            from vmbpy import VmbSystem
            with VmbSystem.get_instance() as vmb:
                for cam in vmb.get_all_cameras():
                    if hasattr(cam, 'get_serial') and cam.get_serial() == serial_number:
                        return cam.get_id()
            display(f"No AVT camera found with serial number {serial_number}", level='warning')
            return None # Not found
        except ImportError:
            display("vmbpy not found, cannot resolve AVT camera by serial.", level='error')
            return None
        except Exception as e:
            display(f"An error occurred while resolving AVT cam by serial: {e}", level='error')
            return None
    elif driver == "hamamatsu":
        try:
            # new API shipped with the `hamamatsu` / `pyDCAM` wheels
            # (they just ctypes-load dcamapi.dll, so the DLL-in-PATH trick you did still works)
            from hamamatsu.dcam import dcam           # or: from pydcam import dcam

            with dcam:                                # opens the DCAM runtime
                for idx, cam in enumerate(dcam):      # camera objects are iterable
                    if cam.info.get("serial_number") == serial_number:  # <-- replacement!
                        return idx                    # DCAM uses the index as camera-ID
        except Exception as exc:
            display(f"Hamamatsu lookup failed: {exc}", level="warning")

        return None
    else:
        display(f"Serial number resolution not implemented for driver: {driver}", level='warning')
        return None
    