"""Focused contract tests for the shared TX interlock policy."""

import pytest

from rigplane.core.tx_interlock_contract import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA as CORE_TX_INTERLOCK_METADATA,
)
from rigplane.core.tx_interlock_contract import (
    TxInterlockCommandFamily as CoreTxInterlockCommandFamily,
)
from rigplane.core.tx_interlock_contract import (
    TxInterlockCommandFamilyMetadata as CoreTxInterlockCommandFamilyMetadata,
)
from rigplane.core.tx_interlock_contract import (
    TxInterlockDisposition as CoreTxInterlockDisposition,
)
from rigplane.runtime._poller_types import (
    MemoryToVfo,
    PttOff,
    PttOn,
    ScanStart,
    ScanStop,
    SendCiv,
    SetAcc1ModLevel,
    SetAntenna1,
    SetBand,
    SetCwPitch,
    SetData1ModInput,
    SetDualWatch,
    SetFreq,
    SetMode,
    SetPowerstat,
    SetRitFrequency,
    SetRitTxStatus,
    SetSplit,
    SetTunerStatus,
    SelectVfo,
    VfoEqualize,
    VfoSwap,
)
from rigplane.runtime.tx_interlock import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA,
    DeferredTxCommandLane,
    RfState,
    TxInterlockCommandFamily,
    TxInterlockCommandFamilyMetadata,
    TxInterlockDeferredOutcome,
    TxInterlockDisposition,
    classify_tx_interlock,
    evaluate_tx_interlock,
    get_tx_interlock_command_family_metadata,
)


def test_runtime_reexports_the_canonical_core_contract_by_identity() -> None:
    assert TxInterlockDisposition is CoreTxInterlockDisposition
    assert TxInterlockCommandFamily is CoreTxInterlockCommandFamily
    assert TxInterlockCommandFamilyMetadata is CoreTxInterlockCommandFamilyMetadata
    assert TX_INTERLOCK_COMMAND_FAMILY_METADATA is CORE_TX_INTERLOCK_METADATA


def test_emergency_stop_commands_take_structural_precedence() -> None:
    for command in (PttOff(), SetPowerstat(on=False), ScanStop(), SetTunerStatus(0)):
        assert classify_tx_interlock(command) is TxInterlockDisposition.ALWAYS_PASS
        decision = evaluate_tx_interlock(command, rf_state=RfState.UNKNOWN)
        assert decision.allowed is True


def test_rf_start_and_disruptive_commands_are_hard_block() -> None:
    for command in (
        PttOn(),
        SendCiv(command=0x00),
        ScanStart(),
        SetAntenna1(on=True),
        SetTunerStatus(1),
        SetTunerStatus(2),
    ):
        assert classify_tx_interlock(command) is TxInterlockDisposition.BLOCK

    assert (
        classify_tx_interlock(SetPowerstat(on=True)) is TxInterlockDisposition.TX_SAFE
    )


def test_tuner_values_keep_mixed_structural_and_default_semantics() -> None:
    assert (
        classify_tx_interlock(SetTunerStatus(0)) is TxInterlockDisposition.ALWAYS_PASS
    )
    assert classify_tx_interlock(SetTunerStatus(1)) is TxInterlockDisposition.BLOCK
    assert classify_tx_interlock(SetTunerStatus(2)) is TxInterlockDisposition.BLOCK
    assert classify_tx_interlock(SetTunerStatus(3)) is TxInterlockDisposition.TX_SAFE


def test_unknown_commands_remain_tx_safe_without_family_metadata() -> None:
    command = object()

    assert classify_tx_interlock(command) is TxInterlockDisposition.TX_SAFE
    assert evaluate_tx_interlock(command, rf_state=RfState.UNKNOWN).allowed is True
    assert get_tx_interlock_command_family_metadata(command) is None


def test_hard_block_fails_closed_for_unknown_rf_state_without_claiming_tx() -> None:
    decision = evaluate_tx_interlock(PttOn(), rf_state=RfState.UNKNOWN)

    assert decision.allowed is False
    assert "RF state is unknown" in decision.reason
    assert "transmitting" not in decision.reason.lower()


