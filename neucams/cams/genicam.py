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
    timeout_ms = 2000  # milliseconds

    def __init__(self, cam_id=None, params=None, format=None):
        self.h = get_harvester()
        if cam_id is None and self.h.device_info_list:
            cam_id = getattr(self.h.device_info_list[0], "serial_number", None)
        self._open_for_format = False

        super().__init__(name='GenICam', cam_id=cam_id, params=params, format=format)

        # ---------- Public snake_case params + defaults ----------
        default_params = {
            'exposure': 29000,
            'frame_rate': 30,
            'gain': 8,
            'gain_auto': False,
            'acquisition_mode': 'Continuous',
            'n_frames': 1,

            # canonical trigger keys
            'trigger_selector': 'frame_start',
            'trigger_mode': 'off',
            'trigger_source': 'line1',
            'trigger_activation': 'rising_edge',

            'line_detection_level': 'ttl',
            'line_debouncing_period': 0,
            'pixel_format': 'mono8',
            'exposure_mode': 'timed',
        }
        self.exposed_params = [
            'frame_rate','gain','exposure','gain_auto',
            'acquisition_mode','n_frames',
            'trigger_selector','trigger_mode','trigger_source','trigger_activation'
        ]

        self.params = {**default_params, **(self.params or {})}

        # Normalize everything to snake_case & canonical values
        self._normalize_params()

    # ---------- Normalization helpers (snake_case only outwardly) ----------
    @staticmethod
    def _snakeify(k: str) -> str:
        # Convert CamelCase or mixedCase to snake_case
        out = []
        prev_lower = False
        for ch in k:
            if ch.isupper() and prev_lower:
                out.append('_')
            out.append(ch.lower())
            prev_lower = ch.islower()
        return ''.join(out)

    @staticmethod
    def _enumize(s: str) -> str:
        """
        Turn 'frame_start' -> 'FrameStart', 'rising_edge' -> 'RisingEdge',
             'mono8' -> 'Mono8', 'on' -> 'On', 'ttl' -> 'TTL' (keep common acronyms).
        """
        if not isinstance(s, str):
            return s
        parts = [p for p in s.strip().split('_') if p]
        if not parts:
            return s
        mapping = {'ttl': 'TTL', 'lvds': 'LVDS', 'cmos': 'CMOS', 'cmosis': 'CMOSIS'}
        out = []
        for p in parts:
            pl = p.lower()
            out.append(mapping.get(pl, pl.capitalize()))
        return ''.join(out)

    def _normalize_params(self):
        p_in = dict(self.params or {})
        p_snake = { self._snakeify(k): v for k, v in p_in.items() }
        # Back-compat: map 'triggered' -> 'trigger_mode'
        if 'triggered' in p_snake:
            if p_snake['triggered']:
                p_snake['trigger_mode'] = 'on'
            else:
                p_snake.setdefault('trigger_mode', 'off')
            try:
                from neucams.utils import display
                display("[GenICam] 'triggered' is deprecated; use 'trigger_mode': 'on'/'off'.", level='warning')
            except Exception:
                pass
        self.params = p_snake
        self.params.setdefault('trigger_selector','frame_start')
        self.params.setdefault('trigger_source','line1')
        self.params.setdefault('trigger_activation','rising_edge')

    # ---------- Node set helper with logging ----------
    @staticmethod
    def _set_if_present(nm, key, val):
        if val is None:
            return
        try:
            if not hasattr(nm, key):
                print(f"[GenICam] Missing node: {key}")
                return
            node = getattr(nm, key)
            if hasattr(node, "is_writable") and not node.is_writable:
                print(f"[GenICam] Skip {key}: not writable right now.")
                return
            node.value = val
        except Exception as e:
            print(f"[GenICam] Failed {key}={val}: {e}")


    # ---------- Format probe ----------
    def _init_format(self):
        # Prefer node map values → works even before first frame, including trigger mode
        try:
            nm = self.cam_handle.remote_device.node_map
            w = int(nm.Width.value) if hasattr(nm, "Width") else None
            h = int(nm.Height.value) if hasattr(nm, "Height") else None
        except Exception:
            w = h = None

        if w and h:
            self.format['width']  = w
            self.format['height'] = h
            self.format['n_chan'] = 1
            self.format['dtype']  = np.uint8
            display(f"[GenICam {self.cam_id}] Ready: {w}x{h} n_chan=1 dtype=uint8")
            return

        # Fallback (rare) – only if already free-running
        try:
            frame, _ = self.image()
            if frame is not None and frame.size:
                self.format['height'] = frame.shape[0]
                self.format['width']  = frame.shape[1]
                self.format['n_chan'] = 1 if frame.ndim == 2 else frame.shape[2]
                self.format['dtype']  = frame.dtype
                display(f"[GenICam {self.cam_id}] Ready (fetched): {self.format['width']}x{self.format['height']}")
        except Exception:
            pass

    # ---------- Software trigger ----------
    def fire_software_trigger(self):
        try:
            if hasattr(self.features, "TriggerSoftware"):
                self.features.TriggerSoftware.execute()  # <-- not .run()
                display(f"[GenICam {self.cam_id}] Software trigger fired.")
            else:
                display(f"[GenICam {self.cam_id}] TriggerSoftware node not present.", level='warning')
        except Exception as e:
            display(f"[GenICam {self.cam_id}] Software trigger failed: {e}", level='error')


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

        if getattr(self, "_open_for_format", False):
            try:
                if hasattr(self.features, "PixelFormat"):
                    self.features.PixelFormat.value = "Mono8"
            except Exception:
                pass
            self._init_format()
            return self

        # normal run
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
        return False  # propagate exceptions

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

        nm = self.features  # node map
        p = self.params

        # Decide trigger mode first; this affects AFR writability
        use_trigger = str(self.params.get('trigger_mode', 'off')).lower() == 'on'
        self._triggered = use_trigger  # keep internal flag for logs

        # Stop stream while applying (avoid "Node is not writable" mid-stream)
        was_recording = getattr(self, 'is_recording', False)
        if was_recording:
            try:
                self.cam_handle.stop()
                self.is_recording = False
                display(f"[GenICam {self.cam_id}] Paused stream to apply settings…")
            except Exception as e:
                display(f"[GenICam {self.cam_id}] Could not pause: {e}", level='warning')

        # ---- Always-on base (snake_case → exact nodes) ----
        pixel_format = self._enumize(p.get('pixel_format', 'mono8'))
        exposure_mode = self._enumize(p.get('exposure_mode', 'timed'))

        base_map = [
            ('EventNotification',                 'On'),  # enum
            ('PixelFormat',                       pixel_format),  # enum
            ('Gain',                              float(p.get('gain', 8))),
            ('ExposureTime',                      float(p.get('exposure', 29000))),
            ('ExposureMode',                      exposure_mode),  # enum

            # Optional bandwidth controls
            ('DeviceLinkThroughputLimit',         p.get('device_link_throughput_limit', None)),
            ('DeviceLinkThroughputLimitMode',     self._enumize(p['device_link_throughput_limit_mode']) if 'device_link_throughput_limit_mode' in p else None),
        ]
        for node, val in base_map:
            self._set_if_present(nm, node, val)

        # Instead of always writing GainAuto:
        if hasattr(nm, 'GainAuto') and hasattr(nm.GainAuto, 'is_writable') and nm.GainAuto.is_writable:
            nm.GainAuto.value = self._enumize('once' if p.get('gain_auto', False) else 'off')
        else:
            print("[GenICam] Skip GainAuto: not present/writable.")

        # ---- Trigger block ----
        if use_trigger:
            # 0) Disarm first so nodes become writable
            self._set_if_present(nm, 'TriggerMode', 'Off')

            # 1) Configure line I/O so trigger-related nodes aren't gated
            try:
                if hasattr(nm, 'LineSelector'): nm.LineSelector.value = 'Line1'
                if hasattr(nm, 'LineMode'):     nm.LineMode.value     = 'Input'
            except Exception:
                pass

            # 2) Now set selector/source/activation (only if writable)
            trig_selector   = self._enumize(self.params.get('trigger_selector','frame_start'))
            trig_source     = self._enumize(self.params.get('trigger_source','line1'))
            trig_activation = self._enumize(self.params.get('trigger_activation','rising_edge'))

            # Check writability before setting each trigger node
            if hasattr(nm, 'TriggerSelector') and hasattr(nm.TriggerSelector, 'is_writable') and nm.TriggerSelector.is_writable:
                nm.TriggerSelector.value = trig_selector
            else:
                print("[GenICam] Skip TriggerSelector: not writable.")

            if hasattr(nm, 'TriggerSource') and hasattr(nm.TriggerSource, 'is_writable') and nm.TriggerSource.is_writable:
                nm.TriggerSource.value = trig_source
            else:
                print("[GenICam] Skip TriggerSource: not writable.")

            if hasattr(nm, 'TriggerActivation') and hasattr(nm.TriggerActivation, 'is_writable') and nm.TriggerActivation.is_writable:
                nm.TriggerActivation.value = trig_activation
            else:
                print("[GenICam] Skip TriggerActivation: not writable.")

            # 3) Arm
            self._set_if_present(nm, 'TriggerMode', 'On')
            display(f"[GenICam {self.cam_id}] Trigger ARMED: selector={trig_selector}, source={trig_source}, activation={trig_activation}")

            # Vendor niceties (DALSA) - only if writable
            if hasattr(nm, 'lineDetectionLevel') and hasattr(nm.lineDetectionLevel, 'is_writable') and nm.lineDetectionLevel.is_writable:
                nm.lineDetectionLevel.value = self._enumize(p['line_detection_level']) if 'line_detection_level' in p else None
            else:
                print("[GenICam] Skip lineDetectionLevel: not writable.")

            if hasattr(nm, 'lineDebouncingPeriod') and hasattr(nm.lineDebouncingPeriod, 'is_writable') and nm.lineDebouncingPeriod.is_writable:
                nm.lineDebouncingPeriod.value = p.get('line_debouncing_period', None)
            else:
                print("[GenICam] Skip lineDebouncingPeriod: not writable.")
        else:
            # Free-run: don't force AFR knobs if they're RO; set if writable
            self._set_if_present(nm, 'TriggerMode', 'Off')
            # Some models lock AFREnable; just try politely:
            self._set_if_present(nm, 'AcquisitionFrameRateEnable', True)
            self._set_if_present(nm, 'AcquisitionFrameRate', float(self.params.get('frame_rate', 30)))
            display(f"[GenICam {self.cam_id}] Trigger OFF (free-run).")

        # Resume if we paused
        if was_recording:
            try:
                self.cam_handle.start()
                self.is_recording = True
                display(f"[GenICam {self.cam_id}] Resumed stream after applying settings.")
            except Exception as e:
                display(f"[GenICam {self.cam_id}] Failed to resume: {e}", level='error')

    # --- is_triggered() ---
    def is_triggered(self) -> bool:
        return str(self.params.get('trigger_mode','off')).lower() == 'on'


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
                    timestamp = buffer.timestamp
            except Exception:
                frame = np.array([])
                timestamp = 0
            yield frame, idx, time.time() - self.t_start
            idx += 1

    def _record(self):
        if not getattr(self, 'cam_handle', None):
            display('_record() called, but camera was never opened.', level='warning')
            return
        try:
            self.cam_handle.start()
            self.t_start = time.time()
            limit = self.params['n_frames'] if self.params['acquisition_mode'] == "MultiFrame" else None
            self.frame_generator = self.get_frame_generator(
                n_frames=limit,
                timeout_ms=self.timeout_ms
            )
            self.is_recording = True

            if self.is_triggered():
                display(f"[GenICam {self.cam_id}] ARMED and listening for trigger … (no frames until trigger)")
            else:
                fr = self.params.get('frame_rate', None)
                display(f"[GenICam {self.cam_id}] Free-run started at ~{fr} fps.")
        except Exception as e:
            display(f"[GenICam {self.cam_id}] Failed to start acquisition: {e}", level='error')
            self.is_recording = False

    def start(self):
        """Public method to begin acquisition."""
        if not self.is_recording:
            display(f"[GenICam {self.cam_id}] Start pressed.")
            self._record()
        else:
            display(f"[GenICam {self.cam_id}] Start pressed but already recording.", level='warning')

    def stop(self):
        if not getattr(self, 'cam_handle', None):
            display('stop() called, but camera was never opened.', level='warning')
            return
        try:
            self.cam_handle.stop()
            self.is_recording = False
            display(f"[GenICam {self.cam_id}] Stopped acquisition.")
        except Exception as e:
            display(f"[GenICam {self.cam_id}] stop() failed: {e}", level='error')

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
