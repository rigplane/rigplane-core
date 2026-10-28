"""Focused contract tests for the shared TX interlock policy."""

from dataclasses import asdict, replace

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
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, FreshnessState, StateStore
from rigplane.runtime._poller_types import (
    MemoryClear,
    MemoryToVfo,
    PttOff,
    PttOn,
    QuickSplit,
    QuickSplitTrigger,
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
    SetMemoryContents,
    SetVfoFreq,
    SetMainSubTracking,
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
    TxInterlockDecision,
    TxInterlockDisposition,
    classify_raw_civ_frame_tx_family,
    classify_tx_interlock,
    evaluate_tx_interlock,
    get_tx_interlock_command_family_metadata,
    is_exact_raw_dekey_frame,
    is_exact_raw_transmission_end_frame,
    resolve_rf_state,
)


def test_runtime_reexports_the_canonical_core_contract_by_identity() -> None:
    assert TxInterlockDisposition is CoreTxInterlockDisposition
    assert TxInterlockCommandFamily is CoreTxInterlockCommandFamily
    assert TxInterlockCommandFamilyMetadata is CoreTxInterlockCommandFamilyMetadata
    assert TX_INTERLOCK_COMMAND_FAMILY_METADATA is CORE_TX_INTERLOCK_METADATA


def _mem() -> object:
    from rigplane.types import MemoryChannel

    return MemoryChannel(
        channel=1,
        frequency_hz=14_074_000,
        mode=1,
        filter=1,
        scan=0,
        datamode=0,
        tonemode=0,
    )


def test_decision_preserves_legacy_equality_and_serialized_shape() -> None:
    decision = evaluate_tx_interlock(PttOn(), rf_state=RfState.UNKNOWN)
    expected = TxInterlockDecision(
        TxInterlockDisposition.BLOCK,
        False,
        "RF state is unknown; this command must not be attempted yet.",
    )

    assert decision == expected
    assert asdict(decision) == {
        "disposition": TxInterlockDisposition.BLOCK,
        "allowed": False,
        "reason": "RF state is unknown; this command must not be attempted yet.",
    }


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


def test_unapproved_disruptive_operations_still_defer() -> None:
    for command in (
        SetSplit(on=True),
        SetDualWatch(on=True),
        SetMainSubTracking(on=True),
        QuickSplit(),
        QuickSplitTrigger(),
        MemoryToVfo(channel=1),
        MemoryClear(channel=1),
        SetMemoryContents(mem=_mem()),
    ):
        assert classify_tx_interlock(command) is TxInterlockDisposition.DEFER


def test_memory_write_and_clear_defer_only_while_rf_is_tx() -> None:
    """MOR-3644: a keyed radio holds memory writes/clears; known RX passes."""
    for command in (MemoryClear(channel=1), SetMemoryContents(mem=_mem())):
        held = evaluate_tx_interlock(command, rf_state=RfState.TX)
        assert held.disposition is TxInterlockDisposition.DEFER
        assert held.allowed is False
        passed = evaluate_tx_interlock(command, rf_state=RfState.RX)
        assert passed.disposition is TxInterlockDisposition.DEFER
        assert passed.allowed is True


def test_authority_approved_control_families_are_tx_safe_in_every_rf_state() -> None:
    for command in (
        SetFreq(7_100_000),
        SetMode("USB"),
        SetBand(4),
        SelectVfo("MAIN"),
        VfoSwap(),
        VfoEqualize(),
        SetRitTxStatus(on=True),
        SetRitFrequency(freq=100),
    ):
        assert classify_tx_interlock(command) is TxInterlockDisposition.TX_SAFE
        for rf_state in (RfState.RX, RfState.TX, RfState.UNKNOWN):
            decision = evaluate_tx_interlock(command, rf_state=rf_state)
            assert decision.allowed is True
            assert decision.disposition is TxInterlockDisposition.TX_SAFE


def test_defer_does_not_loosen_when_rf_state_is_unknown() -> None:
    decision = evaluate_tx_interlock(SetSplit(on=True), rf_state=RfState.UNKNOWN)

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
        (SetVfoFreq(7_100_000, 0, "B", "A", 1), TxInterlockCommandFamily.FREQUENCY),
        (SetMode("USB"), TxInterlockCommandFamily.MODE),
        (SetBand(4), TxInterlockCommandFamily.BAND),
        (SelectVfo("MAIN"), TxInterlockCommandFamily.VFO_SELECT),
        (VfoSwap(), TxInterlockCommandFamily.VFO_CONTENTS),
        (VfoEqualize(), TxInterlockCommandFamily.VFO_CONTENTS),
        (SetSplit(on=True), TxInterlockCommandFamily.VFO_TOPOLOGY),
        (MemoryToVfo(channel=1), TxInterlockCommandFamily.MEMORY),
        (MemoryClear(channel=1), TxInterlockCommandFamily.MEMORY),
        (SetMemoryContents(mem=_mem()), TxInterlockCommandFamily.MEMORY),
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
    command = SetSplit(on=True)

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


