"""Windows capture adaptation, including the recorded W1 USB tree."""

import json
from pathlib import Path

import pytest

from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import PairOk, pair
from rigplane.audio._usb_snapshot import SerialPortRef


@pytest.fixture
def session():
    return json.loads(
        Path("tests/fixtures/usb_audio/windows/w1-gtr7-ic7300.json").read_text()
    )["sessions"][0]


def test_w1_hubs_serial_and_codec_endpoint_owners(session):
    snapshot = adapt_windows(session)
    hubs = [node for node in snapshot.usb if (node.vid, node.pid) == ("0451", "2046")]
    assert len(hubs) == 1 and hubs[0].kind == "hub"
    roots = [node for node in snapshot.usb if node.id.startswith("USB\\ROOT_HUB30")]
    assert roots and all(node.kind == "root_hub" for node in roots)
    serial = next(port for port in snapshot.serial_ports if port.port == "COM3")
    device = next(node for node in snapshot.usb if node.id == serial.node)
    assert (device.vid, device.pid, device.kind) == ("10C4", "EA60", "device")
    assert device.parent == hubs[0].id
    codec = next(
        node
        for node in snapshot.usb
        if node.kind == "device" and (node.vid, node.pid) == ("08BB", "2901")
    )
    assert codec.parent == hubs[0].id
    endpoints = [
        endpoint for endpoint in snapshot.endpoints if endpoint.owner == codec.id
    ]
    assert sorted(endpoint.flow for endpoint in endpoints) == ["capture", "render"]
    assert any(
        node.kind == "media" and node.parent == codec.id for node in snapshot.usb
    )
    virtual = [
        endpoint
        for endpoint in snapshot.endpoints
        if "Virtual Audio Cable" in endpoint.name
    ]
    assert virtual and all(endpoint.owner.startswith("opaque:") for endpoint in virtual)
    assert snapshot.portaudio.hostapis[0].name == "MME"
    assert snapshot.unavailable == ()


def test_parent_join_is_case_insensitive(session):
    for node in session["raw"]["windows"]["devnodes"]:
        node["parent_id"] = node["parent_id"].lower()
    snapshot = adapt_windows(session)
    serial = next(port for port in snapshot.serial_ports if port.port == "COM3")
    assert serial.node.startswith("USB\\VID_10C4&PID_EA60\\")
    codec = next(
        node for node in snapshot.usb if node.kind == "device" and node.vid == "08BB"
    )
    assert (
        len([endpoint for endpoint in snapshot.endpoints if endpoint.owner == codec.id])
        == 2
    )


@pytest.mark.parametrize("name", ["Radio RX (COM3)", "Radio RX (COM4)"])
def test_capture_endpoint_name_with_com_port_keeps_mme_pair(session, name):
    endpoint = next(
        row
        for row in session["raw"]["windows"]["devnodes"]
        if row["friendly_name"] == "Microphone (USB Audio CODEC )"
    )
    original_name = endpoint["friendly_name"]
    endpoint["friendly_name"] = name
    for record in session["raw"]["portaudio"]["devices"]:
        if record["name"] in {original_name, original_name.replace(" )", ")")}:
            record["name"] = name

    result = pair(adapt_windows(session), "COM3", None)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index, result.hostapi) == (1, 5, "MME")


@pytest.mark.parametrize("class_name", ["Ports", "ports", "PORTS", "pOrTs"])
def test_ports_class_preserves_com_reference(session, class_name):
    serial = next(
        row
        for row in session["raw"]["windows"]["devnodes"]
        if row["class_name"] == "Ports"
    )
    serial["class_name"] = class_name
    session["raw"]["serial_ports"] = []

    assert adapt_windows(session).serial_ports == (
        SerialPortRef("COM3", serial["instance_id"]),
    )


def test_root_hub_hardware_id_and_serial_interface_owner(session):
    rows = session["raw"]["windows"]["devnodes"]
    root = next(row for row in rows if row["instance_id"].startswith("USB\\ROOT_HUB30"))
    original = root["instance_id"]
    root["instance_id"] = "opaque:root"
    for row in rows:
        if row["parent_id"] == original:
            row["parent_id"] = root["instance_id"]
    serial = next(row for row in rows if "(COM3)" in row["friendly_name"])
    interface = {
        **serial,
        "instance_id": serial["instance_id"].replace("&PID_EA60", "&PID_EA60&MI_00"),
        "parent_id": serial["instance_id"],
    }
    serial["friendly_name"] = ""
    rows.append(interface)
    snapshot = adapt_windows(session)
    assert (
        next(node for node in snapshot.usb if node.id == "opaque:root").kind
        == "root_hub"
    )
    assert (
        next(node for node in snapshot.usb if node.id == interface["instance_id"]).kind
        == "interface"
    )
    assert (
        next(port for port in snapshot.serial_ports if port.port == "COM3").node
        == serial["instance_id"]
    )


def test_media_class_on_usb_device_still_owns_its_endpoints(session):
    row = next(
        row
        for row in session["raw"]["windows"]["devnodes"]
        if "VID_08BB&PID_2901\\" in row["instance_id"]
    )
    row["class_name"] = "MEDIA"
    snapshot = adapt_windows(session)
    assert (
        next(node for node in snapshot.usb if node.id == row["instance_id"]).kind
        == "device"
    )
    assert (
        len(
            [
                endpoint
                for endpoint in snapshot.endpoints
                if endpoint.owner == row["instance_id"]
            ]
        )
        == 2
    )


@pytest.mark.parametrize("part", ["windows", "serial_ports", "portaudio"])
def test_missing_capture_part_is_unavailable(session, part):
    del session["raw"][part]
    snapshot = adapt_windows(session)
    assert any(item.part == part for item in snapshot.unavailable)


@pytest.mark.parametrize(
    "compatible", ["USB\\USB20_HUB", "usb\\usb30_hub", "USB\\Class_09&SubClass_00"]
)
def test_hub_classification_uses_compatible_ids(session, compatible):
    row = next(
        node
        for node in session["raw"]["windows"]["devnodes"]
        if "VID_0451" in node["instance_id"]
    )
    row["compatible_ids"] = [compatible]
    assert (
        next(
            node for node in adapt_windows(session).usb if node.id == row["instance_id"]
        ).kind
        == "hub"
    )
