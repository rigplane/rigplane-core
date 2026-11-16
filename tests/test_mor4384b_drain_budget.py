"""MOR-4384 round 2: the ACK-sink drain must not consume caller budgets.

Round 1 (``TestAckSinkRobustness`` in ``tests/test_radio.py``) keeps the
fire-and-forget sink of an unanswered command open for its whole answer
window so a late bare FB/FA still retires the sink instead of the next
command's waiter. The drain that enforces this
(``_drain_ack_sinks_before_blocking``) waits out that whole window — up to
``_civ_get_timeout`` (2 s in production) — before *every* blocking command,
and that wait sits inside every caller deadline:

- ``radio.connect()`` after the initial-state sweep leaves dozens of
  unanswered sinks open: the first post-sweep blocking command stalls the
  whole connect by one window (the real-radio IC-705 connect stall).
- a rigctld ``F`` (``command_timeout`` 2 s) after one lost background
  answer reports ETIMEOUT although the radio tuned.
- ``read_transmit_state`` (300 ms) after one lost background answer
  reports ``timeout`` although the radio answered promptly.

Failing-first: each test below fails on the round-1 head (the drain waits
out the full window) and passes once the drain is bounded by its own
separate budget / skipped where the protocol allows, without reintroducing
the round-1 misattribution (those tests stay green).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.core.civ import read_identity_from_frame
from rigplane.core.exceptions import CommandRejectedError
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane_testkit.icom import MockIcomRadio

from _helpers import (
    ack_response as _ack_response,
)
from _helpers import (
    observe_selected_receiver,
)
from _helpers import (
    wrap_civ_in_udp,
)
from serial_stub import SplitIcomTransport
from test_rigctld_wsjtx_split_mk2 import _client, _exchange, _rf_state

_SCOPE_ENABLE = build_civ_frame(
    IC_7610_ADDR, CONTROLLER_ADDR, 0x27, sub=0x10, data=b"\x01"
)
_SET_FREQ = build_civ_frame(
    IC_7610_ADDR, CONTROLLER_ADDR, 0x05, data=bytes.fromhex("0026361400")
)


def _make_radio(transport, *, timeout: float = 2.0) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=timeout, model="IC-7610")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    observe_selected_receiver(radio, 0)
    radio._civ_min_interval = 0.0
    return radio


class _MockTransport:
    """Minimal UDP transport: queued responses released per send count."""

    ordered_ingress = True

    def __init__(self) -> None:
        self.connected = False
        self.sent_packets: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}
        self.my_id = 0x00010001
        self.remote_id = 0xDEADBEEF
        self.send_seq = 0
        self.rx_packet_count = 0

    async def send_tracked(self, data: bytes, **kwargs) -> None:
        self.sent_packets.append(data)
        self.send_seq += 1
        for pkt in self._responses_by_send.pop(self.send_seq, []):
            self._responses.put_nowait(pkt)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response_on_send(self, send_number: int, data: bytes) -> None:
        self._responses_by_send.setdefault(send_number, []).append(data)

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass


class TestDrainBudget:
    """The drain must not wait out a silent sink's whole answer window."""

    @pytest.mark.realtime(reason="pins a wall-clock drain budget against a silent sink")
    @pytest.mark.asyncio
    async def test_blocking_write_does_not_wait_out_unanswered_sink_window(
        self,
    ) -> None:
        """One unanswered fire-and-forget write must not stall the next write.

        The scope-enable ACK is intentionally missing. The blocking write's
        own ACK is queued to arrive as soon as its frame leaves. On the
        round-1 head the drain waits out the sink's whole 2 s window first
        (this test then takes ~2 s); the fix bounds the drain by its own
        separate budget, so the write completes promptly — and still on its
        own ACK, not the missing one.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            assert (
                await runtime._execute_civ_raw(_SCOPE_ENABLE, wait_response=False)
                is None
            )
            transport.queue_response_on_send(2, _ack_response())
            started = time.monotonic()
            await radio.set_freq(7_074_000)
            elapsed = time.monotonic() - started
        finally:
            radio._civ_transport = None
            pump = radio._civ_rx_task
            if pump is not None:
                await pump
            radio._connected = False
        assert elapsed < 1.0, f"blocking write stalled {elapsed:.2f}s on a silent sink"

    @pytest.mark.realtime(reason="pins the 300 ms TX-read budget against a silent sink")
    @pytest.mark.asyncio
    async def test_tx_read_does_not_wait_out_unanswered_sink_window(self) -> None:
        """``read_transmit_state`` keeps its 300 ms answer budget.

        A data-expecting read's answer is attributed by its request key, so
        an older unanswered sink cannot eat it and the drain has nothing to
        protect: the 300 ms ``TX_READ_DEADLINE_SECONDS`` must cover the
        read's own answer, not the drain. On the round-1 head the 300 ms
        ``wait_for`` fires inside the 2 s drain and the read reports
        ``timeout`` although the radio answered promptly.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            assert (
                await runtime._execute_civ_raw(_SCOPE_ENABLE, wait_response=False)
                is None
            )
            ptt_reply = wrap_civ_in_udp(
                build_civ_frame(
                    CONTROLLER_ADDR, IC_7610_ADDR, 0x1C, sub=0x00, data=b"\x00"
                )
            )
            transport.queue_response_on_send(2, ptt_reply)
            started = time.monotonic()
            reading = await radio.read_transmit_state()
            elapsed = time.monotonic() - started
        finally:
            radio._civ_transport = None
            pump = radio._civ_rx_task
            if pump is not None:
                await pump
            radio._connected = False
        assert reading.value is False, f"expected a real OFF readback, got {reading!r}"
        assert reading.failure is None, f"expected no failure, got {reading!r}"
        assert elapsed < 2.0, f"TX read stalled {elapsed:.2f}s on a silent sink"


