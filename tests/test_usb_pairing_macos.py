"""Replay macOS topology through the adapter, resolver and driver."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import plistlib
import struct
import subprocess
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane.audio._portaudio_enum import PortAudioEnumeration
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.audio._usb_raw_macos import RawMacos
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.profiles import get_radio_profile


MAC_RX_ID = (
    "Core Audio|AppleUSBAudioEngine:Burr-Brown from TI:USB Audio CODEC:3121400:2"
)
MAC_TX_ID = (
    "Core Audio|AppleUSBAudioEngine:Burr-Brown from TI:USB Audio CODEC:3121400:1"
)
MAC_PORT = "/dev/cu.usbmodem3121201"
FTX_PORT = "/dev/cu.usbserial-RVDGLVMGQIF0"


def _macos_session():
    return json.loads(
        Path("tests/fixtures/usb_audio/macos/m1-mini-ic705-ftx1.json").read_text()
    )["sessions"][0]


def _raw_macos(session):
    return RawMacos(
        **{key: tuple(value) for key, value in session["raw"]["macos"].items()}
    )


def _adapt(session):
    from rigplane.audio._usb_adapt_macos import adapt_macos

    return adapt_macos(
        _raw_macos(session),
        PortAudioEnumeration.from_json_dict(session["raw"]["portaudio"]),
    )


def _inject_macos(monkeypatch, session, *, real_reader=False):
    enumeration = session["raw"]["portaudio"]
    sd = SimpleNamespace(
        query_devices=lambda: enumeration["devices"],
        query_hostapis=lambda: [
            {
                "name": api["name"],
                "devices": [d["index"] for d in enumeration["devices"]],
                "default_input_device": api["default_input"],
                "default_output_device": api["default_output"],
            }
            for api in enumeration["hostapis"]
        ],
    )
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(d["index"]),
                name=d["name"],
                input_channels=d["max_input_channels"],
                output_channels=d["max_output_channels"],
            )
            for d in enumeration["devices"]
        ]
    )
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Darwin")
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    monkeypatch.setattr(
        "rigplane.audio.usb_driver._extract_sounddevice_module", lambda _backend: sd
    )
    if real_reader:
        _inject_macos_hardware(monkeypatch, session)
    else:
        monkeypatch.setattr(
            "rigplane.audio._usb_raw_macos.read_raw_macos",
            lambda timeout_s: _raw_macos(session),
        )
    return backend, sd


def _inject_macos_hardware(monkeypatch, session):
    from rigplane.audio import _usb_raw_macos as raw

    recording = session["raw"]["macos"]
    nodes = {
        row["registry_id"]: {
            "IORegistryEntryID": row["registry_id"],
            "IORegistryEntryName": str(row["registry_id"]),
            "idVendor": row["vendor_id"],
            "idProduct": row["product_id"],
            "bDeviceClass": row["device_class"],
            "locationID": row["location_id"],
            "IORegistryEntryChildren": [],
            **{
                key: row[field]
                for key, field in (
                    ("USB Product Name", "product"),
                    ("USB Vendor Name", "manufacturer"),
                    ("USB Serial Number", "serial_number"),
                )
                if row[field] is not None
            },
        }
        for row in recording["usb_devices"]
    }
    roots = []
    for row in recording["usb_devices"]:
        parent = nodes.get(row["parent_registry_id"])
        children = roots if parent is None else parent["IORegistryEntryChildren"]
        children.append(nodes[row["registry_id"]])
    for row in recording["serial_devices"]:
        nodes[row["usb_registry_id"]]["IORegistryEntryChildren"].append(
            {
                "IORegistryEntryName": "Serial",
                "IOObjectClass": "IOSerialBSDClient",
                "IOCalloutDevice": row["device"],
            }
        )
    plist = plistlib.dumps(roots)

    def ioreg(command, **kwargs):
        assert command[0] == "/usr/sbin/ioreg", "frozen sidecar was relaunched"
        return subprocess.CompletedProcess(command, 0, plist)

    monkeypatch.setattr(raw.subprocess, "run", ioreg)
    audio = {row["device_id"]: row for row in recording["audio_devices"]}
    monkeypatch.setattr(raw, "_load_coreaudio", object)
    monkeypatch.setattr(
        raw,
        "_get_string_property",
        lambda ca, obj, selector: audio[obj][
            "uid" if selector == 0x75696420 else "name"
        ],
    )

    def property_bytes(ca, obj, selector, scope="glob"):
        if selector == "dev#":
            return struct.pack(f"={len(audio)}I", *audio)
        if selector == "tran":
            return struct.pack("=I", audio[obj]["transport_type"])
        channels = audio[obj][
            "input_channels" if scope == "inpt" else "output_channels"
        ]
        return struct.pack("=I", 1).ljust(
            raw._AudioBufferList.mBuffers.offset, b"\0"
        ) + bytes(raw._AudioBuffer(channels, 0, None))

    monkeypatch.setattr(raw, "_property", property_bytes)


def _synthetic_macos():
    session = _macos_session()
    raw = session["raw"]["macos"]
    ids = {4294970281, 4294970968, 4295496583, 4295496601, 4295496626}
    raw["usb_devices"] = [
        row for row in raw["usb_devices"] if row["registry_id"] in ids
    ]
    raw["serial_devices"] = [
        row for row in raw["serial_devices"] if row["usb_registry_id"] == 4295496601
    ]
    raw["audio_devices"] = [
        row for row in raw["audio_devices"] if row["name"] == "USB Audio CODEC"
    ]
    return session


@pytest.mark.parametrize(
    "profile,port,indices,codec",
    [
        ("IC-705", MAC_PORT, (2, 1), "4295496626"),
        ("FTX-1", FTX_PORT, (3, 3), "4295976942"),
    ],
)
def test_recording_adapter_uses_usb_ancestry_and_uid_location(
    profile, port, indices, codec
):
    snapshot = _adapt(_macos_session())
    result = pair(snapshot, port, get_radio_profile(profile).usb_identity)
    assert isinstance(result, PairOk), result
    assert (result.rx_index, result.tx_index, result.hostapi) == (
        *indices,
        "Core Audio",
    )
    assert result.codec_node == codec
    assert snapshot.os == "macos"
    assert all(
        node.parent is not None or node.kind == "root_hub" for node in snapshot.usb
    )
    if profile == "FTX-1":
        assert snapshot.portaudio.devices[result.rx_index].name == "USB Audio Device"
        assert snapshot.portaudio.devices[result.tx_index].name == "USB Audio Device"


@pytest.mark.parametrize("radio_capture_present", [True, False])
def test_uid_serial_location_collision_never_assigns_webcam_to_radio(
    radio_capture_present,
):
    session = _macos_session()
    raw = session["raw"]["macos"]
    camera = next(row for row in raw["usb_devices"] if row["registry_id"] == 4294970494)
    camera["serial_number"] = "03121400"
    webcam = next(row for row in raw["audio_devices"] if row["device_id"] == 127)
    webcam["uid"] = webcam["uid"].replace("R62PGXVOTB4", "03121400")
    if not radio_capture_present:
        raw["audio_devices"] = [
            row for row in raw["audio_devices"] if row["device_id"] != 137
        ]
    snapshot = _adapt(session)
    result = pair(snapshot, MAC_PORT, get_radio_profile("IC-705").usb_identity)
    if radio_capture_present:
        assert isinstance(result, PairOk), result
        assert (result.rx_index, result.tx_index, result.codec_node) == (
            2,
            1,
            "4295496626",
        )
    else:
        assert isinstance(result, PairRefused), result
        assert result.reason == "no_codec"
        assert result.candidates
        candidate = next(c for c in result.candidates if c.portaudio_index == 4)
        assert (candidate.direction, candidate.near_radio) == ("capture", False)
    endpoint = next(e for e in snapshot.endpoints if e.os_id == webcam["uid"])
    assert endpoint.owner == "opaque:" + webcam["uid"]


@pytest.mark.parametrize("serial", ["R62PGXVOTB4", "03121400", "0000000003121400"])
def test_serial_form_uid_uses_its_own_corroborated_usb_device(serial):
    session = _macos_session()
    raw = session["raw"]["macos"]
    camera = next(row for row in raw["usb_devices"] if row["registry_id"] == 4294970494)
    camera.update(serial_number=serial, manufacturer="Camera vendor")
    webcam = next(row for row in raw["audio_devices"] if row["device_id"] == 127)
    webcam["uid"] = (
        "AppleUSBAudioEngine: Camera  vendor : HD  Pro Webcam C920 :" + serial + ":3"
    )
    snapshot = _adapt(session)
    endpoint = next(e for e in snapshot.endpoints if e.os_id == webcam["uid"])
    assert (endpoint.owner, endpoint.flow) == ("4294970494", "capture")
    result = pair(snapshot, MAC_PORT, get_radio_profile("IC-705").usb_identity)
    assert isinstance(result, PairOk), result
    assert (result.rx_index, result.tx_index) == (2, 1)


@pytest.mark.parametrize(
    "evidence",
    [
        "wrong-manufacturer",
        "wrong-product",
        "missing-manufacturer",
        "missing-product",
        "duplicate-location",
        "conflicting-serial",
    ],
)
def test_uid_ownership_without_unique_name_corroboration_stays_opaque(evidence):
    session = _synthetic_macos()
    raw = session["raw"]["macos"]
    codec = next(row for row in raw["usb_devices"] if row["registry_id"] == 4295496626)
    if evidence.startswith("wrong-"):
        codec[evidence.removeprefix("wrong-")] = "Other device"
    elif evidence.startswith("missing-"):
        codec[evidence.removeprefix("missing-")] = None
    else:
        other = {**codec, "registry_id": 123}
        if evidence == "conflicting-serial":
            other.update(location_id=0xFFFFFFFF, serial_number="3121400")
        raw["usb_devices"].append(other)
    snapshot = _adapt(session)
    assert all(e.owner == "opaque:" + e.os_id for e in snapshot.endpoints)
    result = pair(snapshot, MAC_PORT, get_radio_profile("IC-705").usb_identity)
    assert isinstance(result, PairRefused), result
    assert result.reason == "no_codec"
    assert result.candidates


@pytest.mark.parametrize(
    "condition",
    [
        "undeclared",
        "second-radio",
        "duplicate-cat",
        "duplicate-hri",
        "no-profile",
        "codec-only",
        "empty-functions",
    ],
)
def test_recorded_serial_functions_preserve_ambiguous_refusal(condition):
    session = _macos_session()
    identity = get_radio_profile("FTX-1").usb_identity
    raw = session["raw"]["macos"]
    if condition in {"undeclared", "second-radio", "duplicate-cat", "duplicate-hri"}:
        owners = {
            "undeclared": (4295976929,),
            "second-radio": (4295976883, 4295976929),
            "duplicate-cat": (4295976883,),
            "duplicate-hri": (4295976929,),
        }[condition]
        for index, owner in enumerate(owners):
            original = next(
                row for row in raw["usb_devices"] if row["registry_id"] == owner
            )
            row = {**original, "registry_id": 100 + index}
            if condition == "undeclared":
                row.update(vendor_id=0xFFFF, product_id=0x0001)
            raw["usb_devices"].append(row)
            raw["serial_devices"].append(
                {
                    "device": f"/dev/cu.extra{index}",
                    "usb_registry_id": row["registry_id"],
                }
            )
    elif condition == "no-profile":
        identity = None
    else:
        identity = (
            replace(identity, serial_functions=frozenset())
            if condition == "empty-functions"
            else type(identity)(identity.audio_codec)
        )
    result = pair(_adapt(session), FTX_PORT, identity)
    assert isinstance(result, PairRefused)
    assert (result.reason, result.wire_reason) == ("serial_ambiguous", "usb_ambiguous")
    assert {
        (candidate.portaudio_index, candidate.direction)
        for candidate in result.candidates
        if candidate.near_radio
    } == {(3, "capture"), (3, "render")}


@pytest.mark.parametrize(
    "profile,port,indices", [("IC-705", MAC_PORT, (2, 1)), ("FTX-1", FTX_PORT, (3, 3))]
)
async def test_recording_driver_and_public_resolver_use_shared_rule(
    monkeypatch, profile, port, indices
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver
    from rigplane.usb_audio_resolve import resolve_audio_for_serial_port

    backend, sd = _inject_macos(monkeypatch, _macos_session())
    identity = get_radio_profile(profile).usb_identity
    pairing = Mock(wraps=_usb_pairing.pair)
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    mapping = resolve_audio_for_serial_port(
        port, sounddevice_module=sd, usb_identity=identity
    )
    assert mapping is not None
    assert (
        mapping.rx_device_index,
        mapping.tx_device_index,
        mapping.location_prefix,
    ) == (*indices, None)
    driver = UsbAudioDriver(backend=backend, serial_port=port, usb_identity=identity)
    rx, tx = await driver._select_devices_bounded(direction="rx")
    assert (rx.index, tx.index) == indices
    assert pairing.call_count == 2
    assert all(call.args[2] == identity for call in pairing.call_args_list)


def test_nested_hub_uses_parent_links_even_when_locations_have_no_prefix():
    session = _synthetic_macos()
    rows = session["raw"]["macos"]["usb_devices"]
    codec = next(row for row in rows if row["registry_id"] == 4295496626)
    hub = next(row for row in rows if row["registry_id"] == 4295496583)
    rows.append({**hub, "registry_id": 123, "parent_registry_id": hub["registry_id"]})
    codec["parent_registry_id"] = 123
    codec["location_id"] = 0xABCDEF00
    for endpoint in session["raw"]["macos"]["audio_devices"]:
        endpoint["uid"] = endpoint["uid"].replace("3121400", "abcdef00")
    result = pair(_adapt(session), MAC_PORT, None)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index) == (2, 1)


@pytest.mark.parametrize("same_names", [True, False])
def test_two_radios_same_codec_identity_on_different_hubs_do_not_use_name_rank(
    same_names,
):
    session = _synthetic_macos()
    raw = session["raw"]["macos"]
    for original in tuple(raw["usb_devices"]):
        if original["registry_id"] not in {4295496583, 4295496601, 4295496626}:
            continue
        row = deepcopy(original)
        row["registry_id"] += 100
        if row["parent_registry_id"] == 4295496583:
            row["parent_registry_id"] += 100
        row["location_id"] += 0x10000
        raw["usb_devices"].append(row)
    raw["serial_devices"].append(
        {"device": "/dev/cu.other", "usb_registry_id": 4295496701}
    )
    for original in tuple(raw["audio_devices"]):
        row = deepcopy(original)
        row["uid"] = row["uid"].replace("3121400", "3131400")
        if not same_names:
            row["name"] = "Second codec"
        raw["audio_devices"].append(row)
    if not same_names:
        devices = session["raw"]["portaudio"]["devices"]
        for original in (devices[1], devices[2]):
            devices.append({**original, "index": len(devices), "name": "Second codec"})
    for port, indices in ((MAC_PORT, (2, 1)), ("/dev/cu.other", (18, 17))):
        result = pair(_adapt(session), port, get_radio_profile("IC-705").usb_identity)
        if same_names:
            assert isinstance(result, PairRefused)
            assert result.reason == "contested"
            assert result.candidates
        else:
            assert isinstance(result, PairOk)
            assert (result.rx_index, result.tx_index) == indices


def test_split_pair_preserves_portaudio_index_zero():
    session = _synthetic_macos()
    devices = session["raw"]["portaudio"]["devices"]
    devices[:] = [devices[1], devices[2]]
    for index, row in enumerate(devices):
        row["index"] = index
    result = pair(_adapt(session), MAC_PORT, None)
    assert isinstance(result, PairOk)
    assert (result.rx_index, result.tx_index) == (1, 0)


@pytest.mark.parametrize(
    "shape,reason",
    [
        ("missing-link", "no_codec"),
        ("root-only", "containment_unproven"),
        ("foreign-audio", "contested"),
        ("second-serial", "serial_ambiguous"),
    ],
)
def test_synthetic_refusals_keep_typed_candidates(shape, reason):
    session = _synthetic_macos()
    raw = session["raw"]["macos"]
    if shape == "missing-link":
        for row in raw["audio_devices"]:
            row["uid"] = row["uid"].replace("3121400", "ffffffff")
    elif shape == "root-only":
        for row in raw["usb_devices"]:
            if row["registry_id"] in {4295496601, 4295496626}:
                row["parent_registry_id"] = None
                row["parent_path"] = "IOUSB:/Controller@0"
    elif shape == "second-serial":
        raw["serial_devices"].append(
            {"device": "/dev/cu.other", "usb_registry_id": 123}
        )
        raw["usb_devices"].append({**raw["usb_devices"][-1], "registry_id": 123})
    else:
        raw["usb_devices"].append(
            {**raw["usb_devices"][-1], "registry_id": 123, "location_id": 0xFFFFFFFF}
        )
        for row in tuple(raw["audio_devices"]):
            if row["input_channels"]:
                raw["audio_devices"].append(
                    {**row, "uid": row["uid"].replace("3121400", "ffffffff")}
                )
    snapshot = _adapt(session)
    if shape == "foreign-audio":
        assert any(endpoint.owner == "123" for endpoint in snapshot.endpoints)
        assert (
            next(node.parent for node in snapshot.usb if node.id == "123")
            == "4295496583"
        )
    result = pair(snapshot, MAC_PORT, None)
    assert isinstance(result, PairRefused)
    assert result.reason == reason
    assert {(c.portaudio_index, c.direction) for c in result.candidates} == {
        (2, "capture"),
        (1, "render"),
    }


@pytest.mark.parametrize("part", ["usb", "serial", "coreaudio", "coreaudio:42:uid"])
def test_partial_read_refuses_even_when_remaining_records_look_pairable(part):
    session = _synthetic_macos()
    session["raw"]["macos"]["unavailable"] = [
        {"part": "macos:" + part, "reason": "failed"}
    ]
    result = pair(_adapt(session), MAC_PORT, None)
    assert isinstance(result, PairRefused)
    assert result.reason == "unavailable"
    assert result.candidates


@pytest.mark.parametrize("indices", [(2, 1), (1, 2), (3, 3)])
async def test_stable_uid_overrides_survive_reorder_and_bypass_pairing(
    monkeypatch, indices
):
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver

    session = _macos_session()
    devices = session["raw"]["portaudio"]["devices"]
    if indices == (1, 2):
        devices[1], devices[2] = devices[2], devices[1]
        for index, row in enumerate(devices):
            row["index"] = index
    backend, _ = _inject_macos(monkeypatch, session)
    pairing = Mock(side_effect=AssertionError("explicit choice must bypass pairing"))
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    rx_id, tx_id, port = MAC_RX_ID, MAC_TX_ID, MAC_PORT
    if indices == (3, 3):
        rx_id = tx_id = (
            "Core Audio|AppleUSBAudioEngine:C-Media Electronics Inc.:"
            "USB Audio Device:3122200:2,1"
        )
        port = FTX_PORT
    driver = UsbAudioDriver(
        backend=backend, serial_port=port, rx_device=rx_id, tx_device=tx_id
    )
    rx, tx = await driver._select_devices_bounded(direction="rx")
    assert (rx.index, tx.index) == indices
    pairing.assert_not_called()


async def test_macos_raw_reader_is_off_loop_and_late_result_cannot_select(monkeypatch):
    from rigplane.audio._usb_pairing import WindowsAudioTopologyError
    from rigplane.audio.usb_driver import UsbAudioDriver

    session = _macos_session()
    backend, _ = _inject_macos(monkeypatch, session)
    entered, release = Event(), Event()
    caller_thread = get_ident()

    def blocked(timeout_s):
        assert get_ident() != caller_thread
        entered.set()
        assert release.wait(5)
        return _raw_macos(session)

    monkeypatch.setattr("rigplane.audio._usb_raw_macos.read_raw_macos", blocked)
    driver = UsbAudioDriver(
        backend=backend, serial_port=MAC_PORT, capture_open_timeout=0.15
    )
    task = asyncio.create_task(driver._select_devices_bounded(direction="rx"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        with pytest.raises(WindowsAudioTopologyError):
            await task
        assert driver.selected_rx_device is None
        assert driver.selected_tx_device is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
