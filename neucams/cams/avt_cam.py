# neucams/cams/avt_cam.py
import os, sys, ctypes
from pathlib import Path
import numpy as np
from multiprocessing import shared_memory

# ---------- optional: prefer bundled vmbpy/VmbC if present ----------
BASE    = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
AVT_DIR = BASE / "vmbpy"
PLUGS   = AVT_DIR / "plugins"

def _add_dir(p: Path):
    if p.exists():
        os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(p))

#_add_dir(AVT_DIR)
#_add_dir(PLUGS)

if (AVT_DIR / "__init__.py").exists():
    sys.path.insert(0, str(BASE))

core = AVT_DIR / "VmbC.dll"
if core.exists():
    try:
        ctypes.WinDLL(str(core))
    except OSError:
        pass

# ---------- vmbpy ----------
from vmbpy import VmbSystem, PixelFormat, VmbTimeout

from .generic_cam import GenericCam
from neucams.utils import display


def _has(cam, feat: str) -> bool:
    return hasattr(cam, feat)

def _auto_mode(val):
    if isinstance(val, bool):
        return "Continuous" if val else "Off"
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("off", "once", "continuous"):
            return s.capitalize()
    return None

def _clamp_to_node_range(node, value: float) -> float:
    try:
        lo, hi = node.get_range()
        v = min(max(float(value), float(lo)), float(hi))
        try:
            inc = node.get_increment()
            if inc and inc > 0:
                v = lo + round((v - lo) / inc) * inc
        except Exception:
            pass
        return float(v)
    except Exception:
        return float(value)


