from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from _helpers import wrap_civ_in_udp
from tx_observation_fakes import (
    ScriptedCivLink,
    ScriptedLanTransport,
    civ_transmit_state_reply,
)

from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.civ import (
    PTT_REPLY_QUIESCENCE_S,
    PttReplyLedger,
    is_directed_ptt_reply,
    request_key_from_frame,
)
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.transport import IcomTransport
from rigplane.runtime import _civ_rx
from rigplane.runtime.radio import CoreRadio, IcomRadio


class _LanTransport(ScriptedLanTransport, IcomTransport):
    pass


def _request():
    return parse_civ_frame(build_civ_frame(0x98, CONTROLLER_ADDR, 0x1C, sub=0))


def _reply(shape="rx"):
    return parse_civ_frame(civ_transmit_state_reply(shape, 0x98))


@pytest.mark.parametrize(
    "shape,valid",
    [
        ("rx", True),
        ("tx_cat", True),
        ("broadcast", False),
        ("unmapped", False),
        ("misaddressed", False),
        ("setter_echo", False),
        ("ack", False),
    ],
)
def test_directed_shape_and_controller_address(shape, valid):
    assert CONTROLLER_ADDR == 0xE0
    assert is_directed_ptt_reply(_reply(shape), 0x98) is valid
    for data in (b"", b"\x02"):
        frame = parse_civ_frame(build_civ_frame(0xE0, 0x98, 0x1C, sub=0, data=data))
        assert not is_directed_ptt_reply(frame, 0x98)


def test_fifo_identity_orphan_and_writes():
    now = [0.0]
    ledger = PttReplyLedger(clock=lambda: now[0], answer_window=2)
    assert PTT_REPLY_QUIESCENCE_S == 2.0
    assert ledger.lifetime == 4.0
    a, b = ledger.note_dispatch(_request()), ledger.note_dispatch(_request())
    ledger.note_written(b)
    reply = _reply()
    assert ledger.note_reply(reply) is b and b.answered_by is reply
    assert not ledger.eligible()
    ledger.note_written(a)
    assert ledger.note_reply(reply) is a and a.answered_by is reply
    assert ledger.eligible() and ledger.quarantine_until == 0
    assert ledger.note_reply(reply) is None
    assert ledger.orphan_count == 1 and ledger.quarantine_until == 2
    assert not ledger.eligible()
    now[0] = 2
    assert ledger.eligible()
    for shape in ("rx", "tx_cat", "ack"):
        assert ledger.note_dispatch(_reply(shape)) is None
    assert ledger.owed_count == 0


@pytest.mark.parametrize("age,owed", [(3.999, 1), (4.0, 1), (4.001, 0)])
@pytest.mark.parametrize("operation", ["eligible", "dispatch", "reply"])
def test_expiry_is_strict_and_has_no_answer(age, owed, operation):
    now = [0.0]
    ledger = PttReplyLedger(clock=lambda: now[0], answer_window=2)
    entry = ledger.note_dispatch(_request())
    ledger.note_written(entry)
    now[0] = age
    if operation == "dispatch":
        ledger.note_dispatch(_request())
        assert ledger.owed_count == owed + 1
    elif operation == "reply":
        assert (ledger.note_reply(_reply()) is entry) is bool(owed)
        assert ledger.orphan_count == 1 - owed
    else:
        assert ledger.eligible() is (not owed)
        assert ledger.owed_count == owed
    if not owed or operation != "reply":
        assert entry.answered_by is None


@pytest.fixture
async def radio(monkeypatch):
    radio = IcomRadio("", model="IC-7610", timeout=0.03)
    radio._civ_transport = _LanTransport()
    radio._civ_transport.answer = "silence"
    radio._connected = True
    radio._civ_min_interval = radio._civ_ack_sink_grace = 0
    monkeypatch.setattr(CoreRadio, "_PTT_READBACK_SETTLE_S", 0.001)
    try:
        yield radio
    finally:
        await radio._civ_runtime.stop_worker()
        if radio._civ_rx_task is not None:
            radio._civ_rx_task.cancel()
            await radio._civ_rx_task
        radio._connected = False


async def _inject(radio, shape="rx", generation=None):
    frame = _reply(shape)
    await radio._civ_runtime._route_civ_frame(
        frame, generation=radio._civ_epoch if generation is None else generation
    )
    return frame


