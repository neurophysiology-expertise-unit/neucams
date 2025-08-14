# neucams/cams/genicam.py
from __future__ import annotations

import os, sys, time, glob
from pathlib import Path
import numpy as np
from harvesters.core import Harvester

from .generic_cam import GenericCam
from neucams.utils import display

# ----------------------------------------------------------------------
# Globals
_HARVESTER: Harvester | None = None
_LOADED_CTI: str | None = None   # for diagnostics/logging

# ----------------------------------------------------------------------
# CTI discovery / selection
def _find_cti_candidates() -> list[str]:
    """Return possible GenTL .cti paths (system/env first, then PyInstaller bundle)."""
    paths: list[str] = []

    # 1) Env vars the system/installer might have set (now highest priority)
    for env in ("HARVESTERS_GENTL_PATH", "GENICAM_GENTL64_PATH"):
        for p in os.environ.get(env, "").split(os.pathsep):
            if p:
                paths += glob.glob(os.path.join(p, "*.cti"))

    # 2) Known Matrix Vision default path (adapt if you support other vendors)
    mv_dir = Path(r"C:/Program Files/MATRIX VISION/mvIMPACT Acquire/bin/x64")
    if mv_dir.exists():
        paths += glob.glob(str(mv_dir / "*.cti"))

    # 3) PyInstaller bundle folder (now lowest priority)
    base = getattr(sys, "_MEIPASS", os.getcwd())
    cti_dir = Path(base) / "gentl"
    paths += glob.glob(str(cti_dir / "*.cti"))

    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for p in paths:
        if p not in seen:
            uniq.append(p)
            seen.add(p)
    return uniq


def get_gentl_producer_path(custom: str | None = None) -> str:
    """
    Return the CTI path we *plan* to use (first candidate or custom override).
    This doesn't guarantee it loads; loading is validated in get_harvester().

    Use this for logging/UI ("which CTI are we aiming to load?").
    """
    if custom:
        if not Path(custom).exists():
            raise FileNotFoundError(f"Custom CTI not found: {custom}")
        return custom

    candidates = _find_cti_candidates()
    if not candidates:
        raise FileNotFoundError(
            "No GenTL .cti files found. Bundle mvGenTLProducer.cti (plus DLLs) or set "
            "HARVESTERS_GENTL_PATH / GENICAM_GENTL64_PATH."
        )
    return candidates[0]


def _load_first_working_cti(h: Harvester, candidates: list[str]) -> str:
    """Try each candidate until one loads successfully. Return the working path."""
    last_error = None
    for cti in candidates:
        try:
            h.add_file(cti)
            h.update()
            return cti
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"Failed to load any CTI from: {candidates}\nLast error: {last_error}")


# ----------------------------------------------------------------------
# Harvester singleton accessor
def get_harvester(debug: bool = False) -> Harvester:
    global _HARVESTER, _LOADED_CTI
    if _HARVESTER is not None:
        return _HARVESTER

    h = Harvester()

    # Optional override for debugging
    force_cti = os.environ.get("GENICAM_FORCE_CTI", "").strip() or None
    candidates = _find_cti_candidates() if force_cti is None else [force_cti]
    if not candidates:
        raise FileNotFoundError(
            "No GenTL .cti files found. Ensure mvGenTLProducer.cti is shipped or env vars are set."
        )

    # Load the first that works
    working = _load_first_working_cti(h, candidates)
    _LOADED_CTI = working

    if debug:
        print("CTI loaded:", working)
        print("Devices:", h.device_info_list)

    _HARVESTER = h
    return _HARVESTER


def get_loaded_cti_path() -> str | None:
    """Return the CTI path that actually loaded (None if not initialized yet)."""
    return _LOADED_CTI


# ----------------------------------------------------------------------
# Convenience
def GenI_get_cam_ids(harvester: Harvester | None = None):
    h = harvester or get_harvester()
    infos = h.device_info_list
    ids = [getattr(dev, "serial_number", None) for dev in infos]
    return ids, infos


