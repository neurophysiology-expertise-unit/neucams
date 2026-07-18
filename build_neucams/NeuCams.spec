# NeuCams.spec — drop-in
# - Prints exactly which sources/folders are used
# - Asserts your package exists so you don't accidentally build the wrong repo
# - Auto-collects pyDCAM submodules if installed
# - Bundles camera runtimes from ./dcam, ./gentl, ./vmbpy when present
# - Defaults to ONEDIR; set env NEUCAMS_ONEFILE=1 for onefile

import os, sys, subprocess
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# -------- Path resolution (relative to this spec file)
try:
    SPEC_DIR = Path(__file__).resolve().parent
except NameError:
    SPEC_DIR = Path.cwd()

ROOT      = SPEC_DIR.parent                # repo root (assumes spec sits in ./build_* folder)
PKG_DIR   = ROOT / "neucams"               # your package
DCAM_DIR  = ROOT / "dcam"                  # Hamamatsu runtime DLLs (optional)
GENTL_DIR = ROOT / "gentl"                 # GenTL producers (optional)
VMB_DIR   = ROOT / "vmbpy"                 # Allied Vision VimbaPy bits (optional)
ICON_PATH = (ROOT / "icon.ico")

# -------- Determine entry script
CANDIDATES = [
    PKG_DIR / "__main__.py",
    PKG_DIR / "main.py",
    ROOT    / "main.py",
    ROOT    / "app.py",
]
ENTRY = next((p for p in CANDIDATES if p.exists()), None)

print(f"[spec] SPEC_DIR = {SPEC_DIR}")
print(f"[spec] ROOT     = {ROOT}")
print(f"[spec] PKG_DIR  = {PKG_DIR}")
print(f"[spec] ENTRY    = {ENTRY}")

assert PKG_DIR.exists(), f"Expected package folder at {PKG_DIR}"
assert ENTRY and ENTRY.exists(), (
    "Couldn't find an entry script. Put one at neucams/__main__.py "
    "or neucams/main.py (or adjust CANDIDATES in the spec)."
)

# -------- Optional: build stamp (helps verify what's installed on target PCs)
STAMP = SPEC_DIR / "build_info.txt"
try:
    branch = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
    commit = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
except Exception:
    branch = "(no-git)"
    commit = "(no-git)"
STAMP.write_text(
    "built="  + __import__("datetime").datetime.now().isoformat() + "\n" +
    "root="   + str(ROOT) + "\n" +
    "entry="  + str(ENTRY) + "\n" +
    "branch=" + branch + "\n" +
    "commit=" + commit + "\n" +
    "python=" + sys.version.replace("\n", " ") + "\n",
    encoding="utf-8"
)
print(f"[spec] wrote build stamp: {STAMP}")

# -------- Hidden imports
def safe_collect(pkg: str):
    try:
        return collect_submodules(pkg)
    except Exception as e:
        print(f"[spec] skip collect_submodules('{pkg}') -> {e}")
        return []

hiddenimports = []
hiddenimports += safe_collect("pyDCAM")   # Hamamatsu wrapper (when installed)
# Add any other plugin packages here if needed:
# hiddenimports += safe_collect("vmbpy")
# hiddenimports += safe_collect("pypylon")

# -------- Data & binary helpers
def collect_dir_files(d: Path, dest_name: str, patterns=("*",)):
    items = []
    if not d.exists():
        return items
    for pat in patterns:
        for p in d.glob(pat):
            if p.is_file():
                items.append((str(p), dest_name))
    return items

datas = []
binaries = []

# Ship the build stamp next to the exe
datas.append((str(STAMP), "."))

# Camera runtimes (only if present)
binaries += collect_dir_files(DCAM_DIR,  "dcam",  patterns=("*.dll", "*.bin", "*.xml", "*.cfg"))
binaries += collect_dir_files(GENTL_DIR, "gentl", patterns=("*.dll", "*.cti", "*.bin", "*.xml"))
binaries += collect_dir_files(VMB_DIR,   "vmbpy", patterns=("*.dll", "*.bin", "*.xml"))

print(f"[spec] dcam files:  {len([b for b in binaries if b[1] == 'dcam'])}")
print(f"[spec] gentl files: {len([b for b in binaries if b[1] == 'gentl'])}")
print(f"[spec] vmbpy files: {len([b for b in binaries if b[1] == 'vmbpy'])}")

# -------- Optional runtime hook that extends DLL search path with our sidecar folders
RTHOOK = SPEC_DIR / "rthook_env.py"
if not RTHOOK.exists():
    RTHOOK.write_text(
        "import os, sys, pathlib\n"
        "base = pathlib.Path(getattr(sys, '_MEIPASS', pathlib.Path(__file__).resolve().parent)).resolve()\n"
        "for sub in ('dcam','gentl','vmbpy'):\n"
        "    p = base / sub\n"
        "    if p.exists():\n"
        "        try:\n"
        "            if hasattr(os, 'add_dll_directory'):\n"
        "                os.add_dll_directory(str(p))\n"
        "        except Exception:\n"
        "            pass\n",
        encoding="utf-8"
    )
    print(f"[spec] created runtime hook: {RTHOOK}")

runtime_hooks = [str(RTHOOK)]

# -------- Build graph
pathex = [str(ROOT), str(PKG_DIR)]
icon = str(ICON_PATH) if ICON_PATH.exists() else None
console = True  # set to False to hide console

from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

a = Analysis(
    scripts=[str(ENTRY)],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=runtime_hooks,
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe_kwargs = dict(
    name="NeuCams",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=console,
    icon=icon,
)

ONEFILE = os.environ.get("NEUCAMS_ONEFILE", "0") not in ("0", "", "false", "False", "no", "No")

if ONEFILE:
    # Single-file build
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        **exe_kwargs,
    )
else:
    # Onedir (recommended for camera SDKs)
    exe = EXE(
        pyz,
        a.scripts,
        **exe_kwargs,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name="NeuCams",
    )
