"""MOR-1013 slices 4 and 5: owner identity and liveness on the Web TX path.

The poller keys on behalf of the control session (D1-a), so the owner must be
the STABLE session id on the queue entry — not the throwaway
``ControlHandler._session_id`` the shared command executor mints per request:
``release_owner`` matches on the owner alone, so a mismatch strands the rig
keyed. A real ``TxSafetySupervisor`` drives these tests because a scripted
fake would answer ACCEPTED to any owner and could never show that.

Slice 5 adds the other half of that identity: the queue entry outlives its
author, and the supervisor grants a lease to any owner, alive or dead. The
queue carries the liveness record because it is the one object both ends
already hold — the handler registers on connect and unregisters on every
teardown path, and the poller refuses a key on behalf of a session gone by
drain time. ON only: an unkey refused for being late strands the rig keyed.

MOR-1187 closes what both leave open on the unkey: binding is not a lookup but
backend code that can fail, so it belongs inside slice 1's teardown guard rather
than above it. It also pins the two managed behaviours that had no test at all.

MOR-1185 closes the last hole in the pair: the teardown unkey went onto the RAW
queue, so it reached the drain owner-less and de-keyed the rig without ever
giving the lease back. Routed through the metadata wrapper it releases what it
took — and, deliberately, drops the unconditional write it used to make.

MOR-1016 PR 5 turns the owner gate into an ingress gate. Until assembly there
was no managed backend, so "binds no owner" could keep falling through to the
raw ``set_ptt`` write; once a rig publishes a supervisor that fallthrough is an
unsupervised key on a managed rig. The gate lives in
``rigplane.runtime.managed_tx_ingress`` — below the poller, so rigctld
(MOR-1014) and the CLI/SDK (MOR-1190) reuse it instead of growing a third copy
of the same two-step read (MOR-1198) — and it is deliberately one-sided: keys
are refusable, unkeys never are.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.capabilities import CAP_AUDIO
from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import ManagedTxApi, PrivilegedTxApi
from rigplane.core.tx_safety import (
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime import managed_tx_ingress
from rigplane.runtime.managed_tx_ingress import (
    bind_managed_tx,
    refuse_key_without_owner,
    resolve_supervisor,
)
from rigplane.web import radio_poller as radio_poller_module
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue, PttOff, PttOn, RadioPoller

_KEY, _TEARDOWN = ["start_tx", "set_ptt(True)"], ["stop_tx", "restart_rx"]
_WS1, _WS2 = TxOwner(TxSource.WEBSOCKET, "ws-1"), TxOwner(TxSource.WEBSOCKET, "ws-2")


class _Supervisor:
    """Async ``ManagedTxSupervisor`` over the real single-target policy."""

    def __init__(self) -> None:
        self.inner = TxSafetySupervisor(watchdog_seconds=None)
        self.inner.replace_provider(0, ready=True)
        self.inner.observe_ptt(
            ProviderPttObservation(RadioTx.OFF, 0, 1, time.monotonic())
        )
        self.entries: list[tuple[bool, TxOwner]] = []
        self.outcomes: list[TxOutcome] = []

    def _record(self, on: bool, owner: TxOwner, t: TxTransition) -> TxTransition:
        self.entries.append((on, owner))
        self.outcomes.append(t.outcome)
        return t

    async def request_on(self, owner: TxOwner) -> TxTransition:
        return self._record(True, owner, self.inner.request_on(owner))

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        released = self.inner.release_owner(owner, reason=reason)
        return self._record(False, owner, released)


class _Radio:
    """Duck-typed provider; deliberately not a ``MagicMock``, which satisfies
    a ``runtime_checkable`` protocol on 3.11 but not on 3.12+ (gh-102433)."""

    def __init__(self, supervisor: _Supervisor | None) -> None:
        self.profile = resolve_radio_profile(model="IC-7610")
        self.capabilities = {CAP_AUDIO}
        self.managed_tx = supervisor
        self.calls: list[str] = []
        self.audio_bus = SimpleNamespace(restart_rx=self._restart_rx)

    async def set_ptt(self, on: bool) -> None:
        self.calls.append(f"set_ptt({on})")

    async def start_tx(self) -> None:
        self.calls.append("start_tx")

    async def stop_tx(self) -> None:
        self.calls.append("stop_tx")

    async def _restart_rx(self) -> None:
        self.calls.append("restart_rx")


class _BrokenSupervisorRadio(_Radio):
    """Backend whose supervisor accessor itself raises.

    ``ManagedTxApi.bind`` reads ``managed_tx`` exactly once, explicitly, and
    settles absence without running it (MOR-1193), so the accessor's failure is
    the bind's failure on every interpreter.
    """

    @property
    def managed_tx(self) -> _Supervisor | None:
        raise RuntimeError("managed_tx accessor exploded")

    @managed_tx.setter
    def managed_tx(self, value: _Supervisor | None) -> None:
        """Absorb ``_Radio.__init__``'s assignment; the getter is the point."""


