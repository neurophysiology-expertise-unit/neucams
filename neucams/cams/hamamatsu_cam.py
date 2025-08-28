# neucams/cams/hamamatsu_cam.py — pyDCAM-backed Hamamatsu driver (lean, AVT-style)
from __future__ import annotations
import time
from typing import List, Optional, Tuple, Dict, Any
import numpy as np

from pyDCAM.dcamapi import dcamapi_init, dcamapi_uninit, HDCAM, DCAMError
from pyDCAM.dcamprop import DCAMIDPROP, DCAMPROPMODEVALUE
from pyDCAM.dcamapi_enum import DCAM_IDSTR

from .generic_cam import GenericCam


# ----------------- Runtime wrapper -----------------
class _DCAMRuntime:
    _booted = False
    _ref = 0

    def __enter__(self):
        if not self._booted:
            dcamapi_init()
            self._booted = True
        self._ref += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._ref -= 1
        if self._booted and self._ref <= 0:
            dcamapi_uninit()
            self._booted = False
            self._ref = 0

    @staticmethod
    def list_camera_ids() -> List[str]:
        cold = not _DCAMRuntime._booted
        if cold:
            count = dcamapi_init()
        else:
            count = dcamapi_init()
        ids: List[str] = []
        try:
            for i in range(count):
                with HDCAM(i) as cam:
                    ids.append(cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID))
        finally:
            if cold:
                dcamapi_uninit()
        return ids


