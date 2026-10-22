"""Provider wiring for fenced local CW and tuner work."""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.commands.commander import Priority
from rigplane.core.types import CivFrame
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.rig_loader import RigConfig, load_rig
from rigplane.runtime.radio import CoreRadio


async def _answer_query(command: str, *args: object, **kwargs: object) -> str:
    """Answer the PTT read-back as a receiving radio; keep the AC answer."""
    del args, kwargs
    return "TX0" if command == "TX;" else "AC101"


def _cw_text_enabled_ftx1() -> RigConfig:
    # MOR-3678: the shipped FTX-1 profile disables send_cw_text until the
    # memory type is verified on a radio; these fence tests enable it.
    rig = load_rig(Path(__file__).resolve().parents[1] / "rigs" / "ftx1.toml")
    return dataclasses.replace(
        rig, cw_text=dataclasses.replace(rig.cw_text, supported=True)
    )


def _fenced_radio(fence: TxAbortFence | None) -> YaesuCatRadio:
    radio = YaesuCatRadio(
        "/dev/null", profile=_cw_text_enabled_ftx1(), tx_abort_fence=fence
    )
    transport = radio._transport
    transport._connected = True
    transport.query = AsyncMock(side_effect=_answer_query)
    transport.flush_rx = AsyncMock(return_value=0)
    transport._drain_responses = AsyncMock(return_value=0)
    transport._raw_write = AsyncMock()
    return radio


def _effect_token() -> EffectToken:
    return EffectToken(1, 1, "force-off-test")


async def _hold_transport(
    radio: YaesuCatRadio,
) -> tuple[asyncio.Event, asyncio.Task[None]]:
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with radio._transport._exchange_gate.exchange():
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await acquired.wait()
    return release, task


async def _force_receive(radio: YaesuCatRadio) -> ActuationResult:
    return await radio.actuate(
        _effect_token(),
        ActuationOperation.FORCE_RECEIVE,
        is_current=lambda: True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_local_work",
    [
        pytest.param(lambda radio: radio.send_cw_text("CQ"), id="cw"),
        pytest.param(lambda radio: radio.set_tuner_status(2), id="tuner"),
    ],
)
async def test_force_receive_bypasses_queued_stale_local_work(
    start_local_work: Callable[[YaesuCatRadio], Awaitable[None]],
) -> None:
    fence = TxAbortFence()
    radio = _fenced_radio(fence)
    release, holder = await _hold_transport(radio)
    local = asyncio.create_task(start_local_work(radio))
    await asyncio.sleep(0)

    cleanup = fence.force_off()
    force_receive = asyncio.create_task(_force_receive(radio))
    await asyncio.sleep(0)
    release.set()

    assert await force_receive is ActuationResult.ACCEPTED
    with pytest.raises(CatTransportError, match="no longer current"):
        await local
    await holder
    await cleanup

    radio._transport._raw_write.assert_awaited_once()
    command = radio._transport._raw_write.await_args.args[0]
    assert command.startswith("TX0")


@pytest.mark.asyncio
async def test_force_receive_suppresses_keyer_playback_after_the_memory_write() -> None:
    fence = TxAbortFence()
    radio = _fenced_radio(fence)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    local = asyncio.create_task(radio.send_cw_text("A" * 25))
    await first_drain_started.wait()

    cleanup = fence.force_off()
    force_receive = asyncio.create_task(_force_receive(radio))
    await asyncio.sleep(0)
    release_first_drain.set()

    assert await force_receive is ActuationResult.ACCEPTED
    with pytest.raises(CatTransportError, match="no longer current"):
        await local
    await cleanup

    wire_commands = [
        call.args[0] for call in radio._transport._raw_write.await_args_list
    ]
    # KM wrote the text; the KY that would play it never reached the wire.
    assert len(wire_commands) == 2
    assert wire_commands[0].startswith("KM5")
    assert wire_commands[1].startswith("TX0")


@pytest.mark.asyncio
async def test_unmanaged_calls_keep_native_writes_and_guard_generic_tuner() -> None:
    radio = _fenced_radio(None)
    radio._transport.write = AsyncMock()

    await radio.send_cw_text("CQ")
    await radio.set_tuner_status(2)

    km_call, ky_call, tuner_call = radio._transport.write.await_args_list
    # MOR-3678: unmanaged KM and KY carry only the stop-generation check.
    for cw_call in (km_call, ky_call):
        assert cw_call.kwargs["is_current"]() is True
    await radio.stop_cw_text()
    assert km_call.kwargs["is_current"]() is False
    assert ky_call.kwargs["is_current"]() is False
    assert tuner_call.kwargs["is_current"]() is True
    radio._transport.stats.reconnects += 1
    assert tuner_call.kwargs["is_current"]() is False
    radio._transport.query.assert_awaited_once_with("AC;")
    assert km_call.args == ("KM5CQ;",)
    assert ky_call.args == ("KY05;",)
    assert tuner_call.args == ("AC103;",)


