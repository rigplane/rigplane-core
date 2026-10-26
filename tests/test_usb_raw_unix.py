import asyncio
import ctypes
from importlib import import_module
import json
from pathlib import Path
import plistlib
import struct
import subprocess
from threading import Event, get_ident
from unittest.mock import Mock

import pytest


def test_macos_records_tree_serial_ancestry_and_audio_uid(monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_macos")
    codec = dict(
        IORegistryEntryName="Codec",
        IORegistryEntryID=4,
        locationID=0x3121400,
        idVendor=0x1234,
        idProduct=2,
        bDeviceClass=0,
        **{"USB Product Name": "USB Audio CODEC", "USB Vendor Name": "Vendor"},
    )
    serial = dict(
        IORegistryEntryName="Serial",
        IORegistryEntryID=3,
        locationID=0x3121300,
        idVendor=0x1234,
        idProduct=1,
        bDeviceClass=2,
        **{"USB Serial Number": "SECRET"},
    )
    hub = dict(
        IORegistryEntryName="Hub",
        IORegistryEntryID=2,
        locationID=0x3121000,
        idVendor=0x1234,
        idProduct=3,
        bDeviceClass=9,
        IORegistryEntryChildren=[serial, codec],
    )
    usb = dict(
        IORegistryEntryName="Root", IORegistryEntryID=1, IORegistryEntryChildren=[hub]
    )
    service = {
        **serial,
        "IORegistryEntryChildren": [
            dict(
                IORegistryEntryName="Interface",
                IORegistryEntryID=5,
                idVendor=0x1234,
                idProduct=1,
                bInterfaceClass=10,
                IORegistryEntryChildren=[
                    dict(
                        IORegistryEntryName="IOSerialBSDClient",
                        IORegistryEntryID=6,
                        IOObjectClass="IOSerialBSDClient",
                        IOCalloutDevice="/dev/cu.usbSECRET",
                        IODialinDevice="/dev/tty.usbSECRET",
                    )
                ],
            )
        ],
    }
    audio = [
        dict(
            device_id=42,
            uid="AppleUSBAudioEngine:Vendor:Codec:3121400:2",
            name="USB Audio CODEC",
            transport_type=0x75736220,
            input_channels=2,
            output_channels=2,
        )
    ]
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess([], 0, plistlib.dumps(usb)),
            subprocess.CompletedProcess([], 0, plistlib.dumps([service])),
        ]
    )
    monkeypatch.setattr(raw.subprocess, "run", run)
    monkeypatch.setattr(raw, "_read_coreaudio", lambda: (audio, []))
    result = raw.read_raw_macos(5).to_json_dict()
    assert result["unavailable"] == []
    nodes = {n["registry_id"]: n for n in result["usb_devices"]}
    assert nodes[2]["is_hub"] is True
    assert nodes[3]["is_hub"] is False
    assert nodes[3]["parent_registry_id"] == nodes[4]["parent_registry_id"] == 2
    assert nodes[3]["serial_number"] == "SECRET"
    assert nodes[4]["location_id"] == 0x3121400
    assert nodes[4]["vendor_id"] == 0x1234
    assert nodes[4]["product"] == "USB Audio CODEC"
    assert len({n["registry_path"] for n in nodes.values()}) == 3
    assert {n["device"] for n in result["serial_devices"]} == {
        "/dev/cu.usbSECRET",
        "/dev/tty.usbSECRET",
    }
    assert {n["usb_registry_id"] for n in result["serial_devices"]} == {3}
    assert result["audio_devices"] == audio
    assert f"{nodes[4]['location_id']:x}" in audio[0]["uid"]
    assert all(0 < call.kwargs["timeout"] <= 5 for call in run.call_args_list)
    assert run.call_count == 2


@pytest.mark.parametrize(
    "failure",
    [OSError("absent"), subprocess.TimeoutExpired("ioreg", 1), ValueError("malformed")],
)
def test_macos_source_failures_are_unavailable(monkeypatch, failure):
    raw = import_module("rigplane.audio._usb_raw_macos")
    monkeypatch.setattr(raw.subprocess, "run", Mock(side_effect=failure))
    monkeypatch.setattr(raw, "_read_coreaudio", Mock(side_effect=failure))
    result = raw.read_raw_macos(1).to_json_dict()
    assert (
        result["usb_devices"]
        == result["serial_devices"]
        == result["audio_devices"]
        == []
    )
    assert {item["part"] for item in result["unavailable"]} == {
        "macos:usb",
        "macos:serial",
        "macos:coreaudio",
    }


