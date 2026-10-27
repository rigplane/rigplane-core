"""Synthetic Linux sysfs/ALSA pairing; not hardware-verified."""

import asyncio
from dataclasses import replace
from functools import partial
import sys
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane.audio import _usb_raw_linux
from rigplane.audio._portaudio_enum import read_portaudio_enumeration
from rigplane.audio._usb_names import endpoint_name_key
from rigplane.audio._usb_pairing import PairOk, PairRefused, pair
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.profiles import get_radio_profile


LINUX_PORT = "/dev/ttyUSB0"
LINUX_ID = "ALSA|/sys/devices/usb1/1-1/1-1.5:pcm0"
LINUX_NAME = "USB Audio CODEC: USB Audio (hw:2,0)"


def _linux_tree(
    root, monkeypatch, *, profile="IC-705", shape="normal", first_card=2, reverse=False
):
    """Build kernel-shaped links from profile data; not hardware-verified."""
    identity = get_radio_profile(profile).usb_identity
    bus = root / "bus/usb/devices"
    bus.mkdir(parents=True)
    for kind in ("tty", "sound"):
        (root / "class" / kind).mkdir(parents=True)

    def node(parent, name, vid="1234", pid="5678", cls="00"):
        path = parent / name
        path.mkdir(parents=True, exist_ok=True)
        for attr, value in {
            "idVendor": vid,
            "idProduct": pid,
            "devpath": "0" if name == "usb1" else name.split("-", 1)[1],
            "busnum": "1",
            "bDeviceClass": cls,
            "product": "Device",
        }.items():
            (path / attr).write_text(value + "\n")
        (bus / name).symlink_to(path, target_is_directory=True)
        return path

    def link(owner, kind, name, cls):
        interface = owner / f"{owner.name}:1.0"
        interface.mkdir(exist_ok=True)
        (interface / "bInterfaceClass").write_text(cls + "\n")
        (bus / interface.name).symlink_to(interface, target_is_directory=True)
        directory = root / "class" / kind / name
        directory.mkdir()
        (directory / "device").symlink_to(interface, target_is_directory=True)
        return directory

    root_hub = node(root / "devices", "usb1", cls="09")
    cards = []
    records = []
    count = 2 if shape == "two-radios" else 1
    codec_paths = []
    for number in range(count):
        hub_name = f"1-{number + 1}"
        hub = root_hub if shape == "root-only" else node(root_hub, hub_name, cls="09")
        functions = sorted(identity.serial_functions, key=lambda f: (f.vid, f.pid))
        functions = functions or [SimpleNamespace(vid="10C4", pid="EA60")]
        for index, function in enumerate(functions):
            serial = node(hub, f"{hub_name}.{index + 1}", function.vid, function.pid)
            link(serial, "tty", f"ttyUSB{number * 2 + index}", "ff")
        if shape == "missing-codec":
            continue
        parent = node(hub, hub_name + ".4", cls="09") if shape == "nested" else hub
        codec_id = next(iter(identity.audio_codec))
        codec = node(
            parent,
            (hub_name if parent == root_hub else parent.name) + ".5",
            codec_id.vid,
            codec_id.pid,
        )
        codec_paths.append("/sys/" + codec.relative_to(root).as_posix())
        for offset in range(2 if shape == "foreign-audio" else 1):
            owner = codec if offset == 0 else node(hub, hub_name + ".6", "FFFF", "0001")
            card = first_card + number + offset
            directory = link(owner, "sound", f"card{card}", "01")
            (directory / "id").write_text("CODEC\n")
            cards.append(f" {card} [CODEC ]: USB-Audio - USB Audio CODEC, USB Audio\n")
            records.append(
                {
                    "name": f"USB Audio CODEC: USB Audio (hw:{card},0)",
                    "hostapi": 0,
                    "max_input_channels": 2,
                    "max_output_channels": 2,
                    "default_samplerate": 48000.0,
                    **{
                        f"default_{latency}_{flow}_latency": 0.01
                        for latency in ("low", "high")
                        for flow in ("input", "output")
                    },
                }
            )
    if shape == "no-tty-link":
        (root / "class/tty/ttyUSB0/device").unlink()
    if reverse:
        records.reverse()
    monkeypatch.setattr(_usb_raw_linux, "_read_asound_cards", lambda: "".join(cards))
    sd = SimpleNamespace(
        query_devices=lambda: records,
        query_hostapis=lambda: [
            {
                "name": "ALSA",
                "devices": list(range(len(records))),
                "default_input_device": 0,
                "default_output_device": 0,
            }
        ],
    )
    return sd, identity, codec_paths


