"""Windows present-device tree read directly from cfgmgr32 via ctypes.

Replaces the PowerShell/WMI enumeration as the data source for the Windows
USB audio topology (MOR-3556; timings in MOR-3526). Importable on every OS;
only :class:`_CfgMgr32` needs Windows.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Any, Protocol

# (instance_id, parent_instance_id, class name, device name); absent → "".
DevNodeRow = tuple[str, str, str, str]

_CR_SUCCESS = 0x00
_CR_NO_SUCH_DEVNODE = 0x0D
_CR_BUFFER_SMALL = 0x1A
_CR_NO_SUCH_VALUE = 0x25
_DEVPROP_TYPE_STRING = 0x12
_DEVPROP_TYPE_STRING_LIST = 0x2012


@dataclass(frozen=True, slots=True)
class DevNodeRecord:
    instance_id: str
    parent_id: str
    class_name: str
    friendly_name: str
    device_desc: str
    hardware_ids: tuple[str, ...]
    compatible_ids: tuple[str, ...]


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class DevPropKey(ctypes.Structure):
    _fields_ = [("fmtid", _Guid), ("pid", ctypes.c_uint32)]

    def __hash__(self) -> int:
        return hash(bytes(self))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DevPropKey) and bytes(self) == bytes(other)


def _key(data1: int, data2: int, data3: int, data4: bytes, pid: int) -> DevPropKey:
    return DevPropKey(_Guid(data1, data2, data3, (ctypes.c_ubyte * 8)(*data4)), pid)


# devpkey.h. On GTR7 Get-PnpDevice's FriendlyName matched FriendlyName, else
# DeviceDesc, for the nodes the resolver reads
# (test_real_cfgmgr32_matches_powershell_and_is_fast), and
# CM_Get_DevNode_PropertyW returned CR_NO_SUCH_VALUE for DEVPKEY_NAME.
_DEVPKEY_DEVICE_FMTID = (
    0xA45C254E,
    0xDF1C,
    0x4EFD,
    b"\x80\x20\x67\xd1\x46\xa8\x50\xe0",
)
DEVPKEY_DEVICE_DESC = _key(*_DEVPKEY_DEVICE_FMTID, 2)
DEVPKEY_DEVICE_HARDWARE_IDS = _key(*_DEVPKEY_DEVICE_FMTID, 3)
DEVPKEY_DEVICE_COMPATIBLE_IDS = _key(*_DEVPKEY_DEVICE_FMTID, 4)
DEVPKEY_DEVICE_CLASS = _key(*_DEVPKEY_DEVICE_FMTID, 9)
DEVPKEY_DEVICE_FRIENDLY_NAME = _key(*_DEVPKEY_DEVICE_FMTID, 14)
DEVPKEY_DEVICE_PARENT = _key(
    0x4340A6C5, 0x93FA, 0x4706, b"\x97\x2c\x7b\x64\x80\x08\xa5\xa7", 8
)


class DevNodeApi(Protocol):
    def root(self) -> int: ...
    def child(self, devnode: int) -> int | None: ...
    def sibling(self, devnode: int) -> int | None: ...
    def device_id(self, devnode: int) -> str: ...
    def string_property(self, devnode: int, key: DevPropKey) -> str | None: ...
    def string_list_property(
        self, devnode: int, key: DevPropKey
    ) -> tuple[str, ...]: ...


class _CfgMgr32:
    """The cfgmgr32 calls the walk needs. Raises ``OSError`` off Windows."""

    _dll: Any

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("cfgmgr32 is only available on Windows")
        dll = ctypes.WinDLL("cfgmgr32")
        u32, p_u32 = ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)
        signatures = {
            "CM_Locate_DevNodeW": [p_u32, ctypes.c_wchar_p, u32],
            "CM_Get_Child": [p_u32, u32, u32],
            "CM_Get_Sibling": [p_u32, u32, u32],
            "CM_Get_Device_ID_Size": [p_u32, u32, u32],
            "CM_Get_Device_IDW": [u32, ctypes.c_wchar_p, u32, u32],
            "CM_Get_DevNode_PropertyW": [
                u32,
                ctypes.POINTER(DevPropKey),
                p_u32,
                ctypes.c_void_p,
                p_u32,
                u32,
            ],
        }
        for name, argtypes in signatures.items():
            function = getattr(dll, name)
            function.argtypes = argtypes
            function.restype = u32
        self._dll = dll

    def _check(self, name: str, result: int) -> None:
        if result != _CR_SUCCESS:
            raise OSError(f"{name} failed: CONFIGRET 0x{result:02X}")

    def root(self) -> int:
        devnode = ctypes.c_uint32()
        self._check(
            "CM_Locate_DevNodeW",
            self._dll.CM_Locate_DevNodeW(ctypes.byref(devnode), None, 0),
        )
        return devnode.value

    def _relative(self, name: str, devnode: int) -> int | None:
        found = ctypes.c_uint32()
        result = getattr(self._dll, name)(ctypes.byref(found), devnode, 0)
        if result == _CR_NO_SUCH_DEVNODE:
            return None
        self._check(name, result)
        return found.value

    def child(self, devnode: int) -> int | None:
        return self._relative("CM_Get_Child", devnode)

    def sibling(self, devnode: int) -> int | None:
        return self._relative("CM_Get_Sibling", devnode)

    def device_id(self, devnode: int) -> str:
        size = ctypes.c_uint32()
        self._check(
            "CM_Get_Device_ID_Size",
            self._dll.CM_Get_Device_ID_Size(ctypes.byref(size), devnode, 0),
        )
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        self._check(
            "CM_Get_Device_IDW",
            self._dll.CM_Get_Device_IDW(devnode, buffer, size.value + 1, 0),
        )
        return buffer.value

    def _property_text(
        self, devnode: int, key: DevPropKey, expected_type: int
    ) -> str | None:
        prop_type = ctypes.c_uint32()
        size = ctypes.c_uint32(0)
        get = self._dll.CM_Get_DevNode_PropertyW
        result = get(
            devnode, ctypes.byref(key), ctypes.byref(prop_type), None,
            ctypes.byref(size), 0,
        )  # fmt: skip
        if result == _CR_NO_SUCH_VALUE:
            return None
        if result != _CR_BUFFER_SMALL:
            self._check("CM_Get_DevNode_PropertyW", result)
        buffer = ctypes.create_string_buffer(size.value)
        self._check(
            "CM_Get_DevNode_PropertyW",
            get(
                devnode, ctypes.byref(key), ctypes.byref(prop_type), buffer,
                ctypes.byref(size), 0,
            ),
        )  # fmt: skip
        if prop_type.value != expected_type:
            raise OSError(
                f"device property type 0x{prop_type.value:X}, expected 0x{expected_type:X}"
            )
        return buffer.raw[: size.value].decode("utf-16-le")

    def string_property(self, devnode: int, key: DevPropKey) -> str | None:
        text = self._property_text(devnode, key, _DEVPROP_TYPE_STRING)
        return text.split("\0", 1)[0] if text is not None else None

    def string_list_property(self, devnode: int, key: DevPropKey) -> tuple[str, ...]:
        text = self._property_text(devnode, key, _DEVPROP_TYPE_STRING_LIST)
        return tuple(value for value in text.split("\0") if value) if text else ()


def present_devnode_records(api: DevNodeApi | None = None) -> list[DevNodeRecord]:
    api = api if api is not None else _CfgMgr32()
    records: list[DevNodeRecord] = []
    stack = [api.root()]
    while stack:
        devnode = stack.pop()
        prop = api.string_property
        records.append(
            DevNodeRecord(
                instance_id=api.device_id(devnode),
                parent_id=prop(devnode, DEVPKEY_DEVICE_PARENT) or "",
                class_name=prop(devnode, DEVPKEY_DEVICE_CLASS) or "",
                friendly_name=prop(devnode, DEVPKEY_DEVICE_FRIENDLY_NAME) or "",
                device_desc=prop(devnode, DEVPKEY_DEVICE_DESC) or "",
                hardware_ids=api.string_list_property(
                    devnode, DEVPKEY_DEVICE_HARDWARE_IDS
                ),
                compatible_ids=api.string_list_property(
                    devnode, DEVPKEY_DEVICE_COMPATIBLE_IDS
                ),
            )
        )
        children: list[int] = []
        child = api.child(devnode)
        while child is not None:
            children.append(child)
            child = api.sibling(child)
        stack.extend(reversed(children))
    return records


def present_devnodes(api: DevNodeApi | None = None) -> list[DevNodeRow]:
    """Every present devnode, depth-first from the root of the device tree.

    The parent is ``DEVPKEY_Device_Parent``, the property PowerShell reads;
    on GTR7 it kept lower-case parts that ``CM_Get_Device_IDW`` upper-cases.
    Raises ``OSError`` when cfgmgr32 is unavailable or a call fails.
    """
    api = api if api is not None else _CfgMgr32()
    rows: list[DevNodeRow] = []
    stack = [api.root()]
    while stack:
        devnode = stack.pop()
        prop = api.string_property
        rows.append(
            (
                api.device_id(devnode),
                prop(devnode, DEVPKEY_DEVICE_PARENT) or "",
                prop(devnode, DEVPKEY_DEVICE_CLASS) or "",
                prop(devnode, DEVPKEY_DEVICE_FRIENDLY_NAME)
                or prop(devnode, DEVPKEY_DEVICE_DESC)
                or "",
            )
        )
        children: list[int] = []
        child = api.child(devnode)
        while child is not None:
            children.append(child)
            child = api.sibling(child)
        stack.extend(reversed(children))
    return rows
