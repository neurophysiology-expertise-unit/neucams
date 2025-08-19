# NeuCams_hama.spec
# build with:  pyinstaller --clean -y NeuCams_hama.spec
import os, glob, importlib
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# -------- paths (relative to project root)
BASE      = Path(os.path.abspath('.'))
FILES_DIR = BASE / 'files'
VMB_DIR   = BASE / 'vmbpy'   # Allied Vision bundle you ship
GENTL_DIR = BASE / 'gentl'   # GenTL producer bundle you ship
DCAM_DIR  = BASE / 'dcam'    # Hamamatsu DCAM runtime you drop here

def files(pattern, dest="."):
    return [(str(p), dest) for p in glob.glob(str(pattern))]

# -------- locate cv2 binary (no hardcoded env)
cv2_mod = importlib.import_module('cv2')
CV2_PYD = Path(cv2_mod.__file__)  # e.g. .../site-packages/cv2.cp39-win_amd64.pyd

# -------- binaries
binaries = [
    # AVT / vmbpy native libs
    *files(VMB_DIR / "*.dll", "vmbpy"),

    # GenTL producer DLLs
    *files(GENTL_DIR / "*.dll", "gentl"),

    # Hamamatsu DCAM runtime DLLs
    *files(DCAM_DIR / "*.dll", "dcam"),

    # OpenCV Python extension (explicit so PyInstaller can't miss it)
    (str(CV2_PYD), "."),
]

# -------- data
datas = [
    *files(VMB_DIR / "VmbC.xml", "vmbpy"),
    *files("files/neucams/view/*.ui", "neucams/view"),
    *files(GENTL_DIR / "*.cti", "gentl"),
]

# -------- hidden imports (restore pyqtgraph!)
hiddenimports = [
    "neucams.cams.genicam",
    "neucams.cams.avt_cam",
    "neucams.cams.hamamatsu_cam",
    "pyqtgraph",
] \
+ collect_submodules("pyqtgraph") \
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
    runtime_hooks=['hooks/rthook_env.py'],  # make sure this adds BASE/vmbpy, BASE/gentl, BASE/dcam to PATH
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
    upx=False,
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
    upx=False,
    upx_exclude=[],
    name="NeuCams",
)
