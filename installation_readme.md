# NeuCams - Build & Packaging Guide

This guide describes two ways to get **NeuCams** up and running:

1. **Run from source** (Conda + `python -m neucams`)
2. **Run the installer** (`NeuCams 1.0.0 windows x86_64.exe`)

   * Built with **PyInstaller** → `NeuCams.exe`
   * Wrapped inside a self contained **Conda Constructor** installer

> **Assumptions**
>
> * Windows 10 64-bit, PowerShell/git bash

---

## 1. Quick Start

### 1.1 Option A - Install the pre-built offline package *(easiest)*

1. Download `NeuCams 1.0.0 windows x86_64.exe` from the latest GitHub release.
2. **Run as Administrator** and pick an **empty** install directory (e.g. `C:\NeuCams`).
   *Untick* "Register NeuCams as system Python" unless you *really* need it.
3. Launch NeuCams from the Start Menu shortcut, desktop shortcut **or** `C:\Program Files\NeuCams\NeuCams.exe` (or whatever folder you choose to install it).

### 1.2 Option B - Run from source *(GitHub Repository)*

```powershell
# clone once
git clone https://github.com/neurophysiology-expertise-unit/neucams.git
cd neucams

# create / activate env, the name is arbitrary.
conda env create -f environment.yml -n neucams_env
conda activate neucams_env

# to run the application, use the outer folder
python -m neucams
```

#### IMPORTANT: There are two nested folders, both are called neucams, run the commands from the outer neucams folder.

---

## 2. Camera Setup

### 2.1 USB Cameras (Webcams/Facecams) - *Plug & Play*

**OpenCV-compatible cameras work immediately:**
- Built-in laptop cameras
- USB webcams  
- External USB cameras

**No additional setup required** - NeuCams will detect them automatically with camera IDs 0, 1, 2, etc.

Use the sample configuration `neucams/jsonfiles/webcam_facecam.json` to get started quickly.

### 2.2 Ethernet Cameras (Professional) - *IP Configuration Required*

Whether you installed NeuCams via the **installer** *or* you're running from **source**, Ethernet cameras **won't show up** until the NIC ↔ camera IPs match. Follow these two steps for *every* NIC camera pair:

#### Configure each Ethernet adapter

1. **Settings ▸ Network & Internet ▸ Advanced network settings ▸ Change adapter options**.
2. Double click each **Ethernet X**, hit **Properties ▸ Internet Protocol Version 4 (TCP/IPv4)**.
3. Select **Use the following IP address** and enter

   * **IP address** `192.168.<x>.1`
   * **Subnet mask** `255.255.255.0` (auto fills)
     Pick a unique `<x>` (1-254) per port.
4. **OK** to save.

#### Assign persistent IPs to cameras

1. Launch **mvIPConfigure** (Matrix Vision) - choose **Work as Administrator** if nothing appears.
2. Select a camera ▸ **Configure** ▸ tick **Use Persistent IP**.
3. Set

   * **IPv4 address** `192.168.<x>.10` (same `<x>` as its NIC)
   * **Subnet mask** `255.255.255.0`
4. In **Connected to IPv4 address** pick `192.168.<x>.1` (matching NIC).
5. **Apply changes**. Repeat for every camera.

Done! NeuCams should now list all devices automatically.

---

## 3. Supported Cameras & Drivers

| Camera Type | Driver | Trigger Support | Notes |
|-------------|--------|-----------------|-------|
| **Allied Vision (AVT)** | `avt` | ✅ Full | Hardware/software triggers, all modes |
| **Hamamatsu Orca** | `hamamatsu` | ⚠️ Limited | Internal/External/Software via `trigger_source` |
| **Teledyne Dalsa** | `genicam` | ❌ **Disabled** | **Free-run only** - triggers automatically disabled |
| **USB Webcams/Facecams** | `opencv` | ⚠️ Limited | Primarily free-run, real-time preview |

### Important: Dalsa Trigger Behavior
**Dalsa cameras operate in free-run mode only.** The application automatically:
- Skips Dalsa cameras from global trigger control
- Forces `trigger_mode: "off"` regardless of configuration
- Displays: `"Skipping trigger setting for Dalsa camera 'name' - not supported"`

---

## 4. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Installer shows **"post\_install.bat failed"** | Merely cosmetic - if `NeuCams.exe` exists, you're fine. |
| NeuCams starts but **no cameras detected** | Install/repair **mvIMPACT Acquire** (IDS) *and* **Vimba X** (Allied Vision). Double check the IP settings above. |
| **USB camera not detected** | Try different camera IDs in your config (0, 1, 2...). Check if camera works in other apps first. |
| **Dalsa camera won't trigger** | **Expected behavior** - Dalsa cameras are hardcoded to free-run mode only. |
| **Frame count doesn't reset** | Fixed in recent versions. Check terminal for "Frame count reset: X frames reset to 0" message. |
| **Path shows backslashes** | Fixed in recent versions. All paths now display with forward slashes (`/`). |
| **GENICAM\_GENTL64\_PATH** not persistent | Run the installer **as Administrator** or set the env var manually (point to the `.cti` files). |
| **Recording button disabled** | Set a save path first using the 'Set' button in the main toolbar. |

### Common Configuration Issues

**USB Cameras:**
```json
{
  "description": "my_webcam",
  "driver": "opencv",
  "id": 0,  // Try 0, 1, 2... until you find your camera
  "params": {
    "frame_rate": 30,
    "width": 1280,
    "height": 720
  }
}
```

**Professional Cameras:**
- Ensure proper IP configuration (see Section 2.2)
- Check that camera drivers are installed and up to date
- Verify camera serial numbers match your configuration

---

## 5. Recent Improvements

### Frame Count Reset ✅
- Frame counters now reset when changing save locations
- Clear terminal messages: `"Frame count reset: X frames reset to 0 for new save location"`

### Path Display Consistency ✅  
- All paths display with forward slashes (`/`) for better readability
- Input accepts both `\` and `/` but displays consistently

### OpenCV Camera Support ✅
- Full webcam/facecam support with real-time preview
- Configurable resolution, frame rate, exposure, brightness, etc.
- Multiple camera support (IDs 0, 1, 2...)

### Dalsa Trigger Clarification ✅
- Clear documentation that Dalsa cameras are trigger-disabled
- Automatic fallback to free-run mode
- Updated configuration examples

---

## 6. Building the Installer (simplified)

Use the provided environment and build script. This creates `dist/NeuCams/NeuCams.exe` and then wraps it into the offline installer.

```powershell
# 1) Create the build environment (uses the name inside environment.yml)
conda env create -f environment.yml

# 2) Build the installer
cd build_neucams
powershell.exe -ExecutionPolicy Bypass -File ./build_installer.ps1
```

Notes:
- Step 1 only needs to be done once on a new machine. If the env already exists, run `conda activate <env-name>` instead.
- The script runs PyInstaller and bundles required runtime files. Output `.exe` is placed in `build_neucams`.

---

## 7. Configuration Files

### Pre-made Configurations:
- `webcam_facecam.json` - USB cameras and webcams
- `triple_camera.json` - Multi-camera professional setup  
- `single_avt.json` - Single Allied Vision camera
- `hamamatsu.json` - Hamamatsu Orca setup

### Creating Custom Configurations:
See `jsonreadme.md` for complete parameter documentation and examples for each camera driver.

---

## 8. Credits & Feedback

NeuCams is based on the original labcams by João Couto, extensively rewritten for Python 3 with improved modularity, reliability, and camera support.

For issues or feedback, please use the GitHub repository issue tracker.
