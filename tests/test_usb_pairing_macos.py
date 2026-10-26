"""Replay macOS topology through the adapter, resolver and driver."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
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


def _inject_macos(monkeypatch, session):
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
    monkeypatch.setattr(
        "rigplane.audio._usb_raw_macos.read_raw_macos",
        lambda timeout_s: _raw_macos(session),
    )
    return backend, sd


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
        for row in tuple(raw["audio_devices"]):
            raw["audio_devices"].append(
                {**row, "uid": row["uid"].replace("3121400", "ffffffff")}
            )
    result = pair(_adapt(session), MAC_PORT, None)
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


@pytest.mark.parametrize("indices", [(2, 1), (1, 2)])
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
    driver = UsbAudioDriver(
        backend=backend, serial_port=MAC_PORT, rx_device=MAC_RX_ID, tx_device=MAC_TX_ID
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
