#!/usr/bin/env python3
"""
List all Vimba X (VmbPy) features for a selected camera (and optionally its Interface/Stream/LocalDevice).

Usage examples:
  python list_vimba_features.py                         # first camera, camera (remote device) features
  python list_vimba_features.py --serial 50-0537068788  # select by serial
  python list_vimba_features.py --id DEV_1AB22C00041B   # select by camera ID
  python list_vimba_features.py --module all            # dump camera + interface + stream + local
  python list_vimba_features.py --names                 # print only canonical feature names
"""

import argparse
from typing import Iterable, Tuple

from vmbpy import (
    VmbSystem,
    VmbFeatureError,
    # Feature classes:
    IntFeature, FloatFeature, StringFeature, BoolFeature, EnumFeature, CommandFeature, RawFeature
)

# ---------- helpers ----------

FeatContainer = Iterable  # Camera / Interface / Stream / LocalDevice all implement get_all_features()

def _pick_camera(vmb, cam_id: str = None, serial: str = None):
    cams = vmb.get_all_cameras()
    if not cams:
        raise RuntimeError("No cameras found.")
    if not cam_id and not serial:
        return cams[0]
    for c in cams:
        ok_id = (cam_id and c.get_id() == cam_id)
        ok_sn = (serial and hasattr(c, "get_serial") and c.get_serial() == serial)
        if ok_id or ok_sn:
            return c
    raise RuntimeError(f"Camera not found. id={cam_id!r} serial={serial!r}")

def _fmt_access(feat) -> str:
    r = 'R' if feat.is_readable() else '-'
    w = 'W' if feat.is_writeable() else '-'
    return f"{r}{w}"

def _safe_get(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def _describe_numeric(feat):
    rng = _safe_get(feat.get_range)
    inc = _safe_get(feat.get_increment)
    unit = _safe_get(feat.get_unit)
    parts = []
    if isinstance(rng, tuple):
        parts.append(f"range={rng}")
    if inc is not None:
        parts.append(f"inc={inc}")
    if unit:
        parts.append(f"unit={unit}")
    return ", ".join(parts)

def _value_as_str(feat):
    try:
        if not feat.is_readable():
            return "<not readable>"
        # Type-specific pretty printing
        if isinstance(feat, EnumFeature):
            ent = feat.get()
            name = _safe_get(ent.get_name, "?")
            val  = _safe_get(ent.get_value, "?")
            return f"{name} ({val})"
        elif isinstance(feat, (IntFeature, FloatFeature, StringFeature, BoolFeature)):
            return str(feat.get())
        elif isinstance(feat, RawFeature):
            ln = _safe_get(feat.length, None)
            return f"<raw bytes, length={ln}>"
        elif isinstance(feat, CommandFeature):
            return "<command>"
        else:
            return str(feat.get())
    except VmbFeatureError as e:
        return f"<error: {e}>"
    except Exception as e:
        return f"<error: {e}>"

def _enum_choices(feat: EnumFeature) -> str:
    try:
        entries = feat.get_all_entries()
    except Exception:
        return ""
    names = []
    for e in entries:
        try:
            nm = e.get_name()
            if _safe_get(e.is_available, False):
                names.append(nm)
            else:
                names.append(nm + " [unavailable]")
        except Exception:
            pass
    return ", ".join(names)

def dump_features(container: FeatContainer, title: str, names_only: bool = False):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    try:
        feats = sorted(container.get_all_features(),
                       key=lambda f: ( _safe_get(f.get_category, "") or "",
                                       _safe_get(f.get_name, "") or ""))
    except RuntimeError as e:
        # e.g., called outside required context
        print(f"(skipped: {e})")
        return

    for feat in feats:
        name = _safe_get(feat.get_name, "<noname>")
        if names_only:
            print(name)
            continue

        typ = _safe_get(lambda: feat.get_type().__name__, "?")
        cat = _safe_get(feat.get_category, "?")
        acc = _fmt_access(feat)
        val = _value_as_str(feat)

        line1 = f"{name}"
        line2 = f"  type={typ} | cat={cat} | access={acc} | value={val}"

        extra = ""
        if isinstance(feat, (IntFeature, FloatFeature)):
            extra = _describe_numeric(feat)
        elif isinstance(feat, EnumFeature):
            choices = _enum_choices(feat)
            extra = f"choices=[{choices}]"

        print(line1)
        print(line2 + (f" | {extra}" if extra else ""))

def main():
    ap = argparse.ArgumentParser(description="List VmbPy / Vimba X features.")
    ap.add_argument("--id", dest="cam_id", help="Camera ID (e.g. DEV_xxx)")
    ap.add_argument("--serial", help="Camera serial (e.g. 50-05xxxxxx)")
    ap.add_argument("--module", choices=["camera", "interface", "stream", "local", "all"],
                    default="camera", help="Which feature set to dump")
    ap.add_argument("--names", action="store_true", help="Print only feature names")
    args = ap.parse_args()

    with VmbSystem.get_instance() as vmb:
        cam = _pick_camera(vmb, cam_id=args.cam_id, serial=args.serial)

        # Always dump camera (remote device) features when module=camera/all
        if args.module in ("camera", "all"):
            with cam:  # required before accessing camera & local features
                dump_features(cam, f"Camera (Remote Device) Features — {cam.get_name()} [{cam.get_id()}]", names_only=args.names)

        if args.module in ("interface", "all"):
            iface = cam.get_interface()
            dump_features(iface, f"Interface Features — {iface.get_name()} [{iface.get_id()}]", names_only=args.names)

        if args.module in ("stream", "all"):
            with cam:
                streams = cam.get_streams()
                if streams:
                    dump_features(streams[0], f"Stream Features — stream[0] of {cam.get_id()}", names_only=args.names)
                else:
                    print("\n(no streams reported)")

        if args.module in ("local", "all"):
            with cam:
                local = cam.get_local_device()
                dump_features(local, f"LocalDevice Features — {cam.get_id()}", names_only=args.names)

if __name__ == "__main__":
    main()
