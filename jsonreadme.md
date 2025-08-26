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
| **pixel\_format**         |      `"Mono8"` | Output pixel format (e.g., `"Mono8"`).                                            |
| **exposure**              |      `10000.0` | Exposure time in **μs**.                                                          |
| **exposure\_auto**        |        `"Off"` | `"Off"`, `"Once"`, `"Continuous"` (booleans also accepted).                       |
| **gain**                  |          `0.0` | Analog gain value.                                                                |
| **gain\_auto**            |        `"Off"` | `"Off"`, `"Once"`, `"Continuous"` (booleans also accepted).                       |
| **binning**               |            `1` | Sensor binning factor (applies H/V where supported).                              |
| **reverse\_x**            |        `false` | Mirror image horizontally.                                                        |
| **reverse\_y**            |        `false` | Mirror image vertically.                                                          |
| **acquisition\_mode**     | `"Continuous"` | `"Continuous"` or `"MultiFrame"`.                                                 |
| **n\_frames**             |            `1` | Frames to grab in `"MultiFrame"`.                                                 |
| **frame\_rate**           |         `null` | Target FPS (free-run); driver sets bps & AFR to hit it.                           |
| **trigger\_mode**         |        `"Off"` | `"Off"` free-run, `"On"` uses external trigger.                                   |
| **trigger\_source**       |      `"Line1"` | Trigger input source.                                                             |
| **trigger\_activation**   | `"RisingEdge"` | Trigger edge selection.                                                           |
| **trigger\_delay\_us**    |         `null` | Delay after trigger before exposure (μs).                                         |
| **stream\_constrain**     |         `null` | Toggle internal stream frame-rate constrain.                                      |
| **stream\_bps**           |         `null` | Force stream bandwidth (bytes/s); else auto when `frame_rate` set.                |
| **packet\_size**          |         `null` | GigE packet size (bytes).                                                         |
| **require\_full\_access** |        `false` | Refuse read‑only opens; fail if Full access not available.                        |
| **verbose**               |         `null` | Verbose logging. *`null` = follow `NEUCAMS_VERBOSE`; effective default is quiet.* |

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

*End of README*
