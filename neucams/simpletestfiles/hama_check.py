#!/usr/bin/env python3
"""
List all Hamamatsu DCAM (pyDCAM) properties for a selected camera.

Usage examples:
  python list_dcam_features.py                        # first detected camera
  python list_dcam_features.py --index 0              # select by zero-based index
  python list_dcam_features.py --id 003024            # select by DCAM_IDSTR_CAMERAID (exact/substring)
  python list_dcam_features.py --model C11440         # select by model (substring match)
  python list_dcam_features.py --names                # print only property names
"""

import argparse
from typing import List, Optional, Tuple

# pyDCAM imports (works with 'pip install pyDCAM')
from pyDCAM import use_dcamapi
from pyDCAM.dcamapi import HDCAM, DCAMError
from pyDCAM.dcamprop import DCAMIDPROP
from pyDCAM.dcamapi_enum import DCAM_IDSTR

# ---- discovery helpers -------------------------------------------------------

def _openable_cameras(max_probe: int = 16) -> List[Tuple[int, str, str]]:
    """
    Probe camera indices [0..max_probe-1], return list of (index, model, camera_id)
    for indices that can be opened.
    """
    found = []
    for idx in range(max_probe):
        try:
            with HDCAM(idx) as cam:
                model = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_MODEL)
                camid = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)
                found.append((idx, model, camid))
        except DCAMError:
            continue
        except Exception:
            continue
    return found

def _pick_camera(index: Optional[int], want_id: Optional[str], want_model: Optional[str]) -> int:
    """
    Choose a camera index by index / camera-id substring / model substring.
    Raises RuntimeError if nothing matches.
    """
    cams = _openable_cameras()
    if not cams:
        raise RuntimeError("No Hamamatsu/pyDCAM cameras found.")

    # Explicit index wins if valid
    if index is not None:
        for idx, _, _ in cams:
            if idx == index:
                return idx
        raise RuntimeError(f"No camera at index {index}. Detected: {[c[0] for c in cams]}")

    # Match by ID substring
    if want_id:
        for idx, _model, camid in cams:
            if want_id in camid:
                return idx
        raise RuntimeError(f"No camera whose CAMERAID contains {want_id!r}. "
                           f"Detected: {[c[2] for c in cams]}")

    # Match by model substring
    if want_model:
        for idx, model, _camid in cams:
            if want_model in model:
                return idx
        raise RuntimeError(f"No camera whose MODEL contains {want_model!r}. "
                           f"Detected: {[c[1] for c in cams]}")

    # Default: first
    return cams[0][0]

# ---- formatting helpers ------------------------------------------------------

def _access_flags(cam: HDCAM, prop_id: int, current_value) -> str:
    """
    Infer access flags: 'R' if readable, 'W' if we can set the current value back.
    We never change anything: on W test we set the exact same value.
    """
    r = 'R' if current_value is not None else '-'
    w = '-'
    if current_value is not None:
        try:
            # cast to float: DCAM API takes numeric; if it explodes, we mark as non-writable
            cam.dcamprop_setgetvalue(prop_id, float(current_value))
            w = 'W'
        except Exception:
            w = '-'
    return f"{r}{w}"

def _safe_get_value(cam: HDCAM, prop_id: int):
    try:
        return cam.dcamprop_getvalue(prop_id)
    except Exception as e:
        return f"<error: {e}>"

def _prop_name(cam: HDCAM, prop_id: int) -> str:
    try:
        return cam.dcamprop_getname(prop_id)
    except Exception:
        return f"<prop 0x{int(prop_id):08X}>"

# ---- dump logic --------------------------------------------------------------

def dump_properties(cam: HDCAM, names_only: bool = False):
    try:
        prop_ids = cam.dcamprop_ids()  # supported properties only
    except Exception as e:
        print(f"(could not enumerate properties: {e})")
        return

    # Sort by human-readable name if possible
    try:
        prop_ids = sorted(prop_ids, key=lambda pid: _prop_name(cam, pid))
    except Exception:
        pass

    for pid in prop_ids:
        name = _prop_name(cam, pid)
        if names_only:
            print(name)
            continue

        val = _safe_get_value(cam, pid)
        readable_value = None if isinstance(val, str) and val.startswith("<error:") else val
        acc = _access_flags(cam, pid, readable_value)
        print(name)
        print(f"  access={acc} | value={val}")

def dump_key_properties(cam: HDCAM):
    keys = [
        DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME,
        DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE,
        DCAMIDPROP.DCAM_IDPROP_INTERNAL_FRAMEINTERVAL,
        DCAMIDPROP.DCAM_IDPROP_IMAGE_WIDTH,
        DCAMIDPROP.DCAM_IDPROP_IMAGE_HEIGHT,
        DCAMIDPROP.DCAM_IDPROP_BINNING,
    ]
    print("\nKey props:")
    for pid in keys:
        try:
            print(f"{cam.dcamprop_getname(pid)} = {cam.dcamprop_getvalue(pid)}")
        except Exception as e:
            print(f"{pid} <error: {e}>")

# ---- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="List pyDCAM / Hamamatsu DCAM properties.")
    ap.add_argument("--index", type=int, help="Zero-based camera index (probing 0..15)")
    ap.add_argument("--id", dest="camera_id", help="Match DCAM_IDSTR_CAMERAID (substring ok)")
    ap.add_argument("--model", help="Match DCAM_IDSTR_MODEL (substring ok)")
    ap.add_argument("--names", action="store_true", help="Print only property names")
    ap.add_argument("--no-keys", action="store_true", help="Skip the 'Key props' section")
    args = ap.parse_args()

    with use_dcamapi:
        idx = _pick_camera(args.index, args.camera_id, args.model)
        with HDCAM(idx) as cam:
            model = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_MODEL)
            camid = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)
            print("=" * 80)
            print(f"Hamamatsu DCAM Properties — {model} [CAMERAID={camid}] (index={idx})")
            print("=" * 80)

            dump_properties(cam, names_only=args.names)

            if not args.no_keys and not args.names:
                dump_key_properties(cam)

if __name__ == "__main__":
    main()
