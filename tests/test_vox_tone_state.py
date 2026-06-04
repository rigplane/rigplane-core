"""Tests for VOX and Tone/TSQL state fields, CI-V parsing, poller polling, and
optimistic state updates (Issues #404, #406)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState, ReceiverState
from rigplane.rigctld.state_cache import StateCache
from rigplane.types import CivFrame
from rigplane.web.radio_poller import (
    CommandQueue,
    RadioPoller,
    SetAntiVoxGain,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetToneFreq,
    SetTsqlFreq,
    SetVoxDelay,
    SetVoxGain,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(
    cmd: int,
    sub: int | None = None,
    data: bytes = b"",
    receiver: int | None = None,
) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=cmd,
        sub=sub,
        data=data,
        receiver=receiver,
    )


def _make_radio_with_state() -> IcomRadio:
    """IcomRadio with RadioState wired up for _update_radio_state_from_frame tests."""
    from test_civ_rx_coverage import MockTransport  # type: ignore[import]

    r = IcomRadio("192.168.1.100")
    r._civ_transport = MockTransport()
    r._ctrl_transport = r._civ_transport
    r._connected = True
    r._radio_state = RadioState()
    return r


def _make_poller(*, with_state: bool = True) -> tuple[RadioPoller, RadioState]:
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    # Fill AdvancedControlCapable protocol
    from rigplane.radio_protocol import AdvancedControlCapable as _ACC

    try:
        from typing import get_protocol_members as _gpm

        _proto_attrs = _gpm(_ACC)
    except ImportError:
        import typing as _t

        _proto_attrs = _t._get_protocol_attrs(_ACC)  # type: ignore[attr-defined]
    for _attr in _proto_attrs:
        if _attr not in vars(radio):
            setattr(radio, _attr, AsyncMock())

    state = RadioState() if with_state else None
    poller = RadioPoller(
        radio,
        StateCache(),
        CommandQueue(),
        radio_state=state,
    )
    return poller, state  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# RadioState / ReceiverState new fields
# ---------------------------------------------------------------------------


def test_receiver_state_has_repeater_tone_and_tsql_fields() -> None:
    rx = ReceiverState()
    assert rx.repeater_tone is False
    assert rx.repeater_tsql is False
    assert rx.tone_freq == 0
    assert rx.tsql_freq == 0


def test_radio_state_has_vox_delay_field() -> None:
    rs = RadioState()
    assert rs.vox_delay == 0


def test_radio_state_to_dict_includes_vox_delay() -> None:
    rs = RadioState()
    rs.vox_delay = 5
    d = rs.to_dict()
    assert d["vox_delay"] == 5


def test_radio_state_to_dict_includes_receiver_tone_tsql_via_asdict() -> None:
    """repeater_tone/repeater_tsql are in main/sub via asdict()."""
    rs = RadioState()
    rs.main.repeater_tone = True
    rs.sub.tsql_freq = 8850
    d = rs.to_dict()
    assert d["main"]["repeater_tone"] is True
    assert d["sub"]["tsql_freq"] == 8850


# ---------------------------------------------------------------------------
# CI-V parsing: 0x16 0x42 / 0x43 — repeater tone / TSQL
# ---------------------------------------------------------------------------


def test_civ_rx_0x16_0x42_sets_repeater_tone_main(
    tmp_path: object,
) -> None:
    """0x16 0x42 with receiver=0 observes main repeater_tone (MOR-451).

    The legacy RadioState mirror was removed; the StateStore is the source of
    truth and the ReceiverState mirror stays at its default.
    """
    r = _make_radio_with_state()
    rs = r._radio_state
    frame = _make_frame(cmd=0x16, sub=0x42, data=bytes([0x01]), receiver=0x00)
    r._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.main.repeater_tone is False
    field = r._state_store.snapshot().field("receiver.0.operator_toggles.repeater_tone")
    assert field.value is True


def test_civ_rx_0x16_0x42_sets_repeater_tone_off(tmp_path: object) -> None:
    """0x16 0x42 off observation lands in the store (MOR-451)."""
    r = _make_radio_with_state()
    frame = _make_frame(cmd=0x16, sub=0x42, data=bytes([0x00]), receiver=0x00)
    r._civ_runtime._update_state_cache_from_frame(frame)
    field = r._state_store.snapshot().field("receiver.0.operator_toggles.repeater_tone")
    assert field.value is False


def test_civ_rx_0x16_0x43_sets_repeater_tsql_sub(tmp_path: object) -> None:
    """0x16 0x43 with receiver=1 observes sub repeater_tsql (MOR-451)."""
    r = _make_radio_with_state()
    rs = r._radio_state
    frame = _make_frame(cmd=0x16, sub=0x43, data=bytes([0x01]), receiver=0x01)
    r._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.sub.repeater_tsql is False
    field = r._state_store.snapshot().field("receiver.1.operator_toggles.repeater_tsql")
    assert field.value is True


def test_civ_rx_0x16_0x42_notify_event(tmp_path: object) -> None:
    """Unsolicited 0x16 0x42 triggers repeater_tone_changed event."""
    r = _make_radio_with_state()
    events: list[tuple[str, object]] = []
    r._on_state_change = lambda name, data: events.append((name, data))
    frame = _make_frame(cmd=0x16, sub=0x42, data=bytes([0x01]), receiver=0x00)
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert any(name == "repeater_tone_changed" for name, _ in events)


def test_civ_rx_0x16_0x43_notify_event(tmp_path: object) -> None:
    """Unsolicited 0x16 0x43 triggers repeater_tsql_changed event."""
    r = _make_radio_with_state()
    events: list[tuple[str, object]] = []
    r._on_state_change = lambda name, data: events.append((name, data))
    frame = _make_frame(cmd=0x16, sub=0x43, data=bytes([0x00]), receiver=0x00)
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert any(name == "repeater_tsql_changed" for name, _ in events)


# ---------------------------------------------------------------------------
# CI-V parsing: 0x1B 0x00 / 0x01 — tone / TSQL frequency
# ---------------------------------------------------------------------------


def _bcd_tone_freq(hundreds: int, tens_units: int, tenths: int) -> bytes:
    """3-byte BCD encoding: [hundreds, tens+units, tenths digit]."""
    h = ((hundreds // 10) << 4) | (hundreds % 10)
    tu = ((tens_units // 10) << 4) | (tens_units % 10)
    t = ((tenths // 10) << 4) | (tenths % 10)
    return bytes([h, tu, t])


def test_civ_rx_0x1b_0x00_sets_tone_freq_main(tmp_path: object) -> None:
    """0x1B 0x00 with receiver=0 observes main tone_freq in centihz (MOR-451).

    The legacy RadioState mirror was removed; the StateStore is the source of
    truth and the ReceiverState mirror stays at its default 0.
    """
    r = _make_radio_with_state()
    rs = r._radio_state
    # 88.5 Hz → [0x00, 0x88, 0x05]
    data = _bcd_tone_freq(0, 88, 5)
    frame = _make_frame(cmd=0x1B, sub=0x00, data=data, receiver=0x00)
    r._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.main.tone_freq == 0
    field = r._state_store.snapshot().field("receiver.0.operator_controls.tone_freq")
    assert field.value == 8850  # 88.50 Hz in centihz


def test_civ_rx_0x1b_0x01_sets_tsql_freq_sub(tmp_path: object) -> None:
    """0x1B 0x01 with receiver=1 observes sub tsql_freq in centihz (MOR-451)."""
    r = _make_radio_with_state()
    rs = r._radio_state
    # 100.0 Hz → [0x01, 0x00, 0x00]
    data = _bcd_tone_freq(1, 0, 0)
    frame = _make_frame(cmd=0x1B, sub=0x01, data=data, receiver=0x01)
    r._civ_runtime._update_state_cache_from_frame(frame)
    assert rs.sub.tsql_freq == 0
    field = r._state_store.snapshot().field("receiver.1.operator_controls.tsql_freq")
    assert field.value == 10000  # 100.00 Hz in centihz


def test_civ_rx_0x1b_short_data_ignored(tmp_path: object) -> None:
    """0x1B with fewer than 3 data bytes does not update state."""
    r = _make_radio_with_state()
    rs = r._radio_state
    frame = _make_frame(cmd=0x1B, sub=0x00, data=bytes([0x00, 0x88]), receiver=0x00)
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.main.tone_freq == 0  # unchanged


# ---------------------------------------------------------------------------
# CI-V parsing: 0x14 0x16 / 0x17 — vox_gain / anti_vox_gain
# ---------------------------------------------------------------------------


def _bcd_level(value: int) -> bytes:
    """2-byte BCD for 0-255 level (stored as 4-digit BCD, e.g. 128 → 0x01 0x28)."""
    d = f"{value:04d}"
    return bytes([(int(d[0]) << 4) | int(d[1]), (int(d[2]) << 4) | int(d[3])])


def test_civ_rx_0x14_0x16_sets_vox_gain(tmp_path: object) -> None:
    """0x14 0x16 updates rs.vox_gain."""
    r = _make_radio_with_state()
    rs = r._radio_state
    frame = _make_frame(cmd=0x14, sub=0x16, data=_bcd_level(128))
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.vox_gain == 128


def test_civ_rx_0x14_0x17_sets_anti_vox_gain(tmp_path: object) -> None:
    """0x14 0x17 updates rs.anti_vox_gain."""
    r = _make_radio_with_state()
    rs = r._radio_state
    frame = _make_frame(cmd=0x14, sub=0x17, data=_bcd_level(64))
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.anti_vox_gain == 64


# ---------------------------------------------------------------------------
# CI-V parsing: 0x1A 0x05 0x02 0x92 — vox_delay (ctl_mem)
# ---------------------------------------------------------------------------


def _ctl_mem_bcd(prefix: bytes, value: int) -> bytes:
    """Build 0x1A 0x05 data: prefix + 1-byte BCD value."""
    d = f"{value:02d}"
    bcd = bytes([(int(d[0]) << 4) | int(d[1])])
    return prefix + bcd


def test_civ_rx_0x1a_0x05_vox_delay(tmp_path: object) -> None:
    """0x1A 0x05 with prefix 0x02 0x92 updates rs.vox_delay."""
    r = _make_radio_with_state()
    rs = r._radio_state
    data = _ctl_mem_bcd(b"\x02\x92", 10)  # 10 = 1.0 sec
    frame = _make_frame(cmd=0x1A, sub=0x05, data=data)
    r._civ_runtime._update_radio_state_from_frame(frame)
    assert rs.vox_delay == 10


# ---------------------------------------------------------------------------
# Polling queries: repeater_tone, repeater_tsql, tone_freq, tsql_freq included
# ---------------------------------------------------------------------------


def test_build_state_queries_includes_repeater_tone_and_tsql() -> None:
    """_build_state_queries includes 0x16/0x42, 0x16/0x43, 0x1B/0x00, 0x1B/0x01."""
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    queries = poller._STATE_QUERIES  # noqa: SLF001
    cmd_sub_pairs = {(cmd, sub) for cmd, sub, _ in queries}
    assert (0x16, 0x42) in cmd_sub_pairs, "repeater_tone not polled"
    assert (0x16, 0x43) in cmd_sub_pairs, "repeater_tsql not polled"
    assert (0x1B, 0x00) in cmd_sub_pairs, "tone_freq not polled"
    assert (0x1B, 0x01) in cmd_sub_pairs, "tsql_freq not polled"
    assert (0x14, 0x16) in cmd_sub_pairs, "vox_gain not polled"
    assert (0x14, 0x17) in cmd_sub_pairs, "anti_vox_gain not polled"


def test_build_state_queries_includes_notch_width() -> None:
    """_build_state_queries includes 0x16/0x57 (manual notch width) for IC-7610."""
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    queries = poller._STATE_QUERIES  # noqa: SLF001
    cmd_sub_pairs = {(cmd, sub) for cmd, sub, _ in queries}
    assert (0x16, 0x57) in cmd_sub_pairs, "manual notch width (0x16/0x57) not polled"


def test_build_state_queries_includes_break_in_delay() -> None:
    """_build_state_queries includes 0x14/0x0F (break-in delay) as common query."""
    profile = resolve_radio_profile(model="IC-7610")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active="MAIN")
    radio.send_civ = AsyncMock()
    poller = RadioPoller(radio, StateCache(), CommandQueue())

    queries = poller._STATE_QUERIES  # noqa: SLF001
    cmd_sub_pairs = {(cmd, sub) for cmd, sub, _ in queries}
    assert (0x14, 0x0F) in cmd_sub_pairs, "break_in_delay (0x14/0x0F) not polled"


# ---------------------------------------------------------------------------
# Optimistic state updates in _execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_set_vox_delay_updates_radio_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetVoxDelay(level=8))  # noqa: SLF001
    assert state.vox_delay == 8


@pytest.mark.asyncio
async def test_execute_set_vox_delay_no_state_no_crash() -> None:
    poller, _ = _make_poller(with_state=False)
    await poller._execute(SetVoxDelay(level=5))  # noqa: SLF001
    # Should not raise


@pytest.mark.asyncio
async def test_execute_set_repeater_tone_updates_main_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetRepeaterTone(on=True, receiver=0))  # noqa: SLF001
    assert state.main.repeater_tone is True


@pytest.mark.asyncio
async def test_execute_set_repeater_tone_updates_sub_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetRepeaterTone(on=False, receiver=1))  # noqa: SLF001
    assert state.sub.repeater_tone is False


@pytest.mark.asyncio
async def test_execute_set_repeater_tsql_updates_main_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetRepeaterTsql(on=True, receiver=0))  # noqa: SLF001
    assert state.main.repeater_tsql is True


@pytest.mark.asyncio
async def test_execute_set_repeater_tsql_updates_sub_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetRepeaterTsql(on=True, receiver=1))  # noqa: SLF001
    assert state.sub.repeater_tsql is True


@pytest.mark.asyncio
async def test_execute_set_tone_freq_updates_main_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetToneFreq(freq_hz=8850, receiver=0))  # noqa: SLF001
    assert state.main.tone_freq == 8850


@pytest.mark.asyncio
async def test_execute_set_tone_freq_updates_sub_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetToneFreq(freq_hz=9700, receiver=1))  # noqa: SLF001
    assert state.sub.tone_freq == 9700


@pytest.mark.asyncio
async def test_execute_set_tsql_freq_updates_main_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetTsqlFreq(freq_hz=10000, receiver=0))  # noqa: SLF001
    assert state.main.tsql_freq == 10000


@pytest.mark.asyncio
async def test_execute_set_tsql_freq_updates_sub_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetTsqlFreq(freq_hz=8850, receiver=1))  # noqa: SLF001
    assert state.sub.tsql_freq == 8850


@pytest.mark.asyncio
async def test_execute_set_vox_gain_updates_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetVoxGain(level=200))  # noqa: SLF001
    assert state.vox_gain == 200


@pytest.mark.asyncio
async def test_execute_set_anti_vox_gain_updates_state() -> None:
    poller, state = _make_poller()
    await poller._execute(SetAntiVoxGain(level=50))  # noqa: SLF001
    assert state.anti_vox_gain == 50
