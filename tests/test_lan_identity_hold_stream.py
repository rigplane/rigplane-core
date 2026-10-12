"""The LAN identity hold re-requests the CI-V stream and gives up a dead session.

The hold stops the CI-V data watchdog for its duration, so after a soft
reconnect whose single OpenClose(open) did not restart the radio's stream,
nothing re-requests it: the held link then sends only unanswered ``19 00``
re-reads. ``CoreRadio._lan_identity_reread_loop`` must send one
OpenClose(open) before each re-read until any CI-V payload arrives within
the hold (``_last_civ_data_received`` newer than the hold start).

The hold also asks the radio, by token renewal
(``ControlPhaseRuntime.probe_control_session``), whether it still holds
the control session: on the first turn of every hold, and on a later
turn only while the last renewal has gone unanswered past the probe
timeout. A rejected renewal, or three unanswered ones, ends the re-read
with one ``CivRuntime.request_recovery_now`` call; a dead verdict within
``CoreRadio._LAN_IDENTITY_ESCALATION_SPACING_S`` of the previous
escalation keeps holding.
"""

from __future__ import annotations

import asyncio
import struct
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.exceptions import ConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime._control_phase import ControlPhaseRuntime

from test_radio import MockTransport
from test_radio_connect import ConnectMockTransport, _build_conninfo, _ConnectOnceStubs

_TOKEN = 0x5EC0DE01
_RENEWAL_OK = 0x00000000
_RENEWAL_REJECT = 0xFFFFFFFF


def _renewal_answer(token: int, result: int, *, request_type: int = 0x05) -> bytes:
    """A 0x40 token answer: reply flag 0x02 at 0x14, the request type at
    0x15, the token at 0x1C and the u32 result at 0x30."""
    pkt = bytearray(0x40)
    struct.pack_into("<I", pkt, 0x00, 0x40)
    struct.pack_into("<H", pkt, 0x06, 1)
    struct.pack_into(">I", pkt, 0x10, 0x30)
    pkt[0x14] = 0x02
    pkt[0x15] = request_type
    struct.pack_into("<I", pkt, 0x1C, token)
    struct.pack_into(">H", pkt, 0x24, 0x0798)
    struct.pack_into("<I", pkt, 0x30, result)
    return bytes(pkt)


class _RenewalAnswers:
    """Answers each token renewal sent on the mock control transport.

    ``result`` None stays silent. ``sent_at`` records when each renewal
    went out; ``on_renewal`` runs before the answer is scheduled.
    """

    def __init__(
        self, radio: IcomRadio, transport: MockTransport, result: int | None
    ) -> None:
        self.result = result
        self.sent_at: list[float] = []
        self.on_renewal: Callable[[], None] | None = None
        send = transport.send_tracked

        async def send_tracked(data: bytes) -> None:
            await send(data)
            if len(data) != 0x40 or data[0x15] != 0x05:
                return
            self.sent_at.append(time.monotonic())
            if self.on_renewal is not None:
                self.on_renewal()
            if self.result is not None:
                # Lands like a datagram: after the send returns.
                asyncio.get_running_loop().call_soon(
                    radio._control_phase._on_token_reply,
                    _renewal_answer(radio._token, self.result),
                )

        transport.send_tracked = send_tracked


def make_radio(
    renewal_result: int | None = _RENEWAL_OK,
) -> tuple[IcomRadio, MockTransport, _RenewalAnswers]:
    """Real CoreRadio over a mock transport whose control session looks open."""
    radio = IcomRadio("192.168.1.100", model="IC-7610", timeout=0.5)
    radio._profile = replace(radio._profile, expected_identity_ids=("0B90",))
    transport = MockTransport()
    transport._udp_transport = object()  # control session looks open
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._token = _TOKEN
    return radio, transport, _RenewalAnswers(radio, transport, renewal_result)