@pytest.mark.parametrize(
    "ending",
    [
        "valid",
        "malformed",
        "sink-removed",
        "timeout",
        "cancel",
        "none",
        "raise",
        "raise-wait",
        "raise-none",
        "foreign-radio",
        "foreign-controller",
    ],
)
async def test_poll_debt_outlives_waiters_and_sinks(radio, ending, monkeypatch):
    runtime = radio._civ_runtime
    raw = build_civ_frame(0x98, CONTROLLER_ADDR, 0x1C, sub=0)
    if ending.startswith("foreign"):
        raw = build_civ_frame(
            0x88 if ending == "foreign-radio" else 0x98,
            0xE1 if ending == "foreign-controller" else 0xE0,
            0x1C,
            sub=0,
        )
        await runtime.execute_civ_transaction(raw, expect="none")
        assert runtime._ptt_reply_ledger.eligible()
        assert (await radio.read_transmit_state()).failure != "not-quiescent"
        return
    if ending.startswith("raise"):

        async def broken(_packet):
            raise OSError("send failed")

        monkeypatch.setattr(radio._civ_transport, "send_tracked", broken)
        with pytest.raises(OSError):
            if ending == "raise-none":
                await runtime.execute_civ_transaction(raw, expect="none")
            else:
                await runtime.execute_civ_raw(raw, wait_response=ending == "raise-wait")
    elif ending == "none":
        await runtime.execute_civ_transaction(raw, expect="none")
    elif ending in ("timeout", "cancel"):
        task = asyncio.create_task(runtime.execute_civ_raw(raw))
        while not radio._civ_transport.sent_packets:
            await asyncio.sleep(0)
        if ending == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if ending == "cancel" else TimeoutError
        ):
            await task
    else:
        await runtime.execute_civ_raw(raw, wait_response=False)
        if ending == "sink-removed":
            radio._civ_request_tracker.drop_ack_sinks()
        if ending == "malformed":
            for shape in (
                "unmapped",
                "broadcast",
                "setter_echo",
                "ack",
                "misaddressed",
            ):
                await _inject(radio, shape)
            for sender, data in ((0x88, b"\x00"), (0x98, b""), (0x98, b"\x02")):
                await runtime._route_civ_frame(
                    parse_civ_frame(
                        build_civ_frame(0xE0, sender, 0x1C, sub=0, data=data)
                    ),
                    generation=radio._civ_epoch,
                )
        assert runtime._ptt_reply_ledger.owed_count == 1
    ledger = runtime._ptt_reply_ledger
    assert ledger.owed_count == 1 and not ledger.eligible()
    assert (await radio.read_transmit_state()).failure == "not-quiescent"
    await _inject(radio)
    assert ledger.eligible() and ledger.quarantine_until == 0