def _inject_linux(root, monkeypatch, **kwargs):
    """Use the real reader over synthetic sysfs; not hardware-verified."""
    sd, identity, codecs = _linux_tree(root, monkeypatch, **kwargs)
    monkeypatch.setattr(
        _usb_raw_linux,
        "read_raw_linux",
        partial(_usb_raw_linux.read_raw_linux, sysfs_root=str(root)),
    )
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Linux")
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})
    monkeypatch.setattr(
        "rigplane.audio.usb_driver._extract_sounddevice_module", lambda _: sd
    )
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(index),
                name=row["name"],
                input_channels=row["max_input_channels"],
                output_channels=row["max_output_channels"],
            )
            for index, row in enumerate(sd.query_devices())
        ]
    )
    return backend, sd, identity, codecs


@pytest.mark.parametrize("profile", ["IC-705", "FTX-1"])
@pytest.mark.parametrize("shape", ["normal", "nested", "two-radios"])
def test_linux_adapter_pairs_sysfs_owners_without_ranking(
    tmp_path, monkeypatch, profile, shape
):
    """Synthetic radio families and hubs; not hardware-verified."""
    from rigplane.audio._usb_adapt_linux import adapt_linux

    sd, identity, codecs = _linux_tree(
        tmp_path, monkeypatch, profile=profile, shape=shape
    )
    raw = _usb_raw_linux.read_raw_linux(str(tmp_path))
    snapshot = adapt_linux(raw, read_portaudio_enumeration(sd))
    assert snapshot.os == "linux"
    assert not snapshot.unavailable
    assert len([n for n in snapshot.usb if n.kind == "root_hub"]) == 1
    for number, codec in enumerate(codecs):
        result = pair(snapshot, f"/dev/ttyUSB{number * 2}", identity)
        assert isinstance(result, PairOk), result
        assert (result.rx_index, result.tx_index, result.codec_node) == (
            number,
            number,
            codec,
        )
        assert result.rx_id == result.tx_id == codec + ":pcm0"


@pytest.mark.parametrize(
    "shape,reason",
    [
        ("missing-codec", "no_codec"),
        ("foreign-audio", "codec_ambiguous"),
        ("root-only", "containment_unproven"),
        ("no-tty-link", "serial_not_found"),
    ],
)
def test_linux_topology_refusals(tmp_path, monkeypatch, shape, reason):
    """Synthetic refusal boundaries; not hardware-verified."""
    from rigplane.audio._usb_adapt_linux import adapt_linux

    sd, identity, _ = _linux_tree(tmp_path, monkeypatch, shape=shape)
    snapshot = adapt_linux(
        _usb_raw_linux.read_raw_linux(str(tmp_path)), read_portaudio_enumeration(sd)
    )
    result = pair(snapshot, LINUX_PORT, identity)
    assert isinstance(result, PairRefused), result
    assert result.reason == reason
    if shape != "missing-codec":
        assert result.candidates
        expected_id = (
            "ALSA|/sys/devices/usb1/1-1.5:pcm0" if shape == "root-only" else LINUX_ID
        )
        assert result.candidates[0].stable_id == expected_id


@pytest.mark.parametrize("declared", [False, True])
def test_linux_multiple_serial_functions_require_profile_declaration(
    tmp_path, monkeypatch, declared
):
    """Synthetic multi-function radio; not hardware-verified."""
    from rigplane.audio._usb_adapt_linux import adapt_linux

    sd, identity, _ = _linux_tree(tmp_path, monkeypatch, profile="FTX-1")
    if not declared:
        identity = replace(identity, serial_functions=frozenset())
    snapshot = adapt_linux(
        _usb_raw_linux.read_raw_linux(str(tmp_path)), read_portaudio_enumeration(sd)
    )
    result = pair(snapshot, LINUX_PORT, identity)
    assert isinstance(result, PairOk if declared else PairRefused)
    if not declared:
        assert result.reason == "serial_ambiguous"


@pytest.mark.parametrize("part", ["usb", "tty", "sound", "asound"])
def test_linux_partial_snapshot_refuses_with_candidates(tmp_path, monkeypatch, part):
    """Synthetic partial reads; not hardware-verified."""
    from rigplane.audio._usb_adapt_linux import adapt_linux

    sd, identity, _ = _linux_tree(tmp_path, monkeypatch)
    raw = replace(
        _usb_raw_linux.read_raw_linux(str(tmp_path)),
        unavailable=({"part": "linux:" + part, "reason": "failed"},),
    )
    result = pair(
        adapt_linux(raw, read_portaudio_enumeration(sd)), LINUX_PORT, identity
    )
    assert isinstance(result, PairRefused)
    assert result.reason == "unavailable"
    assert result.candidates[0].stable_id == LINUX_ID


