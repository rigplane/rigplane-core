"""Windows PnP topology read from cfgmgr32 instead of PowerShell (MOR-3556)."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from rigplane.audio import _usb_resolve
from rigplane.audio._windows_devnode import (
    DEVPKEY_DEVICE_CLASS,
    DEVPKEY_NAME,
    DevPropKey,
    present_devnodes,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "windows_pnp_gtr7_ic7300.json"


class FakeCfgMgr32:
    """Device tree served the way the cfgmgr32 calls see it.

    ``nodes`` rows are ``(instance_id, parent_id, class, name)``; ``None``
    for class or name means the property is absent on that devnode.
    """

    def __init__(self, nodes: list[tuple[str, str, str | None, str | None]]) -> None:
        self.ids = [n[0] for n in nodes]
        self.props = {
            i: {DEVPKEY_DEVICE_CLASS: n[2], DEVPKEY_NAME: n[3]}
            for i, n in enumerate(nodes)
        }
        index = {n[0].upper(): i for i, n in enumerate(nodes)}
        self.children: dict[int, list[int]] = {}
        self.root_index: int | None = None
        for i, n in enumerate(nodes):
            if n[1]:
                self.children.setdefault(index[n[1].upper()], []).append(i)
            else:
                assert self.root_index is None
                self.root_index = i
        self.siblings: dict[int, int] = {}
        for kids in self.children.values():
            for a, b in zip(kids, kids[1:]):
                self.siblings[a] = b

    def root(self) -> int:
        assert self.root_index is not None
        return self.root_index

    def child(self, devnode: int) -> int | None:
        kids = self.children.get(devnode)
        return kids[0] if kids else None

    def sibling(self, devnode: int) -> int | None:
        return self.siblings.get(devnode)

    def device_id(self, devnode: int) -> str:
        return self.ids[devnode]

    def string_property(self, devnode: int, key: DevPropKey) -> str | None:
        return self.props[devnode][key]


def _gtr7() -> dict[str, list]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _gtr7_cfgmgr32() -> FakeCfgMgr32:
    """The recorded GTR7 tree as cfgmgr32 reports it.

    The capture came from Get-PnpDevice, whose InstanceId is upper-cased,
    while DEVPKEY_Device_Parent keeps the case the devnode was created with.
    cfgmgr32 returns that original case for every node, so a node referenced
    as a parent gets the spelling its children recorded.
    """
    tree = _gtr7()["tree"]
    spelled = {row[1].upper(): row[1] for row in tree if row[1]}
    return FakeCfgMgr32(
        [
            (spelled.get(i.upper(), i), parent, cls or None, name or None)
            for i, parent, cls, name in tree
        ]
    )


def _key(record: _usb_resolve.WindowsPnpDevice) -> tuple[str, ...]:
    return (
        record.pnp_device_id,
        record.parent_pnp_id,
        str(record.vid),
        str(record.pid),
        str(record.com_port),
        str(record.audio_endpoint_name),
    )


def test_gtr7_walk_matches_recorded_powershell_records() -> None:
    fixture = _gtr7()
    old = _usb_resolve._pnp_records_from_powershell_text(
        "\r\n".join(fixture["powershell_stdout"])
    )
    new = _usb_resolve._pnp_records_from_devnodes(present_devnodes(_gtr7_cfgmgr32()))

    assert len(old) == 19
    assert sorted(map(_key, new)) == sorted(map(_key, old))
    assert len(new) == len({r.pnp_device_id for r in new})
    serial = next(r for r in new if r.com_port == "COM3")
    assert serial.pnp_device_id == "USB\\VID_10C4&PID_EA60\\IC-7300_00000001"
    assert serial.parent_pnp_id == "USB\\VID_0451&PID_2046\\6&3878ef1f&0&2"
    assert {
        r.audio_endpoint_name for r in new if r.pnp_device_id.startswith("SWD\\")
    } >= {"Speakers (USB Audio CODEC )", "Microphone (USB Audio CODEC )"}


def test_gtr7_walk_resolves_the_same_ic7300_codec() -> None:
    from types import SimpleNamespace

    fixture = _gtr7()
    old = _usb_resolve._pnp_records_from_powershell_text(
        "\r\n".join(fixture["powershell_stdout"])
    )
    new = _usb_resolve._pnp_records_from_devnodes(present_devnodes(_gtr7_cfgmgr32()))
    devices = [
        {
            "name": "USB Audio CODEC",
            "hostapi": 3,
            "max_input_channels": 0,
            "max_output_channels": 2,
        },
        {
            "name": "USB Audio CODEC",
            "hostapi": 3,
            "max_input_channels": 2,
            "max_output_channels": 0,
        },
    ]
    sd = SimpleNamespace(query_devices=lambda: devices)
    results = [
        _usb_resolve._resolve_windows(
            "COM3", sounddevice_module=sd, pnp_query=lambda records=records: records
        )
        for records in (old, list(reversed(new)), new)
    ]
    assert results[0] is not None
    assert (results[0].rx_device_index, results[0].tx_device_index) == (1, 0)
    assert results[1] == results[0] == results[2]


def test_walk_visits_children_and_siblings_depth_first() -> None:
    api = FakeCfgMgr32(
        [
            ("HTREE\\ROOT\\0", "", None, None),
            ("PCI\\a", "HTREE\\ROOT\\0", "System", "Bridge"),
            ("USB\\b", "PCI\\a", "USB", "Hub"),
            ("PCI\\c", "HTREE\\ROOT\\0", "Net", None),
        ]
    )
    assert present_devnodes(api) == [
        ("HTREE\\ROOT\\0", "", "", ""),
        ("PCI\\a", "HTREE\\ROOT\\0", "System", "Bridge"),
        ("USB\\b", "PCI\\a", "USB", "Hub"),
        ("PCI\\c", "HTREE\\ROOT\\0", "Net", ""),
    ]


def test_records_follow_powershell_selection_rules() -> None:
    hub = "USB\\VID_0451&PID_2046\\6&abc&0&2"
    composite = "USB\\VID_08BB&PID_2901\\7&def&1&4"
    nodes = [
        ("HTREE\\ROOT\\0", "", "", ""),
        ("PCI\\VEN_1022\\3&x", "HTREE\\ROOT\\0", "System", "Bridge"),
        ("USB\\ROOT_HUB30\\5&r", "PCI\\VEN_1022\\3&x", "USB", "Root Hub"),
        ("USB\\VID_CCCC&PID_0003\\3", "USB\\ROOT_HUB30\\5&r", "USB", "Top Hub"),
        ("USB\\VID_AAAA&PID_0001\\1", "USB\\VID_CCCC&PID_0003\\3", "USB", "Outer Hub"),
        ("USB\\VID_BBBB&PID_0002\\2", "USB\\VID_AAAA&PID_0001\\1", "USB", "Mid Hub"),
        (hub, "USB\\VID_BBBB&PID_0002\\2", "USB", "Generic USB Hub"),
        (composite, hub, "USB", "USB Composite Device"),
        (
            "USB\\VID_08BB&PID_2901&MI_00\\8&ghi&0&0000",
            composite,
            "MEDIA",
            "USB Audio CODEC ",
        ),
        ("USB\\VID_10C4&PID_EA60\\IC-7300_x", hub, "Ports", "CP210x (com3)"),
        ("ROOT\\SYSTEM\\0003", "HTREE\\ROOT\\0", "System", "Bus"),
        ("FABULATECH\\VSP\\1", "ROOT\\SYSTEM\\0003", "Ports", "Virtual (COM8)"),
        ("USB\\VID_046D&PID_C52B\\9", hub, "HIDClass", "Receiver"),
        (
            "SWD\\MMDEVAPI\\{a}",
            "USB\\VID_08BB&PID_2901&MI_00\\8&ghi&0&0000",
            "audioendpoint",
            "Speakers (USB Audio CODEC )",
        ),
    ]
    records = _usb_resolve._pnp_records_from_devnodes(nodes)
    by_id = {r.pnp_device_id: r for r in records}

    # Eligible nodes plus at most three USB\VID_ ancestors; ids upper-cased
    # like Get-PnpDevice InstanceId, parents spelled as the devnode records them.
    assert set(by_id) == {
        "USB\\VID_08BB&PID_2901&MI_00\\8&GHI&0&0000",
        composite.upper(),
        hub.upper(),
        "USB\\VID_BBBB&PID_0002\\2",
        "USB\\VID_10C4&PID_EA60\\IC-7300_X",
        "FABULATECH\\VSP\\1",
        "SWD\\MMDEVAPI\\{A}",
    }
    assert by_id[composite.upper()].parent_pnp_id == hub
    assert by_id["USB\\VID_08BB&PID_2901&MI_00\\8&GHI&0&0000"].audio_endpoint_name == (
        "USB Audio CODEC"
    )
    # Get-PnpDevice matching is case-insensitive; the COM parser is not.
    assert by_id["USB\\VID_10C4&PID_EA60\\IC-7300_X"].com_port is None
    assert by_id["FABULATECH\\VSP\\1"].com_port == "COM8"
    assert by_id["SWD\\MMDEVAPI\\{A}"].audio_endpoint_name == (
        "Speakers (USB Audio CODEC )"
    )


def test_cfgmgr32_failure_falls_back_to_powershell_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stdout = b"USB\\VID_0D8C&PID_0012\\SER\tPARENT\tPorts\tSerial (COM3)\r\n"

    def broken() -> list[tuple[str, str, str, str]]:
        raise OSError("cfgmgr32 unavailable")

    with (
        patch.object(_usb_resolve.platform, "system", return_value="Windows"),
        patch.object(_usb_resolve, "present_devnodes", broken),
        patch.object(_usb_resolve.subprocess, "run") as run,
        caplog.at_level(logging.WARNING, logger=_usb_resolve.logger.name),
    ):
        run.return_value = subprocess.CompletedProcess([], 0, stdout)
        records = _usb_resolve._query_windows_pnp_devices()

    assert [r.com_port for r in records] == ["COM3"]
    assert run.call_count == 1
    assert any(
        "cfgmgr32" in r.getMessage() and "PowerShell" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_cfgmgr32_success_does_not_start_powershell() -> None:
    with (
        patch.object(_usb_resolve.platform, "system", return_value="Windows"),
        patch.object(
            _usb_resolve,
            "present_devnodes",
            lambda: present_devnodes(_gtr7_cfgmgr32()),
        ),
        patch.object(_usb_resolve.subprocess, "run") as run,
    ):
        records = _usb_resolve._query_windows_pnp_devices()

    assert len(records) == 19
    run.assert_not_called()


def test_non_windows_reads_nothing() -> None:
    with (
        patch.object(_usb_resolve.platform, "system", return_value="Darwin"),
        patch.object(_usb_resolve, "present_devnodes") as walk,
        patch.object(_usb_resolve.subprocess, "run") as run,
    ):
        assert _usb_resolve._query_windows_pnp_devices() == []
    walk.assert_not_called()
    run.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="real cfgmgr32 needs Windows")
def test_real_cfgmgr32_matches_powershell_and_is_fast() -> None:
    start = time.perf_counter()
    nodes = present_devnodes()
    elapsed = time.perf_counter() - start
    new = _usb_resolve._pnp_records_from_devnodes(nodes)
    old = _usb_resolve._query_windows_pnp_devices_powershell()

    assert nodes and nodes[0][1] == ""
    assert elapsed < 0.5
    assert sorted(map(_key, new)) == sorted(map(_key, old))