def test_hard_block_is_allowed_only_when_rx_is_known() -> None:
    assert evaluate_tx_interlock(PttOn(), rf_state=RfState.RX).allowed is True
    assert evaluate_tx_interlock(PttOn(), rf_state=RfState.TX).allowed is False


def test_disruptive_operations_defer() -> None:
    for command in (
        SetFreq(7_100_000),
        SetMode("USB"),
        SetBand(4),
        SelectVfo("MAIN"),
        VfoSwap(),
        VfoEqualize(),
        SetSplit(on=True),
        SetDualWatch(on=True),
        MemoryToVfo(channel=1),
        SetRitTxStatus(on=True),
        SetRitFrequency(freq=100),
    ):
        assert classify_tx_interlock(command) is TxInterlockDisposition.DEFER


def test_defer_does_not_loosen_when_rf_state_is_unknown() -> None:
    decision = evaluate_tx_interlock(SetFreq(7_100_000), rf_state=RfState.UNKNOWN)

    assert decision.disposition is TxInterlockDisposition.DEFER
    assert decision.allowed is False
    assert "RF state is unknown" in decision.reason


def test_cw_and_modulation_input_controls_are_tx_safe() -> None:
    for command in (
        SetCwPitch(600),
        SetData1ModInput(source=1),
        SetAcc1ModLevel(level=10),
    ):
        assert classify_tx_interlock(command) is TxInterlockDisposition.TX_SAFE
        assert evaluate_tx_interlock(command, rf_state=RfState.UNKNOWN).allowed is True


def test_validated_override_produces_one_fail_closed_effective_decision() -> None:
    command = SetPowerstat(on=True)
    overrides = {
        TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER,
    }

    base = evaluate_tx_interlock(command, rf_state=RfState.UNKNOWN)
    assert base.disposition is TxInterlockDisposition.TX_SAFE
    assert base.allowed is True

    unknown = evaluate_tx_interlock(
        command, rf_state=RfState.UNKNOWN, disposition_overrides=overrides
    )
    transmitting = evaluate_tx_interlock(
        command, rf_state=RfState.TX, disposition_overrides=overrides
    )
    receiving = evaluate_tx_interlock(
        command, rf_state=RfState.RX, disposition_overrides=overrides
    )
    assert (unknown.disposition, unknown.allowed) == (
        TxInterlockDisposition.DEFER,
        False,
    )
    assert (transmitting.disposition, transmitting.allowed) == (
        TxInterlockDisposition.DEFER,
        False,
    )
    assert (receiving.disposition, receiving.allowed) == (
        TxInterlockDisposition.DEFER,
        True,
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.TX_SAFE},
        {TxInterlockCommandFamily.PTT_ON: TxInterlockDisposition.DEFER},
        {"power-on": TxInterlockDisposition.DEFER},
    ),
)
def test_invalid_or_loosening_override_cannot_yield_pass(overrides: object) -> None:
    with pytest.raises(ValueError, match="override"):
        evaluate_tx_interlock(
            SetPowerstat(on=True),
            rf_state=RfState.UNKNOWN,
            disposition_overrides=overrides,
        )

    emergency = evaluate_tx_interlock(
        PttOff(), rf_state=RfState.UNKNOWN, disposition_overrides=overrides
    )
    assert emergency.disposition is TxInterlockDisposition.ALWAYS_PASS
    assert emergency.allowed is True