@pytest.mark.parametrize("profile", ["IC-705", "FTX-1"])
async def test_linux_public_resolver_driver_and_discovery_share_profile_rule(
    tmp_path, monkeypatch, profile
):
    """Synthetic reader-to-consumer integration; not hardware-verified."""
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver
    from rigplane.backends.discovery import _resolve_profile_usb_audio_metadata
    from rigplane.usb_audio_resolve import resolve_audio_for_serial_port

    backend, sd, identity, _ = _inject_linux(tmp_path, monkeypatch, profile=profile)
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    pairing = Mock(wraps=_usb_pairing.pair)
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    mapping = resolve_audio_for_serial_port(
        LINUX_PORT, sounddevice_module=sd, usb_identity=identity
    )
    assert mapping is not None
    assert (
        mapping.rx_device_index,
        mapping.tx_device_index,
        mapping.location_prefix,
    ) == (0, 0, None)
    driver = UsbAudioDriver(
        backend=backend, serial_port=LINUX_PORT, usb_identity=identity
    )
    rx, tx = await driver._select_devices_bounded(direction="rx")
    assert (rx.index, tx.index) == (0, 0)
    assert await _resolve_profile_usb_audio_metadata(LINUX_PORT, profile) == {
        "rx_device_index": 0,
        "tx_device_index": 0,
        "serial_port": LINUX_PORT,
        "location_prefix": None,
    }
    assert pairing.call_count == 3
    assert all(call.args[2] == identity for call in pairing.call_args_list)


async def test_linux_reader_is_off_loop_and_late_result_cannot_select(
    tmp_path, monkeypatch
):
    """Synthetic timeout cleanup; not hardware-verified."""
    from rigplane.audio._usb_pairing import WindowsAudioTopologyError
    from rigplane.audio.usb_driver import UsbAudioDriver

    backend, _, identity, _ = _inject_linux(tmp_path, monkeypatch)
    read = _usb_raw_linux.read_raw_linux
    caller = get_ident()
    entered, release = Event(), Event()

    def blocked(*, timeout_s):
        assert get_ident() != caller
        entered.set()
        assert release.wait(5)
        return read(timeout_s=timeout_s)

    monkeypatch.setattr(_usb_raw_linux, "read_raw_linux", blocked)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port=LINUX_PORT,
        usb_identity=identity,
        capture_open_timeout=0.2,
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


@pytest.mark.parametrize("reverse,first_card", [(False, 2), (True, 9)])
async def test_linux_stable_override_survives_portaudio_and_alsa_renumbering(
    tmp_path, monkeypatch, reverse, first_card
):
    """Synthetic identity and enumeration changes; not hardware-verified."""
    from rigplane.audio import _usb_pairing
    from rigplane.audio.usb_driver import UsbAudioDriver

    backend, _, _, _ = _inject_linux(
        tmp_path,
        monkeypatch,
        shape="two-radios",
        reverse=reverse,
        first_card=first_card,
    )
    pairing = Mock(side_effect=AssertionError("explicit choice must bypass pairing"))
    monkeypatch.setattr(_usb_pairing, "pair", pairing)
    driver = UsbAudioDriver(
        backend=backend, serial_port=LINUX_PORT, rx_device=LINUX_ID, tx_device=LINUX_ID
    )
    rx, tx = await driver._select_devices_bounded(direction="rx")
    assert rx.index == tx.index == int(reverse)
    assert rx.platform_uid == tx.platform_uid == f"hw:{first_card},0"
    pairing.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "USB Audio CODEC: USB Audio (hw:2,0)",
        "USB Audio CODEC, USB Audio (plughw:2,0)",
        "USB Audio CODEC (hw:2,0)",
    ],
)
def test_shared_name_key_normalizes_alsa_without_discarding_card_identity(name):
    """Synthetic ALSA decorations; not hardware-verified."""
    assert endpoint_name_key(name) == "USB Audio CODEC (hw:2,0)"
    assert endpoint_name_key(name) != endpoint_name_key("USB Audio CODEC (hw:3,0)")
    assert endpoint_name_key("Radio: Special Edition") == "Radio: Special Edition"
