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
(MOR-1014) and the CLI/SDK (already routed, landed under MOR-1170/MOR-1171)
reuse it instead of growing a third copy of the same two-step read (MOR-1198)
— and it is deliberately one-sided: keys are refusable, unkeys never are. The
serial-backend arm gap this owner/ingress gate does not itself touch is a
separate ticket: MOR-1219 (serial Icom managed arm), with MOR-1190 covering
its Yaesu CAT / rigctld-client siblings.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from rigplane.core.capabilities import CAP_AUDIO
from rigplane.core.tx_safety import (
    ProviderPttObservation,
    RadioTx,
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxTransition,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.managed_tx_ingress import refuse_key_without_owner
from rigplane.web.radio_poller import CommandQueue, PttOff, RadioPoller

# MOR-1879: this suite drives PTT dispatch directly; the interlock seat at
# ``_execute`` now gates ptt_on too, so the RF premise (radio observed in
# RX before keying) is stated once here — see the conftest fixture.
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")

_TEARDOWN = ["stop_tx", "restart_rx"]


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


async def test_a_gone_session_may_still_unkey() -> None:
    """Gating OFF would strand the rig keyed — the opposite of the point."""
    poller, radio = _poller(None)
    poller._queue.register_session("ws-1")
    poller._queue.unregister_session("ws-1")

    await poller._execute(PttOff(), command_id="c1", session_id="ws-1")

    assert radio.calls == ["set_ptt(False)", *_TEARDOWN]


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