def _park_hold(radio: IcomRadio) -> None:
    """An owned ``no_response`` hold on the current epoch, without a gate."""
    radio._conn_state = RadioConnectionState.CONNECTING
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model=radio.model
    )
    radio._lan_identity_owned_epoch = radio._civ_epoch


def _fast_reread(radio: IcomRadio) -> None:
    """Collapse the re-read backoff so loop turns land quickly."""
    radio._LAN_IDENTITY_REREAD_BACKOFF_S = (0.01,)
    radio._LAN_IDENTITY_REREAD_STEADY_S = 0.01


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


async def _stop_reread(radio: IcomRadio) -> None:
    task = radio._lan_identity_reread_task
    radio._cancel_lan_identity_reread()
    if task is not None:
        with suppress(asyncio.CancelledError):
            await task


async def test_hold_re_requests_stream_before_every_reread() -> None:
    radio, _transport, _answers = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    # Production never enters the hold with None here: soft_reconnect stamps
    # _last_civ_data_received before the gate starts the hold.
    radio._last_civ_data_received = time.monotonic() - 1.0
    order: list[str] = []

    async def open_close(*, open_stream: bool) -> None:
        order.append("open" if open_stream else "close")

    async def read() -> bytes | None:
        order.append("read")
        return None

    radio._send_open_close = AsyncMock(side_effect=open_close)
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: len(order) >= 6)
    finally:
        await _stop_reread(radio)
    assert order[:6] == ["open", "read", "open", "read", "open", "read"]
    assert radio._send_open_close.await_count == 3
    assert all(
        call.kwargs == {"open_stream": True}
        for call in radio._send_open_close.await_args_list
    )


async def test_payload_since_hold_start_stops_the_stream_nudge() -> None:
    radio, _transport, _answers = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    # A stamp older than the hold start (as soft_reconnect leaves it) must
    # not stop the nudge; only a payload stamped within the hold may.
    radio._last_civ_data_received = time.monotonic() - 1.0
    reads = 0
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()

    async def read() -> bytes | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            # Park the loop so the payload stamp lands before the next turn.
            first_read_started.set()
            await release_first_read.wait()
        return None

    radio._send_open_close = AsyncMock()
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await asyncio.wait_for(first_read_started.wait(), 2)
        assert radio._send_open_close.await_count == 1
        # A CI-V payload arrived within the hold: the stamp postdates the
        # loop start, so the next re-read must not re-request the stream.
        radio._last_civ_data_received = time.monotonic()
        release_first_read.set()
        assert await _wait_until(lambda: reads >= 2)
    finally:
        await _stop_reread(radio)
    assert reads >= 2
    assert radio._send_open_close.await_count == 1


async def test_failing_stream_nudge_does_not_end_the_hold() -> None:
    radio, _transport, _answers = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    reads = 0

    async def read() -> bytes | None:
        nonlocal reads
        reads += 1
        return None

    radio._send_open_close = AsyncMock(side_effect=ConnectionError("nudge lost"))
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: reads >= 2)
        task = radio._lan_identity_reread_task
        assert task is not None and not task.done()
    finally:
        await _stop_reread(radio)
    assert reads >= 2
    assert radio._send_open_close.await_count >= 2


async def test_acceptable_answer_completes_the_hold() -> None:
    radio, _transport, _answers = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    radio._send_open_close = AsyncMock()
    radio._read_lan_identity_payload = AsyncMock(return_value=b"\x0b\x90")
    finish = AsyncMock()
    radio._finish_lan_identity_completion = finish
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: finish.await_count >= 1)
    finally:
        await _stop_reread(radio)
    finish.assert_awaited_once()
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    assert radio._lan_identity_answer_epoch == radio._civ_epoch


# ---------------------------------------------------------------------------
# A hold on a dead control session escalates to a re-login.
# ---------------------------------------------------------------------------