def test_macos_malformed_plist_does_not_discard_other_sources(monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_macos")
    monkeypatch.setattr(
        raw.subprocess,
        "run",
        Mock(
            side_effect=[
                subprocess.CompletedProcess([], 0, b"not a plist"),
                subprocess.CompletedProcess([], 0, plistlib.dumps([])),
            ]
        ),
    )
    monkeypatch.setattr(raw, "_read_coreaudio", lambda: ([], []))
    result = raw.read_raw_macos(1)
    assert [item["part"] for item in result.unavailable] == ["macos:usb"]


async def test_coreaudio_timeout_is_unavailable_without_blocking_loop(monkeypatch):
    from _portaudio_pool_helpers import drain_pool
    from rigplane.audio import _usb_raw_macos as raw
    from rigplane.audio.usb_driver import bounded_portaudio_pool

    entered, release = Event(), Event()
    caller = get_ident()
    monkeypatch.setattr(
        raw.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, plistlib.dumps([])),
    )

    def blocked():
        assert get_ident() != caller
        entered.set()
        assert release.wait(5)
        return object()

    monkeypatch.setattr(raw, "_load_coreaudio", blocked)
    task = asyncio.create_task(asyncio.to_thread(raw.read_raw_macos, 0.15))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        result = await asyncio.wait_for(task, 1)
        assert result.audio_devices == ()
        assert result.unavailable == (
            {"part": "macos:coreaudio", "reason": "TimeoutError"},
        )
        assert bounded_portaudio_pool.inflight == 1
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await drain_pool()


