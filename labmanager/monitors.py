"""Monitors of this PC, numbered the way the Remote Desktop client (`mstsc /l`) numbers them.

mstsc counts the display outputs attached to the desktop in the order EnumDisplayDevices returns them;
the primary monitor is not necessarily 0. Size and position come from the current display settings
(physical pixels), so Windows' scaling does not change them.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
ENUM_CURRENT_SETTINGS = -1


@dataclass(frozen=True)
class Device:
    name: str
    attached: bool
    primary: bool
    width: int
    height: int
    left: int
    top: int


@dataclass(frozen=True)
class Monitor:
    rdp_id: int
    label: str
    primary: bool
    width: int
    height: int


def numbered(devices: list[Device]) -> list[Monitor]:
    """Attached devices in EnumDisplayDevices order get the ids 0, 1, 2 …; labels name their position."""
    attached = [d for d in devices if d.attached]
    by_left = sorted(attached, key=lambda d: d.left)
    if len(by_left) == 2:
        names = {by_left[0].name: "links", by_left[1].name: "rechts"}
    elif len(by_left) == 3:
        names = {by_left[0].name: "links", by_left[1].name: "Mitte", by_left[2].name: "rechts"}
    else:
        names = {d.name: f"{i + 1}. von links" for i, d in enumerate(by_left)} if len(by_left) > 1 else {}
    result: list[Monitor] = []
    for rdp_id, d in enumerate(attached):
        parts = [p for p in (names.get(d.name, ""), f"{d.width}×{d.height}", "Hauptbildschirm" if d.primary else "") if p]
        result.append(Monitor(rdp_id=rdp_id, label=" · ".join(parts), primary=d.primary, width=d.width, height=d.height))
    return result


def selection_order(chosen: set[int], available: list[Monitor]) -> tuple[int, ...]:
    """The primary monitor first (it becomes the session's main screen), the others by id."""
    primary = [m.rdp_id for m in available if m.primary and m.rdp_id in chosen]
    return tuple(primary + sorted(i for i in chosen if i not in primary))


class _DisplayDevice(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class _DevMode(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD), ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", wintypes.SHORT), ("dmDuplex", wintypes.SHORT), ("dmYResolution", wintypes.SHORT),
                ("dmTTOption", wintypes.SHORT), ("dmCollate", wintypes.SHORT), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD), ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD), ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


def devices() -> list[Device]:
    """All display outputs of this PC from Windows (EnumDisplayDevicesW, EnumDisplaySettingsW)."""
    user32 = ctypes.windll.user32
    found: list[Device] = []
    index = 0
    while True:
        dd = _DisplayDevice(cb=ctypes.sizeof(_DisplayDevice))
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(dd), 0):
            return found
        index += 1
        attached = bool(dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)
        mode = _DevMode(dmSize=ctypes.sizeof(_DevMode))
        has_mode = attached and user32.EnumDisplaySettingsW(dd.DeviceName, ENUM_CURRENT_SETTINGS, ctypes.byref(mode))
        found.append(Device(name=dd.DeviceName, attached=attached,
                            primary=bool(dd.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                            width=mode.dmPelsWidth if has_mode else 0, height=mode.dmPelsHeight if has_mode else 0,
                            left=mode.dmPositionX if has_mode else 0, top=mode.dmPositionY if has_mode else 0))


def current() -> list[Monitor]:
    return numbered(devices())
