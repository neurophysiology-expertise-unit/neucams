# neucams/cams/avt_cam.py (vmbpy 1.1.1) — AVT driver with deterministic FPS and canonical snake_case params
import os, sys, ctypes, traceback
from pathlib import Path
import numpy as np
from multiprocessing import shared_memory

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
AVT_DIR = BASE / "vmbpy"
PLUGS   = AVT_DIR / "plugins"

def _add_dir(p: Path):
    if p.exists():
        os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(p))
        # print(f"[AVT][bootstrap] Added to PATH: {p}")

# If you bundle vmbpy/VmbC alongside the app, uncomment these:
# _add_dir(AVT_DIR); _add_dir(PLUGS)

def _env_verbose() -> bool:
    return str(os.environ.get("NEUCAMS_VERBOSE", "")).strip().lower() in ("1","true","yes","on")

# Best-effort preload (quiet unless env verbose)
if (AVT_DIR / "VmbC.dll").exists():
    try:
        ctypes.WinDLL(str(AVT_DIR / "VmbC.dll"))
        if _env_verbose():
            print(f"[AVT][bootstrap] Preloaded VmbC from {AVT_DIR / 'VmbC.dll'}")
    except OSError as e:
        if _env_verbose():
            print(f"[AVT][bootstrap] Could not preload VmbC: {e}")

from vmbpy import VmbSystem, PixelFormat, VmbTimeout, AccessMode, VmbCameraError
from .generic_cam import GenericCam

# ----------------- helpers -----------------
def _has(cam, feat: str) -> bool:
    return hasattr(cam, feat)

def _auto_mode(val):
    # accept: True/False or "off"/"once"/"continuous"
    if isinstance(val, bool):
        return "Continuous" if val else "Off"
    if isinstance(val, (int, float)):
        return "Continuous" if float(val) != 0.0 else "Off"
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("off", "false", "0"): return "Off"
        if s in ("once",):             return "Once"
        if s in ("on", "true", "1", "continuous"): return "Continuous"
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

def _safe_set(node, feat_name, value, clamp=False, vprint=lambda *_: None):
    try:
        before = None
        try:
            before = node.get()
        except Exception:
            pass
        v = _clamp_to_node_range(node, value) if clamp else value
        node.set(v)
        after = None
        try:
            after = node.get()
        except Exception:
            pass
        vprint(f"[AVT][set] {feat_name}: before={before} requested={value} now={after}")
        return after
    except Exception as e:
        vprint(f"[AVT][set:EXC] {feat_name}: requested={value} error={e}")
        return None