def test_command_family_metadata_pins_typed_policy_without_classifying_defaults() -> (
    None
):
    cases = (
        (PttOff(), TxInterlockCommandFamily.PTT_OFF),
        (SetPowerstat(on=False), TxInterlockCommandFamily.POWER_OFF),
        (SetPowerstat(on=True), TxInterlockCommandFamily.POWER_ON),
        (ScanStop(), TxInterlockCommandFamily.SCAN_STOP),
        (SetTunerStatus(0), TxInterlockCommandFamily.TUNER_OFF),
        (PttOn(), TxInterlockCommandFamily.PTT_ON),
        (SendCiv(command=0x00), TxInterlockCommandFamily.RAW_CIV),
        (ScanStart(), TxInterlockCommandFamily.SCAN_START),
        (SetAntenna1(on=True), TxInterlockCommandFamily.ANTENNA_SWITCH),
        (SetTunerStatus(1), TxInterlockCommandFamily.TUNER_ENGAGE),
        (SetTunerStatus(2), TxInterlockCommandFamily.TUNER_ENGAGE),
        (SetFreq(7_100_000), TxInterlockCommandFamily.FREQUENCY),
        (SetMode("USB"), TxInterlockCommandFamily.MODE),
        (SetBand(4), TxInterlockCommandFamily.BAND),
        (SelectVfo("MAIN"), TxInterlockCommandFamily.VFO_SELECT),
        (VfoSwap(), TxInterlockCommandFamily.VFO_TOPOLOGY),
        (SetSplit(on=True), TxInterlockCommandFamily.VFO_TOPOLOGY),
        (MemoryToVfo(channel=1), TxInterlockCommandFamily.MEMORY),
        (SetRitTxStatus(on=True), TxInterlockCommandFamily.RIT_XIT),
    )

    for command, family in cases:
        metadata = get_tx_interlock_command_family_metadata(command)

        assert metadata is not None
        assert metadata.family is family
        assert metadata.base_disposition is classify_tx_interlock(command)

    assert get_tx_interlock_command_family_metadata(SetTunerStatus(3)) is None
    assert get_tx_interlock_command_family_metadata(SetCwPitch(600)) is None


def test_deferred_lane_holds_then_releases_after_continuous_known_rx() -> None:
    lane = DeferredTxCommandLane()
    command = SetFreq(7_100_000)

    held = lane.defer(command, now=10.0)
    assert held.outcome is TxInterlockDeferredOutcome.HELD
    assert held.command is command
    assert held.expires_at == 13.0

    assert (
        lane.observe(rf_state=RfState.RX, now=10.0).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=10.999).outcome
        is TxInterlockDeferredOutcome.HELD
    )

    released = lane.observe(rf_state=RfState.RX, now=11.0)
    assert released.outcome is TxInterlockDeferredOutcome.RELEASED
    assert released.command is command
    assert lane.pending is None


def test_deferred_lane_reuses_effective_decision_without_reclassification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    overrides = {
        TxInterlockCommandFamily.POWER_ON: TxInterlockDisposition.DEFER,
    }
    first = SetPowerstat(on=True)
    second = SetPowerstat(on=True)
    first_decision = evaluate_tx_interlock(
        first, rf_state=RfState.TX, disposition_overrides=overrides
    )
    second_decision = evaluate_tx_interlock(
        second, rf_state=RfState.TX, disposition_overrides=overrides
    )
    receiving_decision = evaluate_tx_interlock(
        second, rf_state=RfState.RX, disposition_overrides=overrides
    )
    unknown_decision = evaluate_tx_interlock(
        second, rf_state=RfState.UNKNOWN, disposition_overrides=overrides
    )
    monkeypatch.setattr(
        "rigplane.runtime.tx_interlock.evaluate_tx_interlock",
        lambda *_args, **_kwargs: pytest.fail("effective decision was recomputed"),
    )

    lane = DeferredTxCommandLane()
    held = lane.defer(first, now=10.0, decision=first_decision)
    superseded = lane.defer(second, now=12.5, decision=second_decision)
    assert held.expires_at == 13.0
    assert superseded.outcome is TxInterlockDeferredOutcome.SUPERSEDED
    assert superseded.expires_at == 13.0
    assert lane.observe(rf_state=RfState.RX, now=13.0).outcome is (
        TxInterlockDeferredOutcome.EXPIRED
    )

    for refused in (receiving_decision, unknown_decision):
        with pytest.raises(ValueError, match="held"):
            lane.defer(SetPowerstat(on=True), now=20.0, decision=refused)