@pytest.mark.parametrize(
    "queued,ending",
    [
        (False, "answer"),
        (True, "answer"),
        (True, "outer-timeout"),
        (True, "orphan"),
        (False, "pacing"),
        (False, "outer-sent"),
        (False, "broadcast"),
        (False, "unmapped"),
    ],
)
async def test_invocation_context_survives_queue_and_timeout(
    radio, monkeypatch, queued, ending
):
    runtime = radio._civ_runtime
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime.execute_civ_raw

    async def executor(frame, wait_response=True, *, is_current=None):
        entered.set()
        await release.wait()
        return await original(frame, wait_response, is_current=is_current)

    if queued:
        from rigplane.commands.commander import IcomCommander

        radio._commander = IcomCommander(executor)
        radio._commander.start()
    else:
        release.set()
    if ending == "pacing":
        radio._last_civ_send_monotonic = _civ_rx.time.monotonic()
        radio._civ_min_interval = 0.03
    if ending == "outer-sent":
        radio._civ_get_timeout = 1
    task = asyncio.create_task(radio.read_transmit_state())
    try:
        if queued:
            await asyncio.wait_for(entered.wait(), 1)
        if ending in ("orphan", "pacing"):
            if ending == "pacing":
                await asyncio.sleep(0)
            await _inject(radio)
            release.set()
            assert (await task).failure == "not-quiescent"
            assert not radio._civ_transport.sent_packets
        elif ending in ("broadcast", "unmapped"):
            while not radio._civ_transport.sent_packets:
                await asyncio.sleep(0)
            await _inject(radio, ending)
            assert (await task).failure == "unverifiable-provenance"
            assert runtime._ptt_reply_ledger.owed_count == 1
        elif ending == "outer-sent":
            assert (await task).failure == "timeout"
            assert runtime._ptt_reply_ledger.owed_count == 1
        elif ending == "outer-timeout":
            assert (await task).failure == "timeout"
            release.set()
            await asyncio.sleep(0)
            assert runtime._ptt_reply_ledger.owed_count == 0
        else:
            release.set()
            while not radio._civ_transport.sent_packets:
                await asyncio.sleep(0)
            await runtime.execute_civ_raw(
                build_civ_frame(0x98, 0xE0, 0x1C, sub=0), False
            )
            frame = await _inject(radio)
            assert (await task).value is False
            assert runtime._ptt_reply_ledger.owed_count == 1
            await _inject(radio)
            assert runtime._ptt_reply_ledger.owed_count == 0
            assert frame is not None
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "variant",
    [
        "zero",
        "scope",
        "echo",
        "boundary",
        "after-q",
        "mixed",
        "restart",
        "headers",
        "sparse",
        "beyond-lifetime",
        "serial",
    ],
)
async def test_pump_replay_cache(radio, monkeypatch, variant):
    runtime = radio._civ_runtime
    if variant == "serial":
        radio._civ_transport = SerialCivTransport(ScriptedCivLink())
        await radio._civ_transport._civ_link.connect()
    now = [0.0]
    monkeypatch.setattr(_civ_rx, "time", SimpleNamespace(monotonic=lambda: now[0]))
    runtime._ptt_ledger = PttReplyLedger(clock=lambda: now[0], answer_window=2)
    delivered, ready = [], asyncio.Event()
    sentinel = [b""]
    batches = [0]

    def raw(frame):
        delivered.append(frame)
        if frame == sentinel[0]:
            ready.set()

    monkeypatch.setattr(runtime, "deliver_raw_civ", raw)
    marker = build_civ_frame(0xE0, 0x98, 0x55)
    reply = civ_transmit_state_reply("rx", 0x98)
    payload = reply + marker if variant == "mixed" else reply
    packet = wrap_civ_in_udp(payload, seq=1 if variant == "sparse" else 0)

    async def pump(*packets):
        ready.clear()
        batches[0] += 1
        sentinel[0] = build_civ_frame(0xE0, 0x98, 0x55, data=bytes([batches[0]]))
        for pkt in (*packets, wrap_civ_in_udp(sentinel[0], seq=9)):
            if variant == "serial":
                radio._civ_transport._civ_link._replies.put_nowait(pkt[0x15:])
            else:
                radio._civ_transport.queue_response(pkt)
        runtime.start_pump()
        await asyncio.wait_for(ready.wait(), 1)
        await asyncio.sleep(0)

    if variant == "echo":
        echo = build_civ_frame(0x98, 0xE0, 0x1C, sub=0)
        await pump(wrap_civ_in_udp(echo, seq=0), wrap_civ_in_udp(echo, seq=0))
        assert delivered.count(echo) == 2
    a = runtime._ptt_reply_ledger.note_dispatch(_request())
    runtime._ptt_reply_ledger.note_written(a)
    await pump(packet)
    assert a.answered_by is not None
    if variant == "scope":
        scope = build_civ_frame(0xE0, 0x98, 0x27, sub=0, data=b"\x00\x00\x00")
        await pump(*(wrap_civ_in_udp(scope, seq=i) for i in range(300)))
    if variant in ("restart", "serial"):
        radio._civ_rx_task.cancel()
        await radio._civ_rx_task
        radio._civ_epoch += 1
        radio._civ_request_tracker.advance_generation()
        radio._civ_transport = (
            _LanTransport()
            if variant == "restart"
            else SerialCivTransport(ScriptedCivLink())
        )
        if variant == "serial":
            await radio._civ_transport._civ_link.connect()
    now[0] = {"boundary": 4.0, "after-q": 2.1, "beyond-lifetime": 4.001}.get(
        variant, 1.0
    )
    b = runtime._ptt_reply_ledger.note_dispatch(_request())
    runtime._ptt_reply_ledger.note_written(b)
    before = delivered.count(reply)
    mixed_before = delivered.count(marker)
    if variant == "headers":
        packet = wrap_civ_in_udp(reply, seq=0, sender_id=23)
    if variant == "sparse":
        await pump(*(wrap_civ_in_udp(reply, seq=i) for i in (32768, 65535)))
    sparse_before = delivered.count(reply)
    await pump(packet)
    if variant == "sparse":
        assert delivered.count(reply) == sparse_before
    accepted = variant in ("headers", "beyond-lifetime", "sparse", "serial")
    assert (b.answered_by is not None) is accepted
    if not accepted:
        assert delivered.count(reply) == before
        assert delivered.count(marker) == mixed_before
        now[0] = 4.001
        await pump(packet)
        assert b.answered_by is not None


