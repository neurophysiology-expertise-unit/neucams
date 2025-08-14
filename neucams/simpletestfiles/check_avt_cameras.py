#!/usr/bin/env python3
"""
Print current access mode and permitted access modes for key Mako features.

Usage:
  python probe_access_modes.py
  python probe_access_modes.py --serial 50-0537068788
  python probe_access_modes.py --id DEV_xxx
"""

import argparse
from vmbpy import VmbSystem, VmbFeatureError

FEATURES = [
    # frame rate / exposure / gain
    "AcquisitionFrameRateAbs",
    "ExposureTimeAbs",
    "ExposureAuto",
    "ExposureMode",
    "Gain",
    "GainRaw",
    "GainAuto",
    # trigger
    "TriggerSelector",
    "TriggerMode",
    "TriggerSource",
    "TriggerActivation",
    "TriggerDelayAbs",
    # image format
    "PixelFormat",
    "BinningHorizontal",
    "BinningVertical",
    "ReverseX",
    "ReverseY",
    # gigE transport that can constrain fps
    "StreamFrameRateConstrain",
    "StreamBytesPerSecond",
    "GevSCPSPacketSize",
]

def _pick_camera(vmb, cam_id=None, serial=None):
    cams = vmb.get_all_cameras()
    if not cams:
        raise RuntimeError("No cameras found.")
    if not cam_id and not serial:
        return cams[0]
    for c in cams:
        if cam_id and c.get_id() == cam_id:
            return c
        if serial and hasattr(c, "get_serial") and c.get_serial() == serial:
            return c
    raise RuntimeError(f"Camera not found (id={cam_id!r}, serial={serial!r})")

def _mode_to_str(m):
    try:
        return m.name
    except Exception:
        return str(m)

def dump_access(cam):
    # one pass over features to avoid re-querying names repeatedly
    names = {f.get_name() for f in cam.get_all_features()}

    print("\n=== CAMERA ACCESS ===")
    try:
        cm = cam.get_access_mode()
        print("camera.get_access_mode():", _mode_to_str(cm))
    except Exception as e:
        print("camera access query error:", e)

    print("\n=== FEATURE ACCESS (now) ===")
    print(f"{'Feature':30s} {'Mode':6s} {'Permitted'}")
    print("-" * 80)
    for name in FEATURES:
        if name not in names:
            print(f"{name:30s} {'n/a':6s} (feature not present)")
            continue
        try:
            feat = cam.get_feature_by_name(name)
            mode_now = _mode_to_str(feat.get_access_mode())
        except VmbFeatureError as e:
            print(f"{name:30s} error   {e}")
        except Exception as e:
            print(f"{name:30s} error   {e}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", dest="cam_id")
    ap.add_argument("--serial")
    args = ap.parse_args()

    with VmbSystem.get_instance() as vmb:
        cam = _pick_camera(vmb, cam_id=args.cam_id, serial=args.serial)
        with cam:  # must be inside context to query feature access correctly
            dump_access(cam)

if __name__ == "__main__":
    main()
