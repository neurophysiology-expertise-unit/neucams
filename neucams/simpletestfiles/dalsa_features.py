#!/usr/bin/env python3
"""
Dump all GenICam features for a selected device via Harvester (GenTL).

Usage:
  python dump_genicam_features.py                    # first camera
  python dump_genicam_features.py --serial 123456    # select by serial
  python dump_genicam_features.py --id DEV_ABCDEF    # select by camera-id
  python dump_genicam_features.py --json out.json    # also write JSON
  python dump_genicam_features.py --writable         # only writable nodes

Environment (CTI discovery order):
  - GENICAM_FORCE_CTI       (optional exact .cti path)
  - HARVESTERS_GENTL_PATH   or GENICAM_GENTL64_PATH (paths to *.cti)
  - "C:/Program Files/MATRIX VISION/mvIMPACT Acquire/bin/x64"
  - ./gentl inside a PyInstaller bundle (if present)
"""
import argparse, glob, json, os, sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from harvesters.core import Harvester

def _find_cti_candidates() -> List[str]:
    paths: List[str] = []
    for env in ("HARVESTERS_GENTL_PATH", "GENICAM_GENTL64_PATH"):
        for p in os.environ.get(env, "").split(os.pathsep):
            if p:
                paths += glob.glob(os.path.join(p, "*.cti"))
    mv = Path(r"C:/Program Files/MATRIX VISION/mvIMPACT Acquire/bin/x64")
    if mv.exists():
        paths += glob.glob(str(mv / "*.cti"))
    base = getattr(sys, "_MEIPASS", os.getcwd())
    cti_dir = Path(base) / "gentl"
    paths += glob.glob(str(cti_dir / "*.cti"))
    # dedupe, preserve order
    seen = set(); uniq = []
    for p in paths:
        if p not in seen:
            uniq.append(p); seen.add(p)
    return uniq

def _load_first_working_cti(h: Harvester, candidates: List[str]) -> str:
    last_err = None
    for cti in candidates:
        try:
            h.add_file(cti)
            h.update()
            return cti
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Failed to load any CTI from {candidates}\nLast error: {last_err}")

def _pick_device(h: Harvester, serial: str = None, cam_id: str = None) -> int:
    devs = h.device_info_list
    if not devs:
        raise RuntimeError("No GenTL devices found.")
    if cam_id:
        for i, d in enumerate(devs):
            if getattr(d, "id_", None) == cam_id or getattr(d, "id_", None) == str(cam_id):
                return i
        raise RuntimeError(f"Camera id '{cam_id}' not found. Available: {[d.id_ for d in devs]}")
    if serial:
        for i, d in enumerate(devs):
            if getattr(d, "serial_number", None) == serial:
                return i
        raise RuntimeError(f"Serial '{serial}' not found. Available: {[getattr(d,'serial_number',None) for d in devs]}")
    return 0

def _node_summary(node) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try: info["type"] = type(node).__name__
    except Exception: pass
    # access
    for attr in ("is_readable", "is_writable", "is_implemented", "is_available", "is_streamable"):
        try: info[attr] = bool(getattr(node, attr))
        except Exception: pass
    # value / string
    try:
        # Most nodes expose .value
        v = node.value
        # Avoid huge binary blobs
        info["value"] = v if isinstance(v, (int, float, str, bool)) else str(v)
    except Exception:
        try:
            info["value"] = node.to_string()
        except Exception:
            pass
    # numeric limits (if present)
    for attr in ("min", "max", "inc"):
        if hasattr(node, attr):
            try: info[attr] = getattr(node, attr)
            except Exception: pass
    # enum entries (best-effort)
    for attr in ("entries", "symbolics", "symbols", "get_entries", "get_symbolics"):
        if hasattr(node, attr):
            try:
                e = getattr(node, attr)
                e = e() if callable(e) else e
                # normalize to list of strings
                if isinstance(e, (list, tuple)):
                    info["enum_entries"] = [str(x) for x in e]
                else:
                    info["enum_entries"] = [str(e)]
            except Exception:
                pass
            break
    return info

def dump_features(node_map, only_writable: bool = False) -> List[Tuple[str, Dict[str, Any]]]:
    features: List[Tuple[str, Dict[str, Any]]] = []
    names = sorted({n for n in dir(node_map) if not n.startswith("_")})
    for name in names:
        try:
            node = getattr(node_map, name)
        except Exception:
            continue
        try:
            summary = _node_summary(node)
            if only_writable and not summary.get("is_writable", False):
                continue
            features.append((name, summary))
        except Exception:
            # keep going
            continue
    return features

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", help="Select camera by serial")
    ap.add_argument("--id", dest="cam_id", help="Select camera by GenTL id")
    ap.add_argument("--json", help="Write JSON to this path")
    ap.add_argument("--writable", action="store_true", help="Show only writable nodes")
    args = ap.parse_args()

    h = Harvester()
    force = os.environ.get("GENICAM_FORCE_CTI", "").strip() or None
    candidates = [force] if force else _find_cti_candidates()
    if not candidates:
        raise SystemExit("No CTI found. Set HARVESTERS_GENTL_PATH/GENICAM_GENTL64_PATH or install mvGenTL.")
    cti = _load_first_working_cti(h, candidates)

    idx = _pick_device(h, serial=args.serial, cam_id=args.cam_id)
    with h.create(idx) as ia:
        nm = ia.remote_device.node_map
        feats = dump_features(nm, only_writable=args.writable)

    # Pretty print
    print(f"# CTI: {cti}")
    dev = h.device_info_list[idx]
    print(f"# Device: id={getattr(dev,'id_',None)}  serial={getattr(dev,'serial_number',None)}  vendor={getattr(dev,'vendor','?')}  model={getattr(dev,'model','?')}")
    print(f"# Features ({'writable only' if args.writable else 'all'}): {len(feats)}\n")
    for name, meta in feats:
        line = f"{name}"
        val = meta.get("value", None)
        if val is not None:
            line += f" = {val}"
        t = meta.get("type", None)
        if t: line += f"  [{t}]"
        if "min" in meta or "max" in meta:
            mn = meta.get("min","?"); mx = meta.get("max","?")
            inc = meta.get("inc", None)
            line += f"  (min={mn}, max={mx}" + (f", inc={inc}" if inc is not None else "") + ")"
        if "enum_entries" in meta:
            line += f"  options={meta['enum_entries']}"
        if meta.get("is_writable") is False:
            line += "  [RO]"
        print(line)

    if args.json:
        out = {
            "cti": cti,
            "device": {
                "id": getattr(dev, 'id_', None),
                "serial": getattr(dev, 'serial_number', None),
                "vendor": getattr(dev, 'vendor', None),
                "model": getattr(dev, 'model', None),
            },
            "features": [{ "name": n, **m } for n, m in feats],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote JSON → {args.json}")

if __name__ == "__main__":
    main()
