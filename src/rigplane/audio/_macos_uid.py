"""macOS CoreAudio device UID lookup via ctypes.

Maps PortAudio / sounddevice device names to stable CoreAudio device UIDs
(``kAudioDevicePropertyDeviceUID``).  These UIDs persist across reboots and
re-enumeration, unlike integer indices which can shift when devices are
plugged / unplugged.

This module is macOS-only.  Callers must gate imports behind a
``sys.platform == "darwin"`` check.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CoreAudio constants
# ---------------------------------------------------------------------------
# AudioObjectPropertySelector values
kAudioHardwarePropertyDevices = 0x64657623  # 'dev#'
kAudioObjectPropertyName = 0x6C6E616D  # 'lnam'
kAudioDevicePropertyDeviceUID = 0x75696420  # 'uid '

# AudioObjectPropertyScope / Element
kAudioObjectPropertyScopeGlobal = 0x676C6F62  # 'glob'
kAudioObjectPropertyElementMain = 0  # kAudioObjectPropertyElementMaster on older SDKs

# Well-known object IDs
kAudioObjectSystemObject = 1


# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------
class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _load_coreaudio() -> ctypes.CDLL:
    """Load the CoreAudio framework, raising *OSError* on failure."""
    path = ctypes.util.find_library("CoreAudio")
    if path is None:
        raise OSError("CoreAudio framework not found")
    return ctypes.cdll.LoadLibrary(path)


def _get_all_device_ids(ca: ctypes.CDLL) -> list[int]:
    """Return AudioObjectIDs for every audio device on the system."""
    prop = AudioObjectPropertyAddress(
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )
    size = ctypes.c_uint32(0)
    status = ca.AudioObjectGetPropertyDataSize(
        ctypes.c_uint32(kAudioObjectSystemObject),
        ctypes.byref(prop),
        ctypes.c_uint32(0),
        None,
        ctypes.byref(size),
    )
    if status != 0 or size.value == 0:
        return []

    count = size.value // ctypes.sizeof(ctypes.c_uint32)
    buf = (ctypes.c_uint32 * count)()
    status = ca.AudioObjectGetPropertyData(
        ctypes.c_uint32(kAudioObjectSystemObject),
        ctypes.byref(prop),
        ctypes.c_uint32(0),
        None,
        ctypes.byref(size),
        ctypes.byref(buf),
    )
    if status != 0:
        return []
    return list(buf)


def _cfstring_to_python(cf: ctypes.c_void_p) -> str | None:
    """Convert a CFStringRef to a Python str, returning *None* on failure."""
    CoreFoundation = ctypes.cdll.LoadLibrary(
        ctypes.util.find_library("CoreFoundation")  # type: ignore[arg-type]
    )
    CFStringGetLength = CoreFoundation.CFStringGetLength
    CFStringGetLength.argtypes = [ctypes.c_void_p]
    CFStringGetLength.restype = ctypes.c_long

    CFStringGetCString = CoreFoundation.CFStringGetCString
    CFStringGetCString.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_uint32,
    ]
    CFStringGetCString.restype = ctypes.c_bool

    kCFStringEncodingUTF8 = 0x08000100
    length = CFStringGetLength(cf)
    buf_size = length * 4 + 1  # worst-case UTF-8
    buf = ctypes.create_string_buffer(buf_size)
    ok = CFStringGetCString(cf, buf, buf_size, kCFStringEncodingUTF8)
    if not ok:
        return None
    return buf.value.decode("utf-8")


def _get_string_property(ca: ctypes.CDLL, device_id: int, selector: int) -> str | None:
    """Read a CFString property from an AudioObject."""
    prop = AudioObjectPropertyAddress(
        selector,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain,
    )
    size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    cf_ref = ctypes.c_void_p()
    status = ca.AudioObjectGetPropertyData(
        ctypes.c_uint32(device_id),
        ctypes.byref(prop),
        ctypes.c_uint32(0),
        None,
        ctypes.byref(size),
        ctypes.byref(cf_ref),
    )
    if status != 0 or not cf_ref.value:
        return None
    try:
        return _cfstring_to_python(cf_ref)
    finally:
        CoreFoundation = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("CoreFoundation")  # type: ignore[arg-type]
        )
        CoreFoundation.CFRelease(cf_ref)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_device_uid_map() -> dict[str, str]:
    """Return ``{device_name: device_uid}`` for all CoreAudio devices.

    On failure (non-macOS, missing framework, etc.) returns an empty dict
    so callers can treat absence as "UID not available".
    """
    try:
        ca = _load_coreaudio()
    except OSError:
        logger.debug("CoreAudio not available — UIDs will be empty")
        return {}

    uid_map: dict[str, str] = {}
    try:
        device_ids = _get_all_device_ids(ca)
        for dev_id in device_ids:
            name = _get_string_property(ca, dev_id, kAudioObjectPropertyName)
            uid = _get_string_property(ca, dev_id, kAudioDevicePropertyDeviceUID)
            if name and uid:
                uid_map[name] = uid
    except Exception:
        logger.debug("CoreAudio UID enumeration failed", exc_info=True)

    return uid_map
