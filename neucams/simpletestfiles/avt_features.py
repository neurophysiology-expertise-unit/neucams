"""
List all Vimba X (VmbPy) features for a selected camera (and optionally its Interface/Stream/LocalDevice).

Usage examples:
  python list_vimba_features.py                         # first camera, camera (remote device) features
  python list_vimba_features.py --serial 50-0537068788  # select by serial
  python list_vimba_features.py --id DEV_1AB22C00041B   # select by camera ID
  python list_vimba_features.py --module all            # dump camera + interface + stream + local
  python list_vimba_features.py --names                 # print only canonical feature names

Exports:
  By default, also writes an XML and (if possible) an XLSX report. Use --no-xml/--no-xlsx to skip.
"""

import argparse
from typing import Iterable, List, Dict, Any, Optional
import os
from datetime import datetime
from xml.etree.ElementTree import Element, SubElement, ElementTree

try:
    import pandas as pd  # for optional xlsx export
except Exception:
    pd = None

from vmbpy import (
    VmbSystem,
    VmbFeatureError,
    # Feature classes:
    IntFeature, FloatFeature, StringFeature, BoolFeature, EnumFeature, CommandFeature, RawFeature
)

# ---------- helpers ----------

FeatContainer = Iterable  # Camera / Interface / Stream / LocalDevice all implement get_all_features()

def _safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default

def _pick_camera(vmb, cam_id: Optional[str] = None, serial: Optional[str] = None):
    cams = vmb.get_all_cameras()
    if not cams:
        raise RuntimeError("No cameras found.")

    # Prefer ID if provided
    if cam_id:
        for c in cams:
            if _safe_call(c.get_id) == cam_id:
                return c

    # Serial may require opening the camera
    if serial:
        for c in cams:
            try:
                with c:
                    if _safe_call(c.get_serial) == serial:
                        return c
            except Exception:
                pass

    # Fallback: first camera
    return cams[0]

def _fmt_access(feat) -> str:
    r = 'R' if feat.is_readable() else '-'
    w = 'W' if feat.is_writeable() else '-'
    return f"{r}{w}"

def _describe_numeric(feat):
    rng = _safe_call(feat.get_range)
    inc = _safe_call(feat.get_increment)
    unit = _safe_call(feat.get_unit)
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

        if isinstance(feat, EnumFeature):
            ent = feat.get()
            name = _safe_call(ent.get_name, "?")
            val  = _safe_call(ent.get_value, "?")
            return f"{name} ({val})"

        if isinstance(feat, (IntFeature, FloatFeature, StringFeature, BoolFeature)):
            return str(feat.get())

        if isinstance(feat, RawFeature):
            # Prefer a cheap length query; fall back carefully
            ln = None
            ln = _safe_call(feat.get_length, None)
            if ln is None:
                # As a last resort; may be expensive on huge blobs
                ln = _safe_call(lambda: len(feat.get()), None)
            return f"<raw bytes, length={ln}>"

        if isinstance(feat, CommandFeature):
            return "<command>"

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
            if _safe_call(e.is_available, False):
                names.append(nm)
            else:
                names.append(nm + " [unavailable]")
        except Exception:
            pass
    return ", ".join(names)

def dump_features(container: FeatContainer, title: str, names_only: bool = False,
                  collector: Optional[List[Dict[str, Any]]] = None, module_name: str = "camera"):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    try:
        feats = sorted(
            container.get_all_features(),
            key=lambda f: (
                _safe_call(f.get_category, "") or "",
                _safe_call(f.get_name, "") or ""
            )
        )
    except RuntimeError as e:
        # e.g., called outside required context
        print(f"(skipped: {e})")
        return

    for feat in feats:
        name = _safe_call(feat.get_name, "<noname>")
        if names_only:
            print(name)
            continue

        # Use class name; reliable across vmbpy versions
        typ = feat.__class__.__name__
        cat = _safe_call(feat.get_category, "?")
        acc = _fmt_access(feat)
        val = _value_as_str(feat)

        range_min = range_max = increment = unit = None
        choices = ""

        if isinstance(feat, (IntFeature, FloatFeature)):
            rng = _safe_call(feat.get_range)
            if isinstance(rng, tuple) and len(rng) >= 2:
                range_min, range_max = rng[0], rng[1]
            increment = _safe_call(feat.get_increment)
            unit = _safe_call(feat.get_unit)
            extra = _describe_numeric(feat)
        elif isinstance(feat, EnumFeature):
            choices = _enum_choices(feat)
            extra = f"choices=[{choices}]"
        else:
            extra = ""

        print(name)
        print(f"  type={typ} | cat={cat} | access={acc} | value={val}" + (f" | {extra}" if extra else ""))

        if collector is not None:
            collector.append({
                "module": module_name,
                "name": name,
                "type": typ,
                "category": cat,
                "access": acc,
                "readable": feat.is_readable(),
                "writeable": feat.is_writeable(),
                "value": val,
                "range_min": range_min,
                "range_max": range_max,
                "increment": increment,
                "unit": unit,
                "enum_choices": choices,
            })