def test_deferred_lane_applies_effective_decision_to_the_bound_command() -> None:
    first = SetSplit(on=True)
    second = SetSplit(on=True)

    lane = DeferredTxCommandLane()
    for refused in (RfState.UNKNOWN, RfState.RX):
        with pytest.raises(ValueError, match="held"):
            lane.defer(first, now=10.0, rf_state=refused)

    held = lane.defer(first, now=10.0, rf_state=RfState.TX)
    superseded = lane.defer(second, now=12.5, rf_state=RfState.TX)
    assert held.expires_at == 13.0
    assert superseded.outcome is TxInterlockDeferredOutcome.SUPERSEDED
    assert superseded.expires_at == 13.0
    assert lane.observe(rf_state=RfState.RX, now=13.0).outcome is (
        TxInterlockDeferredOutcome.EXPIRED
    )


@pytest.mark.parametrize(
    "command",
    (PttOn(), PttOff(), SetPowerstat(on=False)),
)
def test_deferred_lane_rejects_unbound_forged_decision(command: object) -> None:
    forged = TxInterlockDecision(
        TxInterlockDisposition.DEFER,
        False,
        "forged denied-DEFER/TX claim",
    )

    with pytest.raises(TypeError, match="decision"):
        DeferredTxCommandLane().defer(command, now=10.0, decision=forged)


def test_deferred_lane_resets_only_quiet_progress_for_unknown_or_renewed_tx() -> None:
    lane = DeferredTxCommandLane()
    command = SetSplit(on=True)
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
    command = SetSplit(on=True)
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
    first = SetSplit(on=True)
    second = SetSplit(on=False)
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
    first = SetSplit(on=True)
    second = SetSplit(on=False)
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
    first = SetSplit(on=True)
    second = SetSplit(on=False)
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


def _ptt_store(
    *, value: object = False, max_age: float | None = 1.0
) -> tuple[StateStore, FreshnessClock]:
    """Store holding one tx_state.ptt observation at t0=10.0 (manual clock)."""

    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=value,
            source=SourceMetadata(
                source="poll_response",
                provider="test",
                transport="fake",
                native_id="test",
            ),
            timestamp_monotonic=clock.now(),
            max_age=max_age,
        )
    )
    return store, clock


def test_resolve_rf_state_reads_known_rx_inside_ttl() -> None:
    store, _clock = _ptt_store(value=False, max_age=1.0)

    assert resolve_rf_state(store.snapshot()) is RfState.RX


def test_resolve_rf_state_reads_known_tx_inside_ttl() -> None:
    store, _clock = _ptt_store(value=True, max_age=1.0)

    assert resolve_rf_state(store.snapshot()) is RfState.TX


def test_resolve_rf_state_recomputes_age_past_ttl_despite_fresh_flag() -> None:
    store, clock = _ptt_store(value=False, max_age=1.0)
    clock.advance(1.03)
    snapshot = store.snapshot()
    assert (
        snapshot.field(FieldPath.global_("tx_state", "ptt")).freshness
        is FreshnessState.FRESH
    )

    assert resolve_rf_state(snapshot) is RfState.UNKNOWN


def test_resolve_rf_state_treats_ttl_boundary_as_expired() -> None:
    store, clock = _ptt_store(value=False, max_age=1.0)
    clock.advance(1.0)

    assert resolve_rf_state(store.snapshot()) is RfState.UNKNOWN


def test_resolve_rf_state_without_ptt_field_is_unknown() -> None:
    store = StateStore(freshness_clock=FreshnessClock(start=10.0))

    assert resolve_rf_state(store.snapshot()) is RfState.UNKNOWN


def test_resolve_rf_state_without_max_age_is_unknown() -> None:
    store, _clock = _ptt_store(value=False, max_age=None)

    assert resolve_rf_state(store.snapshot()) is RfState.UNKNOWN


def test_resolve_rf_state_with_non_bool_value_is_unknown() -> None:
    store, _clock = _ptt_store(value=1, max_age=1.0)

    assert resolve_rf_state(store.snapshot()) is RfState.UNKNOWN


def test_resolve_rf_state_with_negative_snapshot_generation_is_unknown() -> None:
    store, _clock = _ptt_store(value=False, max_age=1.0)
    snapshot = store.snapshot()
    path = FieldPath.global_("tx_state", "ptt")
    field = replace(snapshot.field(path), provider_generation=-1)
    negative = replace(snapshot, fields=(field,), provider_generation=-1)

    assert resolve_rf_state(negative) is RfState.UNKNOWN


