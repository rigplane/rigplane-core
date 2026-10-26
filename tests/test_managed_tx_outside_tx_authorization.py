"""MOR-3764: outside-only TX audio authorization, atomic with the submission.

A client (Pro) sends TX audio as binary ``0x12`` frames ("TX audio,
outside-only"). Core admits such a frame only while the radio is observed
keyed OUTSIDE Core — managed intent ``rx``, no release debt, no
``last_error``, and a ``RadioTxOrigin.FOREIGN`` PTT readback — and the
admission check plus the push to the radio form ONE critical section that
excludes every Core-client keying transition
(:meth:`ManagedTxAuthority.outside_tx_submission`). These tests pin the
rule, the ticket-acceptance race, the atomicity against keying (and the
non-blocking unkeying), the push budget, and the wire-level behavior of
``0x12`` — with ``0x11`` unchanged.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _order_sensitive_radios import LanLikeRadio
from test_controller_sessions import FakeRadioTx, _radio_reports, radio_tx_authority
from test_managed_tx_production_composition import LegacyRadio, RecordingActuator
from test_managed_tx_web_projection import _GatePort
from test_web_audio_tx_session import _SessionLanRadio, _pcm_tx_frame, _start_tx
from test_web_controller import remote, station  # noqa: F401

from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.runtime.controller_authority import ControllerTicket
from rigplane.runtime.managed_tx_authority import (
    OUTSIDE_TX_SUBMISSION_BUDGET_S,
    ManagedTxAuthority,
    RadioTxOrigin,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.web.handlers.audio import AudioHandler
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX_OUTSIDE,
)
from rigplane.web.server import WebConfig, WebServer

ACCEPTED = ManagedTxOutcome.ACCEPTED
REJECTED = ManagedTxOutcome.REJECTED


class _RaisingRadioTx(FakeRadioTx):
    """A PTT readback that fails instead of answering (probe exception)."""

    def radio_tx_since(self, since: float) -> RadioTxOrigin | None:
        raise RuntimeError("readback lost")


def _outside_tx_frame(payload: bytes) -> bytes:
    return (
        bytes([MSG_TYPE_AUDIO_TX_OUTSIDE, AUDIO_CODEC_PCM16])
        + b"\x00" * (AUDIO_HEADER_SIZE - 2)
        + payload
    )


async def _assert_refused(managed: ManagedTxAuthority) -> None:
    """The submission yields False and the caller's push never runs."""
    pushed: list[bytes] = []
    async with managed.outside_tx_submission() as admitted:
        if admitted:
            pushed.append(b"frame")
    assert pushed == []


# ---------------------------------------------------------------------------
# Authority level: the outside-only rule
# ---------------------------------------------------------------------------


async def test_outside_submission_admitted_while_radio_keyed_outside_core() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        probe.tx_at = clock.now  # keyed outside Core, observed after idle
        pushed: list[bytes] = []
        async with managed.outside_tx_submission() as admitted:
            assert admitted is True
            pushed.append(b"frame-a")  # the caller's push runs in the section
        assert pushed == [b"frame-a"]
    finally:
        await managed.close()


async def test_outside_submission_refused_without_tx_reading() -> None:
    managed, _clock, _lane, probe = radio_tx_authority()
    try:
        probe.tx_at = None  # RX / unknown: nothing keyed outside Core
        await _assert_refused(managed)
    finally:
        await managed.close()


async def test_outside_submission_refused_for_unreleased_core_tx() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        # Core's own TX the radio has not released is NOT "outside".
        probe.tx_at, probe.origin = clock.now, RadioTxOrigin.UNRELEASED
        await _assert_refused(managed)
    finally:
        await managed.close()


async def test_outside_submission_refused_when_readback_raises() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    managed._radio_tx = _RaisingRadioTx()
    try:
        await _assert_refused(managed)
    finally:
        await managed.close()


async def test_outside_submission_refused_without_readback_probe() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    managed._radio_tx = None
    try:
        await _assert_refused(managed)
    finally:
        await managed.close()