def _poller(supervisor: _Supervisor | None) -> tuple[RadioPoller, _Radio]:
    radio = _Radio(supervisor)
    return RadioPoller(radio, CommandQueue()), radio  # type: ignore[arg-type]


def _run_handler(
    queue: CommandQueue | None = None,
) -> tuple[ControlHandler, CommandQueue]:
    """A handler whose ``run()`` reaches teardown on the first ``recv``."""
    queue = CommandQueue() if queue is None else queue

    async def recv() -> tuple[int, bytes]:
        await asyncio.sleep(0.01)  # let the event-sender task run before EOF
        raise EOFError

    return ControlHandler(
        ws=SimpleNamespace(send_text=AsyncMock(), recv=recv),
        radio=SimpleNamespace(connected=True, radio_ready=True),
        server_version="test",
        radio_model="IC-7610",
        server=SimpleNamespace(
            command_queue=queue,
            register_control_event_queue=MagicMock(),
            unregister_control_event_queue=MagicMock(),
            build_state_update_envelope=MagicMock(return_value={}),
        ),
    ), queue


def _wired(
    supervisor: _Supervisor | None,
) -> tuple[ControlHandler, RadioPoller, _Radio]:
    """One control session and one poller over the queue the server shares."""
    handler, queue = _run_handler()
    radio = _Radio(supervisor)
    return handler, RadioPoller(radio, queue), radio  # type: ignore[arg-type]


def _owner(handler: ControlHandler) -> TxOwner:
    return TxOwner(TxSource.WEBSOCKET, handler._session_id)


async def _drain(poller: RadioPoller) -> None:
    """Execute queued entries exactly as ``_run``'s drain does — including its
    default of ``source="websocket"`` for an entry that carries no source."""
    for entry in poller._queue.drain_entries():
        await poller._execute(
            entry.command,
            command_id=entry.command_id,
            source=entry.source or "websocket",
            session_id=entry.session_id,
            command_service=entry.command_service,
        )


def _settle_release(supervisor: _Supervisor) -> list[ProviderAttemptKind]:
    """Drive the started de-key to completion and report the provider work.

    The policy is pure: ``release_owner`` only STARTS the release. Undriven, the
    lease sits in RELEASE_PENDING and the next session is refused for a reason
    that is not BUSY but is still a refusal, which would prove nothing. Every
    attempt this settles belongs to the de-key, so observing the rig OFF after
    each one is what the runtime's effect service sees on a rig that obeyed.
    """
    kinds: list[ProviderAttemptKind] = []
    seq = 1
    while (attempt := supervisor.inner.snapshot.active_attempt) is not None:
        kinds.append(attempt.kind)
        supervisor.inner.settle_attempt(
            attempt.id, attempt.provider_generation, succeeded=True
        )
        seq += 1
        supervisor.inner.observe_ptt(
            ProviderPttObservation(RadioTx.OFF, 0, seq, time.monotonic())
        )
    return kinds