# ----------------------------------------------------------------------
# Camera wrapper
class GenICam(GenericCam):
    timeout_ms = 2000  # now clearly milliseconds

    def __init__(self, cam_id=None, params=None, format=None):
        self.h = get_harvester()
        if cam_id is None and self.h.device_info_list:
            cam_id = getattr(self.h.device_info_list[0], "serial_number", None)

        super().__init__(name='GenICam', cam_id=cam_id, params=params, format=format)

        default_params = {
            'exposure': 29000,
            'frame_rate': 30,
            'gain': 8,
            'gain_auto': False,
            'acquisition_mode': 'Continuous',
            'n_frames': 1,
            'triggered': False
        }
        self.exposed_params = [
            'frame_rate', 'gain', 'exposure', 'gain_auto',
            'triggered', 'acquisition_mode', 'n_frames'
        ]
        self.params = {**default_params, **self.params}
        default_format = {'dtype': np.uint8}
        self.format = {**default_format, **self.format}
        self._normalize_trigger_params()

    def _normalize_trigger_params(self):
        """Coalesce lower/upper case keys from JSON to the GenICam names we set."""
        p = self.params or {}
        def coalesce(*keys, default=None):
            for k in keys:
                if k in p:
                    return p[k]
            return default
        trig_mode = coalesce('TriggerMode', 'trigger_mode', default='Off')
        trig_src  = coalesce('TriggerSource', 'trigger_source', default='Line1')
        trig_act  = coalesce('TriggerActivation', 'trigger_activation', default='RisingEdge')
        # normalize into canonical keys used by apply_params()
        self.params['TriggerMode'] = trig_mode
        self.params['TriggerSource'] = trig_src
        self.params['TriggerActivation'] = trig_act
        # convenience: 'triggered' true if TriggerMode is On
        self.params['triggered'] = bool(p.get('triggered', False)) or str(trig_mode).lower() == 'on'

    # ------------------------------------------------------------------
    def is_connected(self):
        if self.h is None:
            display("Harvester library not available.", level='error')
            return False
        ids, devices = GenI_get_cam_ids(self.h)
        if not devices:
            display("No GenICam cams detected, check connections.", level='error')
            return False
        if self.cam_id in ids:
            display(f"Requested GenICam cam detected {self.cam_id}.", level='info')
            return True
        display(f"Requested GenICam cam NOT detected {self.cam_id}.", level='error')
        return False

    # ------------------------------------------------------------------
    def __enter__(self):
        if self.h is None:
            display('Harvester not available. Cannot open GenICam camera.', level='error')
            self.cam_handle = None
            return self

        ids, devices = GenI_get_cam_ids(self.h)
        cam_index = None
        for idx, dev in enumerate(devices):
            if getattr(dev, 'serial_number', None) == self.cam_id:
                cam_index = idx
                break
        if cam_index is None:
            display(f"Could not find camera with serial_number {self.cam_id}", level='error')
            self.cam_handle = None
            return self

        self.cam_handle = self.h.create(cam_index)
        self.cam_handle.__enter__()
        self.cam_handle.num_buffers = 2
        self.features = self.cam_handle.remote_device.node_map
        self.apply_params()
        self._record()
        self._init_format()
        return self

    # ------------------------------------------------------------------
    def __exit__(self, exc_type, exc_value, exc_traceback):
        if getattr(self, 'cam_handle', None):
            self.cam_handle.__exit__(exc_type, exc_value, exc_traceback)
            display('GenICam cam exited.')
        else:
            display('GenICam cam __exit__ called, but camera was never opened.', level='warning')
        self.close()
        # Return False so exceptions propagate
        return False

    def close(self):
        if getattr(self, 'h', None):
            display('GenICam cam closed.')
        else:
            display('GenICam cam close() called, but harvester was never opened.', level='warning')

    # ------------------------------------------------------------------
    def apply_params(self):
        if not getattr(self, 'cam_handle', None):
            display('apply_params() called, but camera was never opened.', level='warning')
            return
        params = {
            'EventNotification': 'On',
            'PixelFormat': 'Mono8',
            'AcquisitionFrameRate': self.params['frame_rate'],
            'Gain': self.params['gain'],
            'GainAuto': 'Once' if self.params['gain_auto'] else 'Off',
            'ExposureTime': self.params['exposure'],
            'ExposureMode': 'Timed'
        }
        for key, val in params.items():
            try:
                if hasattr(self.features, key):
                    getattr(self.features, key).value = val
            except Exception:
                pass

        # ----- extra: map trigger-related keys from JSON -----
        use_trigger = bool(self.params.get('triggered', False)) or \
                      (str(self.params.get('TriggerMode', 'Off')).lower() != 'off')
        if use_trigger:
            # Some GenICam stacks require TriggerSelector first
            for k, v in {
                'TriggerSelector':       self.params.get('TriggerSelector', 'FrameStart'),
                'TriggerMode':           self.params.get('TriggerMode', 'On'),
                'TriggerSource':         self.params.get('TriggerSource', 'Line1'),
                'TriggerActivation':     self.params.get('TriggerActivation', 'RisingEdge'),
                'lineDetectionLevel':    self.params.get('lineDetectionLevel', 'TTL'),
                'lineDebouncingPeriod':  self.params.get('lineDebouncingPeriod', 0)
            }.items():
                try:
                    if hasattr(self.features, k):
                        getattr(self.features, k).value = v
                except Exception:
                    pass

    # ------------------------------------------------------------------
    def get_features(self):
        if not getattr(self, 'cam_handle', None):
            display('get_features() called, but camera was never opened.', level='warning')
            return ''
        out = []
        nm = self.cam_handle.remote_device.node_map
        for feature_name in dir(nm):
            try:
                out.append(f"{feature_name}: {getattr(nm, feature_name).to_string()}")
            except Exception:
                pass
        return "\n".join(out)

    # ------------------------------------------------------------------
    def get_frame_generator(self, n_frames=None, timeout_ms: int = 0):
        idx = 0
        while (n_frames is None) or idx < n_frames:
            try:
                with self.cam_handle.fetch(timeout=timeout_ms) as buffer:
                    component = buffer.payload.components[0]
                    frame = np.copy(component.data.reshape(component.height, component.width))
                    timestamp = buffer.timestamp  # currently unused
            except Exception:
                frame = np.array([])
                timestamp = 0
            yield frame, idx, time.time() - self.t_start
            idx += 1

    def _record(self):
        if not getattr(self, 'cam_handle', None):
            display('_record() called, but camera was never opened.', level='warning')
            return
        self.cam_handle.start()
        limit = self.params['n_frames'] if self.params['acquisition_mode'] == "MultiFrame" else None
        self.t_start = time.time()
        self.frame_generator = self.get_frame_generator(
            n_frames=limit,
            timeout_ms=self.timeout_ms
        )
        self.is_recording = True

    def start(self):
        """Public method to begin acquisition."""
        # Replace _internal_start_function with whatever function
        # actually starts the frame grabbing in your GenICam class.
        if not self.is_recording:
            self._internal_start_function()

    def stop(self):
        if not getattr(self, 'cam_handle', None):
            display('stop() called, but camera was never opened.', level='warning')
            return
        self.cam_handle.stop()
        self.is_recording = False
        display('GenICam cam stopped.')

    def image(self):
        if not getattr(self, 'cam_handle', None):
            display('image() called, but camera was never opened.', level='warning')
            return None, 'not recording'
        if self.is_recording:
            try:
                frame, frame_id, time_stamp = next(self.frame_generator)
            except StopIteration:
                return None, "stop"
            except Exception:
                return None, 'error'
            if frame.size == 0:
                return None, "timeout"
            return frame, (frame_id, time_stamp)
        return None, 'not recording'
