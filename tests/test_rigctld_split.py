"""Split writes exchange selected/unselected contents, then restore RX."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from serial_stub import SerialMockRadio
from rigplane.core.command_service import (
    CommandExecutionResult,
    command_intent_from_request,
)
from rigplane.core.radio_protocol import (
    Radio,
    ConfirmedModeCapable,
    RelativeVfoReadbackCapable,
    RelativeVfoState,
    VfoSlotCapable,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.exceptions import CommandRejectedError
from rigplane.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.rigctld.contract import HamlibError, RigctldCommand, RigctldConfig
from rigplane.rigctld.handler import (
    RigctldHandler,
    _RigctldCommandExecutor,
    _RigctldCommandFailure,
)


def get_cmd(name: str, *, vfo_arg: str | None = None) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=name, args=(), is_set=False, vfo_arg=vfo_arg
    )


def set_cmd(name: str, *args: str, vfo_arg: str | None = None) -> RigctldCommand:
    return RigctldCommand(
        short_cmd="", long_cmd=name, args=args, is_set=True, vfo_arg=vfo_arg
    )


class _FakeProfile:
    def __init__(self, receiver_count: int = 1) -> None:
        self.model = "IC-7300" if receiver_count == 1 else "IC-7610"
        self.receiver_count = receiver_count
        self.vfo_scheme = "ab" if receiver_count == 1 else "main_sub"
        self.swap_ab_code = 0xB0


class _SplitTxRadioSpec(
    Radio, VfoSlotCapable, RelativeVfoReadbackCapable, ConfirmedModeCapable, Protocol
):
    profile: _FakeProfile


@pytest.fixture
def config() -> RigctldConfig:
    return RigctldConfig()


@pytest.fixture
def radio() -> AsyncMock:
    result = AsyncMock(spec=_SplitTxRadioSpec)
    result.profile = _FakeProfile()
    result.read_relative_vfo.return_value = RelativeVfoState(7_076_000, "LSB", 3, 0)
    return result


@pytest.fixture
def handler(radio: AsyncMock, config: RigctldConfig) -> RigctldHandler:
    return RigctldHandler(radio, config)


_WRITES = [
    ("set_split_freq", ("14080100",)),
    ("set_split_mode", ("RTTY", "1800")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
async def test_split_exchange_write_restore_without_identity(
    handler, radio, name, args
):
    parent = MagicMock(spec=["swap", "freq", "mode"])
    parent.attach_mock(radio.swap_vfo_ab, "swap")
    parent.attach_mock(radio.set_freq, "freq")
    parent.attach_mock(radio.set_mode_confirmed, "mode")
    assert (await handler.execute(set_cmd(name, *args))).ok
    payload = (
        call.freq(14_080_100, receiver=0)
        if name == "set_split_freq"
        else call.mode("RTTY", filter_width=3)
    )
    assert parent.mock_calls == [call.swap(), payload, call.swap()]
    radio.get_vfo_slot.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    assert not handler._command_service.pending_overlays(
        source="rigctld", session_id=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
async def test_split_missing_exchange_capability_refuses(handler, radio, name, args):
    radio.profile.swap_ab_code = None
    response = await handler.execute(set_cmd(name, *args))
    assert response.error is HamlibError.ENAVAIL
    radio.swap_vfo_ab.assert_not_awaited()
    radio.set_freq.assert_not_awaited()
    radio.set_mode_confirmed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
async def test_split_initial_exchange_nak_never_swaps_back(handler, radio, name, args):
    radio.swap_vfo_ab.side_effect = CommandRejectedError("exchange rejected")
    assert (await handler.execute(set_cmd(name, *args))).error is HamlibError.ERJCTED
    radio.swap_vfo_ab.assert_awaited_once_with()
    radio.set_freq.assert_not_awaited()
    radio.set_mode_confirmed.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_unknown_initial_exchange_attempts_restore(handler, radio):
    radio.swap_vfo_ab.side_effect = [RigplaneTimeoutError("ACK lost"), None]
    response = await handler.execute(set_cmd("set_split_freq", "14080100"))
    assert response.error is HamlibError.ETIMEOUT
    assert radio.swap_vfo_ab.await_count == 2
    radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
async def test_split_restore_failure_never_publishes_success(
    handler, radio, name, args
):
    radio.swap_vfo_ab.side_effect = [None, CommandRejectedError("restore rejected")]
    assert (await handler.execute(set_cmd(name, *args))).error is HamlibError.ERJCTED
    assert radio.swap_vfo_ab.await_count == 2
    assert not handler._command_service.pending_overlays(
        source="rigctld", session_id=None
    )


@pytest.mark.asyncio
async def test_split_payload_error_wins_over_restore_error(handler, radio):
    radio.set_freq.side_effect = RigplaneTimeoutError("payload lost")
    radio.swap_vfo_ab.side_effect = [None, CommandRejectedError("restore rejected")]
    response = await handler.execute(set_cmd("set_split_freq", "14080100"))
    assert response.error is HamlibError.ETIMEOUT
    assert radio.swap_vfo_ab.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["exchange", "payload", "restore"])
async def test_split_cancellation_joins_exchange_and_restore(handler, radio, phase):
    entered, release = asyncio.Event(), asyncio.Event()
    swaps = 0

    async def swap():
        nonlocal swaps
        swaps += 1
        if (phase == "exchange" and swaps == 1) or (phase == "restore" and swaps == 2):
            entered.set()
            await release.wait()

    async def payload(freq, receiver=0):
        if phase == "payload":
            entered.set()
            await release.wait()

    radio.swap_vfo_ab.side_effect = swap
    radio.set_freq.side_effect = payload
    task = asyncio.create_task(handler.execute(set_cmd("set_split_freq", "14080100")))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        if phase != "payload":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.02)
            task.cancel()
            assert handler._vfo_slot_lock.locked()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert swaps == 2
        if phase == "exchange":
            radio.set_freq.assert_not_awaited()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["abc", "nan", "inf", "-inf", "1e999"])
async def test_split_nonfinite_frequency_is_einval_without_radio(handler, radio, value):
    response = await handler.execute(set_cmd("set_split_freq", value))
    assert response.error is HamlibError.EINVAL
    radio.swap_vfo_ab.assert_not_awaited()
    radio.set_freq.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [(), ("ZZZ",), ("USB", "wide")])
async def test_split_invalid_mode_is_einval(handler, radio, args):
    assert (
        await handler.execute(set_cmd("set_split_mode", *args))
    ).error is HamlibError.EINVAL
    radio.swap_vfo_ab.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [("USB",), ("USB", "0")])
async def test_split_omitted_filter_uses_actual_tx_filter(handler, radio, args):
    assert (await handler.execute(set_cmd("set_split_mode", *args))).ok
    radio.read_relative_vfo.assert_awaited_once_with(selected=True)
    radio.set_mode_confirmed.assert_awaited_once_with("USB", filter_width=3)


@pytest.mark.asyncio
async def test_split_packet_data_failure_restores_and_rejects(handler, radio):
    radio.set_data_mode.side_effect = CommandRejectedError("DATA rejected")
    response = await handler.execute(set_cmd("set_split_mode", "PKTUSB", "2400"))
    assert response.error is HamlibError.ERJCTED
    radio.set_mode_confirmed.assert_awaited_once_with("USB", filter_width=2)
    radio.set_data_mode.assert_awaited_once_with(True)
    assert radio.swap_vfo_ab.await_count == 2
    assert not handler._command_service.pending_overlays(
        source="rigctld", session_id=None
    )


def _seed(store: StateStore, path: FieldPath, value: object) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
            max_age=60,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
@pytest.mark.parametrize("ptt", [True, False, None])
async def test_split_interlock_gate(config, radio, name, args, ptt):
    store = StateStore()
    if ptt is not None:
        _seed(store, FieldPath.global_("tx_state", "ptt"), ptt)
    handler = RigctldHandler(radio, config, state_store=store)
    response = await handler.execute(set_cmd(name, *args))
    if ptt is None:
        assert response.error is HamlibError.ERJCTED
    else:
        assert response.ok
    assert radio.swap_vfo_ab.await_count == (2 if ptt is False else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
@pytest.mark.parametrize("vfo", [None, "VFOB", "VFOC"])
async def test_split_write_vfo_prefix(handler, radio, name, args, vfo):
    response = await handler.execute(set_cmd(name, *args, vfo_arg=vfo))
    if vfo == "VFOC":
        assert response.error is HamlibError.EVFO
    else:
        assert response.ok
    assert radio.swap_vfo_ab.await_count == (0 if vfo == "VFOC" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "args"), _WRITES + [("get_split_freq", ()), ("get_split_mode", ())]
)
async def test_split_dual_rx_is_enimpl(config, radio, name, args):
    radio.profile = _FakeProfile(receiver_count=2)
    handler = RigctldHandler(radio, config)
    cmd = set_cmd(name, *args) if args else get_cmd(name)
    assert (await handler.execute(cmd)).error is HamlibError.ENIMPL
    radio.swap_vfo_ab.assert_not_awaited()
    radio.read_relative_vfo.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected"),
    [("get_split_freq", ["7076000"]), ("get_split_mode", ["LSB", "1800"])],
)
async def test_split_getters_ignore_cached_slot_and_always_read_live(
    handler, radio, name, expected
):
    _seed(
        handler._state_store, FieldPath.unselected("main", "freq_mode", "freq_hz"), 99
    )
    _seed(
        handler._state_store,
        FieldPath.vfo_slot("main", "B", "freq_mode", "freq_hz"),
        88,
    )
    for _ in range(2):
        assert (await handler.execute(get_cmd(name))).values == expected
    assert radio.read_relative_vfo.await_count == 2
    radio.get_vfo_slot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["get_split_freq", "get_split_mode"])
async def test_split_getter_missing_readback_is_enimpl(handler, radio, name):
    del radio.read_relative_vfo
    assert (await handler.execute(get_cmd(name))).error is HamlibError.ENIMPL


@pytest.mark.asyncio
async def test_split_mode_requires_confirmed_seam_before_exchange(handler, radio):
    del radio.set_mode_confirmed
    response = await handler.execute(set_cmd("set_split_mode", "RTTY", "1800"))
    assert response.error is HamlibError.ENAVAIL
    radio.swap_vfo_ab.assert_not_awaited()


@pytest.mark.asyncio
async def test_selected_reads_after_split_fall_back_without_relative_seam(
    handler, radio
):
    assert (await handler.execute(set_cmd("set_split_freq", "14080100"))).ok
    del radio.read_relative_vfo
    radio.get_freq.return_value = 14_074_000
    radio.get_mode.return_value = ("USB", 1)
    radio.get_data_mode.return_value = False
    assert (await handler.execute(get_cmd("get_freq"))).values == ["14074000"]
    assert (await handler.execute(get_cmd("get_mode"))).values == ["USB", "3000"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), _WRITES)
async def test_managed_split_dispatch_preserves_ingress_defer_ownership(
    handler, radio, monkeypatch, name, args
):
    handler._managed_tx_authority = object()
    gate = MagicMock(
        spec=handler._defer_write_gate, side_effect=AssertionError("legacy gate called")
    )
    monkeypatch.setattr(handler, "_defer_write_gate", gate)
    assert (await handler.execute(set_cmd(name, *args))).ok
    gate.assert_not_called()
    assert radio.swap_vfo_ab.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["get_split_freq", "get_split_mode"])
@pytest.mark.parametrize("vfo", ["VFOB", "VFOC"])
async def test_split_getter_vfo_prefix(handler, radio, name, vfo):
    response = await handler.execute(get_cmd(name, vfo_arg=vfo))
    if vfo == "VFOC":
        assert response.error is HamlibError.EVFO
        radio.read_relative_vfo.assert_not_awaited()
    else:
        assert response.ok
        radio.read_relative_vfo.assert_awaited_once_with(selected=False)


@pytest.mark.asyncio
async def test_split_getter_none_filter_answers_zero_passband(handler, radio):
    radio.read_relative_vfo.return_value = RelativeVfoState(1, "USB", None, 0)
    assert (await handler.execute(get_cmd("get_split_mode"))).values == ["USB", "0"]


@pytest.mark.asyncio
async def test_set_split_freq_missing_argument_is_einval(handler, radio):
    assert (
        await handler.execute(set_cmd("set_split_freq"))
    ).error is HamlibError.EINVAL
    radio.swap_vfo_ab.assert_not_awaited()


@pytest.mark.asyncio
async def test_split_freq_then_main_freq_same_client_does_not_deadlock(handler, radio):
    async def sequence():
        assert (
            await handler.execute(
                set_cmd("set_split_freq", "14080100"), session_id="one"
            )
        ).ok
        assert (
            await handler.execute(set_cmd("set_freq", "14250000"), session_id="one")
        ).ok

    await asyncio.wait_for(sequence(), 2)
    radio.set_freq.assert_has_awaits(
        [call(14_080_100, receiver=0), call(14_250_000, receiver=0)]
    )


@pytest.mark.asyncio
async def test_cancelled_key_waiting_for_split_never_keys(handler, radio):
    await handler._vfo_slot_lock.acquire()
    key = asyncio.create_task(handler._route_ptt(True, session_id="cancelled"))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(key), 0.02)
        key.cancel()
        with pytest.raises(asyncio.CancelledError):
            await key
    finally:
        handler._vfo_slot_lock.release()
        await asyncio.gather(key, return_exceptions=True)
    radio.set_ptt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("dekey", [False, True])
async def test_managed_ptt_waits_for_split_and_off_invalidates_key(
    handler, monkeypatch, dekey
):
    executor = _RigctldCommandExecutor(handler)
    # Keep the lease/submission body intact in production; isolate its entry
    # ordering here, with TCP tests exercising the physical CoreRadio path.
    body = AsyncMock(
        spec=executor._execute_managed_ptt_unlocked,
        return_value=CommandExecutionResult(),
    )
    monkeypatch.setattr(_RigctldCommandExecutor, "_execute_managed_ptt_unlocked", body)
    on = command_intent_from_request(
        "set_ptt", {"on": True}, source="rigctld", command_id="key", session_id="owner"
    )
    off = command_intent_from_request(
        "set_ptt", {"on": False}, source="rigctld", command_id="off", session_id="owner"
    )
    await handler._vfo_slot_lock.acquire()
    key = asyncio.create_task(executor._execute_managed_ptt(on))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(key), 0.02)
        body.assert_not_awaited()
        if dekey:
            await executor._execute_managed_ptt(off)
            body.assert_awaited_once_with(off)
        handler._vfo_slot_lock.release()
        if dekey:
            with pytest.raises(_RigctldCommandFailure) as failure:
                await key
            assert failure.value.error is HamlibError.ERJCTED
            body.assert_awaited_once_with(off)
        else:
            await key
            body.assert_awaited_once_with(on, key_generation=0)
    finally:
        if handler._vfo_slot_lock.locked():
            handler._vfo_slot_lock.release()
        if not key.done():
            key.cancel()
        await asyncio.gather(key, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "data", "expected"),
    [("RTTY", 1, "PKTRTTY"), ("USB", 3, "PKTUSB"), ("LSB", 0, "LSB")],
)
async def test_split_getter_formats_data_mode(handler, radio, mode, data, expected):
    radio.read_relative_vfo.return_value = RelativeVfoState(1, mode, 3, data)
    assert (await handler.execute(get_cmd("get_split_mode"))).values == [
        expected,
        "1800",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("write", "args", "read", "expected"),
    [
        ("set_split_freq", ("14080100",), "get_split_freq", ["14074000"]),
        ("set_split_mode", ("RTTY", "1800"), "get_split_mode", ["USB", "3000"]),
    ],
)
async def test_split_live_read_follows_selection_after_write(
    config, write, args, read, expected
):
    radio = SerialMockRadio(model="IC-7300MK2")
    handler = RigctldHandler(radio, config)
    assert (await handler.execute(set_cmd(write, *args))).ok
    assert (await handler.execute(set_cmd("set_vfo", "VFOB"))).ok
    assert (await handler.execute(get_cmd(read))).values == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["set_freq", "set_mode", "set_vfo"])
async def test_split_transaction_serializes_main_writes(handler, radio, name):
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(freq, receiver=0):
        if freq == 14_080_100:
            entered.set()
            await release.wait()

    radio.set_freq.side_effect = delayed
    split = asyncio.create_task(handler.execute(set_cmd("set_split_freq", "14080100")))
    other = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        args = {
            "set_freq": ("14250000",),
            "set_mode": ("USB", "2400"),
            "set_vfo": ("VFOB",),
        }[name]
        other = asyncio.create_task(handler.execute(set_cmd(name, *args)))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(other), 0.02)
        assert radio.swap_vfo_ab.await_count == 1
        release.set()
        assert (await split).ok
        assert (await other).ok
        assert radio.swap_vfo_ab.await_count == 2
    finally:
        release.set()
        for task in (split, other):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (split, other) if task is not None),
            return_exceptions=True,
        )
