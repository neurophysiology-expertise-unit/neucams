import os, sys
from pathlib import Path

# Where we’re running from (frozen or dev)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))

GENTL_DIR = BASE / "gentl"          # your MatrixVision/etc bundle
AVT_DIR   = BASE / "vmbpy"          # your AVT bundle
DCAM_DIR  = BASE / "dcam"           # your Hamamatsu DCAM runtime bundle

def _prepend(env: str, p: Path):
    pstr = str(p)
    parts = os.environ.get(env, "").split(os.pathsep) if os.environ.get(env) else []
    if pstr not in parts:
        os.environ[env] = pstr + (os.pathsep + os.environ[env] if os.environ.get(env) else "")

# Env vars Harvester/GenICam tools may still read
_prepend("HARVESTERS_GENTL_PATH", GENTL_DIR)
_prepend("GENICAM_GENTL64_PATH", GENTL_DIR)

# PATH for Windows DLL resolver
_prepend("PATH", GENTL_DIR)
_prepend("PATH", AVT_DIR)
_prepend("PATH", DCAM_DIR)

# Python 3.8+ DLL dir hint (Windows only)
if hasattr(os, "add_dll_directory"):
    for p in (GENTL_DIR, AVT_DIR, DCAM_DIR):
        if p.exists():
            os.add_dll_directory(str(p))
