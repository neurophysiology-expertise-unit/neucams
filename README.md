
# This repository is a version of Joao Couto's labcams repository (available at https://bitbucket.org/jpcouto/labcams). 
The name has changed to neucams, the intent is similar, but there are significant changes and a more compatible structure.

It's currently under work.

Changes include:

* different repository structure
* switch to Python 3
* extensive rewrite
* improved modularity and reliability
* multi‑camera acquisition with per‑camera settings; synchronized start/stop where supported

------


Multicamera control and acquisition.

This aims to facilitate video acquisition and automation of experiments, uses separate processes to record and store data.

### Supported cameras

 * Allied Vision (AVT) via Vimba/VmbPy
 * Teledyne Dalsa via GenICam/GenTL (free-run only; triggers disabled)
 * Hamamatsu ORCA via DCAM-API (pyDCAM)
 * USB webcams/facecams via OpenCV

### Features:

 *  Separate processes for viewer, capture, and file writing (stable at high FPS)
 *  Real-time preview with background subtraction and histogram equalization
 *  Multi-camera setups; hardware/software triggers where supported
 *  Recording via TIFF, FFmpeg (H.264/H.265, optional HW accel), or OpenCV/AVI
 *  Remote control over UDP (start/stop, soft trigger, set experiment name)


## Installation (run from source)

Tested on Windows 10/11 64-bit. You need [Miniconda/Anaconda](https://docs.conda.io/en/latest/miniconda.html)
and git.

```powershell
# 1. Clone (default branch is 'main' -- the working one)
git clone https://github.com/neurophysiology-expertise-unit/neucams.git
cd neucams

# 2. Create the conda environment (Python 3.9, PyQt5, OpenCV, ffmpeg, zeromq,
#    and the camera SDK Python bindings: vmbpy / harvesters+genicam / pyDCAM).
#    The env name 'neucams_env' comes from environment.yml.
conda env create -f environment.yml
conda activate neucams_env

# 3. Run (IMPORTANT: from the OUTER neucams folder -- there are two nested
#    folders both named 'neucams').
python -m neucams
```

> **Two nested folders, both named `neucams`.** Always run `python -m neucams`
> from the *outer* one (the repo root), or the package import fails.

### Camera vendor SDKs

The Python bindings are installed by `environment.yml`, but each camera family
also needs its **vendor runtime/driver** installed on the machine:

| Driver     | Camera            | Vendor runtime to install |
|------------|-------------------|---------------------------|
| `avt`      | Allied Vision     | Vimba/Vimba X SDK (GigE + USB transport layers) |
| `genicam`  | Teledyne Dalsa &c | a GenTL producer, e.g. Matrix Vision mvGenTL (provides the `.cti`); Dalsa is free-run only |
| `hamamatsu`| Hamamatsu ORCA    | DCAM-API drivers/runtime (Windows only) |
| `opencv`   | USB webcam/facecam| none (works out of the box) |

See `camera_instructions.md` for per-camera setup (incl. GigE NIC/IP config)
and `jsonreadme.md` for the config-file format.

## Usage

```powershell
conda activate neucams_env
# from the repo root (outer neucams folder):
python -m neucams                                   # opens the launcher/splash
python -m neucams -p neucams/jsonfiles/ch_camera.json   # load a config directly
python -m neucams -p <config.json> --verbose        # verbose logging
```

Ready-made config presets live in `neucams/jsonfiles/` (e.g. `ch_camera.json`
for the AVT Mako, `single_hama.json`, `single_dalsa2.json`, and
`dalsa_hamamatsu_avt.json` as a multi-camera reference).

## Building the standalone Windows executable / installer

PyInstaller + a Conda-Constructor installer. From the `neucams_env` env:

```powershell
# from the repo root:
build_neucams\build_installer.ps1        # builds dist\NeuCams\NeuCams.exe (+ installer if construct.yaml present)
```

The build bundles the camera runtime DLLs from `build_neucams/{dcam,gentl,vmbpy}`
via the `rthook_env.py` runtime hook, and `NeuCams.spec` asserts it is packaging
this repo's `neucams` package (so it can't silently build a stale copy). Full
details in `installation_readme.md`.

## Configuration files:

Configuration files ensure you always use the same parameters during your experiments.

The configuration files are simple ``json`` files. There are 2 parts to the files.

1. ``cams`` - **camera descriptions** - each camera has a section to store acquisition and recording parameters.

Available camera drivers:

 * `avt` - Allied Vision (Vimba/VmbPy runtime). Install Vimba SDK; VmbPy is bundled in the build.
 * `genicam` - Teledyne Dalsa and other GenICam devices via a GenTL producer (e.g., Matrix Vision mvGenTL). Note: Dalsa runs free-run only; trigger control is disabled.
 * `hamamatsu` - Hamamatsu ORCA via DCAM-API (pyDCAM). Windows-only; requires DCAM drivers.
 * `opencv` - Webcams/USB cameras via OpenCV.

Driver requirements (summary):

- AVT: Install Allied Vision Vimba SDK (GigE/USB transport layers). Ensure `VimbaGigETL.cti`/`VimbaUSBTL.cti` available.
- Dalsa/GenICam: Install a GenTL producer (e.g., Matrix Vision mvGenTL). Ensure `.cti` is discoverable (see `build_neucams/gentl/`).
- Hamamatsu: Install DCAM-API (drivers + runtime). pyDCAM is used by the driver; runtime DLLs are included under `build_neucams/dcam/` in the installer.
- OpenCV: Provided via Python dependency; no vendor SDK needed.

Each camera has its own parameters, there are some parameters that are common to all:

* `recorder` - the type of recorder `tiff` `ffmpeg` `opencv` `binary`
 * `haccel` - optional FFmpeg hardware acceleration: `nvidia` (NVENC) or `intel` (Media SDK/oneVPL)

**NOTE:** For NVIDIA acceleration, use an FFmpeg build with `NVENC` support (many Windows conda builds include it). Ensure FFmpeg is on your PATH.


**NOTE** For Intel acceleration, install Intel Media SDK/oneVPL and ensure FFmpeg can use it.


2. **general parameters** to control the remote communication ports and general gui or recording parameters.

 * `recorder_frames_per_file` number of frames per file
 * `recorder_path` the path of the recorder, how to handle substitutions - needs more info.
 

### UDP control

``neucams`` listens for simple UDP commands.

To enable, set both of the following in your config file:

- Under the root (or app-level) settings: ``"server":"udp"``
- Under ``server_params``: ``"udp_enable": true`` and optionally ``"server_port": 9999``

Example:

```json
"server": "udp",
"server_params": {
  "udp_enable": true,
  "server_ip": "0.0.0.0",
  "server_port": 9999,
  "server_refresh_time": 30
}
```

Supported UDP messages:

 * Start acquisition and (optionally) recording: ``start``
 * Stop acquisition and recording: ``stop``
 * Set run/experiment name: send a single message with the desired name, e.g. ``mymouse_session01``

Notes:

 * Messages are plain text. Unknown messages are ignored.
 * If a message does not contain ``=`` and is not ``start``/``stop``, it is treated as the run name.

---

### Credits and License

Joao Couto - jpcouto@gmail.com
See `LICENSE.txt` for licensing.

This project has been substantially rewritten and is maintained here.
Ahmet Cemal Öztürk - ozturk.ace@gmail.com

