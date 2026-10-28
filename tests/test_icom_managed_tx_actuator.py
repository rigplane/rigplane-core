"""Canonical Icom actuator contract for runtime-managed transmit effects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.command_map import CommandMap
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.commands.bound import BoundCommands
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.exceptions import ConnectionError
from rigplane.core.types import CivFrame
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import CoreRadio, IcomRadio
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_serial_civ_link import _FakeWriter, _cleanup_writes, _make_link


class _TrackedTransport:
    def __init__(self, *, entered: asyncio.Event | None = None) -> None:
        self.my_id = 0x01020304
        self.remote_id = 0x05060708
        self.sent: list[bytes] = []
        self._entered = entered
        self.release = asyncio.Event()
        if entered is None:
            self.release.set()

    async def send_tracked(self, packet: bytes, **_kwargs: object) -> None:
        self.sent.append(packet)
        if self._entered is not None:
            self._entered.set()
        await self.release.wait()


def _profile_bound_radio() -> IcomRadio:
    radio = IcomRadio("", model="IC-7610")
    radio._commands = BoundCommands(
        CommandMap(
            {
                "ptt_on": (0x31, 0x01, 0xA1),
                "ptt_off": (0x31, 0x01, 0xA0),
                "stop_cw": (0x32, 0x02),
                "set_tuner_status": (0x33, 0x03),
            }
        )
    )
    return radio


def _token() -> EffectToken:
    return EffectToken(7, 3, "icom-actuator")


def test_icom_radio_satisfies_managed_tx_actuator_protocol() -> None:
    assert isinstance(_profile_bound_radio(), ManagedTxActuator)


@pytest.mark.asyncio
async def test_ptt_and_transmit_on_share_one_profile_command_and_immediate_lane() -> (
    None
):
    radio = _profile_bound_radio()
    radio._send_civ_raw = AsyncMock(return_value=None)

    def current() -> bool:
        return True

    results = [
        await radio.actuate(_token(), operation, is_current=current)
        for operation in (
            ActuationOperation.PTT_ON,
            ActuationOperation.TRANSMIT_ON,
        )
    ]

    assert results == [ActuationResult.ACCEPTED, ActuationResult.ACCEPTED]
    assert [call.args[0] for call in radio._send_civ_raw.await_args_list] == [
        radio._commands.ptt_on(to_addr=radio._radio_addr),
        radio._commands.ptt_on(to_addr=radio._radio_addr),
    ]
    for call in radio._send_civ_raw.await_args_list:
        assert call.kwargs == {
            "priority": Priority.IMMEDIATE,
            "wait_response": False,
            "is_current": current,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "expected", "priority"),
    [
        (
            ActuationOperation.FORCE_RECEIVE,
            (0x31, None, b"\x01\xa0"),
            Priority.FORCE_RELEASE,
        ),
        (AbortOperation.STOP_CW, (0x32, None, b"\x02\xff"), Priority.ABORT),
        (AbortOperation.STOP_TUNE, (0x33, None, b"\x03\x00"), Priority.ABORT),
    ],
)
async def test_release_and_abort_operations_use_profile_bytes_at_strict_priority(
    operation: ActuationOperation | AbortOperation,
    expected: tuple[int, int | None, bytes],
    priority: Priority,
) -> None:
    radio = _profile_bound_radio()
    radio._send_civ_raw = AsyncMock(
        return_value=CivFrame(
            to_addr=CONTROLLER_ADDR, from_addr=radio._radio_addr, command=0xFB
        )
    )

    def current() -> bool:
        return True

    result = await radio.actuate(_token(), operation, is_current=current)

    assert result is ActuationResult.ACCEPTED
    call = radio._send_civ_raw.await_args
    frame = parse_civ_frame(call.args[0])
    assert (frame.command, frame.sub, frame.data) == expected
    # MOR-2860: only the unkey waits for the radio's answer.
    assert call.kwargs == {
        "priority": priority,
        "wait_response": operation is ActuationOperation.FORCE_RECEIVE,
        "is_current": current,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (0xFB, ActuationResult.ACCEPTED),
        (0xFA, ActuationResult.REJECTED),
        (None, ActuationResult.UNCERTAIN),
    ],
    ids=["fb", "fa", "no-answer"],
)
async def test_force_receive_settles_on_the_radio_answer(
    answer: int | None, expected: ActuationResult
) -> None:
    link = _FakeSerialCivLink(ptt_off_answer=answer)
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    try:
        result = await radio.actuate(
            _token(), ActuationOperation.FORCE_RECEIVE, is_current=lambda: True
        )
        sent = list(link.sent_frames)
    finally:
        await radio.disconnect()

    assert result is expected
    assert sent == [
        bytes(radio._commands.get_transceiver_id(to_addr=radio._radio_addr)),
        bytes(radio._commands.ptt_off(to_addr=radio._radio_addr)),
    ] + (
        [build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0)]
        if answer is None
        else []
    )


async def _force_receive(radio: IcomRadio) -> ActuationResult:
    return await radio.actuate(
        _token(), ActuationOperation.FORCE_RECEIVE, is_current=lambda: True
    )


@pytest.mark.asyncio
async def test_a_late_answer_to_a_timed_out_unkey_does_not_settle_the_next() -> None:
    """MOR-2860: FB names no command, so an FB in the second unkey's window
    may be the first one's late answer; one answer window later an FB counts
    again."""
    link = _FakeSerialCivLink(ptt_off_answer=None)
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.5
    await radio.connect()
    try:
        results = [await _force_receive(radio)]
        link.queue_response_on_send(
            len(link.sent_frames) + 1,
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFB),
        )
        results.append(await _force_receive(radio))
        await asyncio.sleep(radio._civ_get_timeout)
        link.ptt_off_answer = 0xFB
        results.append(await _force_receive(radio))
    finally:
        await radio.disconnect()

    assert results == [
        ActuationResult.UNCERTAIN,
        ActuationResult.UNCERTAIN,
        ActuationResult.ACCEPTED,
    ]


@pytest.mark.asyncio
async def test_an_unkey_after_a_dropped_write_sink_stays_uncertain() -> None:
    """MOR-2860: a fire-and-forget write still unanswered when the unkey starts
    loses its sink, so an FB in the unkey's window may be that write's."""
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    try:
        await radio._send_civ_raw(
            build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x0F, data=b"\x01"),
            wait_response=False,
        )
        result = await _force_receive(radio)
        assert parse_civ_frame(link.sent_frames[-1]).data == b""
    finally:
        await radio.disconnect()

    assert result is ActuationResult.UNCERTAIN


@pytest.mark.asyncio
async def test_force_release_overtakes_queued_abort_without_preempting_active() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    executed: list[bytes] = []

    async def execute(payload: bytes, _wait_response: bool) -> None:
        executed.append(payload)
        if payload == b"active":
            entered.set()
            await release.wait()

    commander = IcomCommander(execute)
    commander.start()
    active = asyncio.create_task(commander.send(b"active", priority=Priority.NORMAL))
    abort = None
    late_on = None
    force = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        abort = asyncio.create_task(commander.send(b"stop-cw", priority=Priority.ABORT))
        late_on = asyncio.create_task(
            commander.send(b"late-on", priority=Priority.IMMEDIATE)
        )
        await asyncio.sleep(0)
        force = asyncio.create_task(
            commander.send(b"force-off", priority=Priority.FORCE_RELEASE)
        )
        await asyncio.sleep(0)

        assert executed == [b"active"]
        release.set()
        await asyncio.wait_for(asyncio.gather(active, force, abort, late_on), 1)
        assert executed == [b"active", b"force-off", b"stop-cw", b"late-on"]
    finally:
        release.set()
        for task in (active, force, abort, late_on):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (active, force, abort, late_on) if task is not None),
            return_exceptions=True,
        )
        await commander.stop()


@pytest.mark.asyncio
async def test_unsupported_profile_refuses_before_any_io() -> None:
    radio = IcomRadio("", model="IC-7610")
    radio._commands = BoundCommands(
        CommandMap({}),
        {"ptt_on": "test manufacturer command table"},
    )
    radio._send_civ_raw = AsyncMock(side_effect=AssertionError("I/O attempted"))

    result = await radio.actuate(
        _token(), ActuationOperation.PTT_ON, is_current=lambda: True
    )

    assert result is ActuationResult.REJECTED
    radio._send_civ_raw.assert_not_awaited()


@pytest.mark.asyncio
async def test_commander_propagates_currency_to_final_executor() -> None:
    observed: list[Callable[[], bool] | None] = []

    async def execute(
        _payload: bytes,
        _wait_response: bool,
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        observed.append(is_current)

    commander = IcomCommander(execute)

    def current() -> bool:
        return True

    commander.start()
    try:
        await commander.send(b"managed", is_current=current)
    finally:
        await commander.stop()

    assert observed == [current]


@pytest.mark.asyncio
async def test_provider_replacement_cannot_retarget_queued_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _profile_bound_radio()
    entered = asyncio.Event()
    old_transport = _TrackedTransport(entered=entered)
    replacement = _TrackedTransport()
    radio._civ_transport = old_transport
    radio._connected = True
    radio._civ_min_interval = 0.0
    monkeypatch.setattr(radio._civ_runtime, "start_pump", lambda: None)
    commander = IcomCommander(radio._civ_runtime.execute_civ_raw)
    radio._commander = commander
    commander.start()
    blocker = asyncio.create_task(
        radio._send_civ_raw(
            build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x55, data=b"\x01"),
            wait_response=False,
        )
    )
    currency = {"current": True}
    on = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        on = asyncio.create_task(
            radio.actuate(
                _token(),
                ActuationOperation.PTT_ON,
                is_current=lambda: currency["current"],
            )
        )
        await asyncio.sleep(0)
        currency["current"] = False
        radio._civ_transport = replacement
        old_transport.release.set()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(blocker, 1)
        with pytest.raises(ConnectionError, match="managed TX attempt is stale"):
            await asyncio.wait_for(on, 1)
        assert len(old_transport.sent) == 1
        assert replacement.sent == []
    finally:
        old_transport.release.set()
        for task in (blocker, on):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (blocker, on) if task is not None),
            return_exceptions=True,
        )
        await commander.stop()
        radio._commander = None
        radio._civ_transport = None
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_response", [False, True])
async def test_serial_writer_rechecks_managed_currency(
    monkeypatch: pytest.MonkeyPatch, wait_response: bool
) -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(writer=_FakeWriter(drain_gate=gate))
    transport = SerialCivTransport(link)
    radio = _profile_bound_radio()
    radio._civ_transport = transport
    radio._connected = True
    monkeypatch.setattr(radio._civ_runtime, "start_pump", lambda: None)
    state = {"current": True, "calls": 0}
    entered = asyncio.Event()
    on = None

    def is_current() -> bool:
        state["calls"] += 1
        if state["calls"] == 1 + wait_response:
            entered.set()
        return state["current"]

    try:
        await link.send(b"\x98\xe0\x03")
        await asyncio.wait_for(writer.drain_started.wait(), 1)
        on = asyncio.create_task(
            radio._civ_runtime.execute_civ_raw(
                radio._commands.ptt_on(to_addr=radio._radio_addr),
                wait_response=wait_response,
                is_current=is_current,
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        state["current"] = False
        gate.set()
        with pytest.raises(ConnectionError, match="managed TX attempt is stale"):
            await asyncio.wait_for(on, 1)
        assert len(writer.writes) == 1, "stale ON reached serial writer"
        assert transport.send_seq == transport._udp_error_count == 0
        assert radio._civ_request_tracker.pending_count == 0
        assert link.ready and link._write_queue.empty()
    finally:
        await _cleanup_writes(link, [on] if on else [], writer)


@pytest.fixture(params=["lan", "7610-serial", "7300-serial"])
async def release_radio(request, monkeypatch):
    from _perf_helpers import fast_connect
    from rigplane.backends.ic7300 import Ic7300SerialRadio
    from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
    from rigplane_testkit.icom import MockIcomRadio
    from rigplane_testkit.serial import CivSerialFake
    from tx_observation_fakes import civ_transmit_state_reply

    script = {"read": "rx", "off": "drop-ack", "reads": 0, "offs": 0}
    read_sent = asyncio.Event()

    def answer(frame, original):
        if frame.command == 0x1C and frame.sub == 0:
            if frame.data == b"\x00":
                script["offs"] += 1
                if script["off"] == "nak":
                    return build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0xFA)
                return original if script["off"] == "ack" else None
            if not frame.data:
                script["reads"] += 1
                read_sent.set()
                return civ_transmit_state_reply(script["read"], radio._radio_addr)
        return original

    sim = None
    if request.param == "lan":
        sim = MockIcomRadio(stateful_ptt=True, unsolicited_civ=False)
        await sim.start()
        radio = IcomRadio(
            "127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        dispatch = sim._dispatch_civ

        def lan(cmd, payload, from_addr):
            raw = build_civ_frame(sim._radio_addr, from_addr, cmd, data=payload)
            frame = parse_civ_frame(raw)
            original = (
                None
                if script["off"] == "lost" and frame.data == b"\x00" and cmd == 0x1C
                else dispatch(cmd, payload, from_addr)
            )
            return answer(frame, original)

        monkeypatch.setattr(sim, "_dispatch_civ", lan)
    else:
        model = "IC-7610" if request.param == "7610-serial" else "IC-7300"
        fake = CivSerialFake(model=model)
        link = SerialCivLink(
            device="/dev/fake", open_serial_connection=fake.open_serial_connection
        )
        cls = Icom7610SerialRadio if model == "IC-7610" else Ic7300SerialRadio
        radio = cls(
            device="/dev/fake", civ_link=link, _enumerate_serial_ports_fn=lambda: []
        )
        await radio.connect()
        dispatch = fake._answer

        def serial(frame, conn, raw):
            original = (
                None
                if script["off"] == "lost"
                and frame.command == 0x1C
                and frame.data == b"\x00"
                else dispatch(frame, conn, raw)
            )
            return answer(frame, original)

        monkeypatch.setattr(fake, "_answer", serial)
    script["ptt"] = (lambda: sim.ptt_on) if sim is not None else (lambda: fake.ptt)
    radio._civ_get_timeout = 0.03
    monkeypatch.setattr(CoreRadio, "_PTT_READBACK_SETTLE_S", 0.001)
    radio._civ_min_interval = radio._civ_ack_sink_grace = 0
    radio._civ_runtime._ptt_ledger = None
    assert radio.connected
    runtime = radio._civ_runtime
    raw = build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0)

    async def inject(shape="rx"):
        frame = parse_civ_frame(civ_transmit_state_reply(shape, radio._radio_addr))
        await runtime._route_civ_frame(frame, generation=radio._civ_epoch)
        return frame

    async def start_read(wait_response=False):
        read_sent.clear()
        pending = asyncio.create_task(
            runtime.execute_civ_raw(raw, wait_response=wait_response)
        )
        await asyncio.wait_for(read_sent.wait(), 1)
        return pending, runtime._ptt_reply_ledger._owed[-1]

    async def replay(packet):
        radio._civ_transport._packet_queue.put_nowait(packet)
        # A normal answer wakes a serial pump already awaiting its link reader.
        await runtime.execute_civ_raw(
            build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x03),
            wait_response=False,
        )

    try:
        yield radio, script, read_sent, replay, inject, start_read
    finally:
        with fast_connect():
            await radio.disconnect()
        if sim is not None:
            await sim.stop()


@pytest.mark.parametrize(
    "case,prior,answer,expected",
    [
        ("A1a-silent", "silence", "rx", "uncertain"),
        ("A1b-beyond-L", "expired", "stale", "accepted"),
        ("A2-malformed", "unmapped", "rx", "uncertain"),
        ("A2-broadcast", "broadcast", "rx", "uncertain"),
        ("A3a-owed", "silence", "rx", "uncertain"),
        ("A3b-twin", "cached", "twin", "uncertain"),
        ("A3c-after-Q", "cached", "twin", "uncertain"),
        ("A4-sink", "sink", "rx", "uncertain"),
        ("A5-nak", "refusal", "rx", "uncertain"),
        ("A6-rx", None, "rx", "accepted"),
        ("A6-tx", None, "tx_cat", "uncertain"),
        ("A7-second-emission", None, "rx", "accepted"),
        ("A8-orphan", "orphan", "rx", "uncertain"),
        ("A9-generation", "generation", "rx", "uncertain"),
        ("A10-fb", None, "ack", "uncertain"),
        ("A10-fa", None, "refusal", "uncertain"),
        ("E0-single-reply-limit", None, "foreign", "accepted"),
        ("E-in-code-predispatch", "orphan", "rx", "uncertain"),
        ("E-in-code-settle-read", None, "settle", "uncertain"),
        ("E-in-code-settle-release", None, "settle", "uncertain"),
        ("FIFO-LAN-benign", None, "concurrent", "accepted"),
        ("new-header", None, "re-emitted", "accepted"),
        ("B-malformed", None, "unmapped", "uncertain"),
        ("B-broadcast", None, "broadcast", "uncertain"),
        ("clean-fb", None, "rx", "accepted"),
        ("clean-fa", None, "rx", "rejected"),
        ("ambiguous-fa", None, "rx", "accepted"),
        ("unsupported-read", None, "rx", "uncertain"),
    ],
)
async def test_two_read_release_matrix(
    release_radio, monkeypatch, case, prior, answer, expected
):
    from dataclasses import replace
    from _helpers import wrap_civ_in_udp
    from tx_observation_fakes import civ_transmit_state_reply
    from rigplane.core.civ import PttReplyLedger
    from rigplane.core.exceptions import CommandRejectedError, TimeoutError
    from rigplane.runtime._civ_rx import _PttReadContext

    radio, script, read_sent, replay, inject, start_read = release_radio
    runtime = radio._civ_runtime
    clock = [0.0]
    runtime._ptt_ledger = ledger = PttReplyLedger(
        clock=lambda: clock[0], answer_window=2
    )

    blocked = prior not in (None, "expired", "cached")
    script["read"] = "silence"
    if prior not in (None, "orphan"):
        waiter = prior in ("unmapped", "broadcast", "refusal", "silence", "cached")
        pending, a = await start_read(waiter)
        if prior in ("unmapped", "broadcast", "refusal"):
            await inject(prior)
        if prior in ("refusal", "silence"):
            with pytest.raises(
                CommandRejectedError if prior == "refusal" else TimeoutError
            ):
                await pending
        elif prior != "cached":
            await pending
        assert (
            ledger._owed[0] is a and a.answered_by is None and ledger.orphan_count == 0
        )
        if prior == "sink":
            radio._civ_request_tracker.drop_ack_sinks()
        if prior == "generation":
            runtime.advance_generation("test replacement")
    if prior == "orphan":
        await inject()
    if prior == "expired":
        assert script["ptt"]() is False
        clock[0] = ledger.lifetime + 0.001
    if answer == "re-emitted" or case.startswith("A6-"):
        script["read"] = "rx"
        assert (await radio.read_transmit_state()).value is False
        assert ledger.eligible() and ledger.orphan_count == 0
    if answer in ("stale", "re-emitted"):
        radio._civ_request_tracker.drop_ack_sinks()
        await radio.actuate(
            _token(), ActuationOperation.PTT_ON, is_current=lambda: True
        )
        async with asyncio.timeout(1):
            while radio._civ_request_tracker.ack_sink_count:
                await asyncio.sleep(0)
        script["off"] = "lost"
    if case in ("clean-fb", "clean-fa", "ambiguous-fa"):
        script["off"] = "ack" if case == "clean-fb" else "nak"
        if case == "ambiguous-fa":
            radio._civ_request_tracker.note_ack_unclaimed()
    if case == "unsupported-read":
        radio._profile = replace(radio._profile, command_names=frozenset())
    held = answer in (
        "stale",
        "re-emitted",
        "foreign",
        "concurrent",
        "twin",
        "ack",
        "refusal",
    )
    script["read"] = "silence" if held else "rx" if answer == "settle" else answer
    if prior == "cached":
        packet = wrap_civ_in_udp(
            civ_transmit_state_reply("rx", radio._radio_addr), seq=0
        )
        await replay(packet)
        assert await pending is a.answered_by
        assert ledger.eligible() and ledger.orphan_count == 0
        if case == "A3c-after-Q":
            runtime._ptt_reply_packets[packet] -= 2.1
    read_sent.clear()
    before = script["reads"]
    note_dispatch = runtime._note_ptt_dispatch

    def observe(frame, is_current=None):
        note_dispatch(frame, is_current)
        if answer == "settle" and isinstance(is_current, _PttReadContext):
            current = is_current.is_current

            def settle_current():
                if (
                    asyncio.current_task() is task
                    and is_current.entry.answered_by is not None
                ):
                    ledger.note_reply(
                        parse_civ_frame(
                            civ_transmit_state_reply("rx", radio._radio_addr)
                        )
                    )
                return current is None or current()

            is_current.is_current = settle_current

    monkeypatch.setattr(runtime, "_note_ptt_dispatch", observe)
    task = asyncio.create_task(
        radio.read_transmit_state()
        if case.endswith("settle-read")
        else _force_receive(radio)
    )
    try:
        if held:
            await asyncio.wait_for(read_sent.wait(), 1)
            if answer == "concurrent":
                pending, c = await start_read()
                await pending
                b = ledger._owed[0]
                assert await inject() is b.answered_by and c.answered_by is None
            elif answer == "twin":
                await replay(packet)
            elif answer == "re-emitted":
                await replay(
                    wrap_civ_in_udp(
                        civ_transmit_state_reply("rx", radio._radio_addr), seq=321
                    )
                )
            else:
                await inject(answer if answer in ("ack", "refusal") else "rx")
        result = await asyncio.wait_for(task, 1)
        if case.endswith("settle-read"):
            assert result.value is None and result.failure == "unverifiable-provenance"
        else:
            assert result is ActuationResult(expected)
        if answer == "settle":
            assert ledger.orphan_count == 1
            assert (await radio.read_transmit_state()).failure == "not-quiescent"
        no_read = blocked or case in ("clean-fb", "clean-fa", "unsupported-read")
        assert script["reads"] - before == (
            0 if no_read else 1 + (answer == "concurrent")
        )
        if answer in ("stale", "re-emitted"):
            assert script["ptt"]() is True
        if blocked and prior != "orphan":
            assert ledger._owed[0] is a and a.answered_by is None
            assert await inject() is a.answered_by
            assert ledger.owed_count == ledger.orphan_count == 0
        if case == "A7-second-emission":
            await inject()
            assert not runtime._ptt_reply_ledger.eligible()
        if not case.endswith("settle-read"):
            assert script["offs"] > 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_normal_polling_confirms_on_first_eligible_retry(
    release_radio, monkeypatch
):
    from rigplane.runtime._civ_rx import _PttReadContext

    radio, script, _, _, inject, start_read = release_radio
    runtime = radio._civ_runtime
    confirmations = []
    note_dispatch = runtime._note_ptt_dispatch

    def observe(frame, is_current=None):
        note_dispatch(frame, is_current)
        if isinstance(is_current, _PttReadContext):
            confirmations.append(is_current.entry)

    monkeypatch.setattr(runtime, "_note_ptt_dispatch", observe)
    raw = build_civ_frame(radio._radio_addr, CONTROLLER_ADDR, 0x1C, sub=0)
    script["read"] = "silence"
    pending, a = await start_read()
    await pending
    assert await _force_receive(radio) is ActuationResult.UNCERTAIN
    assert not confirmations
    assert await inject() is a.answered_by
    script["read"] = "rx"

    async def polling():
        while True:
            await runtime.execute_civ_raw(raw, wait_response=False)
            await asyncio.sleep(0.02)

    poller = asyncio.create_task(polling())
    await asyncio.sleep(0)
    try:
        async with asyncio.timeout(1):
            while True:
                result = await _force_receive(radio)
                if confirmations:
                    assert result is ActuationResult.ACCEPTED
                    assert confirmations[0].answered_by.data == b"\x00"
                    break
                assert result is ActuationResult.UNCERTAIN
        assert script["reads"] >= 3
    finally:
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