def test_coreaudio_ctypes_counts_buffers_and_reports_property_errors(monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_macos")
    buf = raw._AudioBuffer
    offset = raw._AudioBufferList.mBuffers.offset

    def channels(*counts):
        return struct.pack("=I", len(counts)).ljust(offset, b"\0") + b"".join(
            bytes(buf(n, 0, None)) for n in counts
        )

    props = {
        (1, "dev#", "glob"): struct.pack("=II", 42, 43),
        (42, "tran", "glob"): struct.pack("=I", 0x75736220),
        (42, "slay", "inpt"): channels(1, 2),
        (42, "slay", "outp"): channels(2),
    }

    def get_property(obj, addr, qualifier_size, qualifier, size, data=None):
        address = addr._obj
        key = (
            obj.value,
            address.mSelector.to_bytes(4, "big").decode(),
            address.mScope.to_bytes(4, "big").decode(),
        )
        if key not in props:
            return -1
        value = props[key]
        size._obj.value = len(value)
        if data is not None:
            ctypes.memmove(data, value, len(value))
        return 0

    ca = Mock(
        AudioObjectGetPropertyDataSize=get_property,
        AudioObjectGetPropertyData=get_property,
    )
    monkeypatch.setattr(raw, "_load_coreaudio", lambda: ca)
    monkeypatch.setattr(
        raw,
        "_get_string_property",
        lambda ca, obj, selector: "uid" if selector == 0x75696420 else "Device",
    )
    devices, unavailable = raw._read_coreaudio()
    assert devices[0] == dict(
        device_id=42,
        uid="uid",
        name="Device",
        transport_type=0x75736220,
        input_channels=3,
        output_channels=2,
    )
    assert devices[1]["input_channels"] is None
    assert unavailable
    ca.AudioObjectGetPropertyDataSize = Mock(return_value=-1)
    with pytest.raises(OSError):
        raw._read_coreaudio()


def _linux_tree(root):
    bus = root / "bus/usb/devices"
    bus.mkdir(parents=True)
    hub = root / "devices/usb1/1-2"
    codec = hub / "1-2.1"
    serial = hub / "1-2.2"
    for node, device_class in ((hub, "09"), (codec, "00"), (serial, "02")):
        node.mkdir(parents=True, exist_ok=True)
        for name, value in dict(
            idVendor="1234",
            idProduct="5678",
            devpath=node.name,
            busnum="1",
            bDeviceClass=device_class,
            product="Device",
            manufacturer="Vendor",
            serial="SECRET",
        ).items():
            (node / name).write_text(value + "\n")
        (bus / node.name).symlink_to(node, target_is_directory=True)
    for node, name, cls in ((codec, "card2", "sound"), (serial, "ttyUSB0", "tty")):
        interface = node / f"{node.name}:1.0"
        interface.mkdir()
        (interface / "bInterfaceClass").write_text("01" if cls == "sound" else "ff")
        (bus / interface.name).symlink_to(interface, target_is_directory=True)
        classdir = root / "class" / cls / name
        classdir.mkdir(parents=True)
        (classdir / "device").symlink_to(interface, target_is_directory=True)
        if cls == "sound":
            (classdir / "id").write_text("CODEC\n")


def test_linux_records_attributes_interfaces_and_links(tmp_path, monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_linux")
    _linux_tree(tmp_path)
    cards = " 2 [CODEC          ]: USB-Audio - USB Audio CODEC\n                      Vendor Codec\n"
    monkeypatch.setattr(raw, "_read_asound_cards", lambda: cards)
    result = raw.read_raw_linux(str(tmp_path), timeout_s=5).to_json_dict()
    assert result["unavailable"] == []
    assert result["asound_cards"] == cards
    nodes = {Path(n["path"]).name: n for n in result["usb_devices"]}
    assert nodes["1-2"]["is_hub"] is True
    assert nodes["1-2.1"]["parent"] == nodes["1-2"]["path"]
    assert nodes["1-2.2"]["parent"] == nodes["1-2"]["path"]
    assert nodes["1-2.1"]["interfaces"][0]["interface_class"] == "01"
    assert nodes["1-2.1"]["serial_number"] == "SECRET"
    assert nodes["1-2.1"]["vendor_id"] == "1234"
    assert nodes["1-2.1"]["busnum"] == "1"
    assert result["serial_devices"][0]["usb_device"] == nodes["1-2.2"]["path"]
    card = result["audio_devices"][0]
    assert card["usb_device"] == nodes["1-2.1"]["path"]
    assert card["usb_interface"].endswith("1-2.1:1.0")
    assert card["id"] == "CODEC"
    assert card["name"] == "USB Audio CODEC"
    assert str(tmp_path) not in json.dumps(result)


def test_linux_missing_sources_and_broken_links_are_unavailable(tmp_path, monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_linux")
    monkeypatch.setattr(raw, "_read_asound_cards", Mock(side_effect=OSError("missing")))
    result = raw.read_raw_linux(str(tmp_path), timeout_s=5).to_json_dict()
    assert result["unavailable"]
    _linux_tree(tmp_path)
    (tmp_path / "class/tty/ttyUSB0/device").unlink()
    (tmp_path / "class/tty/ttyUSB0/device").symlink_to(tmp_path / "missing")
    result = raw.read_raw_linux(str(tmp_path), timeout_s=5).to_json_dict()
    assert any(item["part"].startswith("linux:tty") for item in result["unavailable"])
    assert len(result["usb_devices"]) == 3


def test_linux_virtual_tty_without_device_link_is_not_a_failure(tmp_path, monkeypatch):
    raw = import_module("rigplane.audio._usb_raw_linux")
    _linux_tree(tmp_path)
    (tmp_path / "class/tty/console").mkdir()
    (tmp_path / "class/tty/tty0").mkdir()
    monkeypatch.setattr(raw, "_read_asound_cards", lambda: "")
    result = raw.read_raw_linux(str(tmp_path), timeout_s=5)
    assert result.unavailable == ()
    assert [row["device"] for row in result.serial_devices] == ["/dev/ttyUSB0"]


@pytest.mark.parametrize("module", ["macos", "linux"])
@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_deadlines_fail_closed(module, timeout):
    raw = import_module(f"rigplane.audio._usb_raw_{module}")
    result = getattr(raw, f"read_raw_{module}")(timeout_s=timeout)
    assert result.unavailable


