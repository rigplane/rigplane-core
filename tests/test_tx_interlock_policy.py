"""Focused contract tests for the shared TX interlock policy."""

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
    DeferredTxCommandLane,
    RfState,
    TxInterlockDeferredOutcome,
    TxInterlockDisposition,
    classify_tx_interlock,
    evaluate_tx_interlock,
)


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

    superseded = lane.defer(second, now=10.5)

    assert superseded.outcome is TxInterlockDeferredOutcome.SUPERSEDED
    assert superseded.command is first
    assert superseded.replacement is second
    assert superseded.expires_at == 13.0
    assert lane.pending is second
    assert (
        lane.observe(rf_state=RfState.RX, now=10.5).outcome
        is TxInterlockDeferredOutcome.HELD
    )
    assert (
        lane.observe(rf_state=RfState.RX, now=11.5).outcome
        is TxInterlockDeferredOutcome.RELEASED
    )


def test_deferred_lane_rejects_non_deferred_commands() -> None:
    lane = DeferredTxCommandLane()

    try:
        lane.defer(PttOn(), now=0.0)
    except ValueError as error:
        assert "DEFER" in str(error)
    else:
        raise AssertionError("only DEFER commands may enter the deferred lane")
