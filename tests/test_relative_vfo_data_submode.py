"""MOR-3821 P0e: numeric DATA sub-mode on the relative-VFO mode surface.

``RelativeVfoModeWritable.write_relative_vfo_mode`` takes ``data`` as a
boolean (legacy DATA1 on/off) or as an explicit numeric DATA sub-mode
bounded by the profile's ``data_mode_count``, with write AND readback
through the capability — no per-radio branch. The IC-7610 case runs over
the LAN simulator (``rigplane_testkit/icom/simulator.py``): a split write
of DATA2 asserts B's exact sub-mode and its LAN MOD input before any
key-down. An IC-7300 profile (count 1) pins the bound, and the Yaesu
backend is unaffected by the widened surface: DATA stays a boolean mode
pair there, any integer is refused.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from _perf_helpers import fast_connect

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands import parse_civ_frame
from rigplane.commands.commander import IcomCommander
from rigplane.core.command_dispatch import prepare_command_intent
from rigplane.core.exceptions import CommandError
from rigplane.core.types import CivFrame, Mode
from rigplane.radio import IcomRadio
from rigplane_testkit.icom.simulator import MockIcomRadio
from test_yaesu_relative_vfo import RelativePeer


def _reply(addr: int, payload: str) -> CivFrame:
    return parse_civ_frame(bytes.fromhex(f"FE FE E0 {addr:02X} {payload} FD"))


class _SubModeBackend:
    """CI-V peer tracking the unselected VFO's ``(mode, data, filter)``."""

    def __init__(self, addr: int) -> None:
        self.addr = addr
        self.frames: list[bytes] = []
        self.mode = 0x01
        self.data = 0x00
        self.filter = 0x01

    async def __call__(
        self,
        payload: bytes,
        wait_response: bool = True,
        is_current: object = None,
    ) -> CivFrame:
        del wait_response
        frame = parse_civ_frame(payload)
        if frame.command == 0x25 and len(frame.data) == 1:
            return _reply(self.addr, f"25 {frame.data[0]:02X} 00 40 07 14 00")
        if frame.command == 0x26 and len(frame.data) == 1:
            return _reply(
                self.addr,
                f"26 {frame.data[0]:02X} {self.mode:02X} "
                f"{self.data:02X} {self.filter:02X}",
            )
        if frame.command == 0x26 and len(frame.data) > 1:
            self.frames.append(payload)
            self.mode = frame.data[1]
            self.data = frame.data[2]
            self.filter = frame.data[3]
            return _reply(self.addr, "FB")
        self.frames.append(payload)
        return _reply(self.addr, "FB")


def _mode_sets(frames: list[bytes]) -> list[bytes]:
    sets: list[bytes] = []
    for raw in frames:
        parsed = parse_civ_frame(raw)
        if parsed.command == 0x26 and len(parsed.data) > 1:
            sets.append(parsed.data)
    return sets


def _wired_radio(
    model: str, backend: _SubModeBackend, monkeypatch: pytest.MonkeyPatch
) -> tuple[IcomRadio, IcomCommander]:
    radio = IcomRadio("127.0.0.1", model=model)
    commander = IcomCommander(backend)
    radio._commander = commander
    radio._connected = True
    radio._civ_transport = object()
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    commander.start()
    return radio, commander


async def test_split_write_selects_data2_with_lan_input_on_ic7610() -> None:
    """IC-7610 over the LAN simulator: B's exact sub-mode and MOD input."""
    sim = MockIcomRadio()
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=2.0,
        model="IC-7610",
    )
    try:
        with fast_connect():
            await radio.connect()
        assert radio.profile.data_mode_count == 3
        await radio.set_data1_mod_input(0)  # MIC
        await radio.set_data2_mod_input(5)  # LAN
        observed = await radio.write_relative_vfo_mode(
            Mode.USB.value, selected=False, data=2
        )
        assert observed.mode == "USB"
        assert observed.data_mode == 2
        back = await radio.read_relative_vfo(selected=False)
        assert back.mode == "USB"
        assert back.data_mode == 2
        assert await radio.get_data1_mod_input() == 0
        assert await radio.get_data2_mod_input() == 5
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


async def test_split_write_accepts_data1_on_a_count_1_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IC-7300 profile (count 1): bool and 1 write; 2 never reaches the wire."""
    backend = _SubModeBackend(0x94)
    radio, commander = _wired_radio("IC-7300", backend, monkeypatch)
    try:
        assert radio.profile.data_mode_count == 1
        observed = await radio.write_relative_vfo_mode(
            Mode.USB.value, selected=False, data=1
        )
        assert observed.data_mode == 1
        assert _mode_sets(backend.frames) == [bytes([0x01, 0x01, 0x01, 0x01])]
        observed_true = await radio.write_relative_vfo_mode(
            Mode.USB.value, selected=False, data=True
        )
        assert observed_true.data_mode == 1
        sets_before = len(_mode_sets(backend.frames))
        with pytest.raises(CommandError):
            await radio.write_relative_vfo_mode(Mode.USB.value, selected=False, data=2)
        assert len(_mode_sets(backend.frames)) == sets_before
    finally:
        radio._connected = False
        await commander.stop()


async def test_split_write_bounded_by_data_mode_count_on_ic7610(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IC-7610 profile (count 3): 3 writes and reads back; 4 is refused."""
    backend = _SubModeBackend(0x98)
    radio, commander = _wired_radio("IC-7610", backend, monkeypatch)
    try:
        observed = await radio.write_relative_vfo_mode(
            Mode.USB.value, selected=False, data=3
        )
        assert observed.data_mode == 3
        with pytest.raises(CommandError):
            await radio.write_relative_vfo_mode(Mode.USB.value, selected=False, data=4)
        with pytest.raises(CommandError):
            await radio.write_relative_vfo_mode(
                Mode.USB.value,
                selected=False,
                data="2",
            )
    finally:
        radio._connected = False
        await commander.stop()


async def test_yaesu_relative_mode_keeps_data_boolean_only() -> None:
    """Yaesu has no numbered sub-modes: booleans select the DATA partner,
    any integer is refused (pins test_relative_mode_rejects_non_boolean_data)."""
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    peer = RelativePeer()
    radio._transport.query = AsyncMock(side_effect=peer.query)
    radio._transport.write = AsyncMock(side_effect=peer.write)
    observed = await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=True)
    assert observed.data_mode == 1
    assert peer.modes[1] == "C"
    writes_before = list(peer.writes)
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=1)
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(Mode.USB, selected=False, data=2)
    assert peer.writes == writes_before


class _AnyRadio:
    """DispatchRadio double: every descriptor method is supported."""

    def supports_command(self, command: str, *, receiver: int | None = None) -> bool:
        del command, receiver
        return True


def test_split_mode_binder_accepts_a_numeric_data_submode() -> None:
    intent = prepare_command_intent(
        _AnyRadio(),
        "set_split_mode",
        {"mode": Mode.USB.value, "data": 2, "filter_width": None},
        source="test",
        command_id="split-mode-data-2",
        session_id=None,
    )
    assert intent.params["data"] == 2


def test_split_mode_binder_rejects_non_integer_data() -> None:
    with pytest.raises(ValueError, match="data"):
        prepare_command_intent(
            _AnyRadio(),
            "set_split_mode",
            {"mode": Mode.USB.value, "data": "2", "filter_width": None},
            source="test",
            command_id="split-mode-data-text",
            session_id=None,
        )