async def test_unmanaged_radio_keeps_the_legacy_write_and_ordering() -> None:
    """Every shipped radio binds to ``None`` and keeps today's behaviour."""
    poller, radio = _poller(None)

    assert ManagedTxApi.bind(radio, _WS1) is None
    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    await poller._execute(PttOff(), command_id="c2", session_id="ws-1")

    # start_tx strictly before the key; teardown after the unkey.
    assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]


async def test_key_and_release_share_one_stable_owner() -> None:
    """A real supervisor accepts the release only if the owner matches."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    await poller._execute(PttOff(), command_id="c2", session_id="ws-1")

    assert supervisor.entries == [(True, _WS1), (False, _WS1)]
    # STALE would mean the release missed its own lease.
    assert supervisor.outcomes == [TxOutcome.ACCEPTED, TxOutcome.ACCEPTED]
    # No bypass: the supervisor's own effect path owns the provider write.
    assert radio.calls == ["start_tx", *_TEARDOWN]


async def test_a_second_session_gets_its_own_owner_and_is_refused() -> None:
    """Two sessions, two owners — and a refused key disarms the TX leg."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    with pytest.raises(CommandError, match=TxOutcome.BUSY):
        await poller._execute(PttOn(), command_id="c2", session_id="ws-2")

    assert supervisor.entries == [(True, _WS1), (True, _WS2)]
    assert supervisor.outcomes == [TxOutcome.ACCEPTED, TxOutcome.BUSY]
    # Refusal must not leave modulation flowing towards a rig nobody keyed.
    assert radio.calls == ["start_tx", "start_tx", *_TEARDOWN]


async def test_a_same_owner_re_key_is_accepted_and_keeps_its_leg_armed() -> None:
    """IDEMPOTENT answers 'already yours', which is acceptance, not refusal.

    Read as a refusal it would trip the disarm above — tearing down a LIVE audio
    leg mid-transmission while the lease, and the rig, stay keyed: the operator
    is still on the air with nothing feeding it.
    """
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    await poller._execute(PttOn(), command_id="c2", session_id="ws-1")

    assert supervisor.entries == [(True, _WS1), (True, _WS1)]
    assert supervisor.outcomes == [TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT]
    assert radio.calls == ["start_tx", "start_tx"]  # no _TEARDOWN


