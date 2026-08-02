"""Managed-TX routing for the ``validate`` hardware harness (MOR-1222).

``validation/hardware.py`` is the last TX-causing surface that keyed the rig
with a raw ``Radio.set_ptt`` write: no lease, so no ``BACKEND_MAX_KEY_DOWN``
covering the key, and a raw OFF able to de-key another owner's *managed*
transmission behind the supervisor's back (the MOR-1179 class of defect).

These tests pin the three properties that fix has to hold:

* a managed rig is keyed and unkeyed **only** through the supervisor, under a
  stable validation owner;
* an unmanaged rig keeps the legacy ordering verbatim;
* a refused key fails the check and **never** retries through the raw write —
  the supervisor refuses exactly when somebody else is on the air, which is the
  one moment a raw key is worst.

Every assertion is on the ordered call log shared by the radio and the
supervisor, so a bypass shows up as a call in the wrong place rather than
merely a missing one. No hardware is touched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.core.tx_safety import (
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.interactive import InteractivePrompter
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)

_IDLE_TX_SNAPSHOT = TxSafetySupervisor().snapshot
_FULL_SAFETY = OperatorSafetyBlock(tx_allowed=True, tuner_allowed=True)
_START_POWER = 200


class _RecordingSupervisor:
    """A ``ManagedTxSupervisor`` that logs, answers, and mirrors PTT state.

    Hand written rather than a ``MagicMock``: an ``AsyncMock`` satisfies a
    ``runtime_checkable`` Protocol on 3.11 but not on 3.12+ (gh-102433), so a
    mocked supervisor would make these tests pass for the wrong reason on one
    interpreter and fail on the other.

    ``request_on``/``release_owner`` mirror into ``RadioState.ptt`` because the
    real supervisor owns the provider write: the harness reads that mirror back
    to verify the rig actually keyed, and a fake that only records would make a
    correctly routed key look like a failed one.
    """

    def __init__(
        self,
        log: list[tuple[str, object]],
        state: RadioState,
        *,
        on: TxOutcome = TxOutcome.ACCEPTED,
        off: TxOutcome = TxOutcome.ACCEPTED,
    ) -> None:
        self._log = log
        self._state = state
        self._on = on
        self._off = off
        self.owners: list[TxOwner] = []
        self.reasons: list[TxReleaseReason] = []

    async def request_on(self, owner: TxOwner) -> TxTransition:
        self.owners.append(owner)
        self._log.append(("supervisor.request_on", owner))
        if self._on in (TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT):
            self._state.ptt = True
        return TxTransition(self._on, _IDLE_TX_SNAPSHOT)

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        self.owners.append(owner)
        self.reasons.append(reason)
        self._log.append(("supervisor.release_owner", owner))
        if self._off in (TxOutcome.ACCEPTED, TxOutcome.IDEMPOTENT):
            self._state.ptt = False
        return TxTransition(self._off, _IDLE_TX_SNAPSHOT)


def _tx_radio(
    *,
    managed: bool,
    on: TxOutcome = TxOutcome.ACCEPTED,
    off: TxOutcome = TxOutcome.ACCEPTED,
):
    """Stateful fake rig plus the ordered log of everything it was asked to do.

    ``managed_tx`` is assigned as a real instance attribute, which is what
    ``getattr_static`` (used by ``ManagedTxApi.bind``) requires: a value
    conjured by ``__getattr__`` reads as absent there, and an unmanaged rig
    must have no such member at all rather than a mock child.
    """
    log: list[tuple[str, object]] = []
    state = RadioState()
    power = {"value": _START_POWER}

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"tx", "tuner"}
    radio.radio_state = state

    async def _set_ptt(value: bool) -> None:
        log.append(("radio.set_ptt", bool(value)))
        state.ptt = bool(value)

    async def _get_rf_power() -> int:
        log.append(("radio.get_rf_power", power["value"]))
        return power["value"]

    async def _set_rf_power(level: int) -> None:
        log.append(("radio.set_rf_power", int(level)))
        power["value"] = int(level)

    async def _set_tuner_status(value: int) -> None:
        log.append(("radio.set_tuner_status", int(value)))
        state.tuner_status = int(value)

    async def _get_tuner_status() -> int:
        log.append(("radio.get_tuner_status", state.tuner_status))
        return state.tuner_status

    radio.set_ptt = AsyncMock(side_effect=_set_ptt)
    radio.get_rf_power = AsyncMock(side_effect=_get_rf_power)
    radio.set_rf_power = AsyncMock(side_effect=_set_rf_power)
    radio.set_tuner_status = AsyncMock(side_effect=_set_tuner_status)
    radio.get_tuner_status = AsyncMock(side_effect=_get_tuner_status)

    supervisor: _RecordingSupervisor | None = None
    if managed:
        supervisor = _RecordingSupervisor(log, state, on=on, off=off)
        radio.managed_tx = supervisor
    return radio, supervisor, log, power


def _tx_template() -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="tx.ptt",
                capability="tx",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="ptt",
                tx_adjacent=True,
            ),
            CapabilityDeclarationEntry(
                check_id="tuner.tune",
                capability="tuner",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="tuner tune",
                tx_adjacent=True,
            ),
        ],
    )


def _confirm_yes() -> InteractivePrompter:
    return InteractivePrompter(input_fn=lambda _p: "YES", output_fn=lambda _m: None)


async def _run(radio):
    levels = await execute_hardware_checks(
        radio,
        _tx_template(),
        _FULL_SAFETY,
        allow_writes=True,
        tx_actuate=True,
        prompter=_confirm_yes(),
    )
    return {check.check_id: check for level in levels for check in level.checks}


def _names(log: list[tuple[str, object]]) -> list[str]:
    return [name for name, _payload in log]


# ---------------------------------------------------------------------------
# 1. Managed rig — every emission goes through the supervisor.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_managed_rig_keys_and_unkeys_through_the_supervisor():
    radio, supervisor, log, power = _tx_radio(managed=True)

    checks = await _run(radio)

    ptt = checks["tx.ptt"]
    assert ptt.status is CheckStatus.PASS
    assert ptt.evidence["managed_tx"] is True
    assert ptt.evidence["keyed"] is True
    assert ptt.evidence["unkeyed"] is True

    # The one property the whole ticket exists for: not a single raw provider
    # write. The supervisor's own effect path is the only thing entitled to it.
    assert radio.set_ptt.await_count == 0

    # Ordered, not merely present: the lease is taken AFTER power is at minimum
    # and handed back BEFORE power is restored.
    assert _names(log) == [
        "radio.get_rf_power",
        "radio.set_rf_power",  # -> minimum
        "supervisor.request_on",
        "supervisor.release_owner",
        "radio.set_rf_power",  # -> restored
        "radio.set_tuner_status",
        "radio.get_tuner_status",
    ]
    assert power["value"] == _START_POWER
    assert radio.radio_state.ptt is False


@pytest.mark.asyncio
async def test_every_lease_is_taken_under_one_stable_validation_owner():
    """One identity for the whole run, or a release cannot match its own key."""
    radio, supervisor, _log, _power = _tx_radio(managed=True)

    await _run(radio)

    assert supervisor is not None
    assert len(supervisor.owners) == 2  # key + release, for the one PTT check
    assert len(set(supervisor.owners)) == 1
    owner = supervisor.owners[0]
    # `TxSource` gains no validation member: the harness is an in-process
    # consumer of the public SDK surface, exactly as the CLI is (MOR-1170).
    assert owner.source is TxSource.SDK
    assert owner.session_id.startswith("validation:")
    assert supervisor.reasons == [TxReleaseReason.OPERATOR_RELEASE]


@pytest.mark.asyncio
async def test_the_tuner_tune_cycle_takes_no_lease_and_asserts_no_ptt():
    """The documented carve-out, pinned so it cannot drift back either way.

    ``tuner.tune`` is the one TX-causing actuator MOR-1222 deliberately leaves
    on the legacy path: the rig's firmware keys, sweeps and unkeys itself, so
    the emission is an EXTERNAL-class event rather than an ingress assertion.
    Taking a lease around it would write PTT ON at the *operator's* power into
    a by-definition unmatched load — a key legacy never asserted, and one
    outside the minimum-power-first mitigation this module is credited for.
    """
    radio, supervisor, log, _power = _tx_radio(managed=True)

    checks = await _run(radio)

    tune = checks["tuner.tune"]
    assert tune.status is CheckStatus.PASS
    assert tune.evidence["tune_triggered"] is True
    # No lease is claimed for the tune cycle, and it carries no managed-TX
    # evidence key at all — it is not routed, by design.
    assert "managed_tx" not in tune.evidence

    # Ordered log, from the tune command onwards: the cycle is triggered and
    # read back with no supervisor entry after the PTT check released its lease.
    names = _names(log)
    tail = names[names.index("radio.set_tuner_status") :]
    assert tail == ["radio.set_tuner_status", "radio.get_tuner_status"]
    assert "supervisor.request_on" not in tail
    # Exactly one lease in the whole run, and it belongs to `tx.ptt`.
    assert names.count("supervisor.request_on") == 1
    assert radio.set_ptt.await_count == 0
    assert radio.radio_state.ptt is False


# ---------------------------------------------------------------------------
# 2. Unmanaged rig — the legacy path, verbatim.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmanaged_rig_keeps_the_legacy_direct_write_verbatim():
    radio, supervisor, log, power = _tx_radio(managed=False)

    checks = await _run(radio)

    assert supervisor is None
    assert checks["tx.ptt"].status is CheckStatus.PASS
    assert checks["tx.ptt"].evidence["managed_tx"] is False
    assert checks["tuner.tune"].status is CheckStatus.PASS
    assert "managed_tx" not in checks["tuner.tune"].evidence  # carve-out

    # Same ops, same order, same values as before MOR-1222 — an unmanaged
    # backend must not notice that the managed path exists.
    assert log == [
        ("radio.get_rf_power", _START_POWER),
        ("radio.set_rf_power", 0),
        ("radio.set_ptt", True),
        ("radio.set_ptt", False),
        ("radio.set_rf_power", _START_POWER),
        ("radio.set_tuner_status", 2),
        ("radio.get_tuner_status", 2),
    ]
    assert power["value"] == _START_POWER
    assert radio.radio_state.ptt is False


# ---------------------------------------------------------------------------
# 3. Refused key — fail closed, never bypass an active supervisor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "refusal",
    [
        TxOutcome.BUSY,  # another owner holds the rig
        TxOutcome.NOT_READY,
        TxOutcome.RADIO_NOT_OFF,
        TxOutcome.RELEASE_PENDING,
    ],
)
@pytest.mark.asyncio
async def test_a_refused_key_fails_the_check_and_never_falls_back_to_a_raw_key(
    refusal,
):
    radio, supervisor, log, _power = _tx_radio(managed=True, on=refusal)

    checks = await _run(radio)

    ptt = checks["tx.ptt"]
    assert ptt.status is CheckStatus.FAIL
    assert refusal.value in str(ptt.error)
    assert refusal.value in str(ptt.evidence["key_refused"])
    assert ptt.evidence["keyed"] is False

    # Fail closed. No raw key anywhere, and no raw OFF either: writing OFF here
    # would de-key whatever the supervisor was protecting (MOR-1179).
    assert radio.set_ptt.await_count == 0
    # Exactly one key attempt and one release — no retry, no second identity.
    assert [name for name in _names(log) if name.startswith("supervisor.")] == [
        "supervisor.request_on",
        "supervisor.release_owner",
    ]


@pytest.mark.asyncio
async def test_the_release_is_attempted_even_after_the_key_was_refused():
    """Never gated. An unkey withheld because "nothing was keyed" strands rigs."""
    radio, supervisor, log, _power = _tx_radio(managed=True, on=TxOutcome.BUSY)

    await _run(radio)

    assert supervisor is not None
    assert _names(log).count("supervisor.release_owner") == 1


@pytest.mark.asyncio
async def test_a_refused_release_is_recorded_and_never_escalated_to_force():
    """``force_unkey`` is for keys this process did not take (MOR-1182).

    Reaching for it here would let the validation harness adopt — and de-key —
    a live transmission belonging to somebody else. ``_RecordingSupervisor``
    has no ``force_unkey`` at all, so any escalation is an ``AttributeError``.
    """
    radio, _supervisor, _log, _power = _tx_radio(managed=True, off=TxOutcome.STALE)

    checks = await _run(radio)

    ptt = checks["tx.ptt"]
    assert ptt.evidence["unkeyed"] is False
    assert TxOutcome.STALE.value in str(ptt.evidence["unkey_error"])
    assert ptt.status is CheckStatus.FAIL
    assert radio.set_ptt.await_count == 0
