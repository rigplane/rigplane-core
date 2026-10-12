"""Snapshot/restore must only carry confirmed radio readings (MOR-1818).

Calls the real ``rigplane.runtime.radio_state_snapshot`` functions against a
hand-written fake radio: a failing getter (or a missing one) omits the key,
and ``restore_state`` then touches no setter for the absent keys.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime.radio_state_snapshot import restore_state, snapshot_state
from rigplane.types import Mode
from test_radio import MockTransport


class _SnapshotFakeRadio:
    """Minimal fake exposing the getters/setters snapshot/restore use.

    ``fail`` names getters that raise instead of answering. The ``_last_*``
    / ``_attenuator_state`` / ``_preamp_level`` / ``_filter_width`` mirrors
    hold decoy values: snapshot must never record them.
    """

    def __init__(self, *, fail: tuple[str, ...] = ()) -> None:
        self.fail = set(fail)
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._last_freq_hz: int | None = 7_074_000
        self._last_mode: Mode | None = Mode.LSB
        self._last_power: int | None = 100
        self._filter_width: int | None = 1
        self._last_split: bool | None = True
        self._last_vfo: str | None = "VFOB"
        self._attenuator_state: bool | None = True
        self._preamp_level: int | None = 2

    def _check_connected(self) -> None:
        return None

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise TimeoutError(f"{name} failed")

    async def get_freq(self) -> int:
        self._maybe_fail("get_freq")
        return 14_074_000

    async def get_mode_info(self) -> tuple[Mode, int | None]:
        self._maybe_fail("get_mode_info")
        return (Mode.USB, 1)

    async def get_rf_power(self) -> int:
        self._maybe_fail("get_rf_power")
        return 25

    async def get_split(self) -> bool:
        self._maybe_fail("get_split")
        return True

    async def get_vfo_slot(self, receiver: int = 0) -> str:
        self._maybe_fail("get_vfo_slot")
        return "B"

    async def get_attenuator(self, receiver: int = 0) -> bool:
        self._maybe_fail("get_attenuator")
        return True

    async def get_preamp(self, receiver: int = 0) -> int:
        self._maybe_fail("get_preamp")
        return 2

    async def get_vox(self) -> bool:
        self._maybe_fail("get_vox")
        return False

    async def get_data_mode(self) -> bool:
        self._maybe_fail("get_data_mode")
        return True

    async def get_data_off_mod_input(self) -> int:
        self._maybe_fail("get_data_off_mod_input")
        return 3

    async def get_data1_mod_input(self) -> int:
        self._maybe_fail("get_data1_mod_input")
        return 4

    async def set_split(self, on: bool) -> None:
        self.calls.append(("set_split", (on,)))

    async def _set_vfo_wire(self, vfo: str) -> None:
        self.calls.append(("_set_vfo_wire", (vfo,)))

    async def set_rf_power(self, level: int) -> None:
        self.calls.append(("set_rf_power", (level,)))

    async def set_mode(self, mode: Mode, filter_width: int | None = None) -> None:
        self.calls.append(("set_mode", (mode, filter_width)))

    async def set_freq(self, freq_hz: int) -> None:
        self.calls.append(("set_freq", (freq_hz,)))

    async def set_attenuator(self, on: bool) -> None:
        self.calls.append(("set_attenuator", (on,)))

    async def set_preamp(self, level: int = 1) -> None:
        self.calls.append(("set_preamp", (level,)))

    async def set_vox(self, on: bool) -> None:
        self.calls.append(("set_vox", (on,)))

    async def set_data_mode(self, on: bool) -> None:
        self.calls.append(("set_data_mode", (on,)))

    async def set_data_off_mod_input(self, value: int) -> None:
        self.calls.append(("set_data_off_mod_input", (value,)))

    def called(self, name: str) -> bool:
        return any(call[0] == name for call in self.calls)


async def test_snapshot_omits_freq_mode_filter_power_when_reads_fail() -> None:
    """Failed freq/mode/power reads record nothing, not the mirrors."""
    radio = _SnapshotFakeRadio(fail=("get_freq", "get_mode_info", "get_rf_power"))

    snap = await snapshot_state(radio)  # type: ignore[arg-type]

    assert "frequency" not in snap
    assert "mode" not in snap
    assert "filter" not in snap
    assert "power" not in snap


async def test_snapshot_records_split_vfo_attenuator_preamp_from_getters() -> None:
    """Split/VFO/attenuator/preamp come from the public getters."""
    radio = _SnapshotFakeRadio()

    snap = await snapshot_state(radio)  # type: ignore[arg-type]

    assert snap["split"] is True
    assert snap["vfo"] == "B"
    assert snap["attenuator"] is True
    assert snap["preamp"] == 2


@pytest.mark.parametrize(
    "getter", ["get_split", "get_vfo_slot", "get_attenuator", "get_preamp"]
)
async def test_snapshot_omits_key_when_its_read_fails(getter: str) -> None:
    """A failed split/VFO/attenuator/preamp read omits the key, not mirrors."""
    radio = _SnapshotFakeRadio(fail=(getter,))
    key = {
        "get_split": "split",
        "get_vfo_slot": "vfo",
        "get_attenuator": "attenuator",
        "get_preamp": "preamp",
    }[getter]

    snap = await snapshot_state(radio)  # type: ignore[arg-type]

    assert key not in snap


@pytest.mark.parametrize(
    "getter,key",
    [
        ("get_split", "split"),
        ("get_vfo_slot", "vfo"),
        ("get_attenuator", "attenuator"),
        ("get_preamp", "preamp"),
    ],
)
async def test_snapshot_omits_key_when_radio_has_no_getter(
    getter: str, key: str
) -> None:
    """A key with no public getter is omitted, never taken from a mirror."""
    radio = _SnapshotFakeRadio()
    setattr(radio, getter, None)

    snap = await snapshot_state(radio)  # type: ignore[arg-type]

    assert key not in snap


async def test_restore_skips_absent_keys_and_restores_present_ones() -> None:
    """Restore touches no setter for absent keys; present keys as today."""
    radio = _SnapshotFakeRadio(
        fail=(
            "get_freq",
            "get_mode_info",
            "get_rf_power",
            "get_split",
            "get_vfo_slot",
            "get_attenuator",
            "get_preamp",
        )
    )
    snap = await snapshot_state(radio)  # type: ignore[arg-type]
    assert "frequency" not in snap

    radio.calls.clear()
    await restore_state(radio, snap)  # type: ignore[arg-type]

    for setter in (
        "set_freq",
        "set_mode",
        "set_rf_power",
        "set_split",
        "_set_vfo_wire",
        "set_attenuator",
        "set_preamp",
    ):
        assert not radio.called(setter), setter
    assert radio.called("set_vox")
    assert radio.called("set_data_mode")
    assert radio.called("set_data_off_mod_input")

    full = _SnapshotFakeRadio()
    full_snap = await snapshot_state(full)  # type: ignore[arg-type]
    await restore_state(full, full_snap)  # type: ignore[arg-type]

    assert ("set_freq", (14_074_000,)) in full.calls
    assert ("set_split", (True,)) in full.calls
    assert ("_set_vfo_wire", ("B",)) in full.calls
    assert ("set_attenuator", (True,)) in full.calls
    assert ("set_preamp", (2,)) in full.calls


async def test_snapshot_with_real_radio_omits_unconfirmed_split_attenuator_preamp_and_vfo() -> None:
    """Every read timing out on a real IcomRadio: no split/attenuator/preamp/vfo keys.

    The setter-written mirrors (``_last_split``, ``_attenuator_state``,
    ``_preamp_level``) are set, and no reply is queued, so each read times
    out. The snapshot must record none of them — and no ``vfo`` key, since
    no radio read exists for the VFO slot — and restoring it must call none
    of their setters.
    """
    transport = MockTransport()
    radio = IcomRadio("192.168.1.100", timeout=0.05, model="IC-7610")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    try:
        radio._last_split = True
        radio._attenuator_state = True
        radio._preamp_level = 2

        snap = await snapshot_state(radio)

        assert "split" not in snap
        assert "attenuator" not in snap
        assert "preamp" not in snap
        assert "vfo" not in snap

        with (
            patch.object(radio, "set_split", new=AsyncMock()) as set_split,
            patch.object(radio, "_set_vfo_wire", new=AsyncMock()) as set_vfo,
            patch.object(
                radio, "set_attenuator", new=AsyncMock()
            ) as set_attenuator,
            patch.object(radio, "set_preamp", new=AsyncMock()) as set_preamp,
        ):
            await restore_state(radio, snap)

        set_split.assert_not_awaited()
        set_vfo.assert_not_awaited()
        set_attenuator.assert_not_awaited()
        set_preamp.assert_not_awaited()
    finally:
        radio._connected = False