# ----------------- Camera -----------------
class HamamatsuCam(GenericCam):
    """
    Minimal, opinionated pyDCAM driver with AVT-like params and loud before→after prints.
    Uses the Python API names only (dcamprop_getvalue, dcamprop_setgetvalue, etc.).
    """

    def __init__(
        self,
        cam_id: int | None = None,   # ignored; we resolve by serial if given
        params: dict | None = None,
        format: dict | None = None,
        *,
        serial_number: str | None = None,
        frame_count: int = 16,
    ):
        defaults = {
            # imaging
            "exposure": 20000.0,          # microseconds (converted to seconds)
            "binning": 1,
            # free-run fps (effective with INTERNAL trigger)
            "frame_rate": None,           # Hz; leave None to keep device default
            # trigger
            "trigger_source": "INTERNAL",  # INTERNAL | EXTERNAL | SOFTWARE
            # ROI
            "subarray_mode": False,
            "subarray_size": None,        # (w,h) or None
            "subarray_pos": (0, 0),       # (x,y)
            # logging
            "verbose": True,
        }
        fmt_defaults = {"dtype": np.uint16}

        merged_params = {**defaults, **(params or {})}
        super().__init__(
            name="Hamamatsu",
            cam_id=None,
            params=merged_params,
            format={**fmt_defaults, **(format or {})},
        )
        
        tm = str(merged_params.get("trigger_mode", "")).strip().lower()
        if "trigger_source" not in merged_params and tm:
            if tm in ("on", "true", "1"):
                merged_params["trigger_source"] = "EXTERNAL"
            elif tm in ("off", "false", "0"):
                merged_params["trigger_source"] = "INTERNAL"

        self.serial_number = serial_number
        self._rt = _DCAMRuntime()
        self._cam: Optional[HDCAM] = None
        self._wait = None
        self._bufs = max(3, int(frame_count))
        self._frame_idx = 0
        self.is_recording = False
        self._open_for_format = False

        self.exposed_params = [
            "exposure",
            "binning",
            "frame_rate",
            "trigger_source",
            "subarray_mode",
            "subarray_pos",
            "subarray_size",
            "verbose",
        ]

    # ----- tiny print helper -----
    def _v(self, msg: str):
        if self.params.get("verbose", True):
            print(f"[Hamamatsu] {msg}")

    # ----- helper: safe property write with NOTWRITABLE guard -----
    def _try_set_prop(self, prop, value, label: str):
        """Set a DCAM property; if it's not writable (or constrained), log and skip."""
        try:
            before = self._cam.dcamprop_getvalue(prop)
        except Exception:
            before = None
        try:
            applied = self._cam.dcamprop_setgetvalue(prop, float(value))
            after = self._cam.dcamprop_getvalue(prop)
            self._v(f"{label}: before={before} -> requested={value} -> applied={applied} (now={after})")
            return True
        except DCAMError as e:
            self._v(f"{label}: SKIPPED (not writable / constrained). Reason: {e}")
            return False
        except Exception as e:
            self._v(f"{label}: SKIPPED (unexpected). Reason: {e}")
            return False

    # ----- helper: if control prop is AUTO, don't try to override -----
    def _control_is_auto(self, ctrl_prop) -> bool:
        try:
            val = self._cam.dcamprop_getvalue(ctrl_prop)
            # Common DCAM convention: *_CONTROL enum has AUTO/MANUAL modes in DCAMPROPMODEVALUE
            return bool(val == float(DCAMPROPMODEVALUE.DCAMPROP_MODE__AUTO))
        except Exception:
            return False  # If control not present, treat as not-auto

    def __enter__(self):
        self._rt.__enter__()
        idx = self._resolve_camera_index()
        self._cam = HDCAM(idx).__enter__()
        self._v(
            f"Opened: model={self._cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_MODEL)} "
            f"id={self._cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)}"
        )

        if getattr(self, "_open_for_format", False):
            # apply only binning + subarray so width/height reflect intended format
            p = self.params
            if p.get("binning") is not None:
                try: self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_BINNING, int(p["binning"]), "Binning")
                except Exception: pass
            if bool(p.get("subarray_mode", False)):
                self._cam.subarray_mode = True
                if p.get("subarray_size"): self._cam.subarray_size = tuple(map(int, p["subarray_size"]))
                if p.get("subarray_pos") is not None: self._cam.subarray_pos = tuple(map(int, p["subarray_pos"]))
            else:
                self._cam.subarray_mode = False
            self._query_format()
            return self

        # normal run: _apply_params_minimal(); alloc buffers; start; etc.
        # Apply parameters BEFORE starting acquisition
        self._apply_params_minimal()

        # Learn format
        self._query_format()

        # Allocate & start
        self._cam.dcambuf_alloc(self._bufs)
        self._wait = self._cam.dcamwait_open()
        self._cam.dcamcap_start()
        self.is_recording = True
        self._frame_idx = 0
        self._v("Streaming started")
        self._log_effective_fps()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            self.stop()
        finally:
            if self._cam is not None:
                self._cam.__exit__(exc_type, exc_val, exc_tb)
                self._cam = None
            self._rt.__exit__(exc_type, exc_val, exc_tb)
        return False

    def stop(self):
        if not self._cam:
            return
        if self.is_recording:
            self._cam.dcamcap_stop()
        self.is_recording = False
        try:
            self._cam.dcambuf_release()
        finally:
            self._wait = None
        self._v("Streaming stopped")

    # ----- acquisition -----
    def image(self) -> Tuple[Optional[np.ndarray], str | Tuple[int, float]]:
        if not (self.is_recording and self._cam and self._wait):
            return None, "not recording"
        self._wait.dcamwait_start(timeout=1000)
        frame = self._cam.dcambuf_copyframe()
        if frame is None or frame.size == 0:
            return None, "timeout"
        meta = (self._frame_idx, time.time())
        self._frame_idx += 1
        return frame, meta

    # ----- params & format -----
    def _apply_params_minimal(self):
        p = self.params

        # Trigger source (set early; affects what is writable)
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

        # Exposure (us -> s). If there is an exposure control and it's AUTO, don't fight it.
        if p.get("exposure") is not None:
            # Try to put exposure control into MANUAL if possible (best-effort, no crash)
            try:
                if self._control_is_auto(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME_CONTROL):
                    self._v("ExposureTimeControl is AUTO → not forcing manual; skipping explicit ExposureTime set.")
                else:
                    secs = float(p["exposure"]) / 1e6
                    self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME, secs, "ExposureTime [s]")
            except Exception:
                # If control prop not present, just try to set exposure
                secs = float(p["exposure"]) / 1e6
                self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME, secs, "ExposureTime [s]")

        # Binning
        if p.get("binning") is not None:
            self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_BINNING, int(p["binning"]), "Binning")

        # ROI via high-level helpers
        if bool(p.get("subarray_mode", False)):
            self._cam.subarray_mode = True
            if p.get("subarray_size"):
                self._cam.subarray_size = tuple(map(int, p["subarray_size"]))
            if p.get("subarray_pos") is not None:
                self._cam.subarray_pos = tuple(map(int, p["subarray_pos"]))
            self._v(f"Subarray ON: size={self._cam.subarray_size} pos={self._cam.subarray_pos}")
        else:
            self._cam.subarray_mode = False
            self._v("Subarray OFF")

        # Free-run FPS (has effect with INTERNAL trigger). Only set if writable.
        if p.get("frame_rate") is not None:
            fps = float(p["frame_rate"])
            ok = self._try_set_prop(DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE, fps, "InternalFrameRate [Hz]")
            if not ok:
                self._v("InternalFrameRate is read-only here → using Exposure/ROI to drive effective FPS.")

    def is_triggered(self) -> bool:
        return str(self.params.get("trigger_source", "INTERNAL")).upper() != "INTERNAL"

    def _query_format(self):
        w = int(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_IMAGE_WIDTH))
        h = int(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_IMAGE_HEIGHT))
        self.format.update({
            "width":  w,
            "height": h,
            "n_chan": 1,
            "dtype":  np.uint16,
        })
        self._v(f"Format: {w}x{h} n_chan=1 dtype=uint16")

    def _log_effective_fps(self):
        # Prefer direct INTERNALFRAMERATE; otherwise derive from INTERNAL_FRAMEINTERVAL
        try:
            fr = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE))
            self._v(f"INTERNALFRAMERATE now {fr:.4f} Hz")
            return
        except Exception:
            pass
        try:
            iv = float(self._cam.dcamprop_getvalue(DCAMIDPROP.DCAM_IDPROP_INTERNAL_FRAMEINTERVAL))
            if iv > 0:
                self._v(f"INTERNAL_FRAMEINTERVAL {iv:.6f} s (≈ {1.0/iv:.3f} Hz)")
        except Exception:
            pass

    # ----- discovery -----
    def _resolve_camera_index(self) -> int:
        count = dcamapi_init()  # quick probe
        if count <= 0:
            raise RuntimeError("No Hamamatsu cameras detected.")
        if self.serial_number:
            for i in range(count):
                with HDCAM(i) as cam:
                    camid = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)
                    if str(self.serial_number) in str(camid):
                        return i
            raise RuntimeError(f"No Hamamatsu camera with serial '{self.serial_number}' found.")
        return 0

    def is_connected(self) -> bool:
        try:
            with self._rt:
                if self.serial_number:
                    try:
                        self._resolve_camera_index()
                        return True
                    except Exception:
                        return False
                return dcamapi_init() > 0
        except Exception:
            return False

    @staticmethod
    def list_cameras() -> List[str]:
        return _DCAMRuntime.list_camera_ids()
