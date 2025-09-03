import os, sys, ctypes
from pathlib import Path
import numpy as np
from multiprocessing import shared_memory
import threading
import queue

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
AVT_DIR = BASE / "vmbpy"


def _env_verbose() -> bool:
    val = os.environ.get("NEUCAMS_VERBOSE", "")
    return str(val).strip().lower() in ("1", "true", "yes", "on")

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
    if isinstance(val, bool):
        return "Continuous" if val else "Off"
    if isinstance(val, (int, float)):
        return "Continuous" if float(val) != 0.0 else "Off"
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("off", "false", "0"): return "Off"
        if s in ("once",): return "Once"
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


def _safe_set(node, feat_name, value, clamp=False, vprint=lambda *_: None, log=False):
    try:
        applied_value = None
        if log:
            before = None
            try:
                before = node.get()
            except Exception:
                pass
        v = _clamp_to_node_range(node, value) if clamp else value
        node.set(v)
        if log:
            after = None
            try:
                after = node.get()
            except Exception:
                pass
            vprint(f"[AVT][set] {feat_name}: before={before} desired={value} applied={after}")
            applied_value = after
        return applied_value
    except Exception as e:
        vprint(f"[AVT][set:EXC] {feat_name}: desired={value} error={e}")
        return None


def _first_present(d: dict, names, default=None):
    for n in names:
        if n in d:
            return d[n]
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        ln = n.lower()
        if ln in low:
            return low[ln]
    return default


