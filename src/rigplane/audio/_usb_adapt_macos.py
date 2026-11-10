"""Normalize IORegistry and CoreAudio records for shared USB pairing."""

from __future__ import annotations

import re
from collections.abc import Sequence

from rigplane.audio._portaudio_enum import PortAudioEnumeration
from rigplane.audio._usb_names import endpoint_name_key
from rigplane.audio._usb_raw_macos import CoreAudioDeviceRow, RawMacos
from rigplane.audio._usb_snapshot import (
    AudioEndpoint,
    SerialPortRef,
    Unavailable,
    UsbNode,
    UsbSnapshot,
)


def _portaudio_binding(
    rows: Sequence[CoreAudioDeviceRow], portaudio: PortAudioEnumeration
) -> dict[str, int]:
    """Bind CoreAudio devices to PortAudio records by enumeration order.

    PortAudio caches its device list at initialization and is never
    re-initialized, while this snapshot reads CoreAudio fresh; after a
    hot-plug the two lists disagree. So nothing binds at all unless the
    WHOLE ordered sequence of ``(endpoint_name_key(name),
    input_channels, output_channels)`` of the CoreAudio devices (rows with
    uid and name) equals the ordered sequence of the PortAudio records on
    the "Core Audio" host API. With the sequences equal, both lists walked
    the same ``kAudioHardwarePropertyDevices`` order, and the k-th
    CoreAudio device is the k-th PortAudio record — per name, the k-th to
    the k-th. Keys are CoreAudio UIDs, the endpoint identity.
    """
    api_index = next(
        (api.index for api in portaudio.hostapis if api.name == "Core Audio"),
        None,
    )
    if api_index is None:
        return {}
    devices: list[tuple[str, tuple[str, int | None, int | None]]] = []
    for row in rows:
        uid, name = row["uid"], row["name"]
        if not uid or name is None:
            continue
        devices.append(
            (
                uid,
                (
                    endpoint_name_key(name),
                    row["input_channels"],
                    row["output_channels"],
                ),
            )
        )
    records = sorted(
        (record for record in portaudio.devices if record.hostapi == api_index),
        key=lambda record: record.index,
    )
    if [shape for _, shape in devices] != [
        (
            endpoint_name_key(record.name),
            record.max_input_channels,
            record.max_output_channels,
        )
        for record in records
    ]:
        return {}
    return {
        uid: record.index for (uid, _), record in zip(devices, records, strict=True)
    }


def adapt_macos(raw: RawMacos, portaudio: PortAudioEnumeration) -> UsbSnapshot:
    """Link UID locations or serials with corroborating USB names."""
    nodes = []
    roots = {}
    for row in raw.usb_devices:
        id = str(row["registry_id"])
        parent = row["parent_registry_id"]
        if parent is None:
            path = row["parent_path"]
            parent_id = "root:" + path
            roots[parent_id] = UsbNode(parent_id, None, "root_hub", None, None)
        else:
            parent_id = str(parent)
        nodes.append(
            UsbNode(
                id,
                parent_id,
                "hub" if row["is_hub"] else "device",
                f"{row['vendor_id']:04X}" if row["vendor_id"] is not None else None,
                f"{row['product_id']:04X}" if row["product_id"] is not None else None,
            )
        )
    serial_ports = tuple(
        SerialPortRef(
            row["device"],
            str(row["usb_registry_id"]) if row["usb_registry_id"] is not None else None,
        )
        for row in raw.serial_devices
    )
    unavailable = []
    if not portaudio.fresh:
        unavailable.append(Unavailable("portaudio", "enumeration unavailable"))
    for failure in raw.unavailable:
        part = failure["part"]
        normalized = (
            "endpoints"
            if part.startswith("macos:coreaudio")
            else "serial_ports"
            if part == "macos:serial"
            else "usb"
        )
        unavailable.append(Unavailable(normalized, part + ":" + failure["reason"]))
    endpoints = []
    binding = _portaudio_binding(raw.audio_devices, portaudio)
    for device_row in raw.audio_devices:
        uid = device_row["uid"]
        name = device_row["name"]
        if not uid or name is None:
            unavailable.append(Unavailable("endpoints", "CoreAudio identity missing"))
            continue
        uid_match = re.fullmatch(
            r"AppleUSBAudioEngine:([^:]+):([^:]+):([^:]+):[^:]+", uid
        )
        owners = set()
        if uid_match:
            manufacturer, product, identity = uid_match.groups()
            location = (
                int(identity, 16)
                if re.fullmatch(r"[0-9a-fA-F]{1,8}", identity)
                else None
            )
            owners = {
                str(device["registry_id"])
                for device in raw.usb_devices
                if (
                    (location is not None and location == device["location_id"])
                    or identity == device["serial_number"]
                )
                and endpoint_name_key(manufacturer)
                == endpoint_name_key(device["manufacturer"] or "")
                and endpoint_name_key(product)
                == endpoint_name_key(device["product"] or "")
            }
        owner = next(iter(owners)) if len(owners) == 1 else "opaque:" + uid
        for flow, channels in (
            ("capture", device_row["input_channels"]),
            ("render", device_row["output_channels"]),
        ):
            if channels is None:
                unavailable.append(
                    Unavailable("endpoints", "CoreAudio channels missing")
                )
            elif channels > 0:
                endpoints.append(
                    AudioEndpoint(uid, owner, flow, name, binding.get(uid))
                )
    return UsbSnapshot(
        "macos",
        (*roots.values(), *nodes),
        serial_ports,
        tuple(endpoints),
        portaudio,
        tuple(unavailable),
        serial_devices=tuple(sorted({port.node for port in serial_ports if port.node})),
    )