async def test_outside_submission_refused_with_release_debt_and_last_error() -> None:
    managed, clock, lane, probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("websocket-1") is ACCEPTED
        lane.results.append(ActuationResult.REJECTED)  # the release fails
        assert await managed.ptt_up("websocket-1") is ACCEPTED
        state = (await managed.snapshot()).state
        assert state.release_required and state.last_error is not None
        probe.tx_at = clock.now  # a foreign reading excuses no release debt
        await _assert_refused(managed)
        assert await managed.force_off() is ACCEPTED
    finally:
        await managed.close()


async def test_outside_submission_refused_with_last_error() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        managed._state = replace(managed._state, last_error="release failed")
        probe.tx_at = clock.now
        await _assert_refused(managed)
    finally:
        await managed.close()


async def test_outside_submission_refused_while_a_core_client_holds_ptt() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("rigctld-client-1") is ACCEPTED
        probe.tx_at = clock.now  # even a foreign reading no longer admits
        await _assert_refused(managed)
        assert await managed.ptt_up("rigctld-client-1") is ACCEPTED
    finally:
        await managed.close()


async def test_outside_submission_refused_while_transmit_is_latched() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        assert await managed.transmit_on() is ACCEPTED
        probe.tx_at = clock.now
        await _assert_refused(managed)
        assert await managed.force_off() is ACCEPTED
    finally:
        await managed.close()


async def test_outside_submission_refused_once_closed() -> None:
    managed, clock, _lane, probe = radio_tx_authority()
    probe.tx_at = clock.now
    await managed.close()
    await _assert_refused(managed)


async def test_outside_admission_closes_the_moment_a_core_client_keys() -> None:
    """The ticket-acceptance race: a keying admitted between two readings."""
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        probe.tx_at = clock.now  # FOREIGN
        pushed: list[bytes] = []
        async with managed.outside_tx_submission() as admitted:
            assert admitted is True
            pushed.append(b"frame-a")

        probe.tx_at = None  # key-up observed
        submission = await managed.submit_ptt(True, "rigctld-client-1")
        assert submission.outcome is ACCEPTED

        probe.tx_at = clock.now  # a stale/new TX reading flips back to FOREIGN
        async with managed.outside_tx_submission() as admitted:
            if admitted:
                pushed.append(b"late")
            assert admitted is False
        assert pushed == [b"frame-a"]

        assert await managed.ptt_up("rigctld-client-1") is ACCEPTED
    finally:
        await managed.close()


async def test_outside_push_excludes_keying_and_unkeying_never_waits() -> None:
    """ATOMICITY: an admitted in-flight push bounds every keying transition."""
    managed, clock, _lane, probe = radio_tx_authority()
    try:
        probe.tx_at = clock.now  # FOREIGN
        pushed: list[bytes] = []
        push_started = asyncio.Event()
        release_push = asyncio.Event()

        async def slow_push(frame: bytes) -> None:
            push_started.set()
            await release_push.wait()
            pushed.append(frame)

        async def submission() -> None:
            async with managed.outside_tx_submission() as admitted:
                assert admitted is True
                await slow_push(b"frame-b")

        submission_task = asyncio.ensure_future(submission())
        await asyncio.wait_for(push_started.wait(), 1.0)  # frame B in flight

        # Unkeying never waits behind audio: both complete with B in flight.
        force = await managed.submit_force_off()
        await force.wait_settlement()
        assert await managed.ptt_up("websocket-9") is REJECTED

        ptt_task = asyncio.ensure_future(managed.submit_ptt(True, "rigctld-client-1"))
        # Bounded wait long enough for the PTT to be admitted if nothing
        # excluded it: it must still be waiting behind the in-flight push.
        done, _ = await asyncio.wait({ptt_task}, timeout=0.2)
        assert ptt_task not in done, "keying ran while an outside push was in flight"
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX

        probe.tx_at = None  # the outside TX ends before Core keys
        release_push.set()
        await submission_task
        assert pushed == [b"frame-b"]

        # Only now is the Core client's keying admitted.
        ptt = await ptt_task
        assert ptt.outcome is ACCEPTED
        assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.PTT
        await _assert_refused(managed)
        assert pushed == [b"frame-b"]

        assert await managed.ptt_up("rigctld-client-1") is ACCEPTED
    finally:
        await managed.close()


