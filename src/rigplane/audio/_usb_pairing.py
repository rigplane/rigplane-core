"""Shared, pure USB containment and endpoint-ownership pairing rule."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rigplane.audio._portaudio_enum import HostApiRecord, PortAudioDeviceRecord
from rigplane.audio._usb_names import endpoint_name_key, mme_listed_matches
from rigplane.audio._usb_snapshot import AudioEndpoint, Flow, UsbNode, UsbSnapshot
from rigplane.core.usb_identity import UsbAudioIdentity

RefusalReason = Literal[
    "unavailable",
    "serial_not_found",
    "topology_incomplete",
    "containment_unproven",
    "serial_ambiguous",
    "no_codec",
    "codec_ambiguous",
    "codec_identity_mismatch",
    "contested",
    "endpoint_names",
]


@dataclass(frozen=True, slots=True)
class AudioCandidate:
    stable_id: str
    display_name: str
    direction: Flow
    hostapi: str
    portaudio_index: int
    near_radio: bool


def candidate_stable_id(
    snapshot: UsbSnapshot, record: PortAudioDeviceRecord, api: HostApiRecord, flow: Flow
) -> str:
    matches = _matches(snapshot, record, api, flow)
    identity = matches[0].os_id if len(matches) == 1 else "name:" + record.name
    return api.name + "|" + identity


@dataclass(frozen=True, slots=True)
class PairOk:
    rx_index: int
    tx_index: int
    rx_id: str
    tx_id: str
    codec_node: str
    hostapi: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PairRefused:
    reason: RefusalReason
    wire_reason: Literal[
        "usb_topology_unavailable", "usb_ambiguous", "usb_topology_names"
    ]
    candidates: tuple[AudioCandidate, ...]
    evidence: tuple[str, ...]


class WindowsAudioTopologyError(RuntimeError):
    """A shared pairing refusal, including candidates for explicit selection."""

    def __init__(self, refusal: PairRefused) -> None:
        self.refusal = refusal
        self.reason = refusal.reason
        self.evidence = refusal.evidence
        self.candidates = refusal.candidates
        super().__init__(
            "USB audio parents do not match the serial radio or "
            "cannot establish a unique same-host-API pair "
            f"({refusal.reason}); select explicit RX/TX devices."
        )


class WindowsAudioAmbiguityError(WindowsAudioTopologyError):
    pass


class WindowsAudioEndpointNameError(WindowsAudioTopologyError):
    pass


def pairing_refusal_error(refusal: PairRefused) -> WindowsAudioTopologyError:
    if refusal.wire_reason == "usb_ambiguous":
        return WindowsAudioAmbiguityError(refusal)
    if refusal.wire_reason == "usb_topology_names":
        return WindowsAudioEndpointNameError(refusal)
    return WindowsAudioTopologyError(refusal)


def _matches(
    snapshot: UsbSnapshot, record: PortAudioDeviceRecord, api: HostApiRecord, flow: Flow
) -> tuple[AudioEndpoint, ...]:
    return tuple(
        endpoint
        for endpoint in snapshot.endpoints
        if endpoint.flow == flow
        and (
            mme_listed_matches(record.name, endpoint.name)
            if api.name == "MME"
            else endpoint_name_key(record.name) == endpoint_name_key(endpoint.name)
        )
    )


def _under_hub(owner: str, hub: UsbNode | None, nodes: dict[str, UsbNode]) -> bool:
    node = nodes.get(owner.casefold())
    if hub is None or node is None or node.kind != "device":
        return False
    seen = set()
    while node.parent is not None and node.id.casefold() not in seen:
        seen.add(node.id.casefold())
        if node.parent.casefold() == hub.id.casefold():
            return True
        parent = nodes.get(node.parent.casefold())
        if parent is None:
            return False
        node = parent
    return False


def _candidates(
    snapshot: UsbSnapshot, hub: UsbNode | None, nodes: dict[str, UsbNode]
) -> tuple[AudioCandidate, ...]:
    for api in snapshot.portaudio.hostapis:
        candidates = []
        for record in snapshot.portaudio.devices:
            if record.hostapi != api.index:
                continue
            directions: tuple[tuple[Flow, int], ...] = (
                ("capture", record.max_input_channels),
                ("render", record.max_output_channels),
            )
            for flow, channels in directions:
                if channels <= 0:
                    continue
                matches = _matches(snapshot, record, api, flow)
                if not matches:
                    continue
                candidates.append(
                    AudioCandidate(
                        candidate_stable_id(snapshot, record, api, flow),
                        record.name,
                        flow,
                        api.name,
                        record.index,
                        any(
                            _under_hub(endpoint.owner, hub, nodes)
                            for endpoint in matches
                        ),
                    )
                )
        if candidates:
            return tuple(
                sorted(
                    candidates,
                    key=lambda item: (not item.near_radio, item.portaudio_index),
                )
            )
    return ()


def _complete(nodes: dict[str, UsbNode]) -> bool:
    for node in nodes.values():
        seen = set()
        while True:
            key = node.id.casefold()
            if key in seen:
                return False
            seen.add(key)
            if node.parent is None:
                if node.kind not in {"controller", "root_hub", "other"}:
                    return False
                break
            parent = nodes.get(node.parent.casefold())
            if parent is None:
                return False
            node = parent
    return True


def pair(
    snapshot: UsbSnapshot, serial_port: str, identity: UsbAudioIdentity | None
) -> PairOk | PairRefused:
    """Pair within a non-root hub and at most one nested hub below it."""
    nodes = {node.id.casefold(): node for node in snapshot.usb}
    hub: UsbNode | None = None
    evidence: list[str] = []

    def refuse(reason: RefusalReason) -> PairRefused:
        wire: Literal["usb_topology_unavailable", "usb_ambiguous", "usb_topology_names"]
        if reason in {"serial_ambiguous", "codec_ambiguous", "contested"}:
            wire = "usb_ambiguous"
        elif reason == "endpoint_names":
            wire = "usb_topology_names"
        else:
            wire = "usb_topology_unavailable"
        return PairRefused(
            reason,
            wire,
            _candidates(snapshot, hub, nodes),
            (*evidence, "refused:" + reason),
        )

    if any(
        item.part
        in {"usb", "windows", "device_tree", "serial_ports", "portaudio", "endpoints"}
        for item in snapshot.unavailable
    ):  # rule: S0
        evidence.extend(item.part + ":" + item.reason for item in snapshot.unavailable)
        return refuse("unavailable")
    ports = [
        port
        for port in snapshot.serial_ports
        if port.port.casefold() == serial_port.casefold()
    ]
    if len(ports) != 1 or not ports[0].node:  # rule: S1
        return refuse("serial_not_found")
    serial = nodes.get(ports[0].node.casefold())
    if serial is None or len(nodes) != len(snapshot.usb):
        return refuse("topology_incomplete")
    evidence.append("serial:" + serial.id)
    hub = nodes.get(serial.parent.casefold()) if serial.parent else None
    if serial.kind != "device" or hub is None or hub.kind != "hub":  # rule: S2
        return refuse("containment_unproven")
    evidence.append("hub:" + hub.id)
    if not _complete(nodes):
        return refuse("topology_incomplete")
    children = [
        node
        for node in snapshot.usb
        if node.parent and node.parent.casefold() == hub.id.casefold()
    ]
    nested = {node.id.casefold() for node in children if node.kind == "hub"}
    boundary = children + [
        node
        for node in snapshot.usb
        if node.parent and node.parent.casefold() in nested
    ]
    devices = {node.id.casefold(): node for node in boundary if node.kind == "device"}
    boundary_ids = {node.id.casefold() for node in boundary}
    serial_devices = {
        port.node.casefold()
        for port in snapshot.serial_ports
        if port.node and port.node.casefold() in devices
    } | {
        id.casefold() for id in snapshot.serial_devices if id.casefold() in boundary_ids
    }
    if serial_devices != {serial.id.casefold()}:  # rule: S3
        if not identity or not identity.serial_functions:
            return refuse("serial_ambiguous")
        declared_functions = {
            (function.vid, function.pid) for function in identity.serial_functions
        }
        observed_functions = set()
        for device_id in serial_devices:
            device = devices.get(device_id)
            if (
                device is None
                or device.parent is None
                or device.parent.casefold() != hub.id.casefold()
                or (device.vid, device.pid) not in declared_functions
                or (device.vid, device.pid) in observed_functions
            ):
                return refuse("serial_ambiguous")
            observed_functions.add((device.vid, device.pid))
    codecs = [
        node
        for key, node in devices.items()
        if {
            endpoint.flow
            for endpoint in snapshot.endpoints
            if endpoint.owner.casefold() == key
        }
        == {"capture", "render"}
    ]
    if not codecs:
        return refuse("no_codec")
    if len(codecs) != 1:
        return refuse("codec_ambiguous")
    codec = codecs[0]
    evidence.append("codec:" + codec.id)
    if (
        identity
        and identity.audio_codec
        and not any(
            (function.vid, function.pid) == (codec.vid, codec.pid)
            for function in identity.audio_codec
        )
    ):
        return refuse("codec_identity_mismatch")

    ownership = {}
    for api in snapshot.portaudio.hostapis:
        for record in snapshot.portaudio.devices:
            if record.hostapi != api.index:
                continue
            capture = (
                _matches(snapshot, record, api, "capture")
                if record.max_input_channels > 0
                else ()
            )
            render = (
                _matches(snapshot, record, api, "render")
                if record.max_output_channels > 0
                else ()
            )
            owners = {
                endpoint.owner.casefold() for endpoint in (*capture, *render)
            }  # rule: S4
            ownership[(api.index, record.index)] = (capture, render, owners)

    contested = False
    for api in snapshot.portaudio.hostapis:
        inputs = []
        outputs = []
        api_contested = False
        for record in snapshot.portaudio.devices:
            if record.hostapi != api.index:
                continue
            capture, render, owners = ownership[(api.index, record.index)]
            if codec.id.casefold() not in owners:
                continue
            if len(owners) > 1:
                api_contested = True
                continue
            if capture:
                inputs.append((record, capture))
            if render:
                outputs.append((record, render))
        if api_contested:
            contested = True
            evidence.append("contested:" + api.name)
            continue
        if len(inputs) == len(outputs) == 1:  # rule: S5
            rx, capture = inputs[0]
            tx, render = outputs[0]
            return PairOk(
                rx.index,
                tx.index,
                min(endpoint.os_id for endpoint in capture),
                min(endpoint.os_id for endpoint in render),
                codec.id,
                api.name,
                (*evidence, "hostapi:" + api.name),
            )
    return refuse("contested" if contested else "endpoint_names")
