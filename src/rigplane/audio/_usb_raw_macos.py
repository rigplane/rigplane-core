"""Read-only IORegistry and CoreAudio records for USB captures."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import json
import math
import plistlib
import struct
import subprocess
import sys
import time
from typing import Any

from rigplane.audio._macos_uid import (
    AudioObjectPropertyAddress,
    _get_string_property,
    _load_coreaudio,
)


@dataclass(frozen=True, slots=True)
class RawMacos:
    usb_devices: tuple[dict[str, Any], ...] = ()
    serial_devices: tuple[dict[str, Any], ...] = ()
    audio_devices: tuple[dict[str, Any], ...] = ()
    unavailable: tuple[dict[str, str], ...] = ()

    def to_json_dict(self) -> dict[str, Any]:
        return {
            name: list(getattr(self, name))
            for name in (
                "usb_devices",
                "serial_devices",
                "audio_devices",
                "unavailable",
            )
        }


class _AudioBuffer(ctypes.Structure):
    _fields_ = [
        ("mNumberChannels", ctypes.c_uint32),
        ("mDataByteSize", ctypes.c_uint32),
        ("mData", ctypes.c_void_p),
    ]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", _AudioBuffer * 1)]


def _property(ca: Any, obj: int, selector: str, scope: str = "glob") -> bytes:
    address = AudioObjectPropertyAddress(
        int.from_bytes(selector.encode(), "big"),
        int.from_bytes(scope.encode(), "big"),
        0,
    )
    size = ctypes.c_uint32()
    args = (
        ctypes.c_uint32(obj),
        ctypes.byref(address),
        ctypes.c_uint32(0),
        None,
        ctypes.byref(size),
    )
    status = ca.AudioObjectGetPropertyDataSize(*args)
    if status:
        raise OSError(f"CoreAudio property size status {status}")
    buffer = ctypes.create_string_buffer(size.value)
    capacity = size.value
    status = ca.AudioObjectGetPropertyData(*args, buffer)
    if status or size.value > capacity:
        raise OSError(f"CoreAudio property data status {status}")
    return buffer.raw[: size.value]


def _channels(ca: Any, obj: int, scope: str) -> int:
    data = _property(ca, obj, "slay", scope)
    count = struct.unpack_from("=I", data)[0]
    offset, stride = _AudioBufferList.mBuffers.offset, ctypes.sizeof(_AudioBuffer)
    if count and len(data) < offset + count * stride:
        raise ValueError("truncated CoreAudio buffer list")
    return sum(
        _AudioBuffer.from_buffer_copy(data, offset + i * stride).mNumberChannels
        for i in range(count)
    )


def _read_coreaudio() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    ca = _load_coreaudio()
    devices, unavailable = [], []
    for (obj,) in struct.iter_unpack("=I", _property(ca, 1, "dev#")):
        row: dict[str, Any] = {"device_id": obj}
        for field, read in (
            ("uid", lambda: _get_string_property(ca, obj, 0x75696420)),
            ("name", lambda: _get_string_property(ca, obj, 0x6C6E616D)),
            (
                "transport_type",
                lambda: struct.unpack("=I", _property(ca, obj, "tran"))[0],
            ),
            ("input_channels", lambda: _channels(ca, obj, "inpt")),
            ("output_channels", lambda: _channels(ca, obj, "outp")),
        ):
            try:
                value = read()
                if value is None:
                    raise OSError("CoreAudio property unavailable")
                row[field] = value
            except Exception as exc:
                row[field] = None
                unavailable.append(
                    {
                        "part": f"macos:coreaudio:{obj}:{field}",
                        "reason": type(exc).__name__,
                    }
                )
        devices.append(row)
    return devices, unavailable


def _registry(data: bytes, *, serial: bool) -> list[dict[str, Any]]:
    roots = plistlib.loads(data)
    rows = []

    def walk(node: dict[str, Any], path: str, usb_id: int | None) -> None:
        registry_id = node.get("IORegistryEntryID")
        segment = node.get("IORegistryEntryName", "")
        parent_path = path
        location = node.get("IORegistryEntryLocation")
        if segment != "Root" or not path.endswith(":"):
            path += f"/{segment}" + (f"@{location}" if location is not None else "")
        if "idVendor" in node and "idProduct" in node and "bDeviceClass" in node:
            if not serial:
                rows.append(
                    {
                        "registry_id": registry_id,
                        "registry_path": path,
                        "parent_path": parent_path,
                        "parent_registry_id": usb_id,
                        "location_id": node.get("locationID"),
                        "vendor_id": node.get("idVendor"),
                        "product_id": node.get("idProduct"),
                        "product": node.get("USB Product Name"),
                        "manufacturer": node.get("USB Vendor Name"),
                        "serial_number": node.get("USB Serial Number"),
                        "device_class": node.get("bDeviceClass"),
                        "is_hub": node.get("bDeviceClass") == 9,
                    }
                )
            usb_id = registry_id
        if serial and node.get("IOObjectClass") == "IOSerialBSDClient":
            for key in ("IOCalloutDevice", "IODialinDevice"):
                if device := node.get(key):
                    rows.append(
                        {
                            "device": device,
                            "registry_path": path,
                            "usb_registry_id": usb_id,
                        }
                    )
        for child in node.get("IORegistryEntryChildren", []):
            walk(child, path, usb_id)

    for root in [roots] if isinstance(roots, dict) else roots:
        walk(root, "IOService:" if serial else "IOUSB:", None)
    json.dumps(rows, allow_nan=False)
    return rows


def read_raw_macos(timeout_s: float) -> RawMacos:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        return RawMacos(unavailable=({"part": "macos", "reason": "invalid timeout"},))
    deadline = time.monotonic() + timeout_s
    results: dict[str, Any] = {}
    unavailable = []
    for part, command in (
        ("usb", ["/usr/sbin/ioreg", "-a", "-l", "-p", "IOUSB"]),
        ("serial", ["/usr/sbin/ioreg", "-a", "-l", "-p", "IOService"]),
        (
            "coreaudio",
            [
                sys.executable,
                "-c",
                "import json; "
                "from rigplane.audio._usb_raw_macos import _read_coreaudio; "
                "print(json.dumps(_read_coreaudio()))",
            ],
        ),
    ):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("capture deadline expired")
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=remaining,
                stdin=subprocess.DEVNULL,
            )
            result.check_returncode()
            if part == "coreaudio":
                devices, failures = json.loads(result.stdout)
                results[part] = devices
                unavailable.extend(failures)
            else:
                results[part] = _registry(result.stdout, serial=part == "serial")
        except Exception as exc:
            unavailable.append({"part": f"macos:{part}", "reason": type(exc).__name__})
    return RawMacos(
        tuple(results.get("usb", [])),
        tuple(results.get("serial", [])),
        tuple(results.get("coreaudio", [])),
        tuple(unavailable),
    )
