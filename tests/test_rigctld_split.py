"""Rigctld split-TX frequency/mode commands (I/i/X/x, MOR-3115).

Handler-level coverage for the surface that
``tests/test_rigctld_wsjtx_split_mk2.py`` drives end to end on the wire:
set/get of the split-TX frequency and mode on single-RX profiles via
the VFO-slot dance, error paths, DEFER interlock outcomes (drop on
known TX, refuse on unknown RF truth, dispatch only on known RX),
restore-after-failure coverage, and the SerialMockRadio slot model the
wire test runs against.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from serial_stub import SerialMockRadio
from rigplane.core.radio_protocol import RelativeVfoState
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.exceptions import CommandRejectedError
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.rigctld.contract import (
    HamlibError,
    RigctldCommand,
    RigctldConfig,
)
from rigplane.rigctld.handler import RigctldHandler
from rigplane.runtime import tx_interlock


def get_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=False
    )


def set_cmd(long_cmd: str, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=long_cmd, args=tuple(args), is_set=True
    )


def vfo_get_cmd(long_cmd: str, vfo_arg: str | None, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="",
        long_cmd=long_cmd,
        args=tuple(args),
        is_set=False,
        vfo_arg=vfo_arg,
    )


def vfo_set_cmd(long_cmd: str, vfo_arg: str | None, *args: str) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="",
        long_cmd=long_cmd,
        args=tuple(args),
        is_set=True,
        vfo_arg=vfo_arg,
    )


class _FakeProfile:
    """Minimal stand-in for ``rigplane.profiles.RadioProfile``."""

    def __init__(self, *, receiver_count: int, vfo_scheme: str) -> None:
        self.model = "IC-7300" if receiver_count == 1 else "IC-7610"
        self.receiver_count = receiver_count
        self.vfo_scheme = vfo_scheme


def _mock_radio(*, receiver_count: int, vfo_scheme: str) -> AsyncMock:
    radio = AsyncMock()
    del radio.rigctld_routing
    radio.profile = _FakeProfile(receiver_count=receiver_count, vfo_scheme=vfo_scheme)
    return radio


@pytest.fixture
def config() -> RigctldConfig:
    return RigctldConfig()


@pytest.fixture
def single_rx_radio() -> AsyncMock:
    return _mock_radio(receiver_count=1, vfo_scheme="ab")


@pytest.fixture
def single_rx_handler(
    single_rx_radio: AsyncMock, config: RigctldConfig
) -> RigctldHandler:
    return RigctldHandler(single_rx_radio, config)


@pytest.fixture
def dual_rx_radio() -> AsyncMock:
    return _mock_radio(receiver_count=2, vfo_scheme="main_sub")


@pytest.fixture
def dual_rx_handler(dual_rx_radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(dual_rx_radio, config)


def _seed(store: StateStore, path: FieldPath, value: object) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )
    )


def _store_of(handler: RigctldHandler) -> StateStore:
    return handler._state_store  # noqa: SLF001


_SET_CASES = [
    ("set_split_freq", ("14080100",)),
    ("set_split_mode", ("USB", "0")),
]


# -- set_split_freq ------------------------------------------------------


@pytest.mark.asyncio
async def test_set_split_freq_dances_b_write_a(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_freq, "freq")

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    assert parent.mock_calls == [
        call.slot("B"),
        call.freq(14080100, receiver=0),
        call.slot("A"),
    ]
    assert single_rx_handler._command_service.lifecycle_events()[-1].state == (  # noqa: SLF001
        "acknowledged"
    )


@pytest.mark.asyncio
async def test_set_split_freq_restores_b_when_b_selected(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "B"
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_freq, "freq")

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    assert parent.mock_calls == [
        call.slot("A"),
        call.freq(14080100, receiver=0),
        call.slot("B"),
    ]


@pytest.mark.asyncio
async def test_set_split_freq_falls_back_to_projected_slot(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    del single_rx_radio.get_vfo_slot
    _seed(
        _store_of(single_rx_handler),
        FieldPath.active_slot("main"),
        "B",
    )

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("A"), call("B")])


@pytest.mark.asyncio
async def test_set_split_freq_restores_slot_after_failed_write(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    single_rx_radio.set_freq.side_effect = CommandRejectedError("radio refused")

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.error is HamlibError.ERJCTED
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
async def test_set_split_freq_restores_slot_when_first_switch_raises(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # An ambiguous first-switch failure (ACK timeout / NAK after
    # landing) may have switched the radio anyway: the restore is
    # still attempted, the write never runs, and the original error
    # reaches the client.
    single_rx_radio.get_vfo_slot.return_value = "A"
    single_rx_radio.set_vfo_slot.side_effect = [
        CommandRejectedError("ambiguous NAK after landing"),
        None,
    ]

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.error is HamlibError.ERJCTED
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])
    single_rx_radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_freq_restore_failure_does_not_replace_write_error(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # Best-effort restore: when both the write and the restore fail,
    # the client sees the write's error (ETIMEOUT), not the restore's.
    single_rx_radio.get_vfo_slot.return_value = "A"
    single_rx_radio.set_vfo_slot.side_effect = [
        None,
        CommandRejectedError("restore refused"),
    ]
    single_rx_radio.set_freq.side_effect = RigplaneTimeoutError("write lost")

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.error is HamlibError.ETIMEOUT
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
async def test_set_split_freq_best_effort_restore_logs_and_keeps_success(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    single_rx_radio.set_vfo_slot.side_effect = [
        None,
        CommandRejectedError("restore refused"),
    ]
    caplog.set_level(logging.ERROR, logger="rigplane.rigctld.handler")

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])
    assert any("restoring VFO slot" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_set_split_freq_cancel_mid_dance_still_restores_slot(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # A command-timeout cancellation landing mid-dance (the server
    # cancels the handler task on command_timeout) still runs the
    # shielded restore before the cancellation propagates.
    single_rx_radio.get_vfo_slot.return_value = "A"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_write(freq_hz: int, receiver: int = 0) -> None:
        entered.set()
        await release.wait()

    single_rx_radio.set_freq.side_effect = _slow_write
    task = asyncio.create_task(
        single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))
    )
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
async def test_split_dance_serializes_against_concurrent_set_vfo(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # Two clients on one handler (the server shares the instance across
    # connections): a V issued mid-dance waits for the restore, so the
    # radio sees select-B, restore-A, then the V — never the V between.
    single_rx_radio.get_vfo_slot.return_value = "A"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_write(freq_hz: int, receiver: int = 0) -> None:
        entered.set()
        await release.wait()

    single_rx_radio.set_freq.side_effect = _slow_write
    dance = asyncio.create_task(
        single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))
    )
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    vfo = asyncio.create_task(single_rx_handler.execute(set_cmd("set_vfo", "VFOB")))
    await asyncio.sleep(0.05)
    assert single_rx_radio.set_vfo_slot.await_count == 1
    release.set()
    assert (await dance).ok
    assert (await vfo).ok
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A"), call("B")])


@pytest.mark.asyncio
async def test_split_dance_serializes_against_concurrent_set_freq(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # Two clients on one handler: client 2's F issued mid-dance waits
    # for the restore, so its write lands on the restored selected
    # slot (A) — never on the dance's slot B, and the caller's RX
    # write is not lost.
    single_rx_radio.get_vfo_slot.return_value = "A"
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_write(freq_hz: int, receiver: int = 0) -> None:
        entered.set()
        await release.wait()

    single_rx_radio.set_freq.side_effect = _slow_write
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_freq, "freq")
    dance = asyncio.create_task(
        single_rx_handler.execute(
            set_cmd("set_split_freq", "14080100"), session_id="client-1"
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2.0)
    freq = asyncio.create_task(
        single_rx_handler.execute(set_cmd("set_freq", "14250000"), session_id="client-2")
    )
    await asyncio.sleep(0.05)
    assert single_rx_radio.set_vfo_slot.await_count == 1
    assert single_rx_radio.set_freq.await_count == 1
    release.set()
    assert (await dance).ok
    assert (await freq).ok
    assert parent.mock_calls == [
        call.slot("B"),
        call.freq(14080100, receiver=0),
        call.slot("A"),
        call.freq(14250000, receiver=0),
    ]


@pytest.mark.asyncio
async def test_split_freq_then_main_freq_same_client_does_not_deadlock(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # Sequential I then F from one client: the dance releases the slot
    # lock, so the F that follows is never stuck behind it. The
    # wait_for turns a hang into a failure instead of a stuck suite.
    single_rx_radio.get_vfo_slot.return_value = "A"
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_freq, "freq")

    async def _run_both() -> None:
        resp_split = await single_rx_handler.execute(
            set_cmd("set_split_freq", "14080100"), session_id="client-1"
        )
        assert resp_split.ok
        resp_main = await single_rx_handler.execute(
            set_cmd("set_freq", "14250000"), session_id="client-1"
        )
        assert resp_main.ok

    await asyncio.wait_for(_run_both(), timeout=5.0)
    assert parent.mock_calls == [
        call.slot("B"),
        call.freq(14080100, receiver=0),
        call.slot("A"),
        call.freq(14250000, receiver=0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [(), ("abc",)], ids=["no-arg", "not-a-number"])
async def test_set_split_freq_einval(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    args: tuple[str, ...],
) -> None:
    resp = await single_rx_handler.execute(set_cmd("set_split_freq", *args))

    assert resp.error is HamlibError.EINVAL
    single_rx_radio.set_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_freq_enavail_without_slot_switching(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    del single_rx_radio.set_vfo_slot

    resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.error is HamlibError.ENAVAIL
    single_rx_radio.set_freq.assert_not_awaited()


def _canonical_split_setup(
    config: RigctldConfig, *, ptt: bool | None
) -> tuple[RigctldHandler, AsyncMock]:
    """Single-RX handler with a canonical store seeded to one RF truth.

    The plain fixtures deliberately use the handler's non-canonical
    fallback store, where the defer gate never fires; DEFER outcomes
    need an injected canonical store (as in
    test_rigctld_tx_interlock.py). The premise of each case is asserted
    first so a broken seed cannot pass vacuously.
    """
    store = StateStore()
    if ptt is not None:
        _seed(store, FieldPath.global_("tx_state", "ptt"), ptt)
    radio = _mock_radio(receiver_count=1, vfo_scheme="ab")
    handler = RigctldHandler(radio, config, state_store=store)
    expected = {
        False: tx_interlock.RfState.RX,
        True: tx_interlock.RfState.TX,
        None: tx_interlock.RfState.UNKNOWN,
    }[ptt]
    assert handler._resolve_rigctld_rf_state() is expected  # noqa: SLF001
    return handler, radio


@pytest.mark.asyncio
async def test_set_split_freq_dropped_while_transmitting(
    config: RigctldConfig,
) -> None:
    # DEFER, exactly like the other DEFER writes (S/U SPLIT): known TX
    # answers RPRT 0 with the radio untouched — never even the slot
    # read, since the gate fires before the intent executes.
    handler, radio = _canonical_split_setup(config, ptt=True)

    resp = await handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    radio.set_freq.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    radio.get_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_freq_rejected_on_unknown_rf_truth(
    config: RigctldConfig,
) -> None:
    handler, radio = _canonical_split_setup(config, ptt=None)

    resp = await handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.error is HamlibError.ERJCTED
    radio.set_freq.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    radio.get_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_freq_dispatches_in_known_rx(
    config: RigctldConfig,
) -> None:
    handler, radio = _canonical_split_setup(config, ptt=False)
    radio.get_vfo_slot.return_value = "A"

    resp = await handler.execute(set_cmd("set_split_freq", "14080100"))

    assert resp.ok
    radio.set_freq.assert_awaited_once_with(14080100, receiver=0)
    radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
async def test_set_split_freq_overlay_answers_get_without_readback(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    del single_rx_radio.read_relative_vfo

    set_resp = await single_rx_handler.execute(set_cmd("set_split_freq", "14080100"))
    assert set_resp.ok
    get_resp = await single_rx_handler.execute(get_cmd("get_split_freq"))

    assert get_resp.values == ["14080100"]


# -- set_split_mode ------------------------------------------------------


@pytest.mark.asyncio
async def test_set_split_mode_dances_and_maps_passband(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_mode, "mode")

    resp = await single_rx_handler.execute(set_cmd("set_split_mode", "USB", "2400"))

    assert resp.ok
    assert parent.mock_calls == [
        call.slot("B"),
        call.mode("USB", filter_width=2),
        call.slot("A"),
    ]


@pytest.mark.asyncio
async def test_set_split_mode_without_passband_keeps_filter_none(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    resp = await single_rx_handler.execute(set_cmd("set_split_mode", "LSB"))

    assert resp.ok
    single_rx_radio.set_mode.assert_awaited_once_with("LSB", filter_width=None)


@pytest.mark.asyncio
async def test_set_split_mode_packet_applies_data_on_tx_slot(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    parent = MagicMock()
    parent.attach_mock(single_rx_radio.set_vfo_slot, "slot")
    parent.attach_mock(single_rx_radio.set_mode, "mode")
    parent.attach_mock(single_rx_radio.set_data_mode, "data")

    resp = await single_rx_handler.execute(set_cmd("set_split_mode", "PKTUSB", "0"))

    assert resp.ok
    assert parent.mock_calls == [
        call.slot("B"),
        call.mode("USB", filter_width=None),
        call.data(True),
        call.slot("A"),
    ]


@pytest.mark.asyncio
async def test_set_split_mode_restores_slot_after_failed_write(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.get_vfo_slot.return_value = "A"
    single_rx_radio.set_mode.side_effect = CommandRejectedError("radio refused")

    resp = await single_rx_handler.execute(set_cmd("set_split_mode", "USB", "0"))

    assert resp.error is HamlibError.ERJCTED
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [(), ("ZZZ",), ("USB", "wide")],
    ids=["no-arg", "bad-mode", "bad-passband"],
)
async def test_set_split_mode_einval(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    args: tuple[str, ...],
) -> None:
    resp = await single_rx_handler.execute(set_cmd("set_split_mode", *args))

    assert resp.error is HamlibError.EINVAL
    single_rx_radio.set_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_mode_dropped_while_transmitting(
    config: RigctldConfig,
) -> None:
    # DEFER, exactly like the other DEFER writes (S/U SPLIT): known TX
    # answers RPRT 0 with the radio untouched.
    handler, radio = _canonical_split_setup(config, ptt=True)

    resp = await handler.execute(set_cmd("set_split_mode", "USB", "0"))

    assert resp.ok
    radio.set_mode.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    radio.get_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_mode_rejected_on_unknown_rf_truth(
    config: RigctldConfig,
) -> None:
    handler, radio = _canonical_split_setup(config, ptt=None)

    resp = await handler.execute(set_cmd("set_split_mode", "USB", "0"))

    assert resp.error is HamlibError.ERJCTED
    radio.set_mode.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    radio.get_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_split_mode_dispatches_in_known_rx(
    config: RigctldConfig,
) -> None:
    handler, radio = _canonical_split_setup(config, ptt=False)
    radio.get_vfo_slot.return_value = "A"

    resp = await handler.execute(set_cmd("set_split_mode", "USB", "0"))

    assert resp.ok
    radio.set_mode.assert_awaited_once_with("USB", filter_width=None)
    radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


# -- split writes share the interlock gate -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("long_cmd", "args"), _SET_CASES)
async def test_set_split_passes_through_defer_gate(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    long_cmd: str,
    args: tuple[str, ...],
) -> None:
    seen: list[str] = []
    real_gate = single_rx_handler._defer_write_gate  # noqa: SLF001

    def _spy(intent) -> object:
        seen.append(intent.name)
        return real_gate(intent)

    monkeypatch.setattr(single_rx_handler, "_defer_write_gate", _spy)

    resp = await single_rx_handler.execute(set_cmd(long_cmd, *args))

    assert resp.ok
    assert seen == [long_cmd]


# -- VFO prefix + profile gates (both sets, both gets) --------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("long_cmd", "args"), _SET_CASES)
async def test_set_split_accepts_vfob_prefix_on_single_rx(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    long_cmd: str,
    args: tuple[str, ...],
) -> None:
    resp = await single_rx_handler.execute(vfo_set_cmd(long_cmd, "VFOB", *args))

    assert resp.ok
    single_rx_radio.set_vfo_slot.assert_has_awaits([call("B"), call("A")])


@pytest.mark.asyncio
@pytest.mark.parametrize(("long_cmd", "args"), _SET_CASES)
async def test_set_split_unknown_vfo_is_evfo(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    long_cmd: str,
    args: tuple[str, ...],
) -> None:
    resp = await single_rx_handler.execute(vfo_set_cmd(long_cmd, "VFOC", *args))

    assert resp.error is HamlibError.EVFO
    single_rx_radio.set_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("long_cmd", "args"), _SET_CASES)
async def test_set_split_dual_rx_is_enimpl(
    dual_rx_handler: RigctldHandler,
    dual_rx_radio: AsyncMock,
    long_cmd: str,
    args: tuple[str, ...],
) -> None:
    resp = await dual_rx_handler.execute(set_cmd(long_cmd, *args))

    assert resp.error is HamlibError.ENIMPL
    dual_rx_radio.set_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("long_cmd", "expected"),
    [("get_split_freq", ["14080100"]), ("get_split_mode", ["USB", "3000"])],
)
async def test_get_split_accepts_vfob_prefix_on_single_rx(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    long_cmd: str,
    expected: list[str],
) -> None:
    single_rx_radio.read_relative_vfo.return_value = RelativeVfoState(
        freq_hz=14080100, mode="USB", filter_num=1, data_mode=0
    )

    resp = await single_rx_handler.execute(vfo_get_cmd(long_cmd, "VFOB"))

    assert resp.values == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("long_cmd", ["get_split_freq", "get_split_mode"])
async def test_get_split_unknown_vfo_is_evfo(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    long_cmd: str,
) -> None:
    resp = await single_rx_handler.execute(vfo_get_cmd(long_cmd, "VFOC"))

    assert resp.error is HamlibError.EVFO
    single_rx_radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("long_cmd", ["get_split_freq", "get_split_mode"])
async def test_get_split_dual_rx_is_enimpl(
    dual_rx_handler: RigctldHandler,
    dual_rx_radio: AsyncMock,
    long_cmd: str,
) -> None:
    resp = await dual_rx_handler.execute(get_cmd(long_cmd))

    assert resp.error is HamlibError.ENIMPL
    dual_rx_radio.read_relative_vfo.assert_not_awaited()


# -- get_split_freq ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver_id", ["main", "0"], ids=["main", "legacy-0"])
async def test_get_split_freq_reads_unselected_store(
    single_rx_handler: RigctldHandler,
    single_rx_radio: AsyncMock,
    receiver_id: str,
) -> None:
    _seed(
        _store_of(single_rx_handler),
        FieldPath.unselected(receiver_id, "freq_mode", "freq_hz"),
        14080100,
    )

    resp = await single_rx_handler.execute(get_cmd("get_split_freq"))

    assert resp.values == ["14080100"]
    single_rx_radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_split_freq_ignores_active_slot_store_field(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    # The bare receiver field carries active-slot state; it must never
    # answer a split-TX read, which falls through to the live readback.
    _seed(
        _store_of(single_rx_handler),
        FieldPath.receiver("0", "freq_mode", "freq_hz"),
        14074000,
    )
    single_rx_radio.read_relative_vfo.return_value = RelativeVfoState(
        freq_hz=14080100, mode="USB", filter_num=1, data_mode=0
    )

    resp = await single_rx_handler.execute(get_cmd("get_split_freq"))

    assert resp.values == ["14080100"]


@pytest.mark.asyncio
async def test_get_split_freq_falls_back_to_relative_readback(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.read_relative_vfo.return_value = RelativeVfoState(
        freq_hz=14080100, mode="USB", filter_num=1, data_mode=0
    )

    resp = await single_rx_handler.execute(get_cmd("get_split_freq"))

    assert resp.values == ["14080100"]
    single_rx_radio.read_relative_vfo.assert_awaited_once_with(selected=False)


@pytest.mark.asyncio
async def test_get_split_freq_enimpl_without_readback(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    del single_rx_radio.read_relative_vfo

    resp = await single_rx_handler.execute(get_cmd("get_split_freq"))

    assert resp.error is HamlibError.ENIMPL


# -- get_split_mode ------------------------------------------------------


@pytest.mark.asyncio
async def test_get_split_mode_reads_unselected_store_tuple(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    store = _store_of(single_rx_handler)
    _seed(store, FieldPath.unselected("main", "freq_mode", "mode"), "USB")
    _seed(store, FieldPath.unselected("main", "freq_mode", "filter_num"), 2)
    _seed(store, FieldPath.unselected("main", "freq_mode", "data_mode"), 0)

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.values == ["USB", "2400"]
    single_rx_radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_split_mode_maps_packet_label(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    store = _store_of(single_rx_handler)
    _seed(store, FieldPath.unselected("main", "freq_mode", "mode"), "USB")
    _seed(store, FieldPath.unselected("main", "freq_mode", "filter_num"), 1)
    _seed(store, FieldPath.unselected("main", "freq_mode", "data_mode"), 1)

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.values == ["PKTUSB", "3000"]
    single_rx_radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_split_mode_none_filter_answers_zero_passband(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    store = _store_of(single_rx_handler)
    _seed(store, FieldPath.unselected("main", "freq_mode", "mode"), "CW")
    _seed(store, FieldPath.unselected("main", "freq_mode", "filter_num"), None)
    _seed(store, FieldPath.unselected("main", "freq_mode", "data_mode"), 0)

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.values == ["CW", "0"]
    single_rx_radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_split_mode_partial_store_falls_back_to_live(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    _seed(
        _store_of(single_rx_handler),
        FieldPath.unselected("main", "freq_mode", "mode"),
        "USB",
    )
    single_rx_radio.read_relative_vfo.return_value = RelativeVfoState(
        freq_hz=14080100, mode="LSB", filter_num=3, data_mode=0
    )

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.values == ["LSB", "1800"]


@pytest.mark.asyncio
async def test_get_split_mode_fallback_formats_tuple(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    single_rx_radio.read_relative_vfo.return_value = RelativeVfoState(
        freq_hz=14080100, mode="RTTY", filter_num=2, data_mode=1
    )

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.values == ["PKTRTTY", "2400"]
    single_rx_radio.read_relative_vfo.assert_awaited_once_with(selected=False)


@pytest.mark.asyncio
async def test_get_split_mode_enimpl_without_readback(
    single_rx_handler: RigctldHandler, single_rx_radio: AsyncMock
) -> None:
    del single_rx_radio.read_relative_vfo

    resp = await single_rx_handler.execute(get_cmd("get_split_mode"))

    assert resp.error is HamlibError.ENIMPL


# -- SerialMockRadio slot model ------------------------------------------


@pytest.mark.asyncio
async def test_mock_slots_hold_independent_freq_mode() -> None:
    radio = SerialMockRadio(model="IC-7300MK2")

    assert await radio.get_freq() == 14074000
    await radio.set_vfo_slot("B")
    await radio.set_freq(14080100)
    await radio.set_mode("CW")
    assert await radio.get_freq() == 14080100
    assert await radio.get_mode() == ("CW", 1)
    await radio.set_vfo_slot("A")
    assert await radio.get_freq() == 14074000
    assert await radio.get_mode() == ("USB", 1)


@pytest.mark.asyncio
async def test_mock_relative_readback_reports_both_roles() -> None:
    radio = SerialMockRadio(model="IC-7300MK2")
    await radio.set_vfo_slot("B")
    await radio.set_freq(14080100)
    await radio.set_vfo_slot("A")

    selected = await radio.read_relative_vfo(selected=True)
    unselected = await radio.read_relative_vfo(selected=False)
    assert (selected.freq_hz, selected.mode) == (14074000, "USB")
    assert (unselected.freq_hz, unselected.mode) == (14080100, "USB")
    assert await radio.get_vfo_slot() == "A"

    await radio.set_vfo_slot("B")
    flipped = await radio.read_relative_vfo(selected=False)
    assert (flipped.freq_hz, flipped.mode) == (14074000, "USB")


@pytest.mark.asyncio
async def test_mock_slot_selection_is_validated() -> None:
    radio = SerialMockRadio(model="IC-7300MK2")

    await radio.set_vfo_slot("b")
    assert await radio.get_vfo_slot() == "B"
    with pytest.raises(ValueError, match="slot must be"):
        await radio.set_vfo_slot("C")
    with pytest.raises(ValueError, match="out of range"):
        await radio.set_vfo_slot("A", receiver=9)
    with pytest.raises(ValueError, match="out of range"):
        await radio.get_vfo_slot(receiver=9)


@pytest.mark.asyncio
async def test_mock_mirror_follows_active_slot() -> None:
    radio = SerialMockRadio(model="IC-7300MK2")
    await radio.set_vfo_slot("B")
    await radio.set_freq(14080100)

    assert radio.radio_state.main.active_slot == "B"
    assert radio.radio_state.main.freq == 14080100