class _SilentWriteTransport(SplitIcomTransport):
    """A peer that answers everything except one fire-and-forget write.

    The silenced write models a lost background answer: the sender's ACK
    sink stays open for its whole window because nothing ever retires it.
    """

    silent: set[tuple[int, int | None]] = {(0x1C, 0x00)}

    async def send_tracked(self, packet: bytes, **kwargs) -> None:
        from rigplane.commands import parse_civ_frame

        request = parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
        if (request.command, request.sub) in self.silent and request.data:
            self.requests.append((request.command, request.sub, request.data))
            return
        await super().send_tracked(packet, **kwargs)


class TestSameKeyDrain:
    """A data read must not register its waiter over an identical open sink."""

    @pytest.mark.realtime(
        reason="pins waiter-vs-sink attribution for two reads of one command"
    )
    @pytest.mark.asyncio
    async def test_a_late_answer_to_an_identical_read_retires_its_sink_not_the_new_read(
        self,
    ) -> None:
        """A blocking read must not take a reply owed to an older identical sink.

        Two reads of the same command share one request key, so the key
        cannot tell a reply owed to the older fire-and-forget read (a sink)
        from the new blocking read's own answer. ``resolve`` tries response
        waiters before sinks, so a late answer routed between the new read's
        registration and its send settles the NEW waiter and leaves the sink
        open — on the PTT-release readback that stolen reply fails the
        provenance check (``answered_by is reply``) and every release attempt
        reports UNCERTAIN (tests/integration/test_ptt_release_readback_api.py,
        red on the round-2 head). The drain must hold the new read's
        registration until the identical sink is answered; unrelated sinks
        stay skipped.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        # A window far wider than any test join: only the same-key drain may
        # clear the older read's sink, never a window expiry.
        radio._civ_get_timeout = 10.0
        try:
            runtime = radio._civ_runtime
            ptt_read = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00)
            assert await runtime._execute_civ_raw(ptt_read, wait_response=False) is None
            assert radio._civ_request_tracker.ack_sink_count == 1
            task = asyncio.create_task(runtime._execute_civ_raw(ptt_read))
            try:
                # The blocking read must not go out while the identical sink
                # is still owed its answer.
                await asyncio.sleep(0.05)
                assert len(transport.sent_packets) == 1, (
                    "the blocking read sent while an identical sink was open"
                )
                stale = parse_civ_frame(
                    build_civ_frame(
                        CONTROLLER_ADDR, IC_7610_ADDR, 0x1C, sub=0x00, data=b"\x01"
                    )
                )
                await runtime._route_civ_frame(stale, generation=radio._civ_epoch)
                # The sink is answered now, so the read may send.
                for _ in range(1000):
                    if len(transport.sent_packets) >= 2:
                        break
                    await asyncio.sleep(0.001)
                assert len(transport.sent_packets) == 2, (
                    "the blocking read never went out"
                )
                own = parse_civ_frame(
                    build_civ_frame(
                        CONTROLLER_ADDR, IC_7610_ADDR, 0x1C, sub=0x00, data=b"\x00"
                    )
                )
                await runtime._route_civ_frame(own, generation=radio._civ_epoch)
                result = await asyncio.wait_for(task, timeout=3.0)
                assert result is not None and result.data == b"\x00", (
                    "the read returned the older read's stale answer"
                )
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert radio._civ_request_tracker.ack_sink_count == 0, (
                "the stale answer settled the new read instead of the sink"
            )
        finally:
            radio._civ_transport = None
            pump = radio._civ_rx_task
            if pump is not None:
                await pump
            radio._connected = False


class TestEvidenceExpiry:
    """A newer directed answer retires older dead sinks by evidence."""

    @pytest.mark.realtime(
        reason="pins evidence-based sink retirement against a silent sink"
    )
    @pytest.mark.asyncio
    async def test_newer_directed_answer_retires_older_dead_sink(self) -> None:
        """An answered read proves the older sink's answer is lost (MOR-4209).

        The radio answers in order on an ordered-ingress transport, so once
        a directed answer to a newer request arrives, every older sink's
        answer (data, FB or FA) was lost on the wire: the tracker retires
        those sinks at once instead of making the next blocking command
        wait out their windows. A late answer for the retired sink can no
        longer settle that command — the same attribution the drain
        protects, without the wait.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        # A window far wider than any test join: only evidence retirement
        # can clear the dead sink below, never a window expiry.
        radio._civ_get_timeout = 10.0
        try:
            runtime = radio._civ_runtime
            refused_read = build_civ_frame(
                IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x01
            )
            assert (
                await runtime._execute_civ_raw(refused_read, wait_response=False)
                is None
            )
            assert radio._civ_request_tracker.ack_sink_count == 1
            mode_get = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x04)
            task = asyncio.create_task(runtime._execute_civ_raw(mode_get))
            try:
                for _ in range(1000):
                    if len(transport.sent_packets) >= 2:
                        break
                    await asyncio.sleep(0.001)
                assert len(transport.sent_packets) == 2, (
                    "the blocking read never went out"
                )
                answer = parse_civ_frame(
                    build_civ_frame(
                        CONTROLLER_ADDR, IC_7610_ADDR, 0x04, data=b"\x02\x01"
                    )
                )
                await runtime._route_civ_frame(answer, generation=radio._civ_epoch)
                result = await asyncio.wait_for(task, timeout=3.0)
                assert result is not None and result.command == 0x04
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert radio._civ_request_tracker.ack_sink_count == 0, (
                "the newer answer did not retire the older dead sink by evidence"
            )
        finally:
            radio._civ_transport = None
            pump = radio._civ_rx_task
            if pump is not None:
                await pump
            radio._connected = False


