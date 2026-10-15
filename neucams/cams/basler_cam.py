"""Basler cameras (GigE and USB3) via pypylon.

Tested on an acA1300-60gmNIR (GigE, classic ace: *Abs feature names, 125 MHz ticks)
on Linux. ace 2 / USB3 (a2A...) uses the SFNC names (ExposureTime, Gain, ns ticks);
both name sets are tried, so one driver serves both.

GigE on Linux: raise net.core.rmem_max (e.g. 33554432) or pylon keeps a 208 KB
socket buffer and drops ~40% of frames at 68 fps. `socket_buffer_kb` sets the
stream grabber's buffer; it cannot exceed rmem_max.
"""
import numpy as np
from pypylon import pylon, genicam

from .generic_cam import GenericCam
from ..utils import display

# first writable node wins: classic ace name, then SFNC (ace 2 / USB3)
_NODES = {
    'exposure': ('ExposureTimeAbs', 'ExposureTime'),          # us
    'frame_rate': ('AcquisitionFrameRateAbs', 'AcquisitionFrameRate'),
    'gain': ('GainRaw', 'Gain'),
}
_DTYPES = {'Mono8': np.uint8}  # anything else (Mono10/12/16) arrives as uint16


def _find_device(serial_number=None, cam_id=None):
    devs = pylon.TlFactory.GetInstance().EnumerateDevices()
    if serial_number is not None:
        for d in devs:
            if d.GetSerialNumber() == str(serial_number):
                return d
        return None
    idx = int(cam_id or 0)
    return devs[idx] if idx < len(devs) else None


class BaslerCam(GenericCam):
    def __init__(self, cam_id=None, params=None, serial_number=None, **kwargs):
        super().__init__(name='Basler', cam_id=cam_id if cam_id is not None else serial_number,
                         params=params or {})
        defaults = {'frame_rate': 30.0, 'exposure': 5000.0, 'gain': None,
                    'trigger_mode': 'Off', 'trigger_source': 'Line1',
                    'trigger_activation': 'RisingEdge', 'output_line_source': 'ExposureActive',
                    'pixel_format': 'Mono8', 'socket_buffer_kb': 16384, 'n_buffers': 256}
        for k, v in defaults.items():
            self.params.setdefault(k, v)
        self.exposed_params = ['frame_rate', 'exposure', 'gain', 'trigger_mode', 'trigger_source']
        self.cam = None
        self._tick_hz = 1e9
        self._last_block = None
        self.n_incomplete = 0
        self.n_missing = 0

        dev = _find_device(serial_number, cam_id)
        if dev is None:
            display(f"[Basler] no camera found (serial={serial_number}, id={cam_id})", level='error')
            self.format = {'width': 0, 'height': 0, 'dtype': np.uint8, 'n_chan': 1}
            return
        self.cam = pylon.InstantCamera(pylon.TlFactory.GetInstance().CreateDevice(dev))
        self.cam.Open()
        self.cam_id = dev.GetSerialNumber()
        self.name = f"Basler {dev.GetModelName()}"
        self._set('PixelFormat', self.params['pixel_format'])
        self.format = {'width': self.cam.Width.Value, 'height': self.cam.Height.Value,
                       'dtype': _DTYPES.get(self.cam.PixelFormat.Value, np.uint16), 'n_chan': 1}
        if dev.GetDeviceClass() == 'BaslerGigE':
            self._tick_hz = float(self.cam.GevTimestampTickFrequency.Value)
            sg = self.cam.GetStreamGrabberNodeMap()
            sg.GetNode('SocketBufferSize').SetValue(int(self.params['socket_buffer_kb']))
        display(f"[{self.name} {self.cam_id}] {self.format['width']}x{self.format['height']} "
                f"{self.cam.PixelFormat.Value}")

    # ---------- feature access ----------
    def _node(self, *names):
        nm = self.cam.GetNodeMap()
        for n in names:
            node = nm.GetNode(n)
            if node is not None and genicam.IsWritable(node):
                return node
        return None

    def _set(self, name_or_names, value):
        names = (name_or_names,) if isinstance(name_or_names, str) else name_or_names
        node = self._node(*names)
        if node is None:
            return False
        try:
            node.FromString(str(value))
            return True
        except Exception as e:
            display(f"[{self.name}] could not set {names[0]}={value}: {e}", level='warning')
            return False

    # ---------- GenericCam interface ----------
    def is_connected(self):
        return self.cam is not None and self.cam.IsOpen()

    def apply_params(self):
        if not self.is_connected():
            return
        p = self.params
        triggered = self.is_triggered()
        self._set('ExposureAuto', 'Off')
        self._set(_NODES['exposure'], float(p['exposure']))
        if p.get('gain') is not None:
            self._set('GainAuto', 'Off')
            self._set(_NODES['gain'], p['gain'])
        # free-run: the camera's own clock sets the rate; triggered: the trigger does
        self._set('AcquisitionFrameRateEnable', 'false' if triggered else 'true')
        if not triggered:
            self._set(_NODES['frame_rate'], float(p['frame_rate']))
        self._set('TriggerSelector', 'FrameStart')
        self._set('TriggerMode', 'On' if triggered else 'Off')
        if triggered:
            self._set('TriggerSource', p['trigger_source'])
            self._set('TriggerActivation', p['trigger_activation'])
        # exposure-active out (classic ace: Out1, ace 2: Line2) for logging on the stim unit
        if p.get('output_line_source'):
            for line in ('Out1', 'Line2'):
                if self._set('LineSelector', line):
                    self._set('LineSource', p['output_line_source'])
                    break

    def set_param(self, param, val):
        self.params[param] = val  # applied at acquisition start (handler calls apply_params)

    def get_param(self, param):
        return self.params.get(param)

    def start(self):
        if not self.is_connected() or self.cam.IsGrabbing():
            return
        self._last_block = None
        self.n_incomplete = self.n_missing = 0
        self.cam.MaxNumBuffer.Value = int(self.params['n_buffers'])
        self.cam.StartGrabbing(pylon.GrabStrategy_OneByOne)

    def stop(self):
        if self.cam is not None and self.cam.IsGrabbing():
            self.cam.StopGrabbing()
            if self.n_incomplete or self.n_missing:
                display(f"[{self.name} {self.cam_id}] run lost frames: incomplete={self.n_incomplete} "
                        f"missing block IDs={self.n_missing}", level='warning')

    def close(self):
        self.stop()
        if self.cam is not None:
            self.cam.Close()
            self.cam = None

    def fire_software_trigger(self):
        if self.is_connected():
            self.cam.ExecuteSoftwareTrigger()

    def image(self):
        """(frame, (block_id, t_seconds)) or (None, reason). Never blocks > 100 ms,
        so the handler keeps servicing its queues while waiting for triggers."""
        if self.cam is None or not self.cam.IsGrabbing():
            return None, 'not grabbing'
        r = self.cam.RetrieveResult(100, pylon.TimeoutHandling_Return)
        if not r or not r.IsValid():
            return None, 'timeout'
        try:
            if not r.GrabSucceeded():
                self.n_incomplete += 1
                return None, f'incomplete: {r.ErrorDescription}'
            block = r.BlockID
            if self._last_block is not None and block > self._last_block + 1:
                self.n_missing += block - self._last_block - 1
            self._last_block = block
            # seconds on the camera clock; the writer would mistake 125 MHz ticks for ns
            return r.GetArray().copy(), (block, r.TimeStamp / self._tick_hz)
        finally:
            r.Release()
