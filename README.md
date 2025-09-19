
# NeuCams README

## Overview

NeuCams is a multi-camera acquisition and recording framework supporting AVT, GenICam, Hamamatsu, and OpenCV cameras. Version 2.0 introduces **freerun mode** to eliminate camera warm-up delay and decouple initialization from actual recording.

***

## Features

- Continuous freerun acquisition for instant readiness
- Master Run trigger synchronizes saving across all cameras
- Configurable per-camera parameters (exposure, frame rate, trigger source)
- Multiple output formats: binary, TIFF, FFMPEG, OpenCV
- PyQt5-based GUI with Record, Master Run, and Stop controls
- Modular camera drivers via `CameraFactory`

***

## Installation

1. Clone the repository:

```bash
git clone https://github.com/yourorg/neucams.git
cd neucams
```

2. Create and activate a conda environment:

```bash
conda create -n neucams python=3.9 pyqt=5
conda activate neucams
pip install -r requirements.txt
```


***

## Configuration

Define cameras in a JSON file (e.g. `camera_preferences.json`). Each entry supports:

- `driver`: “avt”, “genicam”, “hamamatsu”, or “opencv”
- `id`: numeric camera index
- `serial`: serial number for identification
- `freerun`: `true` to start continuous acquisition on Record
- `params`: camera-specific parameters

Example:

```json
[
  {
    "driver": "avt",
    "id": 0,
    "serial": "12345678",
    "freerun": true,
    "params": {
      "AcquisitionMode": "Continuous",
      "FrameRate": 30,
      "ExposureTime": 10000,
      "TriggerSource": "Software"
    }
  },
  {
    "driver": "avt",
    "id": 1,
    "serial": "87654321",
    "freerun": true,
    "params": {
      "AcquisitionMode": "Continuous",
      "FrameRate": 30,
      "ExposureTime": 10000,
      "TriggerSource": "Software"
    }
  }
]
```


***

## Usage

1. Launch the GUI:

```bash
python main.py --pref camera_preferences.json
```

2. In the GUI, press **Record**:
    - Each freerun-enabled camera starts continuous acquisition but does not save frames.
3. Press **Master Run**:
    - All cameras begin saving buffered and incoming frames in sync.
4. Press **Stop** to end acquisition.

***

## File Structure

- `main.py`
GUI launcher and preference parsing.
- `camera_handler.py`
Handles camera initialization, freerun mode, trigger logic, and acquisition loop.
- `file_writer.py`
Implements `BinaryWriter`, `TiffWriter`, `FFMPEGWriter`, and `OpenCVWriter`.
- `utils.py`
Display utilities, serial-number resolution, and logging.
- `udp_socket.py`
Real-time control messaging for remote start/stop.
- `cams/`
Individual camera driver modules (`avt_cam.py`, `genicam.py`, etc.).

***

## How It Works

1. **Initialization**
    - `CameraHandler` applies parameters and (if `freerun`) immediately calls `cam.start()`.
2. **Record**
    - UI calls `start_recording()`, which clears triggers and sets the handler ready flag.
3. **Master Run**
    - UI calls `master_run()`, setting `start_trigger`.
    - Handlers unfreeze from `wait_for_trigger()`.
    - If not freerun, they perform `cam.stop()`/`cam.start()`.
    - All cameras begin writing frames in lockstep.
4. **Stop**
    - UI calls `stop()`, handlers cease acquisition and close writers.

***

## Notes

- No changes are required in driver modules (`cams/avt_cam.py`, etc.).
- To disable freerun, set `"freerun": false` or omit the key.
- Ensure network bandwidth and disk I/O can handle your configured frame rates and resolutions.

***

## License

MIT License. See `LICENSE` for details.

