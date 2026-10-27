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

from rigplane.audio import _usb_raw_windows
from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import PairOk, pair
from rigplane.audio._windows_devnode import (
    DEVPKEY_DEVICE_CLASS,
    DEVPKEY_DEVICE_COMPATIBLE_IDS,
    DEVPKEY_DEVICE_DESC,
    DEVPKEY_DEVICE_FRIENDLY_NAME,
    DEVPKEY_DEVICE_HARDWARE_IDS,
    DEVPKEY_DEVICE_PARENT,
    DevNodeRecord,
    DevPropKey,
    present_devnode_records,
)

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "usb_audio" / "windows" / "w1-gtr7-ic7300.json"
)


class FakeCfgMgr32:
    """Device tree served the way the cfgmgr32 calls see it.

    ``nodes`` rows are ``(instance_id, parent_id, class, friendly_name)``;
    ``None`` or ``""`` means the property is absent on that devnode.
    ``descriptions`` maps an instance id to its DeviceDesc.
    """

    def __init__(
        self,
        nodes: list[tuple[str, str, str | None, str | None]],
        descriptions: dict[str, str] | None = None,
        hardware_ids: dict[str, tuple[str, ...]] | None = None,
        compatible_ids: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.ids = [n[0] for n in nodes]
        self.list_props = {
            i: {
                DEVPKEY_DEVICE_HARDWARE_IDS: (hardware_ids or {}).get(n[0], ()),
                DEVPKEY_DEVICE_COMPATIBLE_IDS: (compatible_ids or {}).get(n[0], ()),
            }
            for i, n in enumerate(nodes)
        }
        self.props = {
            i: {
                DEVPKEY_DEVICE_PARENT: n[1] or None,
                DEVPKEY_DEVICE_CLASS: n[2] or None,
                DEVPKEY_DEVICE_FRIENDLY_NAME: n[3] or None,
                DEVPKEY_DEVICE_DESC: (descriptions or {}).get(n[0]),
            }
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

    def string_list_property(self, devnode: int, key: DevPropKey) -> tuple[str, ...]:
        return self.list_props[devnode][key]


def _gtr7() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["sessions"][0]


def _gtr7_cfgmgr32() -> FakeCfgMgr32:
    rows = _gtr7()["raw"]["windows"]["devnodes"]
    return FakeCfgMgr32(
        [
            (r["instance_id"], r["parent_id"], r["class_name"], r["friendly_name"])
            for r in rows
        ],
        descriptions={r["instance_id"]: r["device_desc"] for r in rows},
        hardware_ids={r["instance_id"]: tuple(r["hardware_ids"]) for r in rows},
        compatible_ids={r["instance_id"]: tuple(r["compatible_ids"]) for r in rows},
    )


def test_extended_records_keep_recorded_ids_and_separate_names() -> None:
    records = present_devnode_records(_gtr7_cfgmgr32())
    hub = next(r for r in records if r.instance_id.startswith(r"USB\VID_0451&PID_2046"))
    assert hub.class_name == "USB"
    assert hub.friendly_name == ""
    assert hub.device_desc == "Generic USB Hub"
    assert hub.compatible_ids == (r"USB\USB20_HUB",)
    serial = next(
        r for r in records if r.instance_id.startswith("USB\\VID_10C4&PID_EA60\\")
    )
    assert r"USB\VID_10C4&PID_EA60" in serial.hardware_ids
    assert serial.device_desc == "Silicon Labs CP210x USB to UART Bridge"
    assert serial.friendly_name == "Silicon Labs CP210x USB to UART Bridge (COM3)"


def test_device_id_property_keys_match_devpkey_header() -> None:
    assert bytes(DEVPKEY_DEVICE_HARDWARE_IDS.fmtid) == bytes(DEVPKEY_DEVICE_DESC.fmtid)
    assert bytes(DEVPKEY_DEVICE_COMPATIBLE_IDS.fmtid) == bytes(
        DEVPKEY_DEVICE_DESC.fmtid
    )
    assert DEVPKEY_DEVICE_HARDWARE_IDS.pid == 3
    assert DEVPKEY_DEVICE_COMPATIBLE_IDS.pid == 4


def test_gtr7_walk_matches_recorded_raw_devnodes() -> None:
    expected = _usb_raw_windows.RawWindows.from_json_dict(_gtr7()["raw"]["windows"])
    records = present_devnode_records(_gtr7_cfgmgr32())
    assert {r.instance_id: r for r in records} == {
        r.instance_id: r for r in expected.devnodes
    }
    assert len(records) == len({r.instance_id for r in records})


def test_gtr7_walk_pairs_the_recorded_ic7300_codec() -> None:
    session = _gtr7()
    records = present_devnode_records(_gtr7_cfgmgr32())
    results = []
    for rows in (records, list(reversed(records))):
        replay = {
            **session,
            "raw": {
                **session["raw"],
                "windows": _usb_raw_windows.RawWindows(
                    "cfgmgr32", tuple(rows)
                ).to_json_dict(),
            },
        }
        result = pair(adapt_windows(replay), "COM3", None)
        assert isinstance(result, PairOk)
        assert (result.rx_index, result.tx_index, result.hostapi) == (1, 5, "MME")
        assert result.codec_node.startswith("USB\\VID_08BB&PID_2901\\")
        results.append(result)
    assert results[0] == results[1] == pair(adapt_windows(session), "COM3", None)


def test_walk_visits_children_and_siblings_depth_first() -> None:
    api = FakeCfgMgr32(
        [
            ("HTREE\\ROOT\\0", "", None, None),
            ("PCI\\a", "HTREE\\ROOT\\0", "System", "Bridge"),
            ("USB\\b", "PCI\\a", "USB", "Hub"),
            ("PCI\\c", "htree\\root\\0", "Net", None),
            ("PCI\\d", "HTREE\\ROOT\\0", None, None),
        ],
        descriptions={"PCI\\c": "Ethernet", "PCI\\a": "unused"},
    )
    assert present_devnode_records(api) == [
        DevNodeRecord("HTREE\\ROOT\\0", "", "", "", "", (), ()),
        DevNodeRecord("PCI\\a", "HTREE\\ROOT\\0", "System", "Bridge", "unused", (), ()),
        DevNodeRecord("USB\\b", "PCI\\a", "USB", "Hub", "", (), ()),
        DevNodeRecord("PCI\\c", "htree\\root\\0", "Net", "", "Ethernet", (), ()),
        DevNodeRecord("PCI\\d", "HTREE\\ROOT\\0", "", "", "", (), ()),
    ]


def test_cfgmgr32_failure_falls_back_to_powershell_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stdout = (
        b"USB\\VID_0D8C&PID_0012\\SER\tPARENT\tPorts\tSerial (COM3)\tDevice\t\t\r\n"
    )

    def broken() -> list[DevNodeRecord]:
        raise OSError("cfgmgr32 unavailable")

    with (
        patch.object(_usb_raw_windows.platform, "system", return_value="Windows"),
        patch.object(_usb_raw_windows, "present_devnode_records", broken),
        patch.object(_usb_raw_windows.subprocess, "run") as run,
        caplog.at_level(logging.WARNING, logger=_usb_raw_windows.logger.name),
    ):
        run.return_value = subprocess.CompletedProcess([], 0, stdout)
        capture = _usb_raw_windows.read_raw_windows(4.0)

    assert capture.source == "powershell"
    assert capture.devnodes == (
        DevNodeRecord(
            r"USB\VID_0D8C&PID_0012\SER",
            "PARENT",
            "Ports",
            "Serial (COM3)",
            "Device",
            (),
            (),
        ),
    )
    assert run.call_count == 1
    assert any(
        "cfgmgr32" in r.getMessage() and "PowerShell" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_cfgmgr32_success_does_not_start_powershell() -> None:
    with (
        patch.object(_usb_raw_windows.platform, "system", return_value="Windows"),
        patch.object(
            _usb_raw_windows,
            "present_devnode_records",
            lambda: present_devnode_records(_gtr7_cfgmgr32()),
        ),
        patch.object(_usb_raw_windows.subprocess, "run") as run,
    ):
        capture = _usb_raw_windows.read_raw_windows(4.0)

    assert capture.source == "cfgmgr32"
    assert len(capture.devnodes) == len(_gtr7()["raw"]["windows"]["devnodes"])
    run.assert_not_called()


def test_non_windows_reads_nothing() -> None:
    with (
        patch.object(_usb_raw_windows.platform, "system", return_value="Darwin"),
        patch.object(_usb_raw_windows, "present_devnode_records") as walk,
        patch.object(_usb_raw_windows.subprocess, "run") as run,
    ):
        with pytest.raises(OSError, match="only available on Windows"):
            _usb_raw_windows.read_raw_windows(4.0)
    walk.assert_not_called()
    run.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="real cfgmgr32 needs Windows")
def test_real_cfgmgr32_reads_complete_records_and_is_fast() -> None:
    start = time.perf_counter()
    records = present_devnode_records()
    elapsed = time.perf_counter() - start
    assert records and records[0].parent_id == ""
    assert len(records) == len({r.instance_id for r in records})
    assert elapsed < 0.5
    capture = _usb_raw_windows.RawWindows("cfgmgr32", tuple(records))
    assert _usb_raw_windows.RawWindows.from_json_dict(capture.to_json_dict()) == capture