def _default_prefix(cam) -> str:
    try:
        cid = cam.get_id()
    except Exception:
        cid = "camera"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_id = str(cid).replace(os.sep, "_").replace(":", "_")
    return f"avt_features_{safe_id}_{ts}_5"

def _export_xml(rows: List[Dict[str, Any]], path: str):
    root = Element("features")
    by_mod: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_mod.setdefault(r.get("module", "camera"), []).append(r)
    for mod, items in by_mod.items():
        m_el = SubElement(root, "module", name=str(mod))
        for r in items:
            f_el = SubElement(m_el, "feature", name=str(r.get("name", "")))
            for k in ("type","category","access","readable","writeable","value","unit"):
                SubElement(f_el, k).text = str(r.get(k))
            rng_el = SubElement(f_el, "range")
            if r.get("range_min") is not None:
                SubElement(rng_el, "min").text = str(r.get("range_min"))
            if r.get("range_max") is not None:
                SubElement(rng_el, "max").text = str(r.get("range_max"))
            if r.get("increment") is not None:
                SubElement(f_el, "increment").text = str(r.get("increment"))
            if r.get("enum_choices"):
                SubElement(f_el, "enum_choices").text = str(r.get("enum_choices"))
    ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    print(f"Saved XML: {path}")

def _export_xlsx_or_csv(rows: List[Dict[str, Any]], xlsx_path: str):
    if pd is None:
        raise RuntimeError("pandas not available; cannot write XLSX. Install pandas+openpyxl or use --no-xlsx.")
    df = pd.DataFrame(rows)
    csv_path = os.path.splitext(xlsx_path)[0] + ".csv"
    df.to_csv(csv_path, index=False)

def main():
    ap = argparse.ArgumentParser(description="List VmbPy / Vimba X features.")
    ap.add_argument("--id", dest="cam_id", help="Camera ID (e.g. DEV_xxx)")
    ap.add_argument("--serial", help="Camera serial (e.g. 50-05xxxxxx)")
    ap.add_argument("--module", choices=["camera", "interface", "stream", "local", "all"],
                    default="camera", help="Which feature set to dump")
    ap.add_argument("--names", action="store_true", help="Print only feature names")
    ap.add_argument("--out-prefix", default=None, help="Output file prefix (default: avt_features_<camid>_<timestamp>)")
    ap.add_argument("--no-xml", action="store_true", help="Do not write XML report")
    ap.add_argument("--no-xlsx", action="store_true", help="Do not write XLSX/CSV report")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []

    with VmbSystem.get_instance() as vmb:
        cam = _pick_camera(vmb, cam_id=args.cam_id, serial=args.serial)

        # Camera (remote) features
        if args.module in ("camera", "all"):
            with cam:
                dump_features(cam, f"Camera (Remote Device) Features — {cam.get_name()} [{cam.get_id()}]",
                              names_only=args.names, collector=rows, module_name="camera")

        # Interface features
        if args.module in ("interface", "all"):
            iface = cam.get_interface()
            dump_features(iface, f"Interface Features — {iface.get_name()} [{iface.get_id()}]",
                          names_only=args.names, collector=rows, module_name="interface")

        # Stream features
        if args.module in ("stream", "all"):
            with cam:
                streams = cam.get_streams()
                if streams:
                    dump_features(streams[0], f"Stream Features — stream[0] of {cam.get_id()}",
                                  names_only=args.names, collector=rows, module_name="stream")
                else:
                    print("\n(no streams reported)")

        # Local device features
        if args.module in ("local", "all"):
            with cam:
                local = cam.get_local_device()
                dump_features(local, f"LocalDevice Features — {cam.get_id()}",
                              names_only=args.names, collector=rows, module_name="local")

        # Exports
        prefix = args.out_prefix or _default_prefix(cam)
        if rows:
            if not args.no_xml:
                _export_xml(rows, f"{prefix}.xml")
            if not args.no_xlsx:
                try:
                    _export_xlsx_or_csv(rows, f"{prefix}.xlsx")
                except Exception as e:
                    print(f"Tabular export skipped: {e}")

if __name__ == "__main__":
    main()
