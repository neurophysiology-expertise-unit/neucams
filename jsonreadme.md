# NeuCams - Configure Cameras via JSON

You configure **what matters** through a small, consistent JSON. Every key is **lowercase snake\_case**. Internally we translate to each SDK's naming so **you don't have to**. We intentionally expose a **curated subset** of features-because dumping 100 SDK knobs into JSON is pain and bugs.

This README shows:

* The **common ideas** shared across cameras.
* The **exact, supported parameters** per camera (names + defaults pulled from the code you shared).
* **1‑line explanations** for each param.
* **Per‑camera JSON examples** that match what the drivers accept.
* **Camera-specific behavior notes** and limitations.

> Scope: This document focuses on camera params. Other sections (e.g., `recorder_params`, `server_params`) are out of scope here.

---

## Quick shape of the JSON

Each camera lives inside the top‑level `"cams"` list:

```json
{
  "cams": [
    {
      "description": "friendly-name",
      "driver": "avt | hamamatsu | genicam | opencv",
      "serial_number": "camera-serial",
      "params": { /* camera-specific keys below */ }
    }
  ]
}
```

---

## Common ideas (how to think about the knobs)

* **exposure** (μs): how long the sensor integrates light; longer = brighter, lower max FPS.
* **frame\_rate** (Hz): target FPS for free-running capture (ignored or capped if triggering/exposure says so).
* **gain / gain\_auto**: amplify signal; auto lets the camera pick a level (not all models support gain).
* **binning**: integer; 1 = full resolution, 2 = 2×2 binning, etc. (Only for drivers that expose it.)
* **triggering**: either **free-run** (no trigger) or **triggered** (camera waits for line/software). Names differ per camera but **your JSON stays snake\_case**.

> Below are **per‑camera** parameters, including defaults straight from your code.

---

## AVT (Allied Vision) - `driver: "avt"`

*VmbPy 1.1.1 backend; deterministic FPS via bandwidth budgeting.*

### Supported parameters (and defaults)

| Key                       |        Default | What it does                                                                      |
| ------------------------- | -------------: | --------------------------------------------------------------------------------- |
| **pixel\_format**         |      `"Mono8"` | Sensor output format; mapped to `PixelFormat.*`.                                  |
| **frame\_rate**           |         `30.0` | Target FPS in **free-run**; sets `AcquisitionFrameRateAbs` when `trigger_mode="Off"`. |
| **exposure**              |       `148.0` | Exposure time **in microseconds**; used only when `exposure_auto="Off"`.           |
| **exposure\_auto**        |        `"Off"` | Auto exposure: `"Off"|"Once"|"Continuous"`.                                       |
| **gain**                  |          `0.0` | Analog gain in dB; used only when `gain_auto="Off"`.                              |
| **gain\_auto**            |        `"Off"` | Auto gain: `"Off"|"Once"|"Continuous"`.                                           |
| **binning**               |            `1` | Applies to both H/V when supported.                                               |
| **reverse\_x**            |        `false` | Mirror horizontally (when supported).                                             |
| **reverse\_y**            |        `false` | Mirror vertically (when supported).                                               |
| **trigger\_mode**         |        `"Off"` | `"On"` enables triggering; `"Off"` = free-run.                                   |
| **trigger\_source**       |      `"Line1"` | e.g., `"Line1"`, `"Software"`, etc.                                              |
| **trigger\_activation**   | `"RisingEdge"` | Edge polarity.                                                                    |
| **trigger\_delay\_us**    |         `null` | Trigger delay **in microseconds** (if supported).                                 |
| **line\_selector**        |         `null` | Maps to `LineSelector` (when configuring I/O while trigger is On).                |
| **line\_mode**            |         `null` | Maps to `LineMode`.                                                               |
| **line\_source**          |         `null` | Maps to `LineSource`.                                                             |
| **user\_output\_selector**|         `null` | Maps to `UserOutputSelector`.                                                     |
| **user\_output\_value**   |         `null` | `True`/`False`; maps to `UserOutputValue`.                                       |
| **sync\_out\_source**     |         `null` | Maps to `SyncOutSource`.                                                          |
| **sync\_out\_levels**     |         `null` | Integer; maps to `SyncOutLevels`.                                                 |
| **sync\_out\_selector**   |         `null` | Maps to `SyncOutSelector`.                                                        |
| **sync\_out\_polarity**   |         `null` | Maps to `SyncOutPolarity`.                                                        |
| **acquisition\_mode**     | `"Continuous"` | `"Continuous"` or `"MultiFrame"`.                                                 |
| **n\_frames**             |            `1` | Used only when `acquisition_mode="MultiFrame"`.                                   |
| **stream\_constrain**     |         `null` | Bool → `StreamFrameRateConstrain` (transport).                                     |
| **stream\_bps**           |         `null` | Bytes/sec → `StreamBytesPerSecond` (transport).                                   |
| **packet\_size**          |         `null` | e.g., `8228` or `1500` → `GevSCPSPacketSize`.                                     |
| **require\_full\_access** |        `false` | If `True`, refuse to open in read-only.                                           |
| **asynchronous**          |         `true` | **Streaming mode**: `True` = async callback + queue; `False` = sync polling.      |
| **buffer\_count**         |           `20` | Buffer count passed to `start_streaming()` in async mode.                          |
| **verbose**               |         `null` | If `null`, inherits `NEUCAMS_VERBOSE`; otherwise bool to force logging.           |