@pytest.mark.parametrize(
    ("frame_hex", "expected"),
    (
        ("FEFE98E01CFD", None),
        ("FEFE98E0290029001C0001FD", True),
        ("FEFE98E029001C00FD", False),
    ),
    ids=(
        "short-1c-frame-unparseable",
        "doubly-wrapped-cmd29-fails-closed",
        "cmd29-ptt-read-passes",
    ),
)
def test_raw_frame_classifier_pins_fail_closed_branches(
    frame_hex: str, expected: bool | None
) -> None:
    """Pin the byte-level TX-family verdict on edge frames.

    A 0x1C frame with no sub byte cannot be parsed, a cmd29 wrapper
    around another cmd29 wrapper cannot be unwrapped to a known inner
    command, and a cmd29 wrapper around a PTT read is a read: the first
    two fail closed (refused in managed mode), the read passes.
    """
    assert classify_raw_civ_frame_tx_family(bytes.fromhex(frame_hex)) is expected


@pytest.mark.parametrize(
    ("frame_hex", "expected"),
    (
        ("FEFE98E0280001FD", True),
        ("FEFE98E0280008FD", True),
        ("FEFE98E02900280001FD", True),
        ("FEFE98E0280000FD", True),
        ("FEFE98E02800FD", True),
        ("FEFE98E028000000FD", True),
        ("FEFE98E0280101FD", False),
        ("FEFE98E01741FD", False),
    ),
    ids=(
        "voice-memory-tx-01",
        "voice-memory-tx-08",
        "cmd29-voice-memory-tx",
        "voice-memory-stop-is-family",
        "voice-memory-no-data-stays-in-family",
        "voice-stop-extra-byte-stays-in-family",
        "other-28-sub-not-family",
        "cw-text-not-family",
    ),
)
def test_raw_frame_classifier_voice_tx_memory_rows(
    frame_hex: str, expected: bool
) -> None:
    """MOR-3374: the voice TX memory command 0x28 sub 0x00 is TX-family.

    A non-zero data byte starts RF with no operator key-down, so it is a
    TX-start like PTT-on; its stop form ``28 00 00`` stays in the family
    (managed mode refuses it like every de-key), and the no-data and
    multi-data forms stay in the family too — they must not be classified
    as de-keys.  A different 0x28 sub and raw CW text ``0x17`` are not
    family: the typed ``send_cw_text`` path is not interlocked, so raw
    0x17 keeps passing as before.
    """
    assert classify_raw_civ_frame_tx_family(bytes.fromhex(frame_hex)) is expected


@pytest.mark.parametrize(
    ("frame_hex", "expected"),
    (
        ("FEFE98E0280000FD", True),
        ("FEFE98E02900280000FD", True),
        ("FEFE98E0280001FD", False),
        ("FEFE98E028000000FD", False),
        ("FEFE98E02800FD", False),
        ("FEFE98E0280101FD", False),
    ),
    ids=(
        "voice-memory-stop",
        "cmd29-voice-memory-stop",
        "voice-memory-tx-not-dekey",
        "voice-stop-extra-byte-not-dekey",
        "voice-memory-no-data-not-dekey",
        "other-28-sub-not-dekey",
    ),
)
def test_exact_raw_dekey_voice_tx_memory_rows(frame_hex: str, expected: bool) -> None:
    """MOR-3374: exactly ``28 00 00`` (bare or cmd29-wrapped) is a de-key.

    The stop form has one data byte equal to zero; a transmit payload, a
    second data byte, no data byte, or another 0x28 sub is not a de-key.
    """
    assert is_exact_raw_dekey_frame(bytes.fromhex(frame_hex)) is expected


@pytest.mark.parametrize(
    ("frame_hex", "expected"),
    (
        ("FEFE98E01C0000FD", True),
        ("FEFE98E029001C0000FD", True),
        ("FEFE98E0280000FD", True),
        ("FEFE98E02900280000FD", True),
        ("FEFE98E01C0100FD", False),
        ("FEFE98E029001C0100FD", False),
        ("FEFE98E01C0001FD", False),
        ("FEFE98E01C000000FD", False),
        ("FEFE98E0280001FD", False),
        ("FEFE98E01CFD", False),
    ),
    ids=(
        "ptt-off",
        "cmd29-ptt-off",
        "voice-memory-stop",
        "cmd29-voice-memory-stop",
        "tuner-off-not-transmission-end",
        "cmd29-tuner-off-not-transmission-end",
        "ptt-on-not-transmission-end",
        "ptt-off-extra-byte",
        "voice-stop-tx-payload",
        "short-1c-frame-unparseable",
    ),
)
def test_exact_raw_transmission_end_rows(frame_hex: str, expected: bool) -> None:
    """MOR-3380: the frames that end a transmission, exactly.

    PTT off (``1C 00 00``) and the voice TX memory stop (``28 00 00``),
    bare or cmd29-wrapped, end a transmission; the tuner-off actuation
    (``1C 01 00``), any other payload, and an unparseable envelope do
    not.
    """
    assert is_exact_raw_transmission_end_frame(bytes.fromhex(frame_hex)) is expected