class AVTCam(GenericCam):
    """Allied Vision camera via Vimba X (vmbpy). Emits SHM tuples for frames."""
    timeout = 2000  # ms

    def __init__(self, cam_id=None, params=None, format=None, serial_number=None):
        self.serial_number = serial_number

        defaults = {
            "exposure": 20000.0,          # µs
            "frame_rate": 30.0,           # Hz (free-run only)
            "gain": 0.0,                  # dB
            "gain_auto": "Off",           # 'Off'|'Once'|'Continuous' or bool
            "exposure_auto": "Off",
            "acquisition_mode": "Continuous",
            "n_frames": 1,
            "triggered": False,           # convenience flag
            "trigger_mode": "Off",        # convenience string
            "trigger_source": "Line1",
            "trigger_activation": "RisingEdge",
            "binning": 1,
        }
        fmt = {"dtype": np.uint8}

        super().__init__(
            name="AVT",
            cam_id=cam_id,
            params={**defaults, **(params or {})},
            format={**fmt, **(format or {})},
        )

        self.exposed_params = [
            "frame_rate", "gain", "exposure", "gain_auto", "exposure_auto",
            "triggered", "trigger_mode", "trigger_source", "trigger_activation",
            "acquisition_mode", "n_frames", "binning",
        ]

        self.vimba = None
        self.cam_handle = None
        self._gen = None
        self.is_recording = False

    # ---------- API used by CameraHandler ----------
    def set_param(self, key, value):
        self.params[key] = value

    def is_connected(self) -> bool:
        try:
            with VmbSystem.get_instance() as vmb:
                for c in vmb.get_all_cameras():
                    if self.cam_id and c.get_id() == self.cam_id:
                        return True
                    if self.serial_number and hasattr(c, "get_serial") and c.get_serial() == self.serial_number:
                        return True
        except Exception:
            pass
        return False

    def __enter__(self):
        self.vimba = VmbSystem.get_instance(); self.vimba.__enter__()

        # Resolve serial -> id *after* Vimba is up
        if not self.cam_id and self.serial_number:
            for c in self.vimba.get_all_cameras():
                try:
                    if hasattr(c, "get_serial") and c.get_serial() == self.serial_number:
                        self.cam_id = c.get_id()
                        break
                except Exception:
                    continue

        if not self.cam_id:
            display(f"[AVT] Could not resolve camera (serial={self.serial_number}).", level="error")
            return self

        for c in self.vimba.get_all_cameras():
            if c.get_id() == self.cam_id:
                self.cam_handle = c
                break

        if not self.cam_handle:
            display(f"[AVT] Camera {self.cam_id} vanished.", level="error")
            return self

        self.cam_handle.__enter__()

        # Safe default pixel format
        try:
            self.cam_handle.set_pixel_format(PixelFormat.Mono8)
        except Exception:
            pass

        # Apply params before streaming
        self.apply_params()

        # Start synchronous stream loop
        self._start_stream()

        # Learn format once (blocking read)
        self._init_format()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.is_recording:
                self.stop()
            if self.cam_handle:
                self.cam_handle.__exit__(exc_type, exc_val, exc_tb)
        finally:
            if self.vimba:
                self.vimba.__exit__(exc_type, exc_val, exc_tb)
        return False

    # ---------- Params ----------
    def apply_params(self):
        if not self.cam_handle:
            display("[AVT] apply_params() before open", level="warning")
            return

        was = self.is_recording
        if was:
            self.stop()

        p = self.params

        # Acquisition mode first
        try:
            if _has(self.cam_handle, "AcquisitionMode"):
                self.cam_handle.AcquisitionMode.set(p.get("acquisition_mode", "Continuous"))
        except Exception:
            pass

        # Binning (both axes if present)
        try:
            b = int(p.get("binning", 1))
            if _has(self.cam_handle, "BinningHorizontal"):
                self.cam_handle.BinningHorizontal.set(b)
            if _has(self.cam_handle, "BinningVertical"):
                self.cam_handle.BinningVertical.set(b)
        except Exception:
            pass

        # Autos OFF if we want manual control
        ga = _auto_mode(p.get("gain_auto"))
        ea = _auto_mode(p.get("exposure_auto"))
        try:
            if ga and _has(self.cam_handle, "GainAuto"):
                self.cam_handle.GainAuto.set(ga)
        except Exception:
            pass
        try:
            if ea and _has(self.cam_handle, "ExposureAuto"):
                self.cam_handle.ExposureAuto.set(ea)
        except Exception:
            pass

        # Manual gain/exposure only when autos Off
        if (not ga or ga == "Off"):
            try:
                if _has(self.cam_handle, "Gain"):
                    self.cam_handle.Gain.set(float(p["gain"]))
            except Exception:
                pass
        if (not ea or ea == "Off"):
            exp_us = float(p["exposure"])
            ok = False
            if _has(self.cam_handle, "ExposureTime"):
                try:
                    self.cam_handle.ExposureTime.set(exp_us)  # µs
                    ok = True
                except Exception:
                    pass
            if not ok and _has(self.cam_handle, "ExposureTimeAbs"):
                try:
                    self.cam_handle.ExposureTimeAbs.set(exp_us)  # legacy naming on some models
                except Exception:
                    pass

        # Trigger vs free-run
        use_trigger = bool(p.get("triggered", False)) or \
                      str(p.get("trigger_mode", "Off")).strip().lower() == "on"

        if use_trigger:
            try:
                if _has(self.cam_handle, "TriggerSelector"):
                    self.cam_handle.TriggerSelector.set("FrameStart")
                if _has(self.cam_handle, "TriggerMode"):
                    self.cam_handle.TriggerMode.set("On")
                if _has(self.cam_handle, "TriggerSource"):
                    self.cam_handle.TriggerSource.set(p.get("trigger_source", "Line1"))
                if _has(self.cam_handle, "TriggerActivation"):
                    self.cam_handle.TriggerActivation.set(p.get("trigger_activation", "RisingEdge"))
            except Exception:
                pass
            # Disable AFR limiter in trigger mode
            try:
                if _has(self.cam_handle, "AcquisitionFrameRateEnable"):
                    self.cam_handle.AcquisitionFrameRateEnable.set(False)
            except Exception:
                pass
        else:
            # Free-run: ensure trigger off
            try:
                if _has(self.cam_handle, "TriggerMode"):
                    self.cam_handle.TriggerMode.set("Off")
            except Exception:
                pass

            # Enable AFR and set FPS
            fps_target = float(p.get("frame_rate", 30.0))

            afr_node = getattr(self.cam_handle, "AcquisitionFrameRate", None)
            afr_en   = getattr(self.cam_handle, "AcquisitionFrameRateEnable", None)

            # Enable AFR first if available
            try:
                if afr_en:
                    afr_en.set(True)
            except Exception:
                pass

            # Now set the frame rate (clamped to node range)
            try:
                if afr_node:
                    afr_node.set(_clamp_to_node_range(afr_node, fps_target))
            except Exception:
                pass

        # ---- Readback / verification (no noise, just the useful bits) ----
        applied = []
        def rb(name, fmt=str):
            try:
                if _has(self.cam_handle, name):
                    v = getattr(self.cam_handle, name).get()
                    applied.append(f"{name}={fmt(v)}")
                    return v
            except Exception:
                pass
            return None

        tm     = rb("TriggerMode")
        afr_en = rb("AcquisitionFrameRateEnable")
        if _has(self.cam_handle, "AcquisitionFrameRate"):
            try:
                applied.append(f"AcquisitionFrameRate={self.cam_handle.AcquisitionFrameRate.get():.3f}")
            except Exception:
                pass
        rb("ExposureAuto")
        rb("GainAuto")
        rb("ExposureTime", lambda v: f"{float(v):.1f} µs")
        rb("Gain",         lambda v: f"{float(v):.2f} dB")

        display(f"[AVT {self.cam_id}] Parameters applied → " + (" | ".join(applied) if applied else "n/a"))

        if was:
            self._start_stream()

    # ---------- Streaming: yield SHM tuples ----------
    def _start_stream(self):
        self.is_recording = True

        def _gen():
            prev_shm = None
            while self.is_recording:
                try:
                    f = self.cam_handle.get_frame(timeout_ms=self.timeout)
                except VmbTimeout:
                    continue
                if f is None:
                    continue

                # contiguous copy -> SHM (Windows-safe across processes)
                arr = f.as_numpy_ndarray()
                carr = np.ascontiguousarray(arr)
                shm = shared_memory.SharedMemory(create=True, size=carr.nbytes)
                np.ndarray(carr.shape, dtype=carr.dtype, buffer=shm.buf)[:] = carr
                meta = (f.get_id(), f.get_timestamp())

                # close previous producer handle after a full interval
                if prev_shm is not None:
                    try:
                        prev_shm.close()
                    except Exception:
                        pass
                prev_shm = shm

                yield (shm.name, carr.shape, str(carr.dtype)), meta

            if prev_shm is not None:
                try:
                    prev_shm.close()
                except Exception:
                    pass

        self._gen = _gen()

    def stop(self):
        self.is_recording = False
        self._gen = None
        display(f"[AVT {self.cam_id}] stream stopped.")

    def image(self):
        if not self.is_recording or self._gen is None:
            return None, "not recording"
        try:
            return next(self._gen)
        except StopIteration:
            return None, "stop"
        except Exception as err:
            display(f"[AVT {self.cam_id}] image() error: {err}", level="error")
            return None, "error"

    close = stop

    # ---------- Learn format (single direct frame, no SHM) ----------
    def _init_format(self):
        try:
            f = self.cam_handle.get_frame(timeout_ms=self.timeout)
        except VmbTimeout:
            return
        if f is None:
            return
        try:
            arr = f.as_numpy_ndarray()
            self.format['height'] = arr.shape[0]
            self.format['width']  = arr.shape[1]
            self.format['n_chan'] = arr.shape[2] if arr.ndim == 3 else 1
            if arr.dtype == np.uint16:
                self.format['dtype'] = np.uint16
            display(f"[AVT {self.cam_id}] Ready: {self.format['width']}x{self.format['height']} "
                    f"n_chan={self.format['n_chan']} dtype={self.format['dtype']}")
        except Exception:
            pass