# ----------------- camera -----------------
class AVTCam(GenericCam):
    timeout = 2000  # ms

    # --- SHM ring state ---
    _pool = None
    _pool_size = 12            # reuse a small, fixed pool
    _pool_idx = 0
    _pool_shape = None
    _pool_dtype = None
    _pool_bytes = None

    # --- Async streaming state ---
    _async_queue = None        # queue.Queue of ((shm_tuple), meta)
    _async_handler = None
    _async_lock = None

    def __init__(self, cam_id=None, params=None, format=None, serial_number=None):
        self.serial_number = serial_number
        self._read_only = False
        self._triggered = False
        self._open_for_format = False  # default

        defaults = {
            # imaging
            "pixel_format": "Mono8",
            "exposure": 148.0,              # microseconds (used only if exposure_auto is Off)
            "exposure_auto": "Off",         # Off|Once|Continuous
            "gain": 0.0,                    # dB
            "gain_auto": "Off",             # Off|Once|Continuous
            "binning": 1,
            "reverse_x": False,
            "reverse_y": False,
            # acquisition
            "acquisition_mode": "Continuous",
            "n_frames": 1,
            # free-run fps
            "frame_rate": 30.0,             # target FPS in free-run -> AcquisitionFrameRateAbs
            # trigger
            "trigger_mode": "Off",          # <-- single source of truth
            "trigger_source": "Line1",
            "trigger_activation": "RisingEdge",
            "trigger_delay_us": None,
            # output line (apply only when trigger is On)
            "line_selector": None,           # maps to LineSelector
            "line_mode": None,               # maps to LineMode
            "line_source": None,             # maps to LineSource
            "user_output_selector": None,    # maps to UserOutputSelector
            "user_output_value": None,       # maps to UserOutputValue (bool)
            # sync out controls
            "sync_out_source": None,         # maps to SyncOutSource
            "sync_out_levels": None,        # maps to SyncOutLevels
            "sync_out_selector": None,      # maps to SyncOutSelector
            "sync_out_polarity": None,      # maps to SyncOutPolarity
            # GigE transport (optional)
            "stream_constrain": None,       # bool -> StreamFrameRateConstrain
            "stream_bps": None,             # int bytes/sec -> StreamBytesPerSecond (may be on Stream)
            "packet_size": None,            # int (e.g., 8228 or 1500) -> GevSCPSPacketSize
            # behavior
            "require_full_access": False,
            # streaming mode
            # Accept many spellings; default is ASYNC:
            # - asynchronous/asyncronous (bool)
            # - synchronous/syncronous (bool)
            # - stream_mode: "async"/"asynchronous"/"asyncronous" | "sync"/"synchronous"/"syncronous"
            "asynchronous": True,
            "buffer_count": 20,             # for async start_streaming()
            # logging
            "verbose": None,
            # legacy alias (UI may still set it) — we won't expose it as a param anymore
            "triggered": None,
        }
        fmt = {"dtype": np.uint8}

        merged_params = {**defaults, **(params or {})}
        super().__init__(
            name="AVT",
            cam_id=cam_id,
            params=merged_params,
            format={**fmt, **(format or {})},
        )

        # Only expose the canonical ones (remove legacy confusion)
        self.exposed_params = [
            "pixel_format",
            "frame_rate",
            "exposure",
            "exposure_auto",
            "gain",
            "gain_auto",
            "binning",
            "reverse_x",
            "reverse_y",
            "trigger_mode",
            "trigger_source",
            "trigger_activation",
            "trigger_delay_us",
            # output line controls
            "line_selector",
            "line_mode",
            "line_source",
            "user_output_selector",
            "user_output_value",
            # sync out controls
            "sync_out_source",
            "sync_out_levels",
            "sync_out_selector",
            "sync_out_polarity",
            "acquisition_mode",
            "n_frames",
            "stream_constrain",
            "stream_bps",
            "packet_size",
            "require_full_access",
            # streaming
            "asynchronous",
            "buffer_count",
            "verbose",
        ]

        pverb = self.params.get("verbose", None)
        self._verbose = True if _env_verbose() else (bool(pverb) if pverb is not None else False)
        self._v = (lambda msg: print(msg)) if self._verbose else (lambda *_: None)

        self.vimba = None
        self.cam_handle = None
        self._gen = None
        self.is_recording = False

    # -------- streaming mode resolution --------
    def _is_async_mode(self) -> bool:
        """Canonical: True => async streaming, False => sync/polling."""
        return bool(self.params.get("asynchronous", True))


    def _apply_format_only(self):
        pf = self.params.get("pixel_format", "Mono8")
        try:
            enum = getattr(PixelFormat, pf) if isinstance(pf, str) else pf
            self.cam_handle.set_pixel_format(enum)
        except Exception:
            pass
        if hasattr(self.cam_handle, "BinningHorizontal"):
            try: self.cam_handle.BinningHorizontal.set(int(self.params.get("binning", 1)))
            except Exception: pass
        if hasattr(self.cam_handle, "BinningVertical"):
            try: self.cam_handle.BinningVertical.set(int(self.params.get("binning", 1)))
            except Exception: pass

    # ---------- API used by CameraHandler ----------
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

        # Cache cameras list to avoid multiple enumerations
        try:
            _all_cams = list(self.vimba.get_all_cameras())
        except Exception:
            _all_cams = []

        # Resolve serial -> id after Vimba is up
        if not self.cam_id and self.serial_number:
            for c in _all_cams:
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

        for c in _all_cams:
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
            self.cam_handle.__enter__()  # open
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

        if getattr(self, "_open_for_format", False):
            self._apply_format_only()
            self._init_format()
            return self

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

        self._init_format()
        try:
            self._log_resulting_fps(self.cam_handle)
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

    # --- FPS: set/read AcquisitionFrameRateAbs only ----------------------
    def _set_fps_abs(self, cam, fps: float):
        try:
            feat = cam.get_feature_by_name("AcquisitionFrameRateAbs")
        except Exception:
            self._v("[AVT][fps] AcquisitionFrameRateAbs not present on this camera.")
            return None, None
        if not feat.is_writeable():
            self._v("[AVT][fps] AcquisitionFrameRateAbs is not writeable.")
            return None, None
        try:
            try:
                lo, hi = feat.get_range()[:2]
                target = max(lo, min(float(fps), hi))
            except Exception:
                target = float(fps)
            feat.set(target)
            self._v(f"[AVT][fps] Requested={fps} -> applied {target} via AcquisitionFrameRateAbs")
            return "AcquisitionFrameRateAbs", target
        except Exception as e:
            self._v(f"[AVT][fps:EXC] Could not set fps: {e}")
            return None, None

    def _log_resulting_fps(self, cam):
        try:
            val = cam.get_feature_by_name("AcquisitionFrameRateAbs").get()
            self._v(f"[AVT {getattr(cam, 'get_id', lambda:'?')()}] AcquisitionFrameRateAbs = {val}")
            return "AcquisitionFrameRateAbs", val
        except Exception:
            self._v("[AVT] Could not read back AcquisitionFrameRateAbs")
            return None, None
    # --------------------------------------------------------------------

    # ---------- Params ----------
    def apply_params(self):
        if self._read_only:
            raise RuntimeError("apply_params() called while camera is opened read-only")
        if not self.cam_handle:
            raise RuntimeError("apply_params() before camera open")

        p = self.params
        P = {k.lower(): v for k, v in p.items()}

        def node(name):
            if not _has(self.cam_handle, name):
                self._v(f"[AVT][apply] Feature '{name}' not found")
                raise AttributeError(f"Feature '{name}' not found on camera")
            return getattr(self.cam_handle, name)

        def apply(name, value, clamp=False):
            if not _has(self.cam_handle, name):
                self._v(f"[AVT][apply] skip {name} (missing)")
                return None
            return _safe_set(node(name), name, value, clamp=clamp, vprint=self._v, log=self._verbose)

        # ---- Exposure & Gain autos first ----
        ga = _auto_mode(_first_present(p, ["gain_auto", "GainAuto"]))
        ea = _auto_mode(_first_present(p, ["exposure_auto", "ExposureAuto"]))
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
        if str(P.get("acquisition_mode", "")).lower() == "multiframe":
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

        # ---- Trigger vs free-run — SINGLE source of truth = trigger_mode ----
        # Accept legacy alias 'triggered' only as an override when explicitly set.
        use_trigger = (str(P.get("trigger_mode", "Off")).strip().lower() == "on")
        if p.get("triggered") is not None:  # legacy UI
            use_trigger = bool(p.get("triggered"))
        self._triggered = bool(use_trigger)

        if _has(self.cam_handle, "TriggerSelector"):
            apply("TriggerSelector", "FrameStart")

        if self._triggered:
            apply("TriggerMode", "On")
            if _has(self.cam_handle, "TriggerSource"):
                apply("TriggerSource", p.get("trigger_source", "Line1"))
            if _has(self.cam_handle, "TriggerActivation"):
                apply("TriggerActivation", p.get("trigger_activation", "RisingEdge"))
            if p.get("trigger_delay_us") is not None and _has(self.cam_handle, "TriggerDelayAbs"):
                apply("TriggerDelayAbs", float(p["trigger_delay_us"]), clamp=True)

            # Output line configuration (only when trigger is On)
            if p.get("line_selector") is not None:
                apply("LineSelector", p.get("line_selector"))
            if p.get("line_mode") is not None:
                apply("LineMode", p.get("line_mode"))
            if p.get("line_source") is not None:
                apply("LineSource", p.get("line_source"))
            if p.get("user_output_selector") is not None:
                apply("UserOutputSelector", p.get("user_output_selector"))
            if p.get("user_output_value") is not None:
                apply("UserOutputValue", bool(p.get("user_output_value")))
        else:
            if _has(self.cam_handle, "TriggerMode"):
                apply("TriggerMode", "Off")
            # Set FPS here (Abs only)
            fps_target_rate = float(P.get("frame_rate", 30.0))
            self._set_fps_abs(self.cam_handle, fps_target_rate)

        # GigE transport (optional)
        if p.get("stream_constrain") is not None and _has(self.cam_handle, "StreamFrameRateConstrain"):
            apply("StreamFrameRateConstrain", bool(p["stream_constrain"]))
        if p.get("stream_bps") is not None and _has(self.cam_handle, "StreamBytesPerSecond"):
            apply("StreamBytesPerSecond", int(p["stream_bps"]), clamp=True)
        if p.get("packet_size") is not None and _has(self.cam_handle, "GevSCPSPacketSize"):
            apply("GevSCPSPacketSize", int(p["packet_size"]))

        # IO controls
        if p.get("sync_out_source") is not None and _has(self.cam_handle, "SyncOutSource"):
            apply("SyncOutSource", p["sync_out_source"])
        if p.get("sync_out_levels") is not None and _has(self.cam_handle, "SyncOutLevels"):
            apply("SyncOutLevels", int(p["sync_out_levels"]), clamp=True)
        if p.get("sync_out_selector") is not None and _has(self.cam_handle, "SyncOutSelector"):
            apply("SyncOutSelector", p["sync_out_selector"])
        if p.get("sync_out_polarity") is not None and _has(self.cam_handle, "SyncOutPolarity"):
            apply("SyncOutPolarity", p["sync_out_polarity"])

    def is_triggered(self) -> bool:
        return bool(getattr(self, "_triggered", False))

    # ---------- SHM allocation ----------
    def _alloc_pool(self, h: int, w: int, dtype):
        """Allocate fixed SHM ring once, sized to exactly one frame."""
        itemsize = np.dtype(dtype).itemsize
        nbytes = h * w * itemsize

        self._free_pool()  # just in case

        self._pool = [shared_memory.SharedMemory(create=True, size=nbytes)
                      for _ in range(self._pool_size)]
        self._pool_idx = 0
        self._pool_shape = (h, w)
        self._pool_dtype = np.dtype(dtype)
        self._pool_bytes = nbytes
        self._v(f"[AVT {self.cam_id}] SHM pool: {self._pool_size} × {nbytes} bytes")

    def _free_pool(self):
        """Close+unlink all SHM blocks."""
        if not self._pool:
            return
        for shm in self._pool:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass
        self._pool = None
        self._pool_shape = None
        self._pool_dtype = None
        self._pool_bytes = None
        self._v(f"[AVT {self.cam_id}] SHM pool freed")

    def _next_shm(self):
        """Round-robin next SHM slot."""
        shm = self._pool[self._pool_idx]
        self._pool_idx = (self._pool_idx + 1) % self._pool_size
        return shm

    # ---------- Streaming (SYNC) ----------
    def _start_stream_sync(self):
        self.is_recording = True
        self._v(f"[AVT {self.cam_id}] stream (sync) starting…")

        def _gen():
            n = 0
            timeout_count = 0
            pool_ready = False

            while self.is_recording:
                try:
                    f = self.cam_handle.get_frame(timeout_ms=self.timeout)
                except VmbTimeout:
                    timeout_count += 1
                    if not self._triggered and self._verbose:
                        print(f"[AVT {self.cam_id}] get_frame timeout @ n={n}")
                    elif self._triggered and self._verbose and timeout_count % 100 == 1:
                        print(f"[AVT {self.cam_id}] waiting for trigger… (timeouts={timeout_count})")
                    continue
                if f is None:
                    if self._verbose:
                        print(f"[AVT {self.cam_id}] get_frame returned None @ n={n}")
                    continue

                arr = f.as_numpy_ndarray()
                if arr.ndim == 3:
                    arr = arr[:, :, 0]

                if (not pool_ready or
                    self._pool_shape is None or
                    self._pool_dtype is None or
                    self._pool_shape != arr.shape[:2] or
                    self._pool_dtype != arr.dtype):
                    self._alloc_pool(arr.shape[0], arr.shape[1], arr.dtype)
                    pool_ready = True

                shm = self._next_shm()
                dst = np.ndarray(self._pool_shape, dtype=self._pool_dtype, buffer=shm.buf)
                np.copyto(dst, arr, casting='no')

                meta = (f.get_id(), f.get_timestamp())

                if self._verbose and (n < 3 or (n % 100 == 0)):
                    print(f"[AVT {self.cam_id}] frame#{n} id={meta[0]} ts={meta[1]} shape={dst.shape} dtype={dst.dtype}")

                n += 1
                yield (shm.name, dst.shape, str(dst.dtype)), meta

        self._gen = _gen()

    # ---------- Streaming (ASYNC) ----------
    def _start_stream_async(self):
        self.is_recording = True
        self._v(f"[AVT {self.cam_id}] stream (async) starting…")

        self._async_queue = queue.Queue(maxsize=self._pool_size)
        self._async_lock = threading.Lock()

        def handler(cam, stream, frame):
            # Minimal work in callback: copy -> SHM, enqueue, requeue frame
            try:
                arr = frame.as_numpy_ndarray()
                if arr.ndim == 3:
                    arr = arr[:, :, 0]

                with self._async_lock:
                    if (self._pool is None or
                        self._pool_shape != arr.shape[:2] or
                        self._pool_dtype is None or
                        self._pool_dtype != arr.dtype):
                        self._alloc_pool(arr.shape[0], arr.shape[1], arr.dtype)

                    shm = self._next_shm()
                    dst = np.ndarray(self._pool_shape, dtype=self._pool_dtype, buffer=shm.buf)
                    np.copyto(dst, arr, casting='no')

                meta = (frame.get_id(), frame.get_timestamp())

                try:
                    if not self._async_queue.full():
                        self._async_queue.put_nowait(((shm.name, dst.shape, str(dst.dtype)), meta))
                except Exception:
                    # queue full -> drop (better than blocking callback)
                    pass
            finally:
                # ALWAYS re-queue frame ASAP
                try:
                    cam.queue_frame(frame)
                except Exception:
                    pass

        self._async_handler = handler
        buf_cnt = int(self.params.get("buffer_count", 20))
        # start_streaming arms internally; do NOT call AcquisitionStart separately.
        self.cam_handle.start_streaming(handler=self._async_handler, buffer_count=buf_cnt)

        def _gen():
            while self.is_recording:
                try:
                    item = self._async_queue.get(timeout=1.0)
                    yield item
                except queue.Empty:
                    continue

        self._gen = _gen()

    def start(self):
        """Arm/start acquisition (idempotent)."""
        if self.is_recording:
            return
        # Always (re)apply trigger/fps on start to avoid stale mode
        try:
            if not self._read_only:
                self.apply_params()
        except Exception:
            pass

        if self._is_async_mode():
            # Async streaming manages acquisition lifecycle internally
            self._start_stream_async()
        else:
            # Sync path: optionally run AcquisitionStart; then poll get_frame
            try:
                if hasattr(self.cam_handle, "AcquisitionStart"):
                    self.cam_handle.AcquisitionStart.run()
                    self._v(f"[AVT {self.cam_id}] AcquisitionStart run")
            except Exception as e:
                self._v(f"[AVT {self.cam_id}] AcquisitionStart error: {e}")
            self._start_stream_sync()

    def stop(self):
        """Disarm/stop acquisition and generator."""
        was_async = self._is_async_mode()
        self.is_recording = False
        self._gen = None

        if was_async:
            # Stop async stream first (this also stops acquisition)
            try:
                if hasattr(self.cam_handle, "stop_streaming"):
                    self.cam_handle.stop_streaming()
                    self._v(f"[AVT {self.cam_id}] stop_streaming done")
            except Exception as e:
                self._v(f"[AVT {self.cam_id}] stop_streaming error: {e}")
            # Drain and drop queue
            try:
                if self._async_queue is not None:
                    while not self._async_queue.empty():
                        try:
                            self._async_queue.get_nowait()
                        except Exception:
                            break
            except Exception:
                pass
            self._async_queue = None
            self._async_handler = None
            self._async_lock = None
        else:
            # SYNC: Abort any pending wait and then stop acquisition
            try:
                if hasattr(self.cam_handle, "AcquisitionAbort"):
                    self.cam_handle.AcquisitionAbort.run()
                    self._v(f"[AVT {self.cam_id}] AcquisitionAbort run")
            except Exception as e:
                self._v(f"[AVT {self.cam_id}] AcquisitionAbort error: {e}")

            try:
                if hasattr(self.cam_handle, "AcquisitionStop"):
                    self.cam_handle.AcquisitionStop.run()
                    self._v(f"[AVT {self.cam_id}] AcquisitionStop run")
            except Exception as e:
                self._v(f"[AVT {self.cam_id}] AcquisitionStop error: {e}")

        # Free SHM pool
        self._free_pool()

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

    # ---------- Learn format (single direct frame, no SHM) ----------
    def _init_format(self):
        # 1) Prefer node-based dimensions (works without streaming)
        try:
            w = getattr(self.cam_handle, "Width").get() if hasattr(self.cam_handle, "Width") else None
            h = getattr(self.cam_handle, "Height").get() if hasattr(self.cam_handle, "Height") else None
        except Exception:
            w = h = None

        if w and h:
            self.format['width']  = int(w)
            self.format['height'] = int(h)
            pf = str(self.params.get("pixel_format", "Mono8")).lower()
            self.format['dtype']  = (np.uint16 if ("16" in pf or "12" in pf or "10" in pf) else np.uint8)
            self.format['n_chan'] = 1
            self._v(f"[AVT {self.cam_id}] Ready: {self.format['width']}x{self.format['height']} "
                    f"n_chan={self.format['n_chan']} dtype={self.format['dtype']}")
            return

        # 2) If not triggered, take a probe frame (requires acquisition running on some models)
        if not self._triggered:
            try:
                probe_timeout = min(int(self.timeout), 250)
                f = self.cam_handle.get_frame(timeout_ms=probe_timeout)
            except VmbTimeout:
                self._v(f"[AVT {self.cam_id}] _init_format timeout")
                return
            if f is None:
                self._v(f"[AVT {self.cam_id}] _init_format got None frame")
                return
            try:
                arr = f.as_numpy_ndarray()
                self.format['height'] = arr.shape[0]
                self.format['width']  = arr.shape[1]
                self.format['n_chan'] = arr.shape[2] if arr.ndim == 3 else 1
                if arr.dtype == np.uint16:
                    self.format['dtype'] = np.uint16
                self._v(f"[AVT {self.cam_id}] Ready: {self.format['width']}x{self.format['height']} "
                        f"n_chan={self.format['n_chan']} dtype={self.format['dtype']}")
            except Exception as e:
                self._v(f"[AVT {self.cam_id}] _init_format error: {e}")