class TestRigctldDeadline:
    """A lost background answer must not eat the next command's deadline."""

    @pytest.mark.realtime(
        reason="pins the rigctld 2 s command deadline against a silent sink"
    )
    @pytest.mark.asyncio
    async def test_set_freq_after_lost_background_answer_meets_deadline(
        self,
    ) -> None:
        """rigctld ``F`` answers RPRT 0 promptly despite an unanswered sink.

        A background PTT-off write is never answered (its sink stays open),
        then ``F`` follows 10 ms later and the peer ACKs the setter at once.
        On the round-1 head the drain waits out the sink's whole 2 s window
        first, so the 2 s rigctld command deadline fires and the client sees
        ``RPRT -5`` (ETIMEOUT) although the radio tuned. The fix accounts
        for the drain separately from the command's own answer budget.
        """
        transport = _SilentWriteTransport()
        radio = IcomRadio("127.0.0.1", model="IC-7610", timeout=2.0)
        radio._civ_transport = transport
        radio._ctrl_transport = transport
        radio._connected = True
        observe_selected_receiver(radio, 0)
        radio._civ_min_interval = 0
        config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
        server = RigctldServer(radio, config, allow_not_ready=True)
        server._state_store = radio.state_store
        try:
            await server.start()
            _rf_state(server, False)
            ptt_off = build_civ_frame(
                IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x00"
            )
            assert await radio._execute_civ_raw(ptt_off, wait_response=False) is None
            await asyncio.sleep(0.01)
            async with _client(server) as (reader, writer):
                started = time.monotonic()
                reply = await _exchange(writer, reader, b"F 14074000\n")
                elapsed = time.monotonic() - started
        finally:
            await server.stop()
            await radio._civ_runtime.stop_pump()
            radio._connected = False
        assert reply == b"RPRT 0\n", f"setter missed its deadline: {reply!r}"
        assert elapsed < 1.5, f"rigctld F stalled {elapsed:.2f}s on a silent sink"


