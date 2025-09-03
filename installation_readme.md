# NeuCams   Build & Packaging Guide

This guide describes two ways to get **NeuCams** up and running:

1. **Run from source** (Conda + `python -m neucams`)
2. **Run the installer** (`NeuCams 1.0.0 windows x86_64.exe`)

   * Built with **PyInstaller** → `NeuCams.exe`
   * Wrapped inside a self contained **Conda Constructor** installer

> **Assumptions**
>
> * Windows 10 64-bit, PowerShell/git bash


---

## 1. Quick Start

### 1.1 Option A   Install the pre built offline package *(easiest)*

1. Download `NeuCams 1.0.0 windows x86_64.exe` from the latest GitHub release.
2. **Run as Administrator** and pick an **empty** install directory (e.g. `C:\NeuCams`).
   *Untick* “Register NeuCams as system Python” unless you *really* need it.
3. Launch NeuCams from the Start Menu shortcut, desktop shortcut **or** `C:Program Files\NeuCams\NeuCams.exe` (or whatever folder you choose to install it).

### 1.2 Option B   Run from source *(GitHub Repositoy)*

```powershell
# clone once
git clone https://github.com/AhmetCemalO/neucams.git
cd neucams

# create / activate env, the name is arbitrary.
conda env create -f environment.yml -n neucams_env
conda activate neucams_env

# to run the application, use the outer folder
python -m neucams
```

#### IMPORTANT: There are two nested folders, both are called neucams, run the commands from the outer neucams folder.

---

## 2. IP Configuration (Ethernet cameras)

Whether you installed NeuCams via the **installer** *or* you’re running from **source**, Ethernet cameras **won’t show up** until the NIC ↔ camera IPs match. Follow these two steps for *every* NIC camera pair:

### 2.1 Configure each Ethernet adapter

1. **Settings ▸ Network & Internet ▸ Advanced network settings ▸ Change adapter options**.
2. Double click each **Ethernet X**, hit **Properties ▸ Internet Protocol Version 4 (TCP/IPv4)**.
3. Select **Use the following IP address** and enter

   * **IP address**   `192.168.<x>.1`
   * **Subnet mask**   `255.255.255.0` (auto fills)
     Pick a unique `<x>` (1 254) per port.
4. **OK** to save.

### 2.2 Assign persistent IPs to cameras

1. Launch **mvIPConfigure** (Matrix Vision)   choose **Work as Administrator** if nothing appears.
2. Select a camera ▸ **Configure** ▸ tick **Use Persistent IP**.
3. Set

   * **IPv4 address**   `192.168.<x>.10` (same `<x>` as its NIC)
   * **Subnet mask**   `255.255.255.0`
4. In **Connected to IPv4 address** pick `192.168.<x>.1` (matching NIC).
5. **Apply changes**. Repeat for every camera.

Done! NeuCams should now list all devices automatically.

---

## 3. Troubleshooting

| Symptom                                        | Fix                                                                                                              |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Installer shows **“post\_install.bat failed”** | Merely cosmetic   if `NeuCams.exe` exists, you’re fine.                                                          |
| NeuCams starts but **no cameras detected**     | Install/repair **mvIMPACT Acquire** (IDS) *and* **Vimba X** (Allied Vision). Double check the IP settings above. |
| **GENICAM\_GENTL64\_PATH** not persistent      | Run the installer **as Administrator** or set the env var manually (point to the `.cti` files).                  |

---

## 4. Building the Installer (simplified)

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

## 5. Credits & Feedback