async def test_a_refused_release_is_tolerated_and_still_tears_down() -> None:
    """STALE means nothing of ours is keyed; raising would break ``finally``."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOff(), command_id="c1", session_id="ws-1")

    assert supervisor.outcomes == [TxOutcome.STALE]
    assert radio.calls == _TEARDOWN


async def test_a_raising_managed_tx_accessor_still_tears_the_tx_leg_down() -> None:
    """MOR-1187: the bind belongs INSIDE the guard it depends on.

    Binding is not a read-only lookup — it runs backend code that can fail. Bound
    above the ``try``, a raising accessor skips the ``finally`` outright, and the
    unkey path loses the one thing it must never lose: modulation kept flowing
    into a rig the operator believes is unkeyed. The error must still surface.
    """
    radio = _BrokenSupervisorRadio(None)
    poller = RadioPoller(radio, CommandQueue())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="accessor exploded"):
        await poller._execute(PttOff(), command_id="c1", session_id="ws-1")

    assert radio.calls == _TEARDOWN


async def test_a_websocket_unkey_without_a_session_id_stays_unmanaged() -> None:
    """The ``session_id`` half of the gate carries its own weight.

    A sourceless entry defaults to ``source="websocket"`` at drain, so for any
    entry that carries no id — every non-websocket ingress, and the teardown
    unkey before MOR-1185 routed it through the metadata wrapper — only the
    emptiness check stands between the drain and ``TxOwner``'s empty-id
    ``ValueError``, which would replace a de-key with a raise.
    """
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOff(), command_id="c1", session_id=None)

    assert supervisor.entries == []
    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


async def test_http_ingress_never_binds_an_owner_and_is_refused_the_key() -> None:
    """HTTP has no session and no teardown hook, so a lease taken there could
    never be released — and its params are caller-supplied, not a session.

    MOR-1016 PR 5 draws the consequence the bind alone could not: on a MANAGED
    rig, "binds no owner" used to mean "falls through to the raw
    ``set_ptt(True)``" — an unsupervised key with no lease, no owner and no
    watchdog behind it, which is the bypass management exists to close. So an
    ingress that cannot hold a lease is refused the key outright, and the
    supervisor never hears about it.
    """
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    with pytest.raises(CommandError, match="no owner"):
        await poller._execute(PttOn(), source="http", session_id=None)
    # A caller-supplied ``session_id`` is not a session: same refusal.
    with pytest.raises(CommandError, match="http"):
        await poller._execute(PttOn(), source="http", session_id="forged")

    # No lease attempt, and above all no provider write: the refusal leaves no
    # trace on the air, and each armed TX audio leg is disarmed behind it.
    assert supervisor.entries == []
    assert "set_ptt(True)" not in radio.calls
    assert radio.calls == ["start_tx", *_TEARDOWN, "start_tx", *_TEARDOWN]


async def test_an_http_unkey_on_a_managed_rig_still_writes_the_legacy_off() -> None:
    """The asymmetry, stated: a refused unkey strands a keyed transmitter.

    The key gate above and this are deliberately not symmetric — the same
    doctrine ``_refuse_key_from_gone_session`` is built on. Refusing a key costs
    an operator one denied transmission; refusing an unkey leaves the rig on the
    air with nobody able to take it off. So the ``PttOff`` arm keeps the
    unconditional legacy write for every ingress that binds no owner, managed
    rig or not.
    """
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOff(), source="http", session_id=None)

    assert supervisor.entries == []
    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


async def test_an_unmanaged_rig_keeps_the_legacy_http_path_on_both_arms() -> None:
    """Every shipped backend is still unmanaged, so HTTP PTT is untouched.

    The gate asks the radio, not the request: with no supervisor published there
    is nothing to be refused on behalf of, and refusing here would break HTTP
    PTT for every rig in the field.
    """
    poller, radio = _poller(None)

    await poller._execute(PttOn(), source="http", session_id=None)
    await poller._execute(PttOff(), source="http", session_id=None)

    assert radio.calls == [*_KEY, "set_ptt(False)", *_TEARDOWN]


async def test_a_raising_accessor_on_the_key_path_disarms_the_tx_leg_too() -> None:
    """The gate resolves a supervisor, so it runs backend code that can fail.

    MOR-1187's lesson applied to the other arm: the resolution happens with the
    TX audio leg already armed, so it belongs inside the guard that disarms it.
    A failed resolution is not "unmanaged" — it propagates — but it must not
    leave modulation flowing towards a rig this ingress never keyed.
    """
    radio = _BrokenSupervisorRadio(None)
    poller = RadioPoller(radio, CommandQueue())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="accessor exploded"):
        await poller._execute(PttOn(), source="http", session_id=None)

    assert "set_ptt(True)" not in radio.calls
    assert radio.calls == ["start_tx", *_TEARDOWN]


async def test_a_key_from_a_gone_session_costs_nothing() -> None:
    """Slice 5: the queue entry outlives its author; the key must not."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)
    poller._queue.register_session("ws-1")
    poller._queue.unregister_session("ws-1")

    with pytest.raises(CommandError, match="ws-1"):
        await poller._execute(PttOn(), command_id="c1", session_id="ws-1")

    # Refused ahead of the TX audio leg and ahead of the lease. Raising is the
    # poller's failure channel (``_mark_queued_command_failed`` plus
    # ``future.set_exception``), so a refusal can never be read as success.
    assert supervisor.entries == []
    assert radio.calls == []


