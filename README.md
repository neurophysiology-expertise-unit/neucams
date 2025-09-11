
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


## Usage:

Launch NeuCams from the Start Menu (installer) or run: `python -m neucams`.

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

