# dcam_probe.py
from pyDCAM import *
from pyDCAM.dcamprop import DCAMIDPROP
from pyDCAM.dcamapi_enum import DCAM_IDSTR

def probe(device_index: int = 0):
    print("PROGRAM START")
    with use_dcamapi:
        with HDCAM(device_index) as cam:
            model = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_MODEL)
            camid = cam.dcamdev_getstring(DCAM_IDSTR.DCAM_IDSTR_CAMERAID)
            print(f"Opened: {model} [{camid}]")

            # 1) Make sure the Python-method names we rely on actually exist
            required = [
                "dcamprop_getvalue", "dcamprop_setgetvalue", "dcamprop_setvalue",
                "dcamprop_getname", "dcamprop_ids", "dcamwait_open",
                "dcambuf_alloc", "dcambuf_copyframe", "dcambuf_release",
                "dcamcap_start", "dcamcap_stop", "dcamdev_getstring"
            ]
            for name in required:
                if not hasattr(cam, name):
                    raise RuntimeError(f"Missing pyDCAM method: {name}")
            print("Method check: OK")

            # 2) Enumerate supported properties, read value, and test writability
            print("\nSupported properties (name\tsettable\tvalue):")
            for pid in cam.dcamprop_ids():  # defaults to supported props
                name = cam.dcamprop_getname(pid)
                try:
                    val = cam.dcamprop_getvalue(pid)
                except Exception as e:
                    print(f"{name}\treadable=False\t<error: {e}>")
                    continue
                # "Settable" test: try setting the current value back
                settable = True
                try:
                    cam.dcamprop_setgetvalue(pid, float(val))
                except Exception:
                    settable = False
                print(f"{name}\t{settable}\t{val}")

            # 3) Focused quick check on the usual suspects
            key_props = [
                DCAMIDPROP.DCAM_IDPROP_EXPOSURETIME,
                DCAMIDPROP.DCAM_IDPROP_INTERNALFRAMERATE,
                DCAMIDPROP.DCAM_IDPROP_INTERNAL_FRAMEINTERVAL,
                DCAMIDPROP.DCAM_IDPROP_IMAGE_WIDTH,
                DCAMIDPROP.DCAM_IDPROP_IMAGE_HEIGHT,
                DCAMIDPROP.DCAM_IDPROP_BINNING,
            ]
            print("\nKey props:")
            for pid in key_props:
                try:
                    print(f"{cam.dcamprop_getname(pid)} = {cam.dcamprop_getvalue(pid)}")
                except Exception as e:
                    print(f"{pid} <error: {e}>")

    print("PROGRAM END")

if __name__ == "__main__":
    probe()