async def test_a_live_session_keys_exactly_as_it_does_today() -> None:
    """The gate is invisible to the session that is actually connected."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)
    poller._queue.register_session("ws-1")

    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    await poller._execute(PttOff(), command_id="c2", session_id="ws-1")

    assert supervisor.entries == [(True, _WS1), (False, _WS1)]
    assert supervisor.outcomes == [TxOutcome.ACCEPTED, TxOutcome.ACCEPTED]
    assert radio.calls == ["start_tx", *_TEARDOWN]


async def test_a_queue_no_session_registered_on_assumes_nobody_is_gone() -> None:
    """No registration means no knowledge — not 'every session is dead'."""
    poller, radio = _poller(None)

    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")

    assert poller._queue.session_is_live("ws-1")
    assert radio.calls == _KEY


async def test_a_command_with_no_session_id_keys_and_unkeys() -> None:
    """HTTP PTT carries no session at all, so it has no liveness to check even
    once the queue is tracking sessions. Both source arms matter, because the
    drain defaults a sourceless entry to ``source="websocket"`` — the arm the
    teardown unkey took until MOR-1185 gave it the metadata wrapper."""
    poller, radio = _poller(None)
    poller._queue.register_session("ws-1")
    poller._queue.unregister_session("ws-1")

    await poller._execute(PttOn(), source="http", session_id=None)
    await poller._execute(PttOn(), session_id=None)
    await poller._execute(PttOff(), session_id=None)

    assert radio.calls == [*_KEY, *_KEY, "set_ptt(False)", *_TEARDOWN]


async def test_a_gone_session_may_still_unkey() -> None:
    """Gating OFF would strand the rig keyed — the opposite of the point."""
    poller, radio = _poller(None)
    poller._queue.register_session("ws-1")
    poller._queue.unregister_session("ws-1")

    await poller._execute(PttOff(), command_id="c1", session_id="ws-1")

    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


async def test_a_session_is_registered_for_the_whole_of_its_run() -> None:
    """Published before the recv loop can accept a single command of its own."""
    handler, queue = _run_handler()
    seen: list[bool] = []

    async def recv() -> tuple[int, bytes]:
        seen.append(queue.session_is_live(handler._session_id))
        raise EOFError

    handler._ws.recv = recv
    await handler.run()

    assert seen == [True]
    assert not queue.session_is_live(handler._session_id)


async def test_teardown_marks_the_session_gone_through_a_dead_egress_socket() -> None:
    """``await event_task`` re-raises here and skips everything behind it — the
    trap slice 2 moved the PTT release out of. A session left marked live is a
    session that can still key."""
    handler, queue = _run_handler()
    handler._ws.send_text = AsyncMock(
        side_effect=[None, None, ConnectionResetError("egress socket closed")]
    )
    handler._event_queue.put_nowait({"type": "state_update"})

    with pytest.raises(ConnectionResetError):
        await handler.run()

    assert not queue.session_is_live(handler._session_id)


async def test_a_disconnect_releases_the_lease_it_took_not_just_the_rig() -> None:
    """MOR-1185, acceptance 1 and 2: the next session keys without waiting.

    The teardown OFF used to go onto the RAW server queue, so it reached the
    drain sourceless and session-less, took the legacy write, and left the lease
    with a session that no longer exists. MOR-1191's watchdog clears that after
    ``watchdog_seconds`` — a backstop, not a fix: three minutes of denied TX
    after every disconnect. A real supervisor drives this, so an owner that did
    not match would answer STALE here rather than quietly passing.

    Consequence C rides along: the finally unregisters the session immediately
    after enqueuing the unkey, so the entry is always drained on behalf of an
    author already gone. ``_refuse_key_from_gone_session`` covers ``PttOn`` only
    and must keep to it — an OFF refused for being late strands a transmitter.
    """
    supervisor = _Supervisor()
    handler, poller, radio = _wired(supervisor)
    handler._publish_session_liveness(live=True)

    await handler._enqueue_command("ptt_on", {})
    await _drain(poller)
    await handler.run()  # EOF on the first recv: the session disconnects
    assert not poller._queue.session_is_live(handler._session_id)
    await _drain(poller)

    assert supervisor.entries == [(True, _owner(handler)), (False, _owner(handler))]
    assert supervisor.outcomes == [TxOutcome.ACCEPTED, TxOutcome.ACCEPTED]
    # The de-key is the supervisor's own WRITE_OFF, not a raw defensive write.
    assert _settle_release(supervisor) == [
        ProviderAttemptKind.WRITE_ON,  # the key attempt, cancelled by the release
        ProviderAttemptKind.WRITE_OFF,
    ]
    assert "set_ptt(False)" not in radio.calls

    second, _ = _run_handler(poller._queue)
    second._publish_session_liveness(live=True)
    await second._enqueue_command("ptt_on", {})
    await _drain(poller)

    # ACCEPTED, not BUSY and not RELEASE_PENDING: the lease is genuinely gone.
    assert supervisor.entries[-1] == (True, _owner(second))
    assert supervisor.outcomes[-1] is TxOutcome.ACCEPTED


async def test_the_lease_is_released_through_a_dead_egress_socket() -> None:
    """Acceptance 3: slice 2's guarantee, now with a lease behind it.

    ``await event_task`` re-raises the sender's error and skips everything
    behind it, which is why the release is the FIRST statement of the finally.
    """
    supervisor = _Supervisor()
    handler, poller, radio = _wired(supervisor)
    handler._publish_session_liveness(live=True)
    await handler._enqueue_command("ptt_on", {})
    await _drain(poller)
    handler._ws.send_text = AsyncMock(
        side_effect=[None, None, ConnectionResetError("egress socket closed")]
    )
    handler._event_queue.put_nowait({"type": "state_update"})

    with pytest.raises(ConnectionResetError):
        await handler.run()
    await _drain(poller)

    assert supervisor.entries[-1] == (False, _owner(handler))
    assert supervisor.outcomes[-1] is TxOutcome.ACCEPTED


async def test_a_session_that_never_keyed_writes_nothing_to_a_managed_rig() -> None:
    """Consequence A, stated: this REMOVES a defensive write that fires today.

    ``release_owner`` on a session holding no lease answers STALE and emits no
    WRITE_OFF, so nothing reaches the rig. Correct when it is idle, and required
    when it is not: under management the lease is the authority on who is on the
    air, and one session's disconnect must not de-key another's transmission.
    The rig keyed by something outside the supervisor's knowledge
    (``TxPhase.EXTERNAL_UNOWNED``, still without consumers) stays MOR-1175's to
    close — a per-session blind write is not the instrument for it.
    """
    supervisor = _Supervisor()
    handler, poller, radio = _wired(supervisor)

    await handler.run()
    await _drain(poller)

    assert supervisor.entries == [(False, _owner(handler))]
    assert supervisor.outcomes == [TxOutcome.STALE]
    assert _settle_release(supervisor) == []  # no provider write of any kind
    assert radio.calls == _TEARDOWN


async def test_an_unmanaged_teardown_still_writes_the_unconditional_unkey() -> None:
    """Acceptance 4: no shipped backend assembles a managed runtime until
    MOR-1016, so the unconditional legacy OFF is what production still gets."""
    handler, poller, radio = _wired(None)

    await handler.run()
    await _drain(poller)

    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


# --- the ingress gate itself (rigplane.runtime.managed_tx_ingress) ----------


def test_resolve_supervisor_propagates_a_raising_accessor() -> None:
    """A broken accessor is a broken backend, never a positive 'unmanaged'.

    This is the whole reason the two-step read exists rather than
    ``getattr(radio, "managed_tx", None)``, whose default absorbs an
    ``AttributeError`` raised *inside* the property and hands a managed rig to
    the unsupervised write (MOR-1187, MOR-1193, MOR-1196).
    """
    with pytest.raises(RuntimeError, match="accessor exploded"):
        resolve_supervisor(_BrokenSupervisorRadio(None))


def test_resolve_supervisor_reads_absence_and_a_published_none_as_unmanaged() -> None:
    """Both unmanaged shapes: no such member, and a member holding ``None``."""
    assert resolve_supervisor(object()) is None
    assert resolve_supervisor(_Radio(None)) is None


def test_resolve_supervisor_answers_a_supervisor_with_no_privileged_surface() -> None:
    """``ManagedTxSupervisor`` is the guaranteed minimum, and it is enough.

    ``_Supervisor`` publishes ``request_on``/``release_owner`` and nothing else,
    so ``PrivilegedTxApi`` correctly declines it. The gate must not: requiring
    ``force_unkey`` here would read a conformant managed backend as unmanaged
    and reopen the very fallthrough this gate closes.
    """
    supervisor = _Supervisor()
    radio = _Radio(supervisor)

    assert resolve_supervisor(radio) is supervisor
    assert PrivilegedTxApi.bind(radio, _WS1) is None


def test_bind_managed_tx_binds_only_a_stable_owner() -> None:
    """The poller's old ``_managed_tx`` body, now shared and unchanged."""
    supervisor = _Supervisor()
    radio = _Radio(supervisor)

    managed = bind_managed_tx(radio, "websocket", "ws-1")
    assert managed is not None
    assert managed.owner == _WS1
    assert managed.supervisor is supervisor

    # No stable owner anywhere else: HTTP (with or without a forged id), and a
    # websocket entry that carries no id at all.
    assert bind_managed_tx(radio, "http", None) is None
    assert bind_managed_tx(radio, "http", "forged") is None
    assert bind_managed_tx(radio, "websocket", None) is None
    assert bind_managed_tx(radio, "websocket", "") is None
    # Unmanaged radios bind nothing even from a stable owner.
    assert bind_managed_tx(_Radio(None), "websocket", "ws-1") is None