def _held_radio(
    renewal_result: int | None, *, probe_timeout: float | None = 0.02
) -> tuple[IcomRadio, _RenewalAnswers, list[float], list[str]]:
    """A parked hold whose re-reads go unanswered and whose recovery
    requests are recorded instead of run."""
    radio, _transport, answers = make_radio(renewal_result)
    _park_hold(radio)
    _fast_reread(radio)
    if probe_timeout is not None:
        radio._control_phase._RENEWAL_PROBE_TIMEOUT_S = probe_timeout
    radio._send_open_close = AsyncMock()
    reads: list[float] = []

    async def read() -> bytes | None:
        reads.append(time.monotonic())
        return None

    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    requests: list[str] = []
    radio._civ_runtime.request_recovery_now = requests.append
    return radio, answers, reads, requests


def _reread_task(radio: IcomRadio) -> asyncio.Task[None]:
    radio._start_lan_identity_reread()
    task = radio._lan_identity_reread_task
    assert task is not None
    return task


async def test_rejected_renewal_escalates_at_once_and_ends_the_hold() -> None:
    radio, answers, reads, requests = _held_radio(_RENEWAL_REJECT)
    task = _reread_task(radio)
    try:
        assert await _wait_until(task.done)
    finally:
        await _stop_reread(radio)
    assert requests == ["control session dead: reject"]
    assert len(answers.sent_at) == 1
    assert reads == []  # no identity read after the verdict


async def test_three_silent_renewals_one_second_apart_escalate() -> None:
    assert ControlPhaseRuntime._RENEWAL_PROBE_TIMEOUT_S == 1.0
    assert ControlPhaseRuntime._RENEWAL_PROBE_ATTEMPTS == 3
    radio, answers, reads, requests = _held_radio(None, probe_timeout=None)
    task = _reread_task(radio)
    try:
        assert await _wait_until(task.done, timeout=6.0)
    finally:
        await _stop_reread(radio)
    assert requests == ["control session dead: no renewal answer"]
    assert len(answers.sent_at) == 3
    gaps = [
        later - earlier for earlier, later in zip(answers.sent_at, answers.sent_at[1:])
    ]
    assert all(gap >= 0.95 for gap in gaps), gaps
    assert reads == []


async def test_answered_renewal_keeps_the_hold_and_is_probed_once() -> None:
    radio, answers, reads, requests = _held_radio(_RENEWAL_OK)
    task = _reread_task(radio)
    try:
        assert await _wait_until(lambda: len(reads) >= 5)
        assert not task.done()
    finally:
        await _stop_reread(radio)
    assert requests == []
    assert len(answers.sent_at) == 1


async def test_unanswered_periodic_renewal_probes_again_on_a_later_turn() -> None:
    radio, answers, reads, requests = _held_radio(_RENEWAL_OK)
    task = _reread_task(radio)
    try:
        assert await _wait_until(lambda: len(reads) >= 2)
        assert len(answers.sent_at) == 1
        # The radio stops answering; the periodic renewal goes out once.
        answers.result = None
        await radio._control_phase._send_token(0x05)
        assert await _wait_until(task.done)
    finally:
        await _stop_reread(radio)
    assert requests == ["control session dead: no renewal answer"]
    assert len(answers.sent_at) == 1 + 1 + 3


@pytest.mark.parametrize("change", ["owner_stop", "new_link"])
async def test_hold_lost_during_the_probe_requests_nothing(change: str) -> None:
    radio, answers, reads, requests = _held_radio(_RENEWAL_REJECT)

    def lose_the_hold() -> None:
        if change == "owner_stop":
            radio._owner_stop_epoch += 1
        else:
            radio._civ_runtime.advance_generation("test-new-link")

    answers.on_renewal = lose_the_hold
    task = _reread_task(radio)
    try:
        assert await _wait_until(task.done)
    finally:
        await _stop_reread(radio)
    assert len(answers.sent_at) == 1
    assert requests == []
    assert reads == []


