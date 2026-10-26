"""Normalize IORegistry and CoreAudio records for shared USB pairing."""

from __future__ import annotations

import re

from rigplane.audio._portaudio_enum import PortAudioEnumeration
from rigplane.audio._usb_raw_macos import RawMacos
from rigplane.audio._usb_snapshot import (
    AudioEndpoint,
    Flow,
    SerialPortRef,
    Unavailable,
    UsbNode,
    UsbSnapshot,
)


def adapt_macos(raw: RawMacos, portaudio: PortAudioEnumeration) -> UsbSnapshot:
    """Link USB audio UIDs by exact locationID; retain unlinked endpoints."""
    nodes = []
    roots = {}
    locations: dict[int, list[str]] = {}
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
        location = row["location_id"]
        if location:
            locations.setdefault(location, []).append(id)
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
    for row in raw.audio_devices:
        uid = row["uid"]
        name = row["name"]
        if not uid or name is None:
            unavailable.append(Unavailable("endpoints", "CoreAudio identity missing"))
            continue
        location_match = (
            re.search(r":([0-9a-fA-F]{1,8}):[^:]+$", uid)
            if uid.startswith("AppleUSBAudioEngine:")
            else None
        )
        owners = locations.get(int(location_match[1], 16), []) if location_match else []
        owner = owners[0] if len(owners) == 1 else "opaque:" + uid
        directions: tuple[tuple[Flow, str], ...] = (
            ("capture", "input_channels"),
            ("render", "output_channels"),
        )
        for flow, field in directions:
            channels = row[field]
            if channels is None:
                unavailable.append(
                    Unavailable("endpoints", "CoreAudio channels missing")
                )
            elif channels > 0:
                endpoints.append(AudioEndpoint(uid, owner, flow, name))
    return UsbSnapshot(
        "macos",
        (*roots.values(), *nodes),
        serial_ports,
        tuple(endpoints),
        portaudio,
        tuple(unavailable),
        serial_devices=tuple(sorted({port.node for port in serial_ports if port.node})),
    )
