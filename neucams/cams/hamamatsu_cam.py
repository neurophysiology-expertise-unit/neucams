# neucams/cams/hamamatsu_cam.py — pyDCAM-backed Hamamatsu driver (lean, snake_case, clear logs)
from __future__ import annotations
import time
from typing import List, Optional, Tuple, Dict, Any
import numpy as np

from pyDCAM.dcamapi import dcamapi_init, dcamapi_uninit, HDCAM, DCAMError
from pyDCAM.dcamprop import DCAMIDPROP, DCAMPROPMODEVALUE
from pyDCAM.dcamapi_enum import DCAM_IDSTR

from .generic_cam import GenericCam

class _DCAMRuntime:
    _booted = False
    _ref = 0
    def __enter__(self):
        if not self._booted:
            dcamapi_init(); self._booted = True
        self._ref += 1
        return self
    def __exit__(self, exc_type, exc, tb):
        self._ref -= 1
        if self._booted and self._ref <= 0:
            dcamapi_uninit(); self._booted = False; self._ref = 0
    @staticmethod
    def list_camera_ids() -> List[str]:
        cold = not _DCAMRuntime._booted
        if cold: count = dcamapi_init()
        else:    count = dcamapi_init()
        ids: List[str] = []
        try:
            for i in range(count):
                with HDCAM(i) as cam:
                    ids.append(cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID))
        finally:
            if cold: dcamapi_uninit()
        return ids

