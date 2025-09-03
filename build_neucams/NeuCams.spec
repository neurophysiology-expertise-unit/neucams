import glob, importlib, sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# --- Resolve paths from the spec location (fallback if __file__ is missing)
try:
    SPEC_DIR = Path(__file__).resolve().parent
except NameError:
    SPEC_DIR = Path.cwd()              # PyInstaller sometimes runs spec without __file__

ROOT      = SPEC_DIR.parent            # repo root
PKG_DIR   = ROOT / "neucams"
VMB_DIR   = ROOT / "vmbpy"
GENTL_DIR = ROOT / "gentl"
DCAM_DIR  = ROOT / "dcam"
ICON      = str((ROOT / "icon.ico").resolve())


def files(pattern, dest="."):
    return [(str(p), dest) for p in glob.glob(str(pattern))]

# --- locate cv2 binary
cv2_mod = importlib.import_module("cv2")
CV2_PYD = Path(cv2_mod.__file__)

# --- binaries
binaries = [
    *files(VMB_DIR / "*.dll", "vmbpy"),
    *files(GENTL_DIR / "*.dll", "gentl"),
    *files(DCAM_DIR / "*.dll", "dcam"),
    (str(CV2_PYD), "."),
]

# --- data (include Qt .ui and icon next to exe)
datas = [
    *files(VMB_DIR / "VmbC.xml", "vmbpy"),
    *files(ROOT / "neucams" / "view" / "*.ui", "neucams/view"),
    *files(GENTL_DIR / "*.cti", "gentl"),
    *files(ROOT / "icon.ico", "."),            # window icon available at runtime
]

hiddenimports = [
    "neucams.cams.genicam",
    "neucams.cams.avt_cam",
    "neucams.cams.hamamatsu_cam",
] + collect_submodules("harvesters") \
  + collect_submodules("genicam") \
  + collect_submodules("pyDCAM") \
  + collect_submodules("pyqtgraph")      

block_cipher = None

a = Analysis(
    [str(PKG_DIR / "__main__.py")],            # <-- correct entry
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
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                               # keep console for logs
    icon=ICON,
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