async def test_stuck_outside_push_is_cut_at_the_submission_budget() -> None:
    """An admitted push that never returns is cut at the budget; keying proceeds."""
    managed, clock, _lane, probe = radio_tx_authority()
    submission_task = None
    ptt_task = None
    try:
        probe.tx_at = clock.now  # FOREIGN
        delivered: list[bytes] = []
        push_started = asyncio.Event()

        async def stuck_push(frame: bytes) -> None:
            push_started.set()
            await asyncio.Event().wait()  # never released
            delivered.append(frame)

        async def submission() -> None:
            async with managed.outside_tx_submission() as admitted:
                assert admitted is True
                await stuck_push(b"stuck")

        submission_task = asyncio.ensure_future(submission())
        await asyncio.wait_for(push_started.wait(), 1.0)
        # The outside TX ends now: the stuck push was already admitted, so it
        # is still the one the budget cuts, and the keying below is not
        # refused by MOR-3537 (``radio_tx_unowned``) over a FOREIGN reading.
        probe.tx_at = None
        ptt_task = asyncio.ensure_future(managed.submit_ptt(True, "rigctld-client-1"))

        done, _ = await asyncio.wait(
            {submission_task}, timeout=OUTSIDE_TX_SUBMISSION_BUDGET_S * 4
        )
        assert submission_task in done, "a stuck push escaped the submission budget"
        assert isinstance(submission_task.exception(), TimeoutError)
        assert delivered == []

        done, _ = await asyncio.wait({ptt_task}, timeout=5.0)
        assert ptt_task in done, "keying did not proceed after the budget cut"
        assert ptt_task.result().outcome is ACCEPTED
        assert await managed.ptt_up("rigctld-client-1") is ACCEPTED
    finally:
        for pending in (submission_task, ptt_task):
            if pending is not None and not pending.done():
                pending.cancel()
        off = await managed.submit_force_off()
        await off.wait_settlement()
        await managed.close()


# ---------------------------------------------------------------------------
# Web level: 0x12 through AudioHandler / WebServer
# ---------------------------------------------------------------------------


@pytest.fixture
async def outside_station():
    """A WebServer whose managed authority carries a controllable TX probe."""
    managed, clock, _lane, probe = radio_tx_authority()
    server = WebServer(None, WebConfig(radio_model="IC-7610"))
    server._production_managed_tx_port = _GatePort(managed)  # type: ignore[assignment]
    server._controller_installed = True
    server._controller_enforcement_ready = True
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=ObservedPtt.OFF,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=time.monotonic(),
            max_age=60,
        )
    )
    yield server, managed, clock, probe
    server._controller.revoke()
    await server._controller.settle()
    off = await managed.submit_force_off()
    await off.wait_settlement()
    await managed.close()


def _local_ticket(server: WebServer) -> ControllerTicket:
    key = server._controller.acquire("pro")["controller_key"]
    assert isinstance(key, str)
    return server._controller.attach(key, "primary", "pro-audio")


def _bridge_style_handler(
    server: WebServer, radio: LanLikeRadio, ticket: ControllerTicket
) -> AudioHandler:
    """The /api/v1/audio shape with the bridge gate (test_web_audio_tx_gate)."""
    return AudioHandler(
        SimpleNamespace(send_text=AsyncMock(), send_binary=AsyncMock()),
        radio,
        None,
        tx_gate=server._bridge_tx_gate_open,
        outside_tx_admission=lambda: server._controller_outside_tx_admission(ticket),
    )


class _BlockingPushSessionRadio(_SessionLanRadio):
    """Session radio whose push blocks in flight (the atomicity witness)."""

    def __init__(self) -> None:
        super().__init__()
        self.push_started = asyncio.Event()
        self.release_push = asyncio.Event()

    async def push_tx(self, audio_data: bytes) -> None:
        await LanLikeRadio.push_tx(self, audio_data)  # raises unless TX armed
        self.push_started.set()
        await self.release_push.wait()
        self.pushed.append(audio_data)