**Notes**

* With `trigger_mode: "Off"` **and** a set `frame_rate`, the driver:

  1. disables stream constrain,
  2. computes `StreamBytesPerSecond` with \~25% headroom,
  3. sets `AcquisitionFrameRateAbs` → **stable FPS** (if the link can carry it).
* Ensure `exposure_us ≤ 1e6 / frame_rate` in free‑run.
* **Full trigger support** with all standard trigger modes, sources, and activations.

### Minimal AVT JSON

```json
{
  "description": "Mako AVT",
  "driver": "avt",
  "serial_number": "50-0537068788",
  "params": {
    "pixel_format": "Mono8",
    "exposure": 12000,
    "exposure_auto": "Off",
    "gain_auto": "On",
    "binning": 1,
    "frame_rate": 30,
    "trigger_mode": "Off",
    "verbose": true
  }
}
```

---

## Hamamatsu (Orca) - `driver: "hamamatsu"`

*pyDCAM backend; your model ignores gain controls.*

### Supported parameters (and defaults)

| Key                 |      Default | What it does                                                                         |
| ------------------- | -----------: | ------------------------------------------------------------------------------------ |
| **exposure**        |    `20000.0` | Exposure time in **μs**.                                                             |
| **exposure\_auto**  |      `false` | Auto exposure (true/false).                                                          |
| **binning**         |          `1` | Sensor binning factor.                                                               |
| **frame\_rate**     |       `null` | Internal frame-rate target (effective rate limited by exposure/readout).             |
| **trigger\_source** | `"INTERNAL"` | `"INTERNAL"` free-run, `"EXTERNAL"` hardware trigger, `"SOFTWARE"` software trigger. |
| **gain**            |       `null` | Present for API symmetry; **ignored** on this model.                                 |
| **gain\_auto**      |       `null` | Present for API symmetry; **ignored** on this model.                                 |
| **subarray\_mode**  |      `false` | Enable ROI mode.                                                                     |
| **subarray\_size**  |       `null` | ROI `(width, height)` when `subarray_mode` is true.                                  |
| **subarray\_pos**   |     `(0, 0)` | ROI top‑left `(x, y)` when `subarray_mode` is true.                                  |
| **verbose**         |       `true` | Verbose logging.                                                                     |

**Notes**