def _icom_radio() -> CoreRadio:
    radio = CoreRadio("127.0.0.1", model="IC-7300")
    radio._check_connected = lambda: None
    return radio


def _ack() -> CivFrame:
    return CivFrame(to_addr=0xE0, from_addr=0x94, command=0xFB)


@pytest.mark.asyncio
async def test_icom_composition_injects_its_exact_runner_fence_and_authority(
    tmp_path,
) -> None:
    radio = _icom_radio()
    assert radio._local_tx_work is None
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")

    install_managed_tx_composition(radio, composition)

    assert radio._local_tx_work is composition.local_tx_work_runner
    assert radio._local_tx_work._abort_fence is composition._abort_fence
    assert composition.authority._abort_fence is composition._abort_fence
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_managed_cw_uses_one_operation_and_guards_every_chunk(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    predicates: list[Callable[[], bool] | None] = []

    async def send_raw(
        _frame: bytes,
        **kwargs: object,
    ) -> CivFrame:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        return _ack()

    radio._send_civ_raw = send_raw
    await radio.send_cw_text("A" * 31)

    runner.run.assert_awaited_once()
    assert len(predicates) == 2
    assert predicates[0] is predicates[1]
    assert predicates[0] is not None
    assert predicates[0]() is True
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_force_off_suppresses_late_cw_write_and_remaining_chunks(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    entered = asyncio.Event()
    release = asyncio.Event()
    wire: list[bytes] = []
    predicates: list[Callable[[], bool] | None] = []

    async def delayed_send_raw(
        frame: bytes,
        **kwargs: object,
    ) -> CivFrame:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        entered.set()
        await release.wait()
        if predicate is None or predicate():
            wire.append(frame)
        return _ack()

    radio._send_civ_raw = delayed_send_raw
    sending = asyncio.create_task(radio.send_cw_text("A" * 31))
    await entered.wait()

    cleanup = composition._abort_fence.force_off()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await sending
    result = await cleanup

    runner.run.assert_awaited_once()
    assert result.failures == ()
    assert len(predicates) == 2
    assert predicates[0] is predicates[1]
    assert predicates[0] is not None
    assert predicates[0]() is False
    assert wire == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 2])
