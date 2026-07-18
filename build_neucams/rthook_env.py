import os, sys, pathlib
base = pathlib.Path(getattr(sys, '_MEIPASS', pathlib.Path(__file__).resolve().parent)).resolve()
for sub in ('dcam','gentl','vmbpy'):
    p = base / sub
    if p.exists():
        try:
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(str(p))
        except Exception:
            pass