async def test_second_escalation_waits_out_the_spacing() -> None:
    assert IcomRadio._LAN_IDENTITY_ESCALATION_SPACING_S == 60.0
    radio, _answers, reads, requests = _held_radio(_RENEWAL_REJECT)
    radio._lan_identity_escalated_at = time.monotonic() - 59.0
    task = _reread_task(radio)
    try:
        assert await _wait_until(lambda: len(reads) >= 3)
        assert requests == []
        assert not task.done()
        radio._lan_identity_escalated_at = time.monotonic() - 61.0
        assert await _wait_until(task.done)
    finally:
        await _stop_reread(radio)
    assert requests == ["control session dead: reject"]


async def test_acceptance_resets_the_escalation_spacing() -> None:
    radio, _answers, _reads, requests = _held_radio(_RENEWAL_OK)
    radio._lan_identity_escalated_at = time.monotonic()
    radio._read_lan_identity_payload = AsyncMock(return_value=b"\x0b\x90")
    radio._fetch_initial_state = AsyncMock()
    radio._arm_managed_tx = AsyncMock()
    task = _reread_task(radio)
    try:
        assert await _wait_until(task.done)
    finally:
        await _stop_reread(radio)
        await radio._civ_runtime.stop_data_watchdog()
    radio._fetch_initial_state.assert_awaited_once()
    assert radio._lan_identity_escalated_at is None
    assert requests == []


async def test_owner_connect_and_disconnect_reset_the_escalation_spacing() -> None:
    radio, _transport, _answers = make_radio()
    radio._lan_identity_escalated_at = time.monotonic()
    await radio.disconnect()
    assert radio._lan_identity_escalated_at is None

    radio._lan_identity_escalated_at = time.monotonic()
    refused = AsyncMock(side_effect=ConnectionError("refused"))
    with patch.object(radio._session_lifecycle, "connect", refused):
        with pytest.raises(ConnectionError):
            await radio.connect()
    refused.assert_awaited_once()
    assert radio._lan_identity_escalated_at is None


@pytest.mark.parametrize(
    ("answer", "holds"),
    [
        (_renewal_answer(_TOKEN, _RENEWAL_OK), True),
        (_renewal_answer(_TOKEN, _RENEWAL_REJECT), False),
        (_renewal_answer(_TOKEN ^ 1, _RENEWAL_OK), None),
        (_renewal_answer(_TOKEN ^ 1, _RENEWAL_REJECT), None),
        (_renewal_answer(_TOKEN, _RENEWAL_OK, request_type=0x01), None),
        (_renewal_answer(_TOKEN, 0x00000001), None),
    ],
    ids=[
        "ok",
        "reject",
        "stale-token-ok",
        "stale-token-reject",
        "token-remove-answer",
        "unknown-result",
    ],
)
async def test_token_answer_classifier(answer: bytes, holds: bool | None) -> None:
    radio, _transport, _answers = make_radio()
    phase = radio._control_phase
    waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    phase._renewal_waiter = waiter
    phase._on_token_reply(answer)
    if holds is None:
        assert not waiter.done()
        assert phase._renewal_answered_at is None
    else:
        assert waiter.result() is holds
        assert (phase._renewal_answered_at is not None) is holds


async def test_connect_routes_token_answers_to_the_probe_only_after_setup() -> None:
    radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
    mt = ConnectMockTransport()
    radio._ctrl_transport = mt
    # Left set by an earlier session on the same transport object.
    mt._token_reply_callback = radio._control_phase._on_token_reply
    mt.queue_response(_build_conninfo())
    during_setup: list[object] = []
    real_receive_guid = radio._control_phase._receive_guid

    async def spy_receive_guid() -> bytes | None:
        during_setup.append(mt._token_reply_callback)
        return await real_receive_guid()

    with (
        _ConnectOnceStubs(radio),
        patch.object(
            radio._control_phase, "_receive_guid", side_effect=spy_receive_guid
        ),
        patch.object(radio, "_gate_lan_identity", AsyncMock(return_value=True)),
    ):
        await radio._control_phase._connect_once()
    assert during_setup == [None]
    assert mt._token_reply_callback == radio._control_phase._on_token_reply
