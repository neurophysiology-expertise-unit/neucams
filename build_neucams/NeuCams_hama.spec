# NeuCams_hama.spec
# build with:  pyinstaller --clean -y NeuCams_hama.spec
import glob, os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# ------------------------------------------------------------------ paths (relative, no hardcoded env)
BASE       = Path(os.path.abspath('.'))
FILES_DIR  = BASE / 'files'
VMB_DIR    = BASE / 'vmbpy'   # Allied Vision bundle shipped in repo
GENTL_DIR  = BASE / 'gentl'   # Matrix Vision (or other) GenTL bundle shipped in repo
DCAM_DIR   = BASE / 'dcam'    # Hamamatsu DCAM runtime you will drop here


def files(pattern, dest="."):
    return [(str(p), dest) for p in glob.glob(str(pattern))]

# ------------------------------------------------------------------ binaries (ship vendor DLLs into vendor folders)
binaries = [
    # AVT / vmbpy native libs
    *files(VMB_DIR / "*.dll", "vmbpy"),

    # Matrix Vision (or other) GenTL producer DLLs
    *files(GENTL_DIR / "*.dll", "gentl"),

    # Hamamatsu DCAM runtime DLLs (you provide them in build_neucams/dcam)
    *files(DCAM_DIR / "*.dll", "dcam"),
]

# ------------------------------------------------------------------ data
# Keep UI files and GenTL CTIs together with vendor bundles
# Note: code lives under files/neucams/* (Constructor copies repo here)
datas = [
    *files(VMB_DIR / "VmbC.xml", "vmbpy"),
    *files("files/neucams/view/*.ui", "neucams/view"),

    # Ship CTIs inside gentl folder
    *files(GENTL_DIR / "*.cti", "gentl"),
]

# ------------------------------------------------------------------ hidden imports
hiddenimports = [
    # dynamic camera modules loaded via importlib in camera_handler
    "neucams.cams.genicam",
    "neucams.cams.avt_cam",
    "neucams.cams.hamamatsu_cam",
] \
+ collect_submodules("harvesters") \
+ collect_submodules("genicam") \
+ collect_submodules("pyDCAM")

block_cipher = None

a = Analysis(
    ["files\\neucams\\__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=['hooks/rthook_env.py'],
    excludes=['pyqtgraph.opengl', 'OpenGL'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="NeuCams",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # don't compress vendor DLLs
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    icon="icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,            # no UPX here either
    upx_exclude=[],
    name="NeuCams",
) 