# ----------------- camera -----------------
class AVTCam(GenericCam):
    timeout = 2000  # ms

    def __init__(self, cam_id=None, params=None, format=None, serial_number=None):
        self.serial_number = serial_number
        self._read_only = False

        # Canonical snake_case params only
        defaults = {
            "pixel_format": "Mono8",
            "exposure": 10000.0,            # microseconds
            "exposure_auto": "Off",       # "Off"|"Once"|"Continuous" or True/False
            "gain": 0.0,
            "gain_auto": "Off",
            "binning": 1,
            "reverse_x": False,
            "reverse_y": False,
            "acquisition_mode": "Continuous",
            "n_frames": 1,
            "frame_rate": None,           # Hz; if None, leave as-is
            "trigger_mode": "Off",        # "On"/"Off"
            "trigger_source": "Line1",
            "trigger_activation": "RisingEdge",
            "trigger_delay_us": None,
            "stream_constrain": True,     # bool
            "stream_bps": None,           # override bps if you insist
            "packet_size": None,
            "require_full_access": False,
            "verbose": None,
        }
        fmt = {"dtype": np.uint8}
        merged_params = {**defaults, **(params or {})}
        super().__init__(
            name="AVT",
            cam_id=cam_id,
            params=merged_params,
            format={**fmt, **(format or {})},
        )

        self.exposed_params = [
            "pixel_format","frame_rate","exposure","exposure_auto","gain","gain_auto","binning",
            "reverse_x","reverse_y","trigger_mode","trigger_source","trigger_activation","trigger_delay_us",
            "acquisition_mode","n_frames","stream_constrain","stream_bps","packet_size","require_full_access","verbose",
        ]

        pverb = self.params.get("verbose", None)
        self._verbose = True if _env_verbose() else (bool(pverb) if pverb is not None else False)
        self._v = (lambda msg: print(msg)) if self._verbose else (lambda *_: None)

        self.vimba = None
        self.cam_handle = None
        self._gen = None
        self.is_recording = False

    def set_param(self, key, value):
        self._v(f"[AVT][set_param] {key} := {value}")
        self.params[key] = value

    def is_connected(self) -> bool:
        try:
            with VmbSystem.get_instance() as vmb:
                for c in vmb.get_all_cameras():
                    sid = getattr(c, 'get_serial', lambda: None)()
                    if self.cam_id and c.get_id() == self.cam_id:
                        self._v(f"[AVT][is_connected] Found by id: {self.cam_id}")
                        return True
                    if self.serial_number and sid == self.serial_number:
                        self._v(f"[AVT][is_connected] Found by serial: {self.serial_number}")
                        return True
        except Exception as e:
            self._v(f"[AVT][is_connected:EXC] {e}")
        return False

    def __enter__(self):
        self._v("[AVT][enter] Opening Vimba system…")
        self.vimba = VmbSystem.get_instance()
        self.vimba.__enter__()

        # Resolve serial -> id
        if not self.cam_id and self.serial_number:
            for c in self.vimba.get_all_cameras():
                try:
                    sid = getattr(c, 'get_serial', lambda: None)()
                    self._v(f"[AVT][enter] Probe cam id={c.get_id()} serial={sid}")
                    if sid == self.serial_number:
                        self.cam_id = c.get_id()
                        self._v(f"[AVT][enter] Resolved cam_id from serial {self.serial_number} -> {self.cam_id}")
                        break
                except Exception as e:
                    self._v(f"[AVT][enter:probe:EXC] {e}")

        if not self.cam_id:
            self._v(f"[AVT] Could not resolve camera (serial={self.serial_number}).")
            return self

        for c in self.vimba.get_all_cameras():
            if c.get_id() == self.cam_id:
                self.cam_handle = c
                break

        if not self.cam_handle:
            self._v(f"[AVT] Camera {self.cam_id} vanished.")
            return self

        # ---- Open camera (prefer Full) ----
        try:
            if hasattr(self.cam_handle, "_Camera__access_mode"):
                setattr(self.cam_handle, "_Camera__access_mode", AccessMode.Full)
                self._v("[AVT][enter] Requested AccessMode.Full")
            self.cam_handle.__enter__()
            self._read_only = False
            self._v(f"[AVT {self.cam_id}] Opened with access: Full")
        except VmbCameraError as e:
            if self.params.get("require_full_access", False):
                self._v(f"[AVT][enter] Full access required but not available: {e}")
                raise
            if hasattr(self.cam_handle, "_Camera__access_mode"):
                setattr(self.cam_handle, "_Camera__access_mode", AccessMode.Read)
                self._v("[AVT][enter] Falling back to AccessMode.Read")
            self.cam_handle.__enter__()
            self._read_only = True
            self._v(f"[AVT {self.cam_id}] Opened with access: Read")

        # Pixel format (best-effort)
        pf = self.params.get("pixel_format", "Mono8")
        enum = getattr(PixelFormat, pf) if isinstance(pf, str) else pf
        try:
            self.cam_handle.set_pixel_format(enum)
            self._v(f"[AVT][pixfmt] desired={pf}")
        except Exception as e:
            self._v(f"[AVT][pixfmt:EXC] desired={pf} error={e}")

        if not self._read_only:
            self._v(f"[AVT {self.cam_id}] Applying parameters from config…")
            self.apply_params()
        else:
            self._v(f"[AVT {self.cam_id}] Read-only; skipping apply_params().")

        if self._read_only and bool(self.params.get("require_full_access", False)):
            self._v(f"[AVT {self.cam_id}] require_full_access=True and access is Read. Not starting stream.")
            return self

        self._start_stream()
        self._init_format()
        try:
            self._log_resulting_fps(self.cam_handle)
        except Exception:
            pass
        try:
            self._log_rate_limits_and_measured()
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._v(f"[AVT {self.cam_id}] __exit__ start (exc={exc_type})")
        try:
            if self.is_recording:
                self.stop()
            if self.cam_handle:
                try:
                    if hasattr(self.cam_handle, "close"):
                        self.cam_handle.close()
                    else:
                        self.cam_handle.__exit__(exc_type, exc_val, exc_tb)
                    self._v(f"[AVT {self.cam_id}] Camera closed")
                except Exception as e:
                    self._v(f"[AVT {self.cam_id}] close error: {e}")
        finally:
            if self.vimba:
                self.vimba.__exit__(exc_type, exc_val, exc_tb)
                self._v(f"[AVT {self.cam_id}] Vimba system closed")
        return False

    # --- FPS: deterministic, bandwidth-locked --------------------------------
    def _set_fps_locked(self, cam, fps: float):
        """Compute required link budget, disable constrain, set bps, then set frame rate."""
        # image payload
        try:
            img_size = cam.get_feature_by_name("ImageSize").get()
        except Exception:
            img_size = None

        # 1) disable constrain if present
        try:
            if hasattr(cam, "StreamFrameRateConstrain"):
                _safe_set(getattr(cam, "StreamFrameRateConstrain"), "StreamFrameRateConstrain", False, vprint=self._v)
        except Exception as e:
            self._v(f"[AVT][fps] StreamFrameRateConstrain set error: {e}")

        # 2) set bps with headroom (unless user forced stream_bps)
        if self.params.get("stream_bps") is not None:
            want_bps = int(self.params["stream_bps"])
        else:
            if img_size is None:
                want_bps = None
            else:
                want_bps = int(img_size * float(fps) * 1.25)  # ~25% headroom

        if want_bps is not None:
            try:
                if hasattr(cam, "StreamBytesPerSecond"):
                    _safe_set(getattr(cam, "StreamBytesPerSecond"), "StreamBytesPerSecond", int(want_bps), clamp=True, vprint=self._v)
            except Exception as e:
                self._v(f"[AVT][fps] StreamBytesPerSecond set error: {e}")

        # 3) finally set AcquisitionFrameRateAbs
        try:
            feat = cam.get_feature_by_name("AcquisitionFrameRateAbs")
        except Exception:
            self._v("[AVT][fps] AcquisitionFrameRateAbs not present on this camera.")
            return
        if not feat.is_writeable():
            self._v("[AVT][fps] AcquisitionFrameRateAbs is not writeable.")
            return

        try:
            # clamp via node range
            try:
                lo, hi = feat.get_range()[:2]
                target = max(lo, min(float(fps), hi))
            except Exception:
                target = float(fps)
            before = None
            try:
                before = feat.get()
            except Exception:
                pass
            feat.set(target)
            after = None
            try:
                after = feat.get()
            except Exception:
                pass
            self._v(f"[AVT][fps] AcquisitionFrameRateAbs: before={before} requested={fps} now={after}")
        except Exception as e:
            self._v(f"[AVT][fps:EXC] Could not set fps: {e}")

    def _log_resulting_fps(self, cam):
        try:
            val = cam.get_feature_by_name("AcquisitionFrameRateAbs").get()
            self._v(f"[AVT {getattr(cam, 'get_id', lambda:'?')()}] AcquisitionFrameRateAbs = {val}")
        except Exception:
            self._v("[AVT] Could not read back AcquisitionFrameRateAbs")

    def _log_rate_limits_and_measured(self):
        cam = self.cam_handle

        # Sensor/processing ceiling (read-only info node)
        try:
            afr_lim = cam.get_feature_by_name("AcquisitionFrameRateLimit").get()
        except Exception:
            afr_lim = None

        # Link-derived ceiling (rough): max_bps / (payload * overhead)
        try:
            img_size = cam.get_feature_by_name("ImageSize").get()  # bytes/frame
        except Exception:
            img_size = None
        try:
            bps_hi = cam.StreamBytesPerSecond.get_range()[1] if hasattr(cam, "StreamBytesPerSecond") else None
        except Exception:
            bps_hi = None

        link_ceiling = (bps_hi / (img_size * 1.25)) if (img_size and bps_hi and bps_hi > 0) else None

        # The rate you asked for (if present)
        try:
            afr = cam.get_feature_by_name("AcquisitionFrameRateAbs").get()
        except Exception:
            afr = None

        msg = "[AVT][rate]"
        msg += f" AFRLimit={afr_lim:.2f} Hz" if afr_lim is not None else " AFRLimit=?"
        msg += f" | AFRAbs={afr:.2f} Hz"     if afr is not None else ""
        if link_ceiling is not None:
            msg += f" | LinkCeiling≈{link_ceiling:.2f} Hz (bps_max={bps_hi}, img={img_size})"
        self._v(msg)

    # ---------- Params ----------
    def apply_params(self):
        if self._read_only:
            raise RuntimeError("apply_params() called while camera is opened read-only")
        if not self.cam_handle:
            raise RuntimeError("apply_params() before camera open")

        p = self.params
        P = {k.lower(): v for k, v in p.items()}  # enforce lc only

        def node(name):
            if not _has(self.cam_handle, name):
                self._v(f"[AVT][apply] Feature '{name}' not found")
                raise AttributeError(f"Feature '{name}' not found on camera")
            return getattr(self.cam_handle, name)

        def apply(name, value, clamp=False):
            if not _has(self.cam_handle, name):
                self._v(f"[AVT][apply] skip {name} (missing)")
                return None
            return _safe_set(node(name), name, value, clamp=clamp, vprint=self._v)

        # ---- Autos first (snake_case only) ----
        ga = _auto_mode(P.get("gain_auto"))
        ea = _auto_mode(P.get("exposure_auto"))
        if ga is not None and _has(self.cam_handle, "GainAuto"):
            apply("GainAuto", ga)
        if ea is not None and _has(self.cam_handle, "ExposureAuto"):
            apply("ExposureAuto", ea)

        # ExposureMode for manual
        if _has(self.cam_handle, "ExposureMode"):
            apply("ExposureMode", "Timed")

        # Manual gain only if autos Off/None
        if ga in (None, "Off") and _has(self.cam_handle, "Gain"):
            apply("Gain", float(P.get("gain", 0.0)), clamp=True)

        # Manual exposure only if autos Off/None
        if ea in (None, "Off"):
            exp_us = float(P.get("exposure", 20000.0))
            if _has(self.cam_handle, "ExposureTimeAbs"):
                apply("ExposureTimeAbs", exp_us, clamp=True)
            elif _has(self.cam_handle, "ExposureTime"):
                apply("ExposureTime", exp_us, clamp=True)

        # Acquisition mode / multiframe
        if P.get("acquisition_mode"):
            apply("AcquisitionMode", p.get("acquisition_mode", "Continuous"))
        if P.get("acquisition_mode") == "multiframe":
            if _has(self.cam_handle, "AcquisitionFrameCount"):
                apply("AcquisitionFrameCount", int(P.get("n_frames", 1)))

        # Binning, reverse
        if _has(self.cam_handle, "BinningHorizontal"):
            apply("BinningHorizontal", int(P.get("binning", 1)))
        if _has(self.cam_handle, "BinningVertical"):
            apply("BinningVertical", int(P.get("binning", 1)))
        if _has(self.cam_handle, "ReverseX"):
            apply("ReverseX", bool(P.get("reverse_x", False)))
        if _has(self.cam_handle, "ReverseY"):
            apply("ReverseY", bool(P.get("reverse_y", False)))

        # Trigger vs free-run
        use_trigger = str(P.get("trigger_mode", "Off")).strip().lower() == "on"
        if _has(self.cam_handle, "TriggerSelector"):
            apply("TriggerSelector", "FrameStart")
        if use_trigger:
            apply("TriggerMode", "On")
            if _has(self.cam_handle, "TriggerSource"):
                apply("TriggerSource", p.get("trigger_source", "Line1"))
            if _has(self.cam_handle, "TriggerActivation"):
                apply("TriggerActivation", p.get("trigger_activation", "RisingEdge"))
            if p.get("trigger_delay_us") is not None and _has(self.cam_handle, "TriggerDelayAbs"):
                apply("TriggerDelayAbs", float(p["trigger_delay_us"]), clamp=True)
        else:
            if _has(self.cam_handle, "TriggerMode"):
                apply("TriggerMode", "Off")
            # Deterministic FPS here
            fps_target = P.get("frame_rate")
            if fps_target is not None:
                self._set_fps_locked(self.cam_handle, float(fps_target))

        # GigE transport (optional overrides)
        if p.get("stream_constrain") is not None and _has(self.cam_handle, "StreamFrameRateConstrain"):
            apply("StreamFrameRateConstrain", bool(p["stream_constrain"]))
        if p.get("stream_bps") is not None and _has(self.cam_handle, "StreamBytesPerSecond"):
            apply("StreamBytesPerSecond", int(p["stream_bps"]), clamp=True)
        if p.get("packet_size") is not None and _has(self.cam_handle, "GevSCPSPacketSize"):
            apply("GevSCPSPacketSize", int(p["packet_size"]))

    # ---------- Streaming ----------
    def _start_stream(self):
        self.is_recording = True
        self._v(f"[AVT {self.cam_id}] stream starting…")

        def _gen():
            prev_shm = None
            n = 0
            while self.is_recording:
                try:
                    f = self.cam_handle.get_frame(timeout_ms=self.timeout)
                except VmbTimeout:
                    self._v(f"[AVT {self.cam_id}] get_frame timeout @ n={n}")
                    continue
                if f is None:
                    self._v(f"[AVT {self.cam_id}] get_frame returned None @ n={n}")
                    continue
                arr = f.as_numpy_ndarray()
                carr = np.ascontiguousarray(arr)
                shm = shared_memory.SharedMemory(create=True, size=carr.nbytes)
                np.ndarray(carr.shape, dtype=carr.dtype, buffer=shm.buf)[:] = carr
                meta = (f.get_id(), f.get_timestamp())
                if self._verbose and (n < 1 or (n % 100 == 0)):
                    print(f"[AVT {self.cam_id}] frame#{n} id={meta[0]} ts={meta[1]} shape={carr.shape} dtype={carr.dtype}")
                if prev_shm is not None:
                    try: prev_shm.close()
                    except Exception: pass
                prev_shm = shm
                n += 1
                yield (shm.name, carr.shape, str(carr.dtype)), meta
            if prev_shm is not None:
                try: prev_shm.close()
                except Exception: pass

        self._gen = _gen()

    def stop(self):
        self.is_recording = False
        self._gen = None
        self._v(f"[AVT {self.cam_id}] stream stopped.")

    def image(self):
        if not self.is_recording or self._gen is None:
            return None, "not recording"
        try:
            return next(self._gen)
        except StopIteration:
            return None, "stop"
        except Exception as err:
            self._v(f"[AVT {self.cam_id}] image() error: {err}")
            return None, "error"

    close = stop

    # ---------- Learn format ----------
    def _init_format(self):
        try:
            f = self.cam_handle.get_frame(timeout_ms=self.timeout)
        except VmbTimeout:
            self._v(f"[AVT {self.cam_id}] _init_format timeout")
            return
        if f is None:
            self._v(f"[AVT {self.cam_id}] _init_format got None frame")
            return
        try:
            arr = f.as_numpy_ndarray()
            self.format['height'] = arr.shape[0]
            self.format['width'] = arr.shape[1]
            self.format['n_chan'] = arr.shape[2] if arr.ndim == 3 else 1
            if arr.dtype == np.uint16:
                self.format['dtype'] = np.uint16
            self._v(f"[AVT {self.cam_id}] Ready: {self.format['width']}x{self.format['height']} n_chan={self.format['n_chan']} dtype={self.format['dtype']}")
        except Exception as e:
            self._v(f"[AVT {self.cam_id}] _init_format error: {e}")