* With `trigger_source: "INTERNAL"`, achievable FPS is **capped by exposure + readout**; driver logs a ceiling estimate.
* `gain`/`gain_auto` are logged as ignored on this Orca model.
* **Limited trigger support** via the `trigger_source` parameter:
  - `"INTERNAL"` - Free-run mode
  - `"EXTERNAL"` - External hardware trigger
  - `"SOFTWARE"` - Software trigger

### Minimal Hamamatsu JSON

```json
{
  "description": "Orca",
  "driver": "hamamatsu",
  "serial_number": "003024",
  "params": {
    "exposure": 20000.0,
    "exposure_auto": false,
    "binning": 1,
    "frame_rate": 30,
    "trigger_source": "INTERNAL",
    "subarray_mode": false,
    "verbose": true
  }
}
```

---

## GenICam (e.g., Teledyne Dalsa) - `driver: "genicam"`

*Harvester backend; **IMPORTANT: Trigger support is automatically disabled** for most Dalsa cameras.*

### ⚠️ **Trigger Behavior (Important)**

**Dalsa cameras are completely excluded from trigger operations:**
- **Global trigger control** via the Master Trigger checkbox skips Dalsa cameras entirely
- **Automatic detection**: The driver tests for `TriggerMode`, `TriggerSelector`, and `TriggerSource` nodes
- **Fallback to free-run**: If trigger nodes are missing, the camera automatically operates in free-run mode
- **Log messages**: You'll see `"Skipping trigger setting for Dalsa camera 'camera_name' - not supported"`

This is **hardcoded** in:
1. `neucams/view/widgets.py` - `_broadcast_trigger_setting()` method skips `driver: "genicam"`
2. `neucams/cams/genicam.py` - `apply_params()` method detects trigger support and forces `trigger_mode: "off"`

### Exposed parameters (and defaults)

| Key                   |        Default | What it does                                                       |
| --------------------- | -------------: | ------------------------------------------------------------------ |
| **exposure**          |        `29000` | Exposure time in **μs**.                                           |
| **frame\_rate**       |           `30` | Acquisition frame rate (free-run only).                            |
| **gain**              |            `8` | Camera gain value.                                                 |
| **gain\_auto**        |        `false` | Auto gain (`true` → `"Once"`, `false` → `"Off"`).                  |
| **acquisition\_mode** | `"Continuous"` | `"Continuous"` or `"MultiFrame"`.                                  |
| **n\_frames**         |            `1` | Frames to grab in `"MultiFrame"`.                                  |

### Trigger parameters (present but typically ignored)

> These parameters are **defined in the driver** but will be **automatically disabled** for most Dalsa cameras:

| Key                     |        Default | What it does                               | Status |
| ----------------------- | -------------: | ------------------------------------------ | ------ |
| **trigger\_mode**       |        `"off"` | `"off"` free-run, `"on"` external trigger. | ⚠️ Auto-disabled |
| **trigger\_source**     |      `"line1"` | Which hardware line triggers frames.       | ⚠️ Auto-disabled |
| **trigger\_activation** | `"rising_edge"` | Trigger edge selection.                    | ⚠️ Auto-disabled |
| **trigger\_selector**   | `"frame_start"` | Trigger selector mode.                     | ⚠️ Auto-disabled |

**Notes**

* **Free-run mode only**: Dalsa cameras will always operate in free-run mode regardless of trigger settings
* **Parameter validation**: If `trigger_mode: "on"` is specified, it will be automatically changed to `"off"`  
* **Global trigger immunity**: The Master Trigger checkbox in the UI has no effect on Dalsa cameras
* You can safely include trigger parameters in your JSON - they will be ignored gracefully

### Minimal GenICam JSON

```json
{
  "description": "Dalsa-1",
  "driver": "genicam",
  "serial_number": "H2260407",
  "params": {
    "exposure": 1000,
    "frame_rate": 30,
    "gain": 8,
    "gain_auto": false,
    "acquisition_mode": "Continuous",
    "n_frames": 1
  }
}
```