@pytest.mark.parametrize("stale", [False, True])
async def test_reconnect_retains_debt_and_stale_generation_accounting(radio, stale):
    runtime = radio._civ_runtime
    a = runtime._ptt_reply_ledger.note_dispatch(_request())
    runtime._ptt_reply_ledger.note_written(a)
    radio._civ_epoch += 1
    radio._civ_request_tracker.advance_generation()
    assert (await radio.read_transmit_state()).failure == "not-quiescent"
    waiter = radio._civ_request_tracker.register_response(
        request_key_from_frame(_request())
    )
    if stale:
        await _inject(radio, generation=radio._civ_epoch - 1)
        assert a.answered_by is not None and not waiter.done()
    radio._civ_transport = ScriptedLanTransport()
    radio._civ_transport.queue_response(
        wrap_civ_in_udp(civ_transmit_state_reply("rx", 0x98))
    )
    radio._civ_runtime.start_pump()
    assert await asyncio.wait_for(waiter, 1) is not None
    assert a.answered_by is not None
    if not stale:
        await _inject(radio)
    assert runtime._ptt_reply_ledger.orphan_count == 1
    assert not runtime._ptt_reply_ledger.eligible()


async def test_waiter_completion_charged_to_another_entry(radio):
    tracker = radio._civ_request_tracker
    earlier = tracker.register_response(request_key_from_frame(_request()))
    reading = asyncio.create_task(radio.read_transmit_state())
    while not radio._civ_transport.sent_packets:
        await asyncio.sleep(0)
    ledger = radio._civ_runtime._ptt_reply_ledger
    ledger.note_written(ledger.note_dispatch(_request()))
    first = await _inject(radio)
    assert await earlier is first
    await _inject(radio)
    assert (await reading).failure == "unverifiable-provenance"


@pytest.mark.parametrize("ending", ["written", "cancel", "refuse"])
async def test_unwritten_debt_survives_queue_delay(radio, monkeypatch, ending):
    runtime, now = radio._civ_runtime, [0.0]
    ledger = runtime._ptt_ledger = PttReplyLedger(
        clock=lambda: now[0], answer_window=0.1
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(_packet):
        entered.set()
        await release.wait()
        if ending == "refuse":
            raise CommandError("refused before writing")

    monkeypatch.setattr(radio._civ_transport, "send_tracked", blocked)
    task = asyncio.create_task(
        runtime.execute_civ_transaction(
            build_civ_frame(0x98, 0xE0, 0x1C, sub=0), expect="none"
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    now[0] = 10
    assert (await radio.read_transmit_state()).failure == "not-quiescent"
    release.set()
    if ending == "cancel":
        task.cancel()
    result = (await asyncio.gather(task, return_exceptions=True))[0]
    expected = {"refuse": CommandError, "cancel": asyncio.CancelledError}.get(
        ending, _civ_rx.RawCivTransactionResult
    )
    assert isinstance(result, expected)
    if ending == "written":
        assert result == _civ_rx.RawCivTransactionResult(status="sent")
    assert ledger.eligible() is (ending == "refuse")
    if ending != "refuse":
        assert (await radio.read_transmit_state()).failure == "not-quiescent"
        await _inject(radio)
        assert ledger.eligible()


@pytest.mark.parametrize("change", ["clean", "orphan", "epoch", "transport"])
async def test_reader_settle_checks_receive_pump_and_session(
    radio, monkeypatch, change
):
    entered, release = asyncio.Event(), asyncio.Event()
    sleep = asyncio.sleep
    monkeypatch.setattr(CoreRadio, "_PTT_READBACK_SETTLE_S", 0.007123)

    async def settle(delay):
        if delay == 0.007123:
            entered.set()
            await release.wait()
        else:
            await sleep(delay)

    monkeypatch.setattr(asyncio, "sleep", settle)
    task = asyncio.create_task(radio.read_transmit_state())
    try:
        while not radio._civ_transport.sent_packets:
            await sleep(0)
        radio._civ_transport.queue_response(
            wrap_civ_in_udp(civ_transmit_state_reply("rx", 0x98), seq=71)
        )
        await asyncio.wait_for(entered.wait(), 1)
        if change == "orphan":
            radio._civ_transport.queue_response(
                wrap_civ_in_udp(civ_transmit_state_reply("tx_cat", 0x98), seq=72)
            )
            while radio._civ_runtime._ptt_reply_ledger.orphan_count == 0:
                await sleep(0)
        elif change == "epoch":
            radio._civ_epoch += 1
        elif change == "transport":
            radio._civ_transport = _LanTransport()
        release.set()
        reading = await task
        assert reading.verified_readback is (change == "clean")
        assert reading.failure == {
            "clean": None,
            "orphan": "unverifiable-provenance",
        }.get(change, "read-error")
        if change == "orphan":
            assert (
                reading.value is None
                and not radio._civ_runtime._ptt_reply_ledger.eligible()
            )
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