class TestConnectBudget:
    """A connect with unanswered sweep reads stays within budget (IC-705)."""

    @pytest.mark.realtime(
        reason="pins the connect-time budget with unanswered sweep reads"
    )
    @pytest.mark.asyncio
    async def test_connect_ready_within_budget_when_sweep_reads_unanswered(
        self,
    ) -> None:
        """The full connect must not pay a whole answer window per post-sweep command.

        The simulated IC-705 answers everything the connect blocks on (the
        identity read, the availability reads, PTT) but stays silent on the
        sweep-only GETs that make up most of the initial-state fetch, so the
        fetch leaves dozens of ACK sinks open. On the round-1 head the first
        post-sweep blocking command drains until the last of those windows
        closes — about one window on top of the connect — and the managed-TX
        arm probe even times out inside that drain and skips the arm. The
        fix bounds that wait by its own separate budget, so the connect
        completes while the radio answers.
        """
        sim = MockIcomRadio(
            radio_addr=0xA4,
            single_owner=True,
            keepalive_hold_s=5.0,
            require_control_discovery=True,
        )
        dispatch = sim._dispatch_civ

        _SILENT_SWEEP = (
            {(0x25, 0x00), (0x25, 0x01), (0x1C, 0x01)}
            | {
                (0x14, sub)
                for sub in (
                    0x01,
                    0x02,
                    0x03,
                    0x06,
                    0x07,
                    0x08,
                    0x0A,
                    0x0B,
                    0x0D,
                    0x0E,
                    0x12,
                    0x15,
                    0x16,
                    0x17,
                )
            }
            | {(0x15, 0x02), (0x15, 0x15), (0x15, 0x16)}
            | {
                (0x27, sub)
                for sub in (
                    0x12,
                    0x13,
                    0x14,
                    0x15,
                    0x16,
                    0x17,
                    0x19,
                    0x1A,
                    0x1B,
                    0x1C,
                    0x1D,
                    0x1E,
                )
            }
        )

        def _silent_sweep_reads(cmd: int, payload: bytes, from_addr: int):
            sub = payload[0] if payload else None
            if (cmd, sub) in _SILENT_SWEEP and len(payload) <= 1:
                return None
            return dispatch(cmd, payload, from_addr)

        sim._dispatch_civ = _silent_sweep_reads
        await sim.start()
        radio = IcomRadio(
            "127.0.0.1",
            port=sim.control_port,
            username="testuser",
            password="testpass",
            model="IC-705",
            timeout=2.0,
        )
        try:
            started = time.monotonic()
            await asyncio.wait_for(radio.connect(), timeout=8.0)
            elapsed = time.monotonic() - started
            assert radio.connected
            assert radio.radio_ready
            # The arm probe (0x1C 0x00, 0.5 s budget) must meet its budget:
            # on the round-1 head it times out inside the drain waiting out
            # the sweep sinks' windows, the arm is skipped, and the station
            # loses supervised TX for the whole epoch.
            assert radio._managed_tx_binding_is_live(), (
                "managed TX did not arm: its probe timed out in the ACK-sink drain"
            )
        finally:
            await radio.disconnect()
            await sim.stop()
        assert elapsed < 2.8, (
            f"connect stalled {elapsed:.2f}s on unanswered sweep reads"
        )


def _nak(addr: int):
    return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, addr, 0xFA))


def _ack(addr: int):
    return parse_civ_frame(build_civ_frame(CONTROLLER_ADDR, addr, 0xFB))


async def _stop(radio) -> None:
    radio._civ_transport = None
    pump = radio._civ_rx_task
    if pump is not None:
        await pump
    radio._connected = False