---

## OpenCV (Webcams/Facecams) - `driver: "opencv"`

*OpenCV backend for USB webcams, built-in laptop cameras, and external cameras.*

### Supported parameters (and defaults)

| Key                |  Default | What it does                                           |
| ------------------ | -------: | ------------------------------------------------------ |
| **frame\_rate**    |   `30.0` | Target capture rate in FPS.                            |
| **width**          |    `640` | Capture width in pixels.                               |
| **height**         |    `480` | Capture height in pixels.                              |
| **auto\_exposure** |   `true` | Enable automatic exposure control.                     |
| **exposure**       |    `0.5` | Manual exposure (0.0-1.0) when auto_exposure is off.   |
| **brightness**     |    `0.5` | Brightness adjustment (0.0-1.0).                       |
| **contrast**       |    `0.5` | Contrast control (0.0-1.0).                            |
| **saturation**     |    `0.5` | Color saturation (0.0-1.0).                            |
| **hue**            |    `0.5` | Hue adjustment (0.0-1.0).                              |
| **gain**           |    `0.5` | Camera gain (0.0-1.0).                                 |

**Notes**

* **Camera IDs**: Use `"id": 0` for built-in cameras, `"id": 1, 2, 3...` for external USB cameras
* **Limited trigger support**: Primarily free-run mode only
* **Real-time preview**: Full compatibility with NeuCams UI and recording systems
* **Multiple cameras**: Can run multiple OpenCV cameras simultaneously
* **Auto-detection**: Parameters are applied if supported by the camera hardware

### Minimal OpenCV JSON

```json
{
  "description": "facecam",
  "driver": "opencv",
  "id": 0,
  "params": {
    "frame_rate": 30,
    "width": 1280,
    "height": 720,
    "auto_exposure": true,
    "brightness": 0.5,
    "contrast": 0.5
  }
}
```

---

## File Saving Behavior

**Path Setting**: The save folder path is set via the UI run name controls or UDP commands. Once set, it persists for all subsequent acquisitions until changed.

**File Naming**: Files are automatically numbered with the pattern `YYMMDD_RunNumber_FileIndex.extension` where:
- `YYMMDD` is the current date
- `RunNumber` increments each time recording starts  
- `FileIndex` starts at 1 for each new file in the same run

**Frame Count Reset**: When you change the save name/location and start a new recording, the frame counter resets to 0 with a log message:
```
Frame count reset: 1250 frames reset to 0 for new save location: C:/data/camera1/session1/run2.tif
```

**Rollover**: For TIFF files, a new file is created every 256 frames by default (configurable via `frames_per_file` parameter).

**Path Display**: All paths are displayed with forward slashes (`/`) for consistency, regardless of user input format.

---

## Gotchas & tips

* **Always snake\_case** in JSON. Don't use SDK names like `AcquisitionFrameRateAbs`-use `frame_rate`.
* **AVT deterministic FPS** depends on bandwidth. Weak links → drops (honest signal to fix NIC/switch/jumbos).
* **Hamamatsu gain**: present for symmetry, ignored on your Orca model.
* **Dalsa triggering**: **Completely disabled** - all Dalsa cameras operate in free-run mode only.
* **OpenCV camera IDs**: Test different IDs (0, 1, 2...) to find your cameras.
* **require\_full\_access (AVT)**: set to `true` if you'd rather fail than open read‑only.
* **Verbose**: AVT honors `NEUCAMS_VERBOSE=1/true` if `verbose` is `null`/unset.

---

## Output (recording/export)

If you select a TIFF-based output format, you can optionally control the per-frame file size/tiling via `tiff_size`.

- `tiff_size` (integer, bytes): Target chunk/tile size when writing TIFF. Helps balance I/O vs. memory. If omitted, a driver default is used.

> Note: This parameter is only applied when the selected output is TIFF; it is ignored for other formats.

---

*End of README*
