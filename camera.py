"""DirectShow access to a UVC camera's hardware controls (zoom, pan, focus, brightness...).

Settings are written into the camera itself, so they apply in every app that uses it.
Run `python camera.py` to print what the connected camera supports.
"""
from ctypes import POINTER, c_long, c_ulong, c_void_p
from dataclasses import dataclass

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from comtypes.automation import VARIANT
from comtypes.persist import IPropertyBag

CLSID_SYSTEM_DEVICE_ENUM = GUID("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")
CLSID_VIDEO_INPUT_CATEGORY = GUID("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")
IID_BASE_FILTER = GUID("{56a86895-0ad4-11ce-b03a-0020af0ba770}")
FLAG_AUTO, FLAG_MANUAL = 1, 2


class IMoniker(IUnknown):
    _iid_ = GUID("{0000000f-0000-0000-C000-000000000046}")
    # Only the slots up to BindToStorage are called; earlier ones just keep the vtable order.
    _methods_ = [
        COMMETHOD([], HRESULT, "GetClassID", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "IsDirty"),
        COMMETHOD([], HRESULT, "Load", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "Save", (["in"], c_void_p), (["in"], c_long)),
        COMMETHOD([], HRESULT, "GetSizeMax", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "BindToObject", (["in"], c_void_p), (["in"], c_void_p),
                  (["in"], POINTER(GUID)), (["out"], POINTER(POINTER(IUnknown)))),
        COMMETHOD([], HRESULT, "BindToStorage", (["in"], c_void_p), (["in"], c_void_p),
                  (["in"], POINTER(GUID)), (["out"], POINTER(POINTER(IUnknown)))),
    ]


class IEnumMoniker(IUnknown):
    _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["in"], c_ulong), (["out"], POINTER(POINTER(IMoniker))),
                  (["out"], POINTER(c_ulong))),
    ]


class ICreateDevEnum(IUnknown):
    _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID)),
                  (["out"], POINTER(POINTER(IEnumMoniker))), (["in"], c_ulong)),
    ]


def _range_get_set_methods():
    """Vtable shared by IAMCameraControl and IAMVideoProcAmp: GetRange, Set, Get."""
    out = (["out"], POINTER(c_long))
    return [
        COMMETHOD([], HRESULT, "GetRange", (["in"], c_long), out, out, out, out, out),
        COMMETHOD([], HRESULT, "Set", (["in"], c_long), (["in"], c_long), (["in"], c_long)),
        COMMETHOD([], HRESULT, "Get", (["in"], c_long), out, out),
    ]


class IAMCameraControl(IUnknown):
    _iid_ = GUID("{C6E13370-30AC-11d0-A18C-00A0C9118956}")
    _methods_ = _range_get_set_methods()


class IAMVideoProcAmp(IUnknown):
    _iid_ = GUID("{C6E13360-30AC-11d0-A18C-00A0C9118956}")
    _methods_ = _range_get_set_methods()


# key: (label, group, interface, DirectShow property id). Order = display order.
# Hardware zoom/pan/tilt are left out: the Owl Lite reports them but ignores them (see pipeline.py).
CONTROLS = {
    "focus": ("Focus", "Focus & light", IAMCameraControl, 6),
    "exposure": ("Exposure", "Focus & light", IAMCameraControl, 4),
    "white_balance": ("White balance", "Focus & light", IAMVideoProcAmp, 7),
    "brightness": ("Brightness", "Focus & light", IAMVideoProcAmp, 0),
    "gain": ("Gain", "Focus & light", IAMVideoProcAmp, 9),
    "backlight": ("Backlight compensation", "Focus & light", IAMVideoProcAmp, 8),
    "contrast": ("Contrast", "Color & detail", IAMVideoProcAmp, 1),
    "saturation": ("Saturation", "Color & detail", IAMVideoProcAmp, 3),
    "hue": ("Hue", "Color & detail", IAMVideoProcAmp, 2),
    "sharpness": ("Sharpness", "Color & detail", IAMVideoProcAmp, 4),
    "gamma": ("Gamma", "Color & detail", IAMVideoProcAmp, 5),
}


@dataclass(frozen=True)
class Range:
    """Limits the camera reports for one control."""
    min: int
    max: int
    step: int
    default: int
    can_auto: bool


def list_video_devices():
    """Return [(friendly_name, moniker)] in DirectShow order (same order OpenCV's CAP_DSHOW indexes use)."""
    dev_enum = comtypes.client.CreateObject(CLSID_SYSTEM_DEVICE_ENUM, interface=ICreateDevEnum)
    enum = dev_enum.CreateClassEnumerator(CLSID_VIDEO_INPUT_CATEGORY, 0)
    devices = []
    while enum:
        moniker, fetched = enum.Next(1)
        if not fetched:
            break
        bag = moniker.BindToStorage(None, None, IPropertyBag._iid_).QueryInterface(IPropertyBag)
        devices.append((bag.Read("FriendlyName", VARIANT(), None), moniker))
    return devices


class Camera:
    """One bound camera. All calls must happen on the thread that created it (COM STA)."""

    def __init__(self, name, index, moniker):
        """name: friendly name; index: DirectShow index (for preview); moniker: device moniker."""
        self.name, self.index = name, index
        base = moniker.BindToObject(None, None, IID_BASE_FILTER)
        self._ifaces = {i: base.QueryInterface(i) for i in (IAMCameraControl, IAMVideoProcAmp)}
        self.ranges = self._read_ranges()

    def _read_ranges(self):
        """Return {key: Range} for every control this camera supports."""
        ranges = {}
        for key, (_, _, iface, prop) in CONTROLS.items():
            try:
                lo, hi, step, default, flags = self._ifaces[iface].GetRange(prop)
            except comtypes.COMError:
                continue  # camera doesn't support this control
            ranges[key] = Range(lo, hi, step, default, bool(flags & FLAG_AUTO))
        return ranges

    def get(self, key):
        """Return {"value": int, "auto": bool} for a control."""
        _, _, iface, prop = CONTROLS[key]
        value, flags = self._ifaces[iface].Get(prop)
        return {"value": value, "auto": bool(flags & FLAG_AUTO)}

    def set(self, key, value, auto=False):
        """Write a control to the camera. value is clamped to its range."""
        _, _, iface, prop = CONTROLS[key]
        r = self.ranges[key]
        value = max(r.min, min(r.max, int(value)))
        self._ifaces[iface].Set(prop, value, FLAG_AUTO if auto and r.can_auto else FLAG_MANUAL)

    def snapshot(self):
        """Return {key: {"value", "auto"}} for all supported controls."""
        return {key: self.get(key) for key in self.ranges}

    def apply(self, settings):
        """Write a snapshot-style dict to the camera, skipping unsupported keys."""
        for key, s in settings.items():
            if key in self.ranges:
                self.set(key, s.get("value", self.ranges[key].default), s.get("auto", False))

    def reset(self):
        """Restore every control to the camera's factory default (auto where available)."""
        self.apply({k: {"value": r.default, "auto": r.can_auto} for k, r in self.ranges.items()})


def find_camera(name_contains):
    """Return the first Camera whose name contains name_contains (case-insensitive), or None."""
    for index, (name, moniker) in enumerate(list_video_devices()):
        if name_contains.lower() in name.lower():
            return Camera(name, index, moniker)
    return None


if __name__ == "__main__":
    for i, (name, _) in enumerate(list_video_devices()):
        print(f"[{i}] {name}")
    cam = find_camera("Owl")
    assert cam, "Owl camera not found"
    for k, r in cam.ranges.items():
        print(f"{k:14} {r}  now={cam.get(k)}")
