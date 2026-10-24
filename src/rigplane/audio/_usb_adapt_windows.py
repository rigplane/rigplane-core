"""Pure adaptation of one Windows USB capture session."""

from __future__ import annotations

import re
from typing import Any

from rigplane.audio._portaudio_enum import PortAudioEnumeration
from rigplane.audio._usb_raw_windows import RawWindows
from rigplane.audio._usb_snapshot import (
    AudioEndpoint,
    Flow,
    NodeKind,
    SerialPortRef,
    Unavailable,
    UsbNode,
    UsbSnapshot,
)
from rigplane.audio._windows_devnode import DevNodeRecord


def _kind(row: DevNodeRecord) -> NodeKind:
    instance = row.instance_id.upper()
    hardware = tuple(value.upper() for value in row.hardware_ids)
    compatible = tuple(value.upper() for value in row.compatible_ids)
    if any(value.startswith("USB\\ROOT_HUB") for value in (instance, *hardware)):
        return "root_hub"
    if any(
        value in {"USB\\USB20_HUB", "USB\\USB30_HUB"}
        or value.startswith("USB\\CLASS_09")
        for value in compatible
    ):
        return "hub"
    if instance.startswith("SWD\\MMDEVAPI\\{0.0."):
        return "endpoint"
    function = re.match(r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}", instance)
    interface = function is not None and "&MI_" in instance.split("\\")[1]
    if function and not interface:
        return "device"
    if row.class_name.upper() == "MEDIA":
        return "media"
    if interface:
        return "interface"
    if any(re.search(r"&CC_0C03", value) for value in (*hardware, *compatible)):
        return "controller"
    return "other"


def adapt_windows(session: dict[str, Any]) -> UsbSnapshot:
    """Normalize captured rows without querying Windows or PortAudio."""
    raw = session.get("raw", {})
    unavailable = [Unavailable(**item) for item in session.get("unavailable", [])]
    for part in ("windows", "serial_ports", "portaudio"):
        if part not in raw and not any(item.part == part for item in unavailable):
            unavailable.append(Unavailable(part, "capture part missing"))
    windows = (
        RawWindows.from_json_dict(raw["windows"])
        if "windows" in raw
        else RawWindows("cfgmgr32", ())
    )
    rows = {row.instance_id.casefold(): row for row in windows.devnodes}

    def canonical(id: str) -> str:
        row = rows.get(id.casefold())
        return row.instance_id if row else id

    nodes = []
    for row in windows.devnodes:
        function = re.match(
            r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", row.instance_id.upper()
        )
        nodes.append(
            UsbNode(
                row.instance_id,
                canonical(row.parent_id) if row.parent_id else None,
                _kind(row),
                function[1] if function else None,
                function[2] if function else None,
            )
        )
    by_id = {node.id.casefold(): node for node in nodes}

    def owner(id: str) -> str | None:
        seen = set()
        while id.casefold() not in seen:
            seen.add(id.casefold())
            node = by_id.get(id.casefold())
            if node is None:
                return None
            if node.kind == "device":
                return node.id
            if node.parent is None:
                return None
            id = node.parent
        return None

    serial_ports = []
    endpoints = []
    for row in windows.devnodes:
        port = re.search(r"\((COM\d+)\)", row.friendly_name, re.IGNORECASE)
        if port:
            serial_ports.append(SerialPortRef(port[1].upper(), owner(row.instance_id)))
        flow: Flow
        if row.instance_id.upper().startswith("SWD\\MMDEVAPI\\{0.0.1."):
            flow = "capture"
        elif row.instance_id.upper().startswith("SWD\\MMDEVAPI\\{0.0.0."):
            flow = "render"
        else:
            continue
        endpoint_owner = owner(row.instance_id)
        if endpoint_owner is None:
            endpoint_owner = (
                canonical(row.parent_id)
                if row.parent_id
                else "opaque:" + row.instance_id
            )
        endpoints.append(
            AudioEndpoint(
                row.instance_id,
                endpoint_owner,
                flow,
                row.friendly_name or row.device_desc,
            )
        )
    for port in raw.get("serial_ports", []):
        name = port.get("device")
        if isinstance(name, str) and not any(
            ref.port.casefold() == name.casefold() for ref in serial_ports
        ):
            serial_ports.append(SerialPortRef(name.upper(), None))
    enumeration = (
        PortAudioEnumeration.from_json_dict(raw["portaudio"])
        if "portaudio" in raw
        else PortAudioEnumeration((), (), 0, False)
    )
    return UsbSnapshot(
        "windows",
        tuple(nodes),
        tuple(serial_ports),
        tuple(endpoints),
        enumeration,
        tuple(unavailable),
    )
