"""Windows audio sessions of a capture endpoint, through ctypes vtable calls (MOR-4030).

``IAudioSessionManager2`` lists the sessions of an endpoint, one per program
using it, whether it holds the device exclusively or shares it (verified on a
real Windows host). The calls are small and raw because Core has no COM
dependency: ``capture_sessions`` walks MMDeviceEnumerator -> capture endpoint
whose friendly name matches -> IAudioSessionManager2 ->
IAudioSessionEnumerator -> IAudioSessionControl2 and reads the process id, the
state and the system-sounds flag of each session. ``process_image_name`` maps a
process id to its image path. Both raise OSError off Windows; the caller
(``_usb_holder``) bounds them and swallows every error.

Vtable slots (IUnknown takes 0-2): IMMDeviceEnumerator 3 EnumAudioEndpoints;
IMMDeviceCollection 3 GetCount, 4 Item; IMMDevice 3 Activate, 4
OpenPropertyStore; IPropertyStore 5 GetValue; IAudioSessionManager2 5
GetSessionEnumerator; IAudioSessionEnumerator 3 GetCount, 4 GetSession;
IAudioSessionControl 3 GetState; IAudioSessionControl2 14 GetProcessId, 15
IsSystemSoundsSession.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import POINTER, byref, c_int, c_int32, c_long, c_ubyte, c_uint
from ctypes import c_uint16, c_uint32, c_ulong, c_ushort, c_void_p
from typing import Any

_CLSCTX_ALL = 23
_COINIT_MULTITHREADED = 0
_RPC_E_CHANGED_MODE = 0x80010106
_E_CAPTURE = 1  # EDataFlow eCapture
_DEVICE_STATE_ACTIVE = 1
_STGM_READ = 0
_VT_LPWSTR = 31
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", c_uint32),
        ("Data2", c_uint16),
        ("Data3", c_uint16),
        ("Data4", c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, text: str) -> _GUID:
        value = uuid.UUID(text)
        return cls(
            value.time_low,
            value.time_mid,
            value.time_hi_version,
            (c_ubyte * 8)(*value.bytes[8:]),
        )


class _PropertyKey(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", c_uint32)]


class _PropVariant(ctypes.Structure):
    """PROPVARIANT: 8 header bytes and a two-pointer payload (24 bytes on x64)."""

    _fields_ = [
        ("vt", c_ushort),
        ("reserved1", c_ushort),
        ("reserved2", c_ushort),
        ("reserved3", c_ushort),
        ("data", c_void_p * 2),
    ]


_CLSID_MM_DEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_MM_DEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_AUDIO_SESSION_MANAGER2 = "77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"
_IID_AUDIO_SESSION_CONTROL2 = "BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D"
_PKEY_DEVICE_FRIENDLY_NAME = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)


class _Interface:
    """One COM interface pointer; calls go straight through its vtable."""

    def __init__(self, pointer: int | None) -> None:
        if not pointer:
            raise OSError("COM returned no interface")
        self.pointer = c_void_p(pointer)

    def call(
        self, slot: int, argtypes: tuple[Any, ...] = (), *args: Any, check: bool = True
    ) -> int:
        vtable = ctypes.cast(self.pointer, POINTER(c_void_p))[0]
        address = ctypes.cast(vtable, POINTER(c_void_p))[slot]
        function = ctypes.WINFUNCTYPE(c_long, c_void_p, *argtypes)(address)  # type: ignore[attr-defined]
        result = int(function(self.pointer, *args))
        if check and result < 0:
            raise OSError(f"COM call failed: 0x{result & 0xFFFFFFFF:08x}")
        return result

    def child(self, slot: int, argtypes: tuple[Any, ...], *args: Any) -> _Interface:
        """Call a method whose LAST argument is an interface out-pointer."""
        out = c_void_p()
        self.call(slot, (*argtypes, POINTER(c_void_p)), *args, byref(out))
        return _Interface(out.value)

    def release(self) -> None:
        try:
            self.call(2, check=False)
        except Exception:
            pass


def _friendly_name(device: _Interface) -> str | None:
    store = device.child(4, (c_ulong,), _STGM_READ)
    try:
        fmtid, pid = _PKEY_DEVICE_FRIENDLY_NAME
        key = _PropertyKey(_GUID.parse(fmtid), pid)
        value = _PropVariant()
        store.call(
            5, (POINTER(_PropertyKey), POINTER(_PropVariant)), byref(key), byref(value)
        )
        try:
            if value.vt == _VT_LPWSTR and value.data[0]:
                return ctypes.wstring_at(value.data[0])
            return None
        finally:
            ctypes.WinDLL("ole32").PropVariantClear(byref(value))  # type: ignore[attr-defined]
    finally:
        store.release()


def _same_name(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _endpoint_sessions(device: _Interface) -> list[tuple[int, int, bool]]:
    manager_id = _GUID.parse(_IID_AUDIO_SESSION_MANAGER2)
    manager = device.child(
        3, (POINTER(_GUID), c_ulong, c_void_p), byref(manager_id), _CLSCTX_ALL, None
    )
    sessions: list[tuple[int, int, bool]] = []
    try:
        enumerator = manager.child(5, ())
        try:
            count = c_int()
            enumerator.call(3, (POINTER(c_int),), byref(count))
            control2_id = _GUID.parse(_IID_AUDIO_SESSION_CONTROL2)
            for index in range(count.value):
                control = enumerator.child(4, (c_int,), index)
                try:
                    control2 = control.child(0, (POINTER(_GUID),), byref(control2_id))
                    try:
                        state = c_int()
                        control.call(3, (POINTER(c_int),), byref(state))
                        pid = c_ulong()
                        control2.call(14, (POINTER(c_ulong),), byref(pid))
                        # S_OK (0) means "this is the system-sounds session".
                        system = control2.call(15, (), check=False) == 0
                        sessions.append((int(pid.value), int(state.value), system))
                    finally:
                        control2.release()
                finally:
                    control.release()
        finally:
            enumerator.release()
    finally:
        manager.release()
    return sessions


def _enumerate(device_name: str) -> list[tuple[int, int, bool]] | None:
    clsid = _GUID.parse(_CLSID_MM_DEVICE_ENUMERATOR)
    iid = _GUID.parse(_IID_MM_DEVICE_ENUMERATOR)
    ole32 = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
    out = c_void_p()
    result = int(
        ole32.CoCreateInstance(byref(clsid), None, _CLSCTX_ALL, byref(iid), byref(out))
    )
    if result < 0:
        raise OSError(f"CoCreateInstance failed: 0x{result & 0xFFFFFFFF:08x}")
    enumerator = _Interface(out.value)
    found = False
    sessions: list[tuple[int, int, bool]] = []
    try:
        collection = enumerator.child(
            3, (c_int, c_ulong), _E_CAPTURE, _DEVICE_STATE_ACTIVE
        )
        try:
            count = c_uint()
            collection.call(3, (POINTER(c_uint),), byref(count))
            for index in range(count.value):
                device = collection.child(4, (c_uint,), index)
                try:
                    name = _friendly_name(device)
                    if name is not None and _same_name(name, device_name):
                        found = True
                        sessions.extend(_endpoint_sessions(device))
                finally:
                    device.release()
        finally:
            collection.release()
    finally:
        enumerator.release()
    return sessions if found else None


def capture_sessions(device_name: str) -> list[tuple[int, int, bool]] | None:
    """``(process id, state, is system sounds)`` per session of the capture endpoint
    named *device_name*, or ``None`` when no active capture endpoint has that name.

    State is the AudioSessionState value: 0 inactive, 1 active, 2 expired.
    """
    if sys.platform != "win32":
        raise OSError("Windows audio sessions are only available on Windows")
    ole32 = ctypes.WinDLL("ole32")  # type: ignore[attr-defined]
    ole32.CoInitializeEx.argtypes = [c_void_p, c_uint32]
    ole32.CoInitializeEx.restype = c_int32
    hresult = int(ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)) & 0xFFFFFFFF
    owned = hresult in (0, 1)  # S_OK, S_FALSE: this scope owns one count
    if not owned and hresult != _RPC_E_CHANGED_MODE:
        raise OSError(f"COM initialization failed: 0x{hresult:08x}")
    try:
        return _enumerate(device_name)
    finally:
        if owned:
            ole32.CoUninitialize()


def process_image_name(pid: int) -> str | None:
    """The image path of process *pid* (the caller keeps only the file name)."""
    if sys.platform != "win32":
        raise OSError("process images are only read on Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = c_void_p
    kernel32.OpenProcess.argtypes = [c_ulong, c_int, c_ulong]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = c_ulong(len(buffer))
        kernel32.QueryFullProcessImageNameW.argtypes = [
            c_void_p,
            c_ulong,
            ctypes.c_wchar_p,
            POINTER(c_ulong),
        ]
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle.argtypes = [c_void_p]
        kernel32.CloseHandle(handle)