def test_refuse_key_without_owner_is_exactly_managed_minus_ownable() -> None:
    """Refuse only where both halves hold: managed rig, unownable ingress.

    An owned ingress is not refused — it keys through the supervisor — and an
    unmanaged rig is not refused either, or HTTP PTT would break for every radio
    in the field. The supervisor is resolved only once the ingress has already
    failed the owner test, so the common websocket path runs no backend code.
    """
    managed_radio, unmanaged_radio = _Radio(_Supervisor()), _Radio(None)

    assert refuse_key_without_owner(managed_radio, "http", None) is True
    assert refuse_key_without_owner(managed_radio, "http", "forged") is True
    assert refuse_key_without_owner(managed_radio, "websocket", None) is True
    assert refuse_key_without_owner(managed_radio, "websocket", "ws-1") is False
    assert refuse_key_without_owner(unmanaged_radio, "http", None) is False
    assert refuse_key_without_owner(unmanaged_radio, "websocket", None) is False
    # An owned ingress never resolves a supervisor, so a broken accessor on the
    # radio cannot turn a keyable session into an error.
    owned = refuse_key_without_owner(_BrokenSupervisorRadio(None), "websocket", "ws-1")
    assert owned is False


def test_the_poller_carries_no_second_copy_of_the_supervisor_read() -> None:
    """MOR-1198: one two-step read, in the layer every ingress can reach.

    A structural assertion because the duplication is what the bug is made of:
    three call sites resolved the supervisor independently and two of them got
    the failure discipline wrong. The poller now asks the gate; it builds no
    ``TxOwner`` and reads no ``managed_tx`` member of its own.
    """
    gate_src = Path(managed_tx_ingress.__file__).read_text()
    poller_src = Path(radio_poller_module.__file__).read_text()

    assert "getattr_static" in gate_src  # the canonical read lives here
    assert "getattr_static" not in poller_src
    assert "ManagedTxApi.bind(" not in poller_src
    assert "TxOwner(" not in poller_src
    assert "managed_tx_ingress" in poller_src
