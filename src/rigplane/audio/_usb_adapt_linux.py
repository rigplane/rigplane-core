"""Normalize sysfs, ALSA cards and PortAudio for shared USB pairing."""

from __future__ import annotations

from rigplane.audio._portaudio_enum import PortAudioEnumeration
from rigplane.audio._usb_names import endpoint_name_key
from rigplane.audio._usb_raw_linux import RawLinux
from rigplane.audio._usb_snapshot import (
    AudioEndpoint,
    Flow,
    SerialPortRef,
    Unavailable,
    UsbNode,
    UsbSnapshot,
)


def adapt_linux(raw: RawLinux, portaudio: PortAudioEnumeration) -> UsbSnapshot:
    nodes = tuple(
        UsbNode(
            row["path"],
            row["parent"],
            "root_hub"
            if row["parent"] is None and row["devpath"] == "0" and row["is_hub"]
            else "hub"
            if row["is_hub"]
            else "device",
            row["vendor_id"].upper() if row["vendor_id"] else None,
            row["product_id"].upper() if row["product_id"] else None,
        )
        for row in raw.usb_devices
    )
    serial_ports = tuple(
        SerialPortRef(row["device"], row["usb_device"]) for row in raw.serial_devices
    )
    unavailable = []
    if not portaudio.fresh:
        unavailable.append(Unavailable("portaudio", "enumeration unavailable"))
    for failure in raw.unavailable:
        part = failure["part"]
        normalized = (
            "serial_ports"
            if part == "linux:tty"
            else "endpoints"
            if part in {"linux:sound", "linux:asound"}
            else "usb"
        )
        unavailable.append(Unavailable(normalized, part + ":" + failure["reason"]))
    endpoints = []
    for row in raw.audio_devices:
        name = row["name"]
        if not name:
            unavailable.append(Unavailable("endpoints", "ALSA card name missing"))
            continue
        card = row["device"].removeprefix("card")
        name = f"{name} (hw:{card},0)"
        os_id = (row["usb_device"] or row["path"]) + ":pcm0"
        owner = row["usb_device"] or "opaque:" + os_id
        records = [
            record
            for record in portaudio.devices
            if endpoint_name_key(record.name) == endpoint_name_key(name)
        ]
        directions: tuple[tuple[Flow, str], ...] = (
            ("capture", "max_input_channels"),
            ("render", "max_output_channels"),
        )
        for flow, field in directions:
            if any(getattr(record, field) > 0 for record in records):
                endpoints.append(AudioEndpoint(os_id, owner, flow, name))
    return UsbSnapshot(
        "linux",
        nodes,
        serial_ports,
        tuple(endpoints),
        portaudio,
        tuple(unavailable),
        serial_devices=tuple(sorted({port.node for port in serial_ports if port.node})),
    )
