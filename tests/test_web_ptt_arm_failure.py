"""MOR-1178: a failed TX-audio arm refuses the key.

The ``PttOn`` arm logged the arm failure and fell through to the write. The rig
keyed with its modulation path dead — an unmodulated carrier, which on a remote
rig is airtime nobody can see and nobody at the operator's end can hear is
missing. Every other refusal in the same match arm already disarms the
half-armed leg and raises (*"a refused key leaves no trace on the air"*); this
was the one hole left in it.

Fail-closed costs one denied transmission, reported through the poller's own
failure channel (a raise from ``_execute`` is caught by ``_run``'s drain and
routed to ``_mark_queued_command_failed``), and it must cost that on BOTH
paths: the refusal happens before any lease attempt, so a managed rig never
hears about the key at all.

Helpers come from ``test_web_managed_tx_owner`` — the real ``TxSafetySupervisor``
wrapper and the duck-typed, ordered-call-list ``_Radio`` (deliberately not a
``MagicMock``, which satisfies a ``runtime_checkable`` protocol on 3.11 but not
on 3.12+, gh-102433). The TX leg here is ``_Radio``'s ``start_tx``/``stop_tx``,
not the audio subsystem, so no audio backend is involved.
"""

from __future__ import annotations

import pytest
from test_web_managed_tx_owner import (
    _KEY,
    _TEARDOWN,
    _WS1,
    _Radio,
    _Supervisor,
    _poller,
)

from rigplane.core.exceptions import CommandError
from rigplane.core.tx_safety import TxOutcome
from rigplane.web.radio_poller import CommandQueue, PttOn, RadioPoller

_ARM_FAILED = "TX audio failed to arm"


class _ArmFailureRadio(_Radio):
    """A rig whose TX-audio arm raises; every other surface behaves."""

    async def start_tx(self) -> None:
        self.calls.append("start_tx(RAISES)")
        raise RuntimeError("TX audio device unavailable")


def _arm_failure_poller(
    supervisor: _Supervisor | None, *, audio: bool = True
) -> tuple[RadioPoller, _ArmFailureRadio]:
    """``_poller`` for a radio that cannot arm, with the caps read at build time.

    ``RadioPoller`` snapshots ``radio.capabilities`` in ``__init__``, so dropping
    ``CAP_AUDIO`` has to happen before the poller is constructed.
    """
    radio = _ArmFailureRadio(supervisor)
    if not audio:
        radio.capabilities = set()
    return RadioPoller(radio, CommandQueue()), radio  # type: ignore[arg-type]


async def test_a_managed_key_is_refused_when_the_tx_audio_leg_fails_to_arm() -> None:
    """No lease is even attempted: the refusal is upstream of the supervisor."""
    supervisor = _Supervisor()
    poller, radio = _arm_failure_poller(supervisor)

    with pytest.raises(CommandError, match=_ARM_FAILED) as excinfo:
        await poller._execute(PttOn(), command_id="c1", session_id="ws-1")

    # The supervisor never hears about the key, so nothing downstream of it —
    # lease, watchdog, effect service — can act on a transmission that was
    # refused. Not "granted then withdrawn": never requested.
    assert supervisor.entries == []
    assert supervisor.outcomes == []
    assert "set_ptt(True)" not in radio.calls
    # The half-armed leg is disarmed on the way out, as the ownerless-ingress
    # and rejected-transition refusals below it already do.
    assert radio.calls == ["start_tx(RAISES)", *_TEARDOWN]
    # The arm's own failure is chained, not replaced: the operator is told what
    # actually went wrong with the audio path.
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "TX audio device unavailable" in str(excinfo.value)


async def test_an_unmanaged_key_is_refused_when_the_tx_audio_leg_fails_to_arm() -> None:
    """The kill switch turns off the lease, not the fail-closed arm.

    With ``RIGPLANE_MANAGED_TX`` off (or a backend publishing no supervisor) the
    key is a raw ``set_ptt(True)`` with nothing above it to notice a dead
    modulation path — which makes the refusal matter more here, not less.
    """
    poller, radio = _arm_failure_poller(None)

    with pytest.raises(CommandError, match=_ARM_FAILED):
        await poller._execute(PttOn(), command_id="c1", session_id="ws-1")

    assert "set_ptt(True)" not in radio.calls
    assert radio.calls == ["start_tx(RAISES)", *_TEARDOWN]


async def test_a_radio_with_no_audio_capability_still_keys() -> None:
    """Non-regression: the arm block is gated on ``CAP_AUDIO`` and stays gated.

    A backend that declares no audio has no TX leg for this poller to arm, so
    there is no dead modulation path to refuse over — and refusing here would
    deny the key to a rig that was never going to carry web audio. The gate is
    retained exactly as-is (MOR-1178's recorded decision); ``start_tx`` is never
    reached, which is why this radio keys despite being built to raise from it.
    """
    poller, radio = _arm_failure_poller(None, audio=False)
    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    assert radio.calls == ["set_ptt(True)"]

    supervisor = _Supervisor()
    managed_poller, managed_radio = _arm_failure_poller(supervisor, audio=False)
    await managed_poller._execute(PttOn(), command_id="c2", session_id="ws-1")
    assert supervisor.outcomes == [TxOutcome.ACCEPTED]
    assert managed_radio.calls == []  # the supervisor's effect path owns it


async def test_a_successful_arm_keeps_todays_exact_ordering() -> None:
    """Non-regression: nothing changes for the arm that works.

    ``start_tx`` strictly before the key on the legacy path, and on the managed
    path a lease request that the supervisor accepts, with the provider write
    left to its effect service rather than made here.
    """
    poller, radio = _poller(None)
    await poller._execute(PttOn(), command_id="c1", session_id="ws-1")
    assert radio.calls == _KEY

    supervisor = _Supervisor()
    managed_poller, managed_radio = _poller(supervisor)
    await managed_poller._execute(PttOn(), command_id="c2", session_id="ws-1")
    assert supervisor.entries == [(True, _WS1)]
    assert supervisor.outcomes == [TxOutcome.ACCEPTED]
    assert managed_radio.calls == ["start_tx"]
