# NeuCams — Configure Cameras via JSON

You configure **what matters** through a small, consistent JSON. Every key is **lowercase snake\_case**. Internally we translate to each SDK’s naming so **you don’t have to**. We intentionally expose a **curated subset** of features—because dumping 100 SDK knobs into JSON is pain and bugs.

This README shows:

* The **common ideas** shared across cameras.
* The **exact, supported parameters** per camera (names + defaults pulled from the code you shared).
* **1‑line explanations** for each param.
* **Per‑camera JSON examples** that match what the drivers accept.

> Scope: This document focuses on camera params. Other sections (e.g., `recorder_params`, `server_params`) are out of scope here.

---

## Quick shape of the JSON

Each camera lives inside the top‑level `"cams"` list:

```json
{
  "cams": [
    {
      "description": "friendly-name",
      "driver": "avt | hamamatsu | genicam",
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

## AVT (Allied Vision) — `driver: "avt"`

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

## Hamamatsu (Orca) — `driver: "hamamatsu"`

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

## GenICam (e.g., Teledyne Dalsa) — `driver: "genicam"`

*Harvester backend; clean subset with optional trigger keys.*

### Exposed parameters (and defaults)

| Key                   |        Default | What it does                                                       |
| --------------------- | -------------: | ------------------------------------------------------------------ |
| **exposure**          |        `29000` | Exposure time in **μs**.                                           |
| **frame\_rate**       |           `30` | Acquisition frame rate (free-run).                                 |
| **gain**              |            `8` | Camera gain value.                                                 |
| **gain\_auto**        |        `false` | Auto gain (`true` → `"Once"`, `false` → `"Off"`).                  |
| **acquisition\_mode** | `"Continuous"` | `"Continuous"` or `"MultiFrame"`.                                  |
| **n\_frames**         |            `1` | Frames to grab in `"MultiFrame"`.                                  |
| **triggered**         |        `false` | Shortcut boolean for trigger mode (`true` ≈ `trigger_mode: "On"`). |

### Also recognized trigger keys (normalized & applied when triggering)

> These are **accepted by the driver** (even though not listed in its `exposed_params` array):

| Key                     |        Default | What it does                               |
| ----------------------- | -------------: | ------------------------------------------ |
| **trigger\_mode**       |        `"Off"` | `"Off"` free-run, `"On"` external trigger. |
| **trigger\_source**     |      `"Line1"` | Which hardware line triggers frames.       |
| **trigger\_activation** | `"RisingEdge"` | Trigger edge selection.                    |

**Notes**

* If `triggered: true` or `trigger_mode: "On"`, the camera **waits for triggers**; `frame_rate` becomes a cap or is ignored depending on model.
* Driver sets sane GenICam defaults under the hood (`TriggerSelector="FrameStart"`, TTL level, no debouncing).

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
    "n_frames": 1,
    "trigger_mode": "Off",
    "trigger_source": "Line1",
    "trigger_activation": "RisingEdge"
  }
}
```

---

## Gotchas & tips

* **Always snake\_case** in JSON. Don’t use SDK names like `AcquisitionFrameRateAbs`—use `frame_rate`.
* **AVT deterministic FPS** depends on bandwidth. Weak links → drops (honest signal to fix NIC/switch/jumbos).
* **Hamamatsu gain**: present for symmetry, ignored on your Orca model.
* **require\_full\_access (AVT)**: set to `true` if you’d rather fail than open read‑only.
* **Verbose**: AVT honors `NEUCAMS_VERBOSE=1/true` if `verbose` is `null`/unset.

---

## Output (recording/export)

If you select a TIFF-based output format, you can optionally control the per-frame file size/tiling via `tiff_size`.

- `tiff_size` (integer, bytes): Target chunk/tile size when writing TIFF. Helps balance I/O vs. memory. If omitted, a driver default is used.

> Note: This parameter is only applied when the selected output is TIFF; it is ignored for other formats.

---

*End of README*