class _BlockingPushNeutralRadio(LanLikeRadio):
    """LAN-like stub WITHOUT ``audio_session``: the neutral push path."""

    capabilities = {"audio", "tx"}
    backend_id = "rigplane"
    has_usb_audio = True
    audio_sample_rate = 48000

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[bytes] = []
        self.push_started = asyncio.Event()
        self.release_push = asyncio.Event()

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)  # raises unless TX armed
        self.push_started.set()
        await self.release_push.wait()
        self.pushed.append(audio_data)


async def _assert_web_outside_push_excludes_keying(
    outside_station, radio: LanLikeRadio
) -> None:
    """A 0x12 frame in flight through the REAL handler bounds keying.

    Probe FOREIGN; the frame's push blocks on an event inside the admission
    critical section; a rigctld client's ``submit_ptt`` must wait behind it
    (intent still RX); once the push is released and recorded, the PTT is
    accepted and a further 0x12 frame is refused and counted.
    """
    server, managed, clock, probe = outside_station
    ticket = _local_ticket(server)
    handler = _bridge_style_handler(server, radio, ticket)
    await _start_tx(handler)
    assert handler._tx_active is True
    probe.tx_at = clock.now  # FOREIGN

    push_task = asyncio.ensure_future(
        handler._handle_tx_audio(_outside_tx_frame(b"in-flight"))
    )
    await asyncio.wait_for(radio.push_started.wait(), 1.0)  # push in flight

    probe.tx_at = None  # the outside TX ends before Core keys
    ptt_task = asyncio.ensure_future(managed.submit_ptt(True, "rigctld-client-1"))
    done, _ = await asyncio.wait({ptt_task}, timeout=0.2)
    assert ptt_task not in done, "keying ran while an outside push was in flight"
    assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.RX

    radio.release_push.set()
    await push_task
    assert radio.pushed == [b"in-flight"]

    ptt = await ptt_task
    assert ptt.outcome is ACCEPTED
    assert (await managed.snapshot()).state.intent.kind is ManagedTxIntentKind.PTT

    # Keyed by a Core client now: a further 0x12 frame is refused, not pushed.
    await handler._handle_tx_audio(_outside_tx_frame(b"after-key"))
    assert radio.pushed == [b"in-flight"]
    assert handler._tx_warn_counts["outside_tx_refused"] == 1

    assert await managed.ptt_up("rigctld-client-1") is ACCEPTED


async def test_web_outside_push_excludes_keying_lease_path(outside_station) -> None:
    """ATOMICITY through ``TxLease.push``: the push holds the critical section."""
    await _assert_web_outside_push_excludes_keying(
        outside_station, _BlockingPushSessionRadio()
    )


async def test_web_outside_push_excludes_keying_neutral_path(outside_station) -> None:
    """ATOMICITY through ``_push_tx_outside``'s non-lease branch."""
    radio = _BlockingPushNeutralRadio()
    await _assert_web_outside_push_excludes_keying(outside_station, radio)
    # Really the neutral lifecycle (no audio_session lease was taken).
    assert radio.calls.count("start_tx") == 1


async def test_web_outside_frame_admission_and_plain_tx_unchanged(
    outside_station,
) -> None:
    server, managed, clock, probe = outside_station
    ticket = _local_ticket(server)
    radio = _SessionLanRadio()
    handler = _bridge_style_handler(server, radio, ticket)
    await _start_tx(handler)
    assert handler._tx_active is True

    # Keyed outside Core: the 0x12 frame flows (the managed-key gate is NOT
    # consulted — it is closed here, intent RX and observed PTT OFF).
    probe.tx_at = clock.now
    await handler._handle_tx_audio(_outside_tx_frame(b"outside-a"))
    assert radio.pushed == [b"outside-a"]

    # The outside TX ends; a direct rigctld client keys through Core.
    probe.tx_at = None
    assert await managed.ptt_down("rigctld-client-1") is ACCEPTED
    # Even a fresh foreign-looking reading no longer admits 0x12...
    probe.tx_at = clock.now
    await handler._handle_tx_audio(_outside_tx_frame(b"outside-b"))
    assert radio.pushed == [b"outside-a"]
    # The refusal is counted on the lease path too (one shared wrapper).
    assert handler._tx_warn_counts["outside_tx_refused"] == 1
    # ...while a 0x11 frame in the same keyed state still passes (unchanged).
    await handler._handle_tx_audio(_pcm_tx_frame(b"client-tx"))
    assert radio.pushed == [b"outside-a", b"client-tx"]

    assert await managed.ptt_up("rigctld-client-1") is ACCEPTED