class HamamatsuCam(GenericCam):
    def __init__(self, cam_id: int|None=None, params: dict|None=None, format: dict|None=None, *, serial_number: str|None=None, frame_count: int=16):
        defaults = {
            "exposure": 20000.0,          # microseconds
            "binning": 1,
            "frame_rate": None,           # Hz
            "trigger_source": "INTERNAL", # INTERNAL | EXTERNAL | SOFTWARE
            "exposure_auto": False,
            "gain": None,                 # ignored (not exposed on your model)
            "gain_auto": None,            # ignored
            "subarray_mode": False,
            "subarray_size": None,        # (w,h)
            "subarray_pos": (0,0),
            "verbose": True,
        }
        fmt_defaults = {"dtype": np.uint16}
        merged_params = {**defaults, **(params or {})}
        merged_params = {str(k).lower(): v for k,v in merged_params.items()}
        super().__init__(name="Hamamatsu", cam_id=None, params=merged_params, format={**fmt_defaults, **(format or {})})
        self.serial_number = serial_number
        self._rt = _DCAMRuntime()
        self._cam: Optional[HDCAM] = None
        self._wait = None
        self._bufs = max(3, int(frame_count))
        self._frame_idx = 0
        self.is_recording = False
        self.exposed_params = ["exposure","exposure_auto","binning","frame_rate","trigger_source","subarray_mode","subarray_pos","subarray_size","gain","gain_auto","verbose"]

    def _v(self, msg:str):
        if self.params.get("verbose", True):
            print(f"[Hamamatsu] {msg}")

    def _try_set_prop(self, prop, value, label: str):
        try:
            before = self._cam.dcamprop_getvalue(prop)
        except Exception:
            before = None
        try:
            applied = self._cam.dcamprop_setgetvalue(prop, float(value))
            after = self._cam.dcamprop_getvalue(prop)
            self._v(f"{label}: before={before} requested={value} applied={applied} now={after}")
            return True
        except DCAMError as e:
            self._v(f"{label}: SKIPPED (not writable / constrained). Reason: {e}")
            return False
        except Exception as e:
            self._v(f"{label}: SKIPPED (unexpected). Reason: {e}")
            return False

    def _control_is_auto(self, ctrl_prop) -> bool:
        try:
            val = self._cam.dcamprop_getvalue(ctrl_prop)
            return bool(val == float(DCAMPROPMODEVALUE.DCAMPROP_MODE__AUTO))
        except Exception:
            return False

    def __enter__(self):
        self._rt.__enter__()
        idx = self._resolve_camera_index()
        self._cam = HDCAM(idx).__enter__()
        self._v(f"Opened: model={self._cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_MODEL)} id={self._cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)}")
        self._apply_params_minimal()
        self._query_format()
        self._cam.dcambuf_alloc(self._bufs)
        self._wait = self._cam.dcamwait_open()
        self._cam.dcamcap_start()
        self.is_recording = True
        self._frame_idx = 0
        self._v("Streaming started")
        self._log_effective_fps()
        try:
            self._log_effective_timing()
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.stop()
        finally:
            if self._cam is not None:
                self._cam.__exit__(exc_type, exc_val, exc_tb); self._cam = None
            self._rt.__exit__(exc_type, exc_val, exc_tb)
        return False

    def stop(self):
        if not self._cam: return
        if self.is_recording: self._cam.dcamcap_stop()
        self.is_recording = False
        try: self._cam.dcambuf_release()
        finally: self._wait = None
        self._v("Streaming stopped")

    def image(self) -> Tuple[Optional[np.ndarray], str|Tuple[int,float]]:
        if not (self.is_recording and self._cam and self._wait): return None, "not recording"
        self._wait.dcamwait_start(timeout=1000)
        frame = self._cam.dcambuf_copyframe()
        if frame is None or frame.size == 0: return None, "timeout"
        meta = (self._frame_idx, time.time()); self._frame_idx += 1
        return frame, meta

    def _apply_params_minimal(self):
        p = self.params

        # Trigger source first
        ts = str(p.get("trigger_source", "INTERNAL")).strip().upper()
        ts_map: Dict[str, Any] = {
            "SOFTWARE": DCAMPROPMODEVALUE.DCAMPROP_TRIGGERSOURCE__SOFTWARE,
            "EXTERNAL": DCAMPROPMODEVALUE.DCAMPROP_TRIGGERSOURCE__EXTERNAL,
            "INTERNAL": DCAMPROPMODEVALUE.DCAMPROP_TRIGGERSOURCE__INTERNAL,
        }
        if ts in ts_map:
            self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_TRIGGERSOURCE, ts_map[ts], f"TriggerSource={ts}")
        else:
            self._v(f"TriggerSource ignored (unknown): {ts}")

        # Exposure auto/manual
        if p.get("exposure_auto") is not None:
            try:
                if p["exposure_auto"]:
                    self._cam.dcamprop_setgetvalue(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME_CONTROL, float(DCAMPROPMODEVALUE.DCAMPROP_MODE__AUTO))
                    self._v("ExposureTimeControl := AUTO")
                else:
                    self._cam.dcamprop_setgetvalue(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME_CONTROL, float(DCAMPROPMODEVALUE.DCAMPROP_MODE__MANUAL))
                    self._v("ExposureTimeControl := MANUAL")
            except Exception as e:
                self._v(f"ExposureTimeControl set error: {e}")

        # Exposure (µs → s) only if not AUTO
        if p.get("exposure") is not None:
            try:
                if self._control_is_auto(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME_CONTROL):
                    self._v("ExposureTimeControl is AUTO; skipping manual ExposureTime set.")
                else:
                    secs = float(p["exposure"]) / 1e6
                    self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME, secs, "ExposureTime [s]")
            except Exception:
                secs = float(p["exposure"]) / 1e6
                self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME, secs, "ExposureTime [s]")

        # Gain / GainAuto not present on your model (ignore if provided)
        if p.get("gain") is not None: self._v("Gain ignored (not exposed on this model).")
        if p.get("gain_auto") is not None: self._v("GainAuto ignored (not exposed on this model).")

        # Binning
        if p.get("binning") is not None:
            self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_BINNING, int(p["binning"]), "Binning")

        # ROI
        if bool(p.get("subarray_mode", False)):
            self._cam.subarray_mode = True
            if p.get("subarray_size"):
                try: self._cam.subarray_size = tuple(map(int, p["subarray_size"]))
                except Exception as e: self._v(f"SubarraySize not applied: {e}")
            if p.get("subarray_pos") is not None:
                try: self._cam.subarray_pos = tuple(map(int, p["subarray_pos"]))
                except Exception as e: self._v(f"SubarrayPos not applied: {e}")
            self._v(f"Subarray ON: size={self._cam.subarray_size} pos={self._cam.subarray_pos}")
        else:
            self._cam.subarray_mode = False
            self._v("Subarray OFF")

        # Internal frame rate (only if writable)
        if p.get("frame_rate") is not None:
            fps = float(p["frame_rate"])
            ok = self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE, fps, "InternalFrameRate [Hz]")
            if not ok:
                self._v("InternalFrameRate is read-only or constrained; effective FPS will be set by Exposure/Readout.")

        exp     = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME))
        ceiling = 1.0 / exp
        print(f"[HAMA] ceiling ≈ {ceiling:.2f} Hz  (exp={exp:.6f}s)")


    def _query_format(self):
        try:
            w = int(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_IMAGE_WIDTH))
            h = int(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_IMAGE_HEIGHT))
        except Exception as e:
            self._v(f"Could not get image dimensions: {e}")
            return
        self.format.update({"width": w, "height": h, "n_chan": 1, "dtype": np.uint16})
        self._v(f"Format: {w}x{h} n_chan=1 dtype=uint16")

    def _log_effective_fps(self):
        try:
            fr = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE))
            self._v(f"INTERNALFRAMERATE now {fr:.4f} Hz"); return
        except Exception:
            pass
        try:
            iv = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_INTERNAL_FRAMEINTERVAL))
            if iv > 0: self._v(f"INTERNAL_FRAMEINTERVAL {iv:.6f} s (≈ {1.0/iv:.3f} Hz)")
        except Exception:
            pass

    def _log_effective_timing(self):
        # Exposure (seconds)
        try:
            exp = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME))
        except Exception:
            exp = None

        # The *actual* frame period (seconds) while running
        try:
            iv = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_INTERNAL_FRAMEINTERVAL))
        except Exception:
            iv = None

        msg = "[Hamamatsu][timing]"
        msg += f" exposure={exp:.6f}s" if exp is not None else " exposure=?"
        if iv is not None and iv > 0:
            msg += f" | internal_frame_interval={iv:.6f}s (~{1.0/iv:.2f} Hz)"
        else:
            msg += " | internal_frame_interval=?"
        self._v(msg)

    def _resolve_camera_index(self) -> int:
        count = dcamapi_init()
        if count <= 0: raise RuntimeError("No Hamamatsu cameras detected.")
        if self.serial_number:
            for i in range(count):
                with HDCAM(i) as cam:
                    camid = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)
                    if str(self.serial_number) in str(camid): return i
            raise RuntimeError(f"No Hamamatsu camera with serial '{self.serial_number}' found.")
        return 0

    def is_connected(self) -> bool:
        try:
            with self._rt:
                if self.serial_number:
                    try: self._resolve_camera_index(); return True
                    except Exception: return False
                return dcamapi_init() > 0
        except Exception:
            return False

    @staticmethod
    def list_cameras() -> List[str]:
        return _DCAMRuntime.list_camera_ids()
