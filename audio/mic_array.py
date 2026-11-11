"""Whether the microphone array (ReSpeaker XVF3800) is plugged into this computer.

Read from Windows' list of devices that are present, the same list Device Manager shows. It
only reads the list: the board is not opened, configured or sent anything. A board that is
powered by a charge-only cable lights up but never appears in the list, which is exactly the
"lit, not connected" case: it counts as not connected.

The match is on the USB vendor (Seeed sells the ReSpeaker board; XMOS makes its chip) or on
the name Windows gives it, not on a product id, so a board revision does not break it.
This has not been tried against the real board yet (it was not connected when this was
written): ``find_device`` is tested on devices that were present, and the vendor ids and names
below come from the manufacturers' documentation.
"""
from __future__ import annotations

import sys

# Hardware ids look like "USB\VID_2886&PID_001A&REV_0100".
USB_VENDOR_IDS = ("2886", "20B1")  # Seeed Technology, XMOS
NAME_WORDS = ("xvf3800", "respeaker")


def find_device(vendor_ids: tuple[str, ...] = USB_VENDOR_IDS, words: tuple[str, ...] = NAME_WORDS) -> str | None:
    """The name of a present device that is the microphone array, or None.

    Never raises: where the list cannot be read (not Windows, a failing driver stack) the
    answer is None, "not connected".
    """
    if sys.platform != "win32":
        return None
    try:
        return _find_in_windows(vendor_ids, words)
    except Exception:
        return None


def _find_in_windows(vendor_ids: tuple[str, ...], words: tuple[str, ...]) -> str | None:
    import ctypes
    from ctypes import wintypes

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

    class Guid(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    class DeviceInfoData(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("ClassGuid", Guid), ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    get_devices = setupapi.SetupDiGetClassDevsW
    get_devices.restype = ctypes.c_void_p
    get_devices.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    enum_info = setupapi.SetupDiEnumDeviceInfo
    enum_info.restype = wintypes.BOOL
    enum_info.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DeviceInfoData)]
    get_property = setupapi.SetupDiGetDeviceRegistryPropertyW
    get_property.restype = wintypes.BOOL
    get_property.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(DeviceInfoData), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    destroy = setupapi.SetupDiDestroyDeviceInfoList
    destroy.restype = wintypes.BOOL
    destroy.argtypes = [ctypes.c_void_p]

    digcf_present, digcf_allclasses = 0x2, 0x4
    spdrp_devicedesc, spdrp_hardwareid, spdrp_friendlyname = 0x0, 0x1, 0xC
    invalid = ctypes.c_void_p(-1).value

    def text_of(handle, info, prop) -> list[str]:
        needed = wintypes.DWORD(0)
        get_property(handle, ctypes.byref(info), prop, None, None, 0, ctypes.byref(needed))
        if needed.value == 0:
            return []
        buffer = ctypes.create_string_buffer(needed.value)
        if not get_property(handle, ctypes.byref(info), prop, None, buffer, needed.value, None):
            return []
        return [part for part in buffer.raw.decode("utf-16-le", errors="ignore").split("\x00") if part]

    handle = get_devices(None, "USB", None, digcf_present | digcf_allclasses)
    if handle is None or handle == invalid:
        return None
    # Everything under the USB bus, and the audio devices made from it (their names say XVF3800).
    names_found: str | None = None
    try:
        index = 0
        while True:
            info = DeviceInfoData()
            info.cbSize = ctypes.sizeof(DeviceInfoData)
            if not enum_info(handle, index, ctypes.byref(info)):
                break
            index += 1
            ids = [text.upper() for text in text_of(handle, info, spdrp_hardwareid)]
            names = text_of(handle, info, spdrp_friendlyname) + text_of(handle, info, spdrp_devicedesc)
            by_vendor = any(f"VID_{vendor.upper()}&" in hardware_id for vendor in vendor_ids for hardware_id in ids)
            by_name = any(word in name.lower() for word in words for name in names)
            if by_vendor or by_name:
                names_found = (names[0] if names else ids[0] if ids else "USB").strip()
                break
    finally:
        destroy(handle)
    return names_found