def test_deferred_lane_resets_only_quiet_progress_for_unknown_or_renewed_tx() -> None:
    lane = DeferredTxCommandLane()
    command = SetFreq(7_100_000)
    lane.defer(command, now=10.0)

    lane.observe(rf_state=RfState.RX, now=10.2)
    assert (
        lane.observe(rf_state=RfState.UNKNOWN, now=10.9).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=11.0).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.TX, now=11.8).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=12.0).outcome
        is TxInterlockDeferredOutcome.HELD
    )

    released = lane.observe(rf_state=RfState.RX, now=13.0)
    assert released.outcome is TxInterlockDeferredOutcome.EXPIRED
    assert released.command is command
    assert lane.pending is None


def test_deferred_lane_ttl_never_restarts_on_ptt_qsk_transitions() -> None:
    lane = DeferredTxCommandLane()
    command = SetFreq(7_100_000)
    lane.defer(command, now=0.0)

    lane.observe(rf_state=RfState.TX, now=2.8)
    lane.observe(rf_state=RfState.RX, now=2.9)
    expired = lane.observe(rf_state=RfState.RX, now=3.0)

    assert expired.outcome is TxInterlockDeferredOutcome.EXPIRED
    assert expired.expires_at == 3.0
    assert lane.pending is None
    assert lane.observe(rf_state=RfState.RX, now=4.0) is None


def test_newer_deferred_command_explicitly_supersedes_the_single_slot() -> None:
    lane = DeferredTxCommandLane()
    first = SetFreq(7_100_000)
    second = SetMode("USB")
    lane.defer(first, now=10.0)
    assert (
        lane.observe(rf_state=RfState.RX, now=10.0).outcome
        is TxInterlockDeferredOutcome.HELD
    )

    superseded = lane.defer(second, now=11.1)

    assert superseded.outcome is TxInterlockDeferredOutcome.SUPERSEDED
    assert superseded.command is first
    assert superseded.replacement is second
    assert superseded.expires_at == 13.0
    assert lane.pending is second
    assert (
        lane.observe(rf_state=RfState.RX, now=11.1).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=12.099).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=12.1).outcome
        is TxInterlockDeferredOutcome.RELEASED
    )


def test_superseding_deferred_command_retains_the_original_deadline() -> None:
    lane = DeferredTxCommandLane()
    first = SetFreq(7_100_000)
    second = SetMode("USB")
    lane.defer(first, now=10.0)

    superseded = lane.defer(second, now=12.5)

    assert superseded.outcome is TxInterlockDeferredOutcome.SUPERSEDED
    assert superseded.command is first
    assert superseded.replacement is second
    assert superseded.expires_at == 13.0
    assert lane.pending is second
    assert (
        lane.observe(rf_state=RfState.RX, now=12.5).outcome
        is TxInterlockDeferredOutcome.HELD
    )

    expired = lane.observe(rf_state=RfState.RX, now=13.0)

    assert expired.outcome is TxInterlockDeferredOutcome.EXPIRED
    assert expired.command is second
    assert expired.expires_at == 13.0
    assert lane.pending is None


def test_command_after_expired_entry_starts_a_new_deadline() -> None:
    lane = DeferredTxCommandLane()
    first = SetFreq(7_100_000)
    second = SetMode("USB")
    lane.defer(first, now=10.0)

    expired = lane.defer(second, now=13.0)

    assert expired.outcome is TxInterlockDeferredOutcome.EXPIRED
    assert expired.command is first
    assert expired.replacement is second
    assert expired.expires_at == 13.0
    assert lane.pending is second
    assert (
        lane.observe(rf_state=RfState.TX, now=15.999).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=16.0).outcome
        is TxInterlockDeferredOutcome.EXPIRED
    )


def test_deferred_lane_rejects_non_deferred_commands() -> None:
    lane = DeferredTxCommandLane()

    try:
        lane.defer(PttOn(), now=0.0)
    except ValueError as error:
        assert "DEFER" in str(error)
    else:
        raise AssertionError("only DEFER commands may enter the deferred lane")
