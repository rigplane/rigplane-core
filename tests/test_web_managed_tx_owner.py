"""MOR-1013 slice 4: managed owner identity on the Web TX path.

The poller keys on behalf of the control session (D1-a), so the owner must be
the STABLE session id on the queue entry — not the throwaway
``ControlHandler._session_id`` the shared command executor mints per request:
``release_owner`` matches on the owner alone, so a mismatch strands the rig
keyed. A real ``TxSafetySupervisor`` drives these tests because a scripted
fake would answer ACCEPTED to any owner and could never show that.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from rigplane.core.capabilities import CAP_AUDIO
from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import (
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


def _poller(supervisor: _Supervisor | None) -> tuple[RadioPoller, _Radio]:
    radio = _Radio(supervisor)
    return RadioPoller(radio, CommandQueue()), radio  # type: ignore[arg-type]


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


async def test_a_refused_release_is_tolerated_and_still_tears_down() -> None:
    """STALE means nothing of ours is keyed; raising would break ``finally``."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOff(), command_id="c1", session_id="ws-1")

    assert supervisor.outcomes == [TxOutcome.STALE]
    assert radio.calls == _TEARDOWN


async def test_http_ingress_never_binds_an_owner() -> None:
    """HTTP has no session and no teardown hook, so a lease taken there could
    never be released — and its params are caller-supplied, not a session."""
    supervisor = _Supervisor()
    poller, radio = _poller(supervisor)

    await poller._execute(PttOn(), source="http", session_id=None)
    await poller._execute(PttOn(), source="http", session_id="forged")
    await poller._execute(PttOff(), source="http", session_id=None)

    assert supervisor.entries == []
    assert radio.calls == [*_KEY, *_KEY, "set_ptt(False)", *_TEARDOWN]