class TestUnkeyedNakAttribution:
    """A NAK-terminal read must not register behind an unrelated read sink.

    MOR-4384 round 3: ``ack_sink_count_matching`` sees only data-answer
    collisions, but a blocking read also registers a NAK-only waiter that
    consumes bare FAs. Background read A loses its answer; unrelated read B
    is explicitly refused. The bypass left A's sink ahead of B's NAK waiter,
    so B's FA retired A — marking healthy field A refused (the acquisition
    scheduler then fails A's current acquisition) while B ran out its whole
    answer window and failed as a timeout.
    """

    @pytest.mark.realtime(
        reason="pins the all-read-sink drain budget and B's 2 s answer window"
    )
    @pytest.mark.asyncio
    async def test_refused_read_keeps_its_own_refusal_off_the_older_sink(
        self,
    ) -> None:
        """Through ``_execute_civ_raw``: B is refused, A stays unrefused."""
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            tuner_read = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x01)
            assert (
                await runtime._execute_civ_raw(tuner_read, wait_response=False) is None
            )
            assert radio._civ_request_tracker.ack_sink_count == 1

            mode_get = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x04)
            task = asyncio.create_task(runtime._execute_civ_raw(mode_get))
            try:
                for _ in range(2000):
                    if len(transport.sent_packets) >= 2:
                        break
                    await asyncio.sleep(0.001)
                assert len(transport.sent_packets) == 2, "the read never went out"
                await runtime._route_civ_frame(
                    _nak(IC_7610_ADDR), generation=radio._civ_epoch
                )
                with pytest.raises(CommandRejectedError):
                    await asyncio.wait_for(task, timeout=4.0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert radio._civ_request_tracker.take_refused_reads() == (), (
                "B's refusal was attributed to the older read A"
            )
        finally:
            await _stop(radio)

    @pytest.mark.realtime(
        reason="pins the all-read-sink drain budget and B's 2 s answer window"
    )
    @pytest.mark.asyncio
    async def test_refused_transaction_keeps_its_own_refusal_off_the_older_sink(
        self,
    ) -> None:
        """Same wire through ``execute_civ_transaction`` (``nak_terminal``)."""
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            tuner_read = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x01)
            assert (
                await runtime._execute_civ_raw(tuner_read, wait_response=False) is None
            )
            assert radio._civ_request_tracker.ack_sink_count == 1

            mode_get = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x04)
            task = asyncio.create_task(
                runtime.execute_civ_transaction(mode_get, expect="data")
            )
            try:
                for _ in range(2000):
                    if len(transport.sent_packets) >= 2:
                        break
                    await asyncio.sleep(0.001)
                assert len(transport.sent_packets) == 2, (
                    "the transaction never went out"
                )
                await runtime._route_civ_frame(
                    _nak(IC_7610_ADDR), generation=radio._civ_epoch
                )
                result = await asyncio.wait_for(task, timeout=4.0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert result.status == "nak", (
                "the transaction did not report its own refusal"
            )
            assert radio._civ_request_tracker.take_refused_reads() == (), (
                "the transaction's refusal was attributed to the older read A"
            )
        finally:
            await _stop(radio)


class TestLiveReadSinkOutlivesTheDrainBudget:
    """A read sink lives until its answer window closes (round 3)."""

    @pytest.mark.realtime(
        reason="the read's refusal must land past the 500 ms drain cutoff "
        "but inside the 2 s answer window"
    )
    @pytest.mark.asyncio
    async def test_late_read_refusal_within_window_does_not_reject_a_successful_set(
        self,
    ) -> None:
        """A background read's FA at 600 ms retires the read, not the setter.

        The drain's 500 ms cutoff used to force-expire every open sink with
        ``answer_window=0.0``, recreating the round-1 defect inside the
        configured 2 s window: the read's late refusal then landed on the
        blocking set's fresh waiter and ``set_freq`` failed with "Radio
        rejected MAIN set_frequency(...)" although the radio tuned (its FB
        follows here). The read sink must stay claimable until its real
        window closes, so the FA retires the read it answers and the set
        settles on its own FB.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            refused_read = build_civ_frame(
                IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x01
            )
            assert (
                await runtime._execute_civ_raw(refused_read, wait_response=False)
                is None
            )
            task = asyncio.create_task(radio.set_freq(7_074_000))
            try:
                # Past the drain budget (500 ms), inside the answer window
                # (2 s): on the unfixed head the sink was already presumed
                # lost and this FA rejected the set.
                await asyncio.sleep(0.65)
                await runtime._route_civ_frame(
                    _nak(IC_7610_ADDR), generation=radio._civ_epoch
                )
                for _ in range(2000):
                    if len(transport.sent_packets) >= 2:
                        break
                    await asyncio.sleep(0.001)
                assert len(transport.sent_packets) == 2, "the set never went out"
                await runtime._route_civ_frame(
                    _ack(IC_7610_ADDR), generation=radio._civ_epoch
                )
                await asyncio.wait_for(task, timeout=3.0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert radio._civ_request_tracker.take_refused_reads() == (
                read_identity_from_frame(parse_civ_frame(refused_read)),
            ), "the late refusal did not retire the read it answers"
        finally:
            await _stop(radio)