async def test_icom_force_off_suppresses_late_tuner_write(
    tmp_path,
    value: int,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    entered = asyncio.Event()
    release = asyncio.Event()
    wire: list[bytes] = []
    predicates: list[Callable[[], bool] | None] = []

    async def delayed_send_raw(
        frame: bytes,
        **kwargs: object,
    ) -> None:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        entered.set()
        await release.wait()
        if predicate is None or predicate():
            wire.append(frame)

    radio._send_civ_raw = delayed_send_raw
    tuning = asyncio.create_task(radio.set_tuner_status(value))
    await entered.wait()

    cleanup = composition._abort_fence.force_off()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await tuning
    result = await cleanup

    runner.run.assert_awaited_once()
    assert result.failures == ()
    assert len(predicates) == 1
    assert predicates[0] is not None
    assert predicates[0]() is False
    assert wire == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_managed_empty_cw_stop_and_tuner_off_remain_direct(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    radio._send_civ_raw = AsyncMock(return_value=None)

    await radio.send_cw_text("")
    await radio.stop_cw_text()
    await radio.set_tuner_status(0)

    runner.run.assert_not_awaited()
    assert radio._send_civ_raw.await_args_list[0].kwargs == {
        "priority": Priority.IMMEDIATE
    }
    assert radio._send_civ_raw.await_args_list[1].kwargs == {"wait_response": False}
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_unmanaged_calls_and_public_signatures_stay_direct() -> None:
    radio = _icom_radio()
    radio._send_civ_expect = AsyncMock(return_value=_ack())
    radio._send_civ_raw = AsyncMock(return_value=None)

    await radio.send_cw_text("CQ")
    await radio.set_tuner_status(2)

    assert radio._send_civ_expect.await_args.kwargs == {"label": "send_cw_text"}
    assert radio._send_civ_raw.await_args.kwargs == {"wait_response": False}
    assert "is_current" not in inspect.signature(CoreRadio.send_cw_text).parameters
    assert "is_current" not in inspect.signature(CoreRadio.stop_cw_text).parameters
    assert "is_current" not in inspect.signature(CoreRadio.set_tuner_status).parameters


@pytest.mark.asyncio
async def test_force_receive_cancels_a_cw_send_still_waiting_for_the_keyer_lock() -> (
    None
):
    # MOR-3678: send B waits for send A's KM+KY pair. A force-off must end
    # B too: nothing submitted before the forced receive may key afterwards.
    fence = TxAbortFence()
    radio = _fenced_radio(fence)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    first = asyncio.create_task(radio.send_cw_text("AA"))
    await first_drain_started.wait()
    second = asyncio.create_task(radio.send_cw_text("BB"))
    for _ in range(5):
        await asyncio.sleep(0)

    cleanup = fence.force_off()
    force_receive = asyncio.create_task(_force_receive(radio))
    await asyncio.sleep(0)
    release_first_drain.set()

    assert await force_receive is ActuationResult.ACCEPTED
    results = await asyncio.gather(first, second, return_exceptions=True)
    await cleanup
    assert all(isinstance(r, BaseException) for r in results), results

    wire_commands = [
        call.args[0] for call in radio._transport._raw_write.await_args_list
    ]
    assert not any(c.startswith("KY0") for c in wire_commands), wire_commands
    assert not any(c.startswith("KM5BB") for c in wire_commands), wire_commands


@pytest.mark.asyncio
async def test_stop_cw_text_ends_a_send_whose_memory_write_is_in_flight() -> None:
    # MOR-3678: stop queued behind an in-flight KM must not be followed by
    # that send's KY; the send ends instead of playing after the stop.
    radio = _fenced_radio(None)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    stopping = asyncio.create_task(radio.stop_cw_text())
    await asyncio.sleep(0)
    release_first_drain.set()

    await stopping
    with pytest.raises(CatTransportError, match="no longer current"):
        await sending
    wire_commands = [
        call.args[0] for call in radio._transport._raw_write.await_args_list
    ]
    assert wire_commands == ["KM5CQ;", "KY00;"]


@pytest.mark.asyncio
async def test_stop_cw_text_ends_a_send_still_waiting_for_the_keyer_lock() -> None:
    radio = _fenced_radio(None)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    first = asyncio.create_task(radio.send_cw_text("AA"))
    await first_drain_started.wait()
    second = asyncio.create_task(radio.send_cw_text("BB"))
    for _ in range(5):
        await asyncio.sleep(0)
    stopping = asyncio.create_task(radio.stop_cw_text())
    await asyncio.sleep(0)
    release_first_drain.set()

    await stopping
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(r, CatTransportError) for r in results), results
    wire_commands = [
        call.args[0] for call in radio._transport._raw_write.await_args_list
    ]
    assert wire_commands == ["KM5AA;", "KY00;"]


@pytest.mark.asyncio
async def test_managed_cw_abort_ends_a_send_whose_memory_write_is_in_flight() -> None:
    # MOR-3678: the managed STOP_CW abort (force-off / shutdown) ends an
    # earlier send_cw_text like stop_cw_text does; its KY never follows.
    radio = _fenced_radio(None)
    writer = _real_wire(radio)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    aborting = asyncio.create_task(
        radio.actuate(_effect_token(), AbortOperation.STOP_CW, is_current=lambda: True)
    )
    await asyncio.sleep(0)
    release_first_drain.set()

    await aborting
    with pytest.raises(CatTransportError, match="no longer current"):
        await sending
    assert writer.frames == [b"KM5CQ;", b"KY00;"]


class _RecordingWriter:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.frames.append(data)

    async def drain(self) -> None:
        return None


def _real_wire(radio: YaesuCatRadio) -> _RecordingWriter:
    """Use the transport's real write path, down to the bytes written."""
    transport = radio._transport
    writer = _RecordingWriter()
    transport._writer = writer
    transport._reader = object()
    del transport._raw_write
    return writer


async def _stale_cw_abort(radio: YaesuCatRadio, is_current: Callable[[], bool]) -> None:
    # A stale abort is refused at the transport; how actuate reports that
    # is not under test here, only that nothing reached the wire.
    try:
        await radio.actuate(
            _effect_token(), AbortOperation.STOP_CW, is_current=is_current
        )
    except CatTransportError:
        pass


@pytest.mark.asyncio
async def test_stale_managed_cw_abort_leaves_an_in_flight_send_alone() -> None:
    # MOR-3678 (#4317 review): an abort refused as stale at the transport
    # never reached the radio, so it must not end a newer send.
    radio = _fenced_radio(None)
    writer = _real_wire(radio)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    aborting = asyncio.create_task(_stale_cw_abort(radio, lambda: False))
    await asyncio.sleep(0)
    release_first_drain.set()

    await aborting
    await sending
    assert writer.frames == [b"KM5CQ;", b"KY05;"]


@pytest.mark.asyncio
async def test_managed_cw_abort_superseded_while_queued_leaves_sends_alone() -> None:
    # The abort was current when issued but was superseded while it waited
    # for the transport; the in-flight and the queued send both still play.
    radio = _fenced_radio(None)
    writer = _real_wire(radio)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0
    abort_current = True

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    first = asyncio.create_task(radio.send_cw_text("AA"))
    await first_drain_started.wait()
    aborting = asyncio.create_task(_stale_cw_abort(radio, lambda: abort_current))
    second = asyncio.create_task(radio.send_cw_text("BB"))
    for _ in range(5):
        await asyncio.sleep(0)
    abort_current = False
    release_first_drain.set()

    await aborting
    await asyncio.gather(first, second)
    assert writer.frames == [b"KM5AA;", b"KY05;", b"KM5BB;", b"KY05;"]


@pytest.mark.asyncio
async def test_cw_abort_expiring_between_transport_checks_leaves_the_send_alone() -> (
    None
):
    # #4317 pre-review: the transport checks currency twice (write() and
    # _raw_write()). A managed stop whose claim expires between the two
    # never reaches the wire, so it must not end the pending send either.
    radio = _fenced_radio(None)
    transport = radio._transport
    writer = _real_wire(radio)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    transport._drain_responses = drain
    checks = 0

    def expires_after_first_check() -> bool:
        nonlocal checks
        checks += 1
        return checks == 1

    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    generation = radio._cw_text_generation
    aborting = asyncio.create_task(_stale_cw_abort(radio, expires_after_first_check))
    await asyncio.sleep(0)
    release_first_drain.set()

    await aborting
    await sending
    assert checks == 2
    assert radio._cw_text_generation == generation
    assert writer.frames == [b"KM5CQ;", b"KY05;"]


class _RejectingWriter(_RecordingWriter):
    """A serial writer that synchronously refuses one frame (port gone)."""

    def __init__(self, refused: bytes) -> None:
        super().__init__()
        self._refused = refused

    def write(self, data: bytes) -> None:
        if data == self._refused:
            raise OSError("serial port write refused")
        super().write(data)


@pytest.mark.asyncio
async def test_managed_cw_abort_the_writer_refuses_leaves_the_send_alone() -> None:
    # #4317 gate review: the managed stop ends earlier sends only once the
    # writer has accepted its KY bytes. A writer that refuses KY00 wrote no
    # stop, so the in-flight send stays current and still plays.
    radio = _fenced_radio(None)
    transport = radio._transport
    writer = _RejectingWriter(b"KY00;")
    transport._writer = writer
    transport._reader = object()
    del transport._raw_write
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    transport._drain_responses = drain
    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    generation = radio._cw_text_generation
    aborting = asyncio.create_task(
        radio.actuate(_effect_token(), AbortOperation.STOP_CW, is_current=None)
    )
    await asyncio.sleep(0)
    release_first_drain.set()

    with pytest.raises(CatTransportError, match="serial port write refused"):
        await aborting
    assert radio._cw_text_generation == generation
    await sending
    assert writer.frames == [b"KM5CQ;", b"KY05;"]


class _DrainFailsAfterWriter(_RecordingWriter):
    """A serial writer that accepts one frame, then fails to flush it."""

    def __init__(self, frame: bytes) -> None:
        super().__init__()
        self._frame = frame

    async def drain(self) -> None:
        if self.frames and self.frames[-1] == self._frame:
            raise OSError("serial port flush failed")


@pytest.mark.asyncio
async def test_managed_cw_abort_the_writer_accepted_ends_the_send() -> None:
    # The writer took KY00 (it may already be on its way to the radio)
    # before the flush failed: the stop counts, the in-flight send ends and
    # its KY never plays.
    radio = _fenced_radio(None)
    transport = radio._transport
    writer = _DrainFailsAfterWriter(b"KY00;")
    transport._writer = writer
    transport._reader = object()
    del transport._raw_write
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    transport._drain_responses = drain
    sending = asyncio.create_task(radio.send_cw_text("CQ"))
    await first_drain_started.wait()
    generation = radio._cw_text_generation
    aborting = asyncio.create_task(
        radio.actuate(_effect_token(), AbortOperation.STOP_CW, is_current=None)
    )
    await asyncio.sleep(0)
    release_first_drain.set()

    with pytest.raises(CatTransportError, match="serial port flush failed"):
        await aborting
    assert radio._cw_text_generation == generation + 1
    with pytest.raises(CatTransportError, match="no longer current"):
        await sending
    assert writer.frames == [b"KM5CQ;", b"KY00;"]