async def test_web_outside_frame_dropped_without_admission_hook() -> None:
    """The WebRTC shape: a handler built without outside_tx_admission refuses 0x12."""
    radio = _SessionLanRadio()
    handler = AudioHandler(
        SimpleNamespace(send_text=AsyncMock(), send_binary=AsyncMock()),
        radio,
        None,
    )
    await _start_tx(handler)
    await handler._handle_tx_audio(_outside_tx_frame(b"outside"))
    assert radio.pushed == []
    assert handler._tx_warn_counts["outside_tx_unsupported"] == 1


async def test_web_outside_frame_from_remote_ticket_never_reaches_push(
    station,  # noqa: F811
) -> None:
    server, managed = station
    key, primary = await remote(server)
    ticket = server._controller.attach(key, "auxiliary", "audio")

    # The server-side admission itself is closed for a remote ticket.
    async with server._controller_outside_tx_admission(ticket) as admitted:
        assert admitted is False

    source = object()
    radio = _SessionLanRadio()
    handler = AudioHandler(
        SimpleNamespace(send_text=AsyncMock(), send_binary=AsyncMock()),
        radio,
        None,
        tx_gate=lambda: server._controller_audio_gate(ticket, source),
        controller=server._controller,
        controller_ticket=ticket,
        controller_audio_source=source,
        controller_audio_loss=server._controller_audio_loss,
        controller_track_stop=server._controller_track_audio_stop,
        managed_tx_authority=server._managed_tx_authority(),
        outside_tx_admission=lambda: server._controller_outside_tx_admission(ticket),
    )
    await _start_tx(handler)
    with server._controller.bind(primary):
        assert await managed.ptt_down("native") is ACCEPTED

    # The remote holder's own transmission is live; 0x12 is still refused.
    await handler._handle_tx_audio(_outside_tx_frame(b"remote-outside"))
    assert radio.pushed == []
    assert handler._tx_warn_counts["outside_tx_unsupported"] == 1
    # ...while its 0x11 frames flow as before (default unchanged).
    await handler._handle_tx_audio(_pcm_tx_frame(b"remote-tx"))
    assert radio.pushed == [b"remote-tx"]

    with server._controller.bind(primary):
        assert await managed.ptt_up("native") is ACCEPTED


async def test_composition_probe_drives_web_outside_admission(tmp_path: Path) -> None:
    """The production probe (MOR-3537): UNRELEASED refuses, FOREIGN admits."""
    actuator, radio = RecordingActuator(), LegacyRadio()
    composition = ManagedTxComposition(actuator, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    authority = composition.authority

    server = WebServer(None, WebConfig(radio_model="IC-7610"))
    server._production_managed_tx_port = _GatePort(authority)  # type: ignore[assignment]
    server._controller_installed = True
    server._controller_enforcement_ready = True
    audio_radio = _SessionLanRadio()
    ticket = _local_ticket(server)
    handler = _bridge_style_handler(server, audio_radio, ticket)
    await _start_tx(handler)
    try:
        _radio_reports(store, False)  # RX observed
        assert await authority.ptt_down("websocket-1") is ACCEPTED
        _radio_reports(store, True)  # the radio reports Core's TX
        assert await authority.ptt_up("websocket-1") is ACCEPTED  # unkey ACKed

        # Still TX after Core's unkey, no RX in between: UNRELEASED, refused.
        _radio_reports(store, True)
        await handler._handle_tx_audio(_outside_tx_frame(b"unreleased"))
        assert audio_radio.pushed == []

        _radio_reports(store, False)  # an RX reading...
        _radio_reports(store, True)  # ...then a new TX reading: FOREIGN, admitted.
        await handler._handle_tx_audio(_outside_tx_frame(b"foreign"))
        assert audio_radio.pushed == [b"foreign"]
    finally:
        server._controller.revoke()
        await server._controller.settle()
        await composition.shutdown(asyncio.Event())
