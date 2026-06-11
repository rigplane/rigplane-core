"""Tests for the hardware-execution path of ``rigplane validate``.

These never touch a real serial device — they exercise the in-process mock
UDP radio (``connected_radio`` fixture) for the happy path and a
``MagicMock(spec=Radio)`` with ``AsyncMock`` coroutines plus a real
``RadioState`` for the edge cases.

The IC-7610 mock round-trips set->get ONLY for freq, mode, attenuator, and
preamp; rf_gain/af_level/nb/nr/notch/agc/rit/filter_width are NAKed (mapped to
``CommandError`` -> FAIL/COMMAND_EXECUTION), so PASS for those controls is only
asserted against a stateful ``MagicMock``.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from rigplane.core.exceptions import TimeoutError as RigTimeoutError
from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.core.types import AgcMode
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.registry import CheckKind, CheckSpec, ValueRule
from rigplane.validation.runner import build_validation_artifact, load_template
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    FailureDomain,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    TransportInfo,
    ValidationArtifact,
    ValidationLevel,
    validate_artifact_dict,
)

_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "validation"
    / "templates"
    / "x6200.json"
)

_ISO8601_MS_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _x6200_template() -> MatrixTemplate:
    return load_template(_TEMPLATE_PATH)


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _single_entry_template(
    *,
    check_id: str,
    capability: str,
    level: ValidationLevel = ValidationLevel.CAPABILITY_MATRIX,
    declaration: CapabilityDeclaration = CapabilityDeclaration.SUPPORTED,
    summary: str = "single",
) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        entries=[
            CapabilityDeclarationEntry(
                check_id=check_id,
                capability=capability,
                level=level,
                declaration=declaration,
                summary=summary,
            )
        ],
    )


def _make_mock_radio(*, freq: int = 14_074_000, state_freq: int = 14_074_000):
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"audio", "scope", "tuner", "tx"}
    radio.get_freq = AsyncMock(return_value=freq)
    radio.set_freq = AsyncMock(return_value=None)
    radio.get_mode = AsyncMock(return_value=("USB", 1))
    radio.set_mode = AsyncMock(return_value=None)
    radio.set_ptt = AsyncMock(return_value=None)
    state = RadioState()
    state.main.freq = state_freq
    radio.radio_state = state
    return radio


def _stateful_preamp_mock(*, start: int = 0):
    """A MagicMock(spec=Radio) whose preamp set/get round-trips via a closure."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"preamp"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(level: int, receiver: int = 0) -> None:
        store["value"] = level

    radio.get_preamp = AsyncMock(side_effect=_get)
    radio.set_preamp = AsyncMock(side_effect=_set)
    return radio, store


def _digisel_preamp_mock(*, preamp_start: int = 0, digisel_on: bool = True):
    """A stateful preamp mock that ALSO exposes get/set_digisel.

    ``get_digisel`` is not part of the Radio protocol, so a ``spec=Radio`` mock
    would not auto-provide it; attach it explicitly so the preamp handler's
    DIGI-SEL prerequisite path (MOR-665) can be exercised. Returns the radio
    plus the preamp and digisel state stores.
    """
    radio, preamp_store = _stateful_preamp_mock(start=preamp_start)
    radio.capabilities = {"preamp", "digisel"}
    digisel_store = {"on": digisel_on}

    async def _get_digisel(receiver: int = 0) -> bool:
        return digisel_store["on"]

    async def _set_digisel(on: bool, receiver: int = 0) -> None:
        digisel_store["on"] = on

    radio.get_digisel = AsyncMock(side_effect=_get_digisel)
    radio.set_digisel = AsyncMock(side_effect=_set_digisel)
    return radio, preamp_store, digisel_store


async def test_default_run_produces_artifact_with_expected_statuses(connected_radio):
    template = _x6200_template()
    safety = OperatorSafetyBlock()
    levels = await execute_hardware_checks(
        connected_radio, template, safety, allow_writes=True
    )
    checks = _flatten(levels)

    assert checks["discovery.identify"].status is CheckStatus.PASS
    assert checks["freq.write"].status is CheckStatus.PASS
    assert checks["freq.write"].evidence["restored"] is True
    assert checks["mode.set"].status is CheckStatus.PASS
    assert checks["preamp.set"].status is CheckStatus.PASS
    assert checks["attenuator.set"].status is CheckStatus.PASS
    assert checks["freq.reverse_sync"].status is CheckStatus.PASS
    assert checks["audio.rx"].status is CheckStatus.MANUAL_REQUIRED
    # MOR-660: scope.capture is declared unsupported_pending_evidence, but the
    # mock declares the ``scope`` capability — presence confirms support → PASS.
    assert checks["scope.capture"].status is CheckStatus.PASS
    assert checks["scope.capture"].evidence["scope_capability_present"] is True
    assert checks["tuner.tune"].status is CheckStatus.BLOCKED
    assert checks["tx.ptt"].status is CheckStatus.BLOCKED

    # MOR-660: filter_width is declared unsupported_pending_evidence on the
    # X6200 template, but the IcomRadio fixture satisfies DspControlCapable, so
    # the capability-present pre-gate resolves it to PASS.
    assert checks["filter_width.set"].status is CheckStatus.PASS
    assert checks["filter_width.set"].evidence["capability_present"] is True

    # Controls the IC-7610 mock cannot read back are NAKed (FAIL), never PASS.
    for nak_check in ("rf_gain.set", "af_level.set", "notch.set", "agc.set"):
        assert checks[nak_check].status in {CheckStatus.FAIL, CheckStatus.UNSUPPORTED}
        assert checks[nak_check].status is not CheckStatus.PASS

    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=TransportInfo(backend="fixture"),
        safety=safety,
        core_version="test",
        mode="hardware",
    )
    assert artifact.mode == "hardware"
    # Round-trips through schema validation.
    validate_artifact_dict(json.loads(json.dumps(artifact.to_dict())))


def _write_only_rit_mock():
    """A rit radio whose SET succeeds but GET times out (X6200 behaviour)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"rit"}
    radio.set_rit_frequency = AsyncMock(return_value=None)
    radio.get_rit_frequency = AsyncMock(side_effect=RigTimeoutError("readback timeout"))
    return radio


async def test_write_only_cap_routes_to_set_observe():
    """THE FIX: a write-only-classified cap routes through set-and-observe,
    so a SET-only radio reports PASS instead of read-first-timeout FAIL."""
    radio = _write_only_rit_mock()
    template = _single_entry_template(check_id="rit.set", capability="rit")

    # WITHOUT the write-only classification: the RMVR handler reads first and
    # the readback timeout produces a FAIL.
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["rit.set"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.COMMAND_EXECUTION

    # WITH the classification: route through set-and-observe → honest PASS.
    radio = _write_only_rit_mock()
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=True,
        write_only_capabilities=frozenset({"rit"}),
    )
    check = _flatten(levels)["rit.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["verification"] == "set_observe"
    assert check.evidence["handler"] == "set_and_observe"
    radio.get_rit_frequency.assert_not_called()
    # SET test value (100) then restore (0).
    assert radio.set_rit_frequency.call_count == 2


async def test_non_write_only_cap_still_uses_rmvr():
    """A cap NOT in write_only_capabilities keeps the RMVR read-modify-verify
    path even when other caps are classified write-only."""
    radio, store = _stateful_preamp_mock(start=0)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=True,
        write_only_capabilities=frozenset({"rit"}),
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 1
    assert check.evidence["restored"] is True
    assert check.evidence.get("verification") != "set_observe"


async def test_write_only_read_only_skips():
    """Write-only cap under --read-only skips without touching the radio."""
    radio = _write_only_rit_mock()
    template = _single_entry_template(check_id="rit.set", capability="rit")
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=False,
        write_only_capabilities=frozenset({"rit"}),
    )
    check = _flatten(levels)["rit.set"]
    assert check.status is CheckStatus.SKIP
    radio.set_rit_frequency.assert_not_called()


async def test_write_only_cap_no_spec_falls_through():
    """A cap in write_only_capabilities whose check_id has no registry spec
    falls through to the named/SKIP path without crashing."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"madeup"}
    template = _single_entry_template(
        check_id="madeup.nonexistent", capability="madeup"
    )
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=True,
        write_only_capabilities=frozenset({"madeup"}),
    )
    check = _flatten(levels)["madeup.nonexistent"]
    assert check.status is CheckStatus.SKIP


async def test_rmvr_pass_on_stateful_mock():
    radio, store = _stateful_preamp_mock(start=0)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 1
    assert check.evidence["readback"] == 1
    assert check.evidence["restored"] is True
    assert await radio.get_preamp() == 0


async def test_rmvr_control_does_not_react_yields_fail_readback():
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"preamp"}
    radio.set_preamp = AsyncMock(return_value=None)  # no-op write
    radio.get_preamp = AsyncMock(return_value=0)  # always 0
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK
    assert check.evidence["original"] == 0
    assert check.evidence["readback"] == 0
    assert check.evidence["restored"] is True
    # changed=1 then restore=0 => at least two writes.
    assert radio.set_preamp.call_count >= 2


async def test_rmvr_restore_failure_is_recorded():
    from rigplane.core.exceptions import CommandError

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"preamp"}
    store = {"value": 0}
    calls = {"set": 0}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(level: int, receiver: int = 0) -> None:
        calls["set"] += 1
        if calls["set"] == 1:
            store["value"] = level
        else:
            raise CommandError("restore failed")

    radio.get_preamp = AsyncMock(side_effect=_get)
    radio.set_preamp = AsyncMock(side_effect=_set)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK
    assert "restore_error" in check.evidence


# ---------------------------------------------------------------------------
# MOR-665: preamp.set clears the DIGI-SEL prerequisite, then restores it.
# The IC-7610 enforces PREAMP/DIGI-SEL mutual exclusion, so when DIGI-SEL is
# ON the harness must temporarily disable it, run the PREAMP RMVR, then restore
# DIGI-SEL to its original state.
# ---------------------------------------------------------------------------


async def test_preamp_clears_and_restores_digisel_when_on():
    radio, _preamp_store, digisel_store = _digisel_preamp_mock(
        preamp_start=0, digisel_on=True
    )
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]

    assert check.status is CheckStatus.PASS
    assert check.evidence["digisel_was_on"] is True
    assert check.evidence["digisel_restored"] is True
    # PREAMP RMVR still ran and reacted/restored.
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 1
    assert check.evidence["restored"] is True
    # DIGI-SEL ended back ON.
    assert digisel_store["on"] is True
    # set_digisel called False (clear) then True (restore), in that order.
    calls = [c.args[0] for c in radio.set_digisel.call_args_list]
    assert calls == [False, True]


async def test_preamp_no_toggle_when_digisel_off():
    radio, _preamp_store, digisel_store = _digisel_preamp_mock(
        preamp_start=0, digisel_on=False
    )
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]

    assert check.status is CheckStatus.PASS
    assert check.evidence["digisel_was_on"] is False
    # No prerequisite toggle when DIGI-SEL was already off.
    assert "digisel_restored" not in check.evidence
    radio.set_digisel.assert_not_called()
    assert digisel_store["on"] is False
    # PREAMP RMVR ran as today.
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 1


async def test_preamp_restores_digisel_even_when_rmvr_fails():
    """If the PREAMP write/readback fails mid-check, DIGI-SEL is STILL restored."""
    radio, _preamp_store, digisel_store = _digisel_preamp_mock(
        preamp_start=0, digisel_on=True
    )
    # Make PREAMP a no-op write so the readback never reacts -> FAIL/READBACK.
    radio.set_preamp = AsyncMock(return_value=None)
    radio.get_preamp = AsyncMock(return_value=0)

    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]

    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK
    assert check.evidence["digisel_was_on"] is True
    assert check.evidence["digisel_restored"] is True
    assert digisel_store["on"] is True
    calls = [c.args[0] for c in radio.set_digisel.call_args_list]
    assert calls == [False, True]


async def test_preamp_digisel_restore_oserror_never_escapes():
    """A bare OSError from the UDP send path during the DIGI-SEL restore must be
    contained (it is in _RESTORE_ERRORS), not escape and abort the run."""
    radio, _preamp_store, _digisel_store = _digisel_preamp_mock(
        preamp_start=0, digisel_on=True
    )
    calls: list[bool] = []

    async def _set_digisel(on: bool, receiver: int = 0) -> None:
        calls.append(on)
        if on:  # the restore call -> simulate a mid-restore LAN drop
            raise OSError("sendto: network is down")

    radio.set_digisel = AsyncMock(side_effect=_set_digisel)

    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    # Must not raise despite the OSError in the finally restore.
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]

    assert check.evidence["digisel_was_on"] is True
    assert check.evidence["digisel_restored"] is False
    assert "digisel_restore_error" in check.evidence
    assert calls == [False, True]  # cleared, then restore attempted


async def test_preamp_falls_back_when_get_digisel_unsupported():
    """A radio without get_digisel (or whose read raises) falls back to plain
    RMVR with no DIGI-SEL evidence and no crash."""
    # No get_digisel attached at all (spec=Radio does not provide it).
    radio, _store = _stateful_preamp_mock(start=0)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]

    assert check.status is CheckStatus.PASS
    assert "digisel_was_on" not in check.evidence
    assert "digisel_restored" not in check.evidence

    # And the case where get_digisel exists but raises -> same fallback.
    radio2, _store2 = _stateful_preamp_mock(start=0)
    radio2.get_digisel = AsyncMock(side_effect=RuntimeError("not implemented"))
    radio2.set_digisel = AsyncMock(return_value=None)
    levels2 = await execute_hardware_checks(
        radio2, template, OperatorSafetyBlock(), allow_writes=True
    )
    check2 = _flatten(levels2)["preamp.set"]
    assert check2.status is CheckStatus.PASS
    assert "digisel_was_on" not in check2.evidence
    radio2.set_digisel.assert_not_called()


async def test_read_only_skips_write_checks():
    radio = _make_mock_radio()
    radio.capabilities = {"audio", "scope", "tuner", "tx", "preamp", "rf_gain"}
    radio.set_preamp = AsyncMock(return_value=None)
    radio.get_preamp = AsyncMock(return_value=0)
    radio.set_rf_gain = AsyncMock(return_value=None)
    radio.get_rf_gain = AsyncMock(return_value=0)
    template = _x6200_template()
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    checks = _flatten(levels)

    assert checks["freq.write"].status is CheckStatus.SKIP
    radio.set_freq.assert_not_called()
    assert checks["mode.set"].status is CheckStatus.SKIP
    assert checks["preamp.set"].status is CheckStatus.SKIP
    assert checks["rf_gain.set"].status is CheckStatus.SKIP
    radio.set_preamp.assert_not_called()

    # Read-only checks still run.
    assert checks["freq.reverse_sync"].status is CheckStatus.PASS
    assert checks["audio.rx"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tuner.tune"].status is CheckStatus.BLOCKED


async def test_timestamps_present_and_iso8601(connected_radio):
    template = _x6200_template()
    levels = await execute_hardware_checks(
        connected_radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    for check in _flatten(levels).values():
        assert check.started_at is not None
        assert check.finished_at is not None
        assert _ISO8601_MS_Z.match(check.started_at)
        assert _ISO8601_MS_Z.match(check.finished_at)


async def test_generated_at_round_trips():
    artifact = ValidationArtifact(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        transport=TransportInfo(backend="serial", baud=115200),
        safety=OperatorSafetyBlock(),
        levels=[],
        core_version="test",
        generated_at="2026-05-28T15:42:09.123Z",
    )
    restored = validate_artifact_dict(json.loads(json.dumps(artifact.to_dict())))
    assert restored.generated_at == "2026-05-28T15:42:09.123Z"
    assert _ISO8601_MS_Z.match(restored.generated_at)


async def test_unsupported_when_capability_absent():
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = set()
    radio.set_preamp = AsyncMock(return_value=None)
    radio.get_preamp = AsyncMock(return_value=0)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.UNSUPPORTED
    assert check.evidence["capability_present"] is False
    radio.set_preamp.assert_not_called()


async def test_presence_check_passes_when_capability_present():
    """MOR-660: an ``unsupported_pending_evidence`` presence check whose
    capability IS present resolves PASS — presence is the pending evidence."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"preamp"}
    template = _single_entry_template(
        check_id="preamp.presence",
        capability="preamp",
        level=ValidationLevel.STATIC_PROFILE,
        declaration=CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE,
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.presence"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["capability_present"] is True
    assert check.evidence["declared"] == "unsupported_pending_evidence"


async def test_presence_check_stays_unsupported_when_capability_absent():
    """MOR-660: a presence check whose capability is NOT declared stays
    UNSUPPORTED."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = set()
    template = _single_entry_template(
        check_id="preamp.presence",
        capability="preamp",
        level=ValidationLevel.STATIC_PROFILE,
        declaration=CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE,
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.presence"]
    assert check.status is CheckStatus.UNSUPPORTED
    assert check.evidence["capability_present"] is False


async def test_tx_and_tuner_blocked_without_flags_and_manual_with():
    radio = _make_mock_radio()
    template = MatrixTemplate(
        radio=RadioTarget(model="X6200", profile_id="x6200"),
        entries=[
            CapabilityDeclarationEntry(
                check_id="tuner.tune",
                capability="tuner",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="tuner tune",
                tx_adjacent=True,
            ),
            CapabilityDeclarationEntry(
                check_id="tx.ptt",
                capability="tx",
                level=ValidationLevel.STRESS_RECOVERY,
                declaration=CapabilityDeclaration.MANUAL_REQUIRED,
                summary="ptt",
                tx_adjacent=True,
            ),
        ],
    )

    # Default safety: both blocked.
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    checks = _flatten(levels)
    assert checks["tuner.tune"].status is CheckStatus.BLOCKED
    assert checks["tuner.tune"].failure_domain is FailureDomain.COMMAND_EXECUTION
    assert checks["tx.ptt"].status is CheckStatus.BLOCKED
    assert checks["tx.ptt"].failure_domain is FailureDomain.COMMAND_EXECUTION

    # Authorized: both manual_required, never actuated.
    authorized = OperatorSafetyBlock(tx_allowed=True, tuner_allowed=True)
    levels = await execute_hardware_checks(
        radio, template, authorized, allow_writes=True
    )
    checks = _flatten(levels)
    assert checks["tuner.tune"].status is CheckStatus.MANUAL_REQUIRED
    assert checks["tx.ptt"].status is CheckStatus.MANUAL_REQUIRED
    radio.set_ptt.assert_not_called()


async def test_timeout_yields_fail_command_execution():
    radio = _make_mock_radio()

    async def _slow(*_args, **_kwargs):
        await asyncio.sleep(10)

    radio.get_freq = AsyncMock(side_effect=_slow)
    template = _single_entry_template(
        check_id="discovery.identify",
        capability="",
        level=ValidationLevel.DISCOVERY,
        summary="identify",
    )
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=False,
        per_check_timeout=0.01,
    )
    check = _flatten(levels)["discovery.identify"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.COMMAND_EXECUTION
    assert "timeout" in (check.error or "")


async def test_reverse_sync_mismatch_yields_fail_state_publishing():
    radio = _make_mock_radio(freq=14_074_000, state_freq=7_000_000)
    template = _single_entry_template(
        check_id="freq.reverse_sync",
        capability="",
        level=ValidationLevel.BASIC_CONTROL,
        summary="reverse sync",
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    check = _flatten(levels)["freq.reverse_sync"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.STATE_PUBLISHING
    assert check.evidence["command_freq_hz"] == 14_074_000
    assert check.evidence["state_freq_hz"] == 7_000_000
    assert check.evidence["delta_hz"] == 14_074_000 - 7_000_000


def test_tolerant_equal_within_and_beyond_tolerance():
    from rigplane.validation.hardware import _tolerant_equal

    eq = _tolerant_equal(3)
    assert eq(200, 198) is True
    assert eq(200, 203) is True
    assert eq(200, 196) is False
    assert eq(50, 50) is True


class _OffByTwoRfGainRadio:
    """Dataclass-style fake whose rf_gain readback lags the write by 2.

    Mirrors a real radio that quantizes an analog level: the tolerance-aware
    comparator must still treat the readback as a successful reaction/restore
    while evidence records the exact written/read values.
    """

    def __init__(self) -> None:
        self.connected = True
        self.model = "OffByTwo"
        self.capabilities = {"rf_gain"}
        self.radio_state = RadioState()
        self._value = 100

    async def get_rf_gain(self, receiver: int = 0) -> int:
        # Reads back 2 below whatever was last written.
        return max(0, self._value - 2)

    async def set_rf_gain(self, level: int, receiver: int = 0) -> None:
        self._value = level


async def test_rf_gain_tolerance_passes_with_off_by_two_readback():
    radio = _OffByTwoRfGainRadio()
    template = _single_entry_template(check_id="rf_gain.set", capability="rf_gain")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["rf_gain.set"]

    assert check.status is CheckStatus.PASS
    # original read: 100 -> 98; make_changed(98) -> 200; readback 200 -> 198.
    assert check.evidence["original"] == 98
    assert check.evidence["changed"] == 200
    assert check.evidence["readback"] == 198
    assert check.evidence["restored"] is True
    # Restore wrote back the exact original (98); readback is 96.
    assert check.evidence["restore_readback"] == 96


async def test_artifact_round_trips():
    radio = _make_mock_radio()
    template = _x6200_template()
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=TransportInfo(backend="serial", baud=115200),
        safety=OperatorSafetyBlock(),
        core_version="test",
        mode="hardware",
    )
    restored = validate_artifact_dict(json.loads(json.dumps(artifact.to_dict())))
    assert restored.mode == "hardware"
    assert restored.transport.backend == "serial"


# ---------------------------------------------------------------------------
# MOR-667: interactive operator-confirmed manual-perception checks
# ---------------------------------------------------------------------------


def _perception_template(check_id: str, capability: str) -> MatrixTemplate:
    return _single_entry_template(
        check_id=check_id,
        capability=capability,
        declaration=CapabilityDeclaration.MANUAL_REQUIRED,
        summary="perception",
    )


def _canned_prompter(answer: bool):
    """Build an InteractivePrompter returning *answer*, recording prompts seen."""
    from rigplane.validation.interactive import InteractivePrompter

    seen: list[str] = []

    def _input(prompt: str) -> str:
        seen.append(prompt)
        return "y" if answer else "n"

    return (
        InteractivePrompter(input_fn=_input, output_fn=lambda _msg: None),
        seen,
    )


async def test_interactive_audio_rx_yes_passes():
    radio = _make_mock_radio()
    prompter, seen = _canned_prompter(True)
    levels = await execute_hardware_checks(
        radio,
        _perception_template("audio.rx", "audio"),
        OperatorSafetyBlock(),
        allow_writes=False,
        prompter=prompter,
    )
    check = _flatten(levels)["audio.rx"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["operator_confirmed"] is True
    assert "RX audio" in check.evidence["prompt"]
    assert len(seen) == 1  # exactly one prompt read


async def test_interactive_scope_capture_no_fails():
    radio = _make_mock_radio()
    prompter, _ = _canned_prompter(False)
    levels = await execute_hardware_checks(
        radio,
        _perception_template("scope.capture", "scope"),
        OperatorSafetyBlock(),
        allow_writes=False,
        prompter=prompter,
    )
    check = _flatten(levels)["scope.capture"]
    assert check.status is CheckStatus.FAIL
    assert check.evidence["operator_confirmed"] is False
    assert check.failure_domain is FailureDomain.COMMAND_EXECUTION


async def test_interactive_bsr_select_yes_passes():
    radio = _make_mock_radio()
    prompter, _ = _canned_prompter(True)
    levels = await execute_hardware_checks(
        radio,
        _perception_template("bsr.select", "bsr"),
        OperatorSafetyBlock(),
        allow_writes=False,
        prompter=prompter,
    )
    check = _flatten(levels)["bsr.select"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["operator_confirmed"] is True


async def test_no_prompter_keeps_manual_required_and_reads_no_stdin():
    """Regression guard: without a prompter, perception checks stay
    MANUAL_REQUIRED and nothing ever reads stdin (no hang)."""
    radio = _make_mock_radio()
    levels = await execute_hardware_checks(
        radio,
        _perception_template("audio.rx", "audio"),
        OperatorSafetyBlock(),
        allow_writes=False,
    )
    check = _flatten(levels)["audio.rx"]
    assert check.status is CheckStatus.MANUAL_REQUIRED
    assert "operator_confirmed" not in check.evidence


async def test_assume_yes_auto_passes_without_reading_stdin():
    """``--assume-yes`` (assume_yes prompter) PASSes a perception check without
    ever calling input — proves an unattended run never blocks on stdin."""
    from rigplane.validation.interactive import InteractivePrompter

    def _input(_prompt: str) -> str:  # pragma: no cover - must never run
        raise AssertionError("stdin must not be read under assume_yes")

    prompter = InteractivePrompter(
        input_fn=_input, output_fn=lambda _msg: None, assume_yes=True
    )
    radio = _make_mock_radio()
    levels = await execute_hardware_checks(
        radio,
        _perception_template("audio.rx", "audio"),
        OperatorSafetyBlock(),
        allow_writes=False,
        prompter=prompter,
    )
    check = _flatten(levels)["audio.rx"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["operator_confirmed"] is True


async def test_interactive_does_not_actuate_tx_or_tuner():
    """Perception interactivity must not turn TX/tuner manual checks into a
    prompt — they have no perception entry and stay MANUAL_REQUIRED (authorized)
    or BLOCKED, never auto-yes-able and never actuated."""
    radio = _make_mock_radio()
    prompter, seen = _canned_prompter(True)
    template = MatrixTemplate(
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
        ],
    )
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(tx_allowed=True),
        allow_writes=False,
        prompter=prompter,
    )
    check = _flatten(levels)["tx.ptt"]
    assert check.status is CheckStatus.MANUAL_REQUIRED
    assert seen == []  # no perception prompt for tx.ptt
    radio.set_ptt.assert_not_called()


# ---------------------------------------------------------------------------
# MOR-199: generic _check_from_spec dispatch tests
# ---------------------------------------------------------------------------


def _stateful_squelch_mock(*, start: int = 0):
    """A MagicMock(spec=Radio) whose squelch set/get round-trips via a closure."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"squelch"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(level: int, receiver: int = 0) -> None:
        store["value"] = level

    radio.get_squelch = AsyncMock(side_effect=_get)
    radio.set_squelch = AsyncMock(side_effect=_set)
    return radio, store


async def test_generic_squelch_set_rmvr_pass():
    """squelch.set dispatches via generic handler and round-trips (PASS)."""
    radio, store = _stateful_squelch_mock(start=0)
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.PASS
    # step_level_255(0) -> 200
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 200
    assert check.evidence["readback"] == 200
    assert check.evidence["restored"] is True
    assert check.evidence["handler"] == "generic"
    assert check.evidence["value_rule"] == "step_level_255"


async def test_generic_squelch_set_fail_no_react():
    """set_squelch no-op (value never changes) -> FAIL with READBACK domain."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"squelch"}
    radio.set_squelch = AsyncMock(return_value=None)  # no-op write
    radio.get_squelch = AsyncMock(return_value=100)  # always 100
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK
    # write + restore => at least 2 set_squelch calls
    assert radio.set_squelch.call_count >= 2


class _OffByTwoSquelchRadio:
    """Squelch fake that reads back 2 below whatever was last written."""

    def __init__(self) -> None:
        self.connected = True
        self.model = "OffByTwo"
        self.capabilities = {"squelch"}
        self.radio_state = RadioState()
        self._value = 100

    async def get_squelch(self, receiver: int = 0) -> int:
        return max(0, self._value - 2)

    async def set_squelch(self, level: int, receiver: int = 0) -> None:
        self._value = level


async def test_generic_squelch_set_tolerance():
    """Tolerance=3 on squelch.set allows an off-by-two fake to PASS."""
    radio = _OffByTwoSquelchRadio()
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.PASS


async def test_generic_squelch_set_unsupported_no_setter():
    """squelch capability present but set_squelch absent -> UNSUPPORTED."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"squelch"}
    radio.get_squelch = AsyncMock(return_value=50)
    # Deliberately omit set_squelch from the mock spec attribute access
    # so getattr returns the MagicMock attribute but we override to None.
    del radio.set_squelch
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.UNSUPPORTED


async def test_generic_squelch_set_skip_read_only():
    """allow_writes=False -> SKIP, set_squelch never called."""
    radio, _store = _stateful_squelch_mock(start=50)
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.SKIP
    radio.set_squelch.assert_not_called()


async def test_generic_squelch_set_unsupported_capability_absent():
    """capability 'squelch' absent from radio.capabilities -> UNSUPPORTED."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = set()
    radio.get_squelch = AsyncMock(return_value=50)
    radio.set_squelch = AsyncMock(return_value=None)
    template = _single_entry_template(check_id="squelch.set", capability="squelch")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["squelch.set"]
    assert check.status is CheckStatus.UNSUPPORTED
    assert check.evidence.get("capability_present") is False
    radio.set_squelch.assert_not_called()


async def test_generic_meters_read_pass():
    """meters.read (READ_ONLY) dispatches via generic handler -> PASS."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"meters"}
    radio.get_s_meter = AsyncMock(return_value=-73)
    template = _single_entry_template(
        check_id="meters.read",
        capability="meters",
        level=ValidationLevel.COMPATIBILITY_SURFACES,
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    check = _flatten(levels)["meters.read"]
    assert check.status is CheckStatus.PASS
    assert check.evidence["value"] == -73
    assert check.evidence["op"] == "get_s_meter"
    assert check.evidence["handler"] == "generic"


async def test_generic_meters_read_unsupported_no_getter():
    """meters.read with no get_s_meter on radio -> UNSUPPORTED."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"meters"}
    del radio.get_s_meter
    template = _single_entry_template(
        check_id="meters.read",
        capability="meters",
        level=ValidationLevel.COMPATIBILITY_SURFACES,
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=False
    )
    check = _flatten(levels)["meters.read"]
    assert check.status is CheckStatus.UNSUPPORTED


async def test_named_handler_wins_over_generic():
    """preamp.set has a named handler; it must NOT produce handler=='generic'."""
    radio, _store = _stateful_preamp_mock(start=0)
    template = _single_entry_template(check_id="preamp.set", capability="preamp")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["preamp.set"]
    assert check.status is CheckStatus.PASS
    assert check.evidence.get("handler") != "generic"


async def test_set_and_observe_pass():
    """WRITE_ONLY_OBSERVE + TOGGLE_BOOL: SET accepted -> PASS; restore writes False."""
    from rigplane.core.exceptions import TimeoutError as RigTimeoutError  # noqa: F401
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"xit"}
    radio.set_rit_tx_status = AsyncMock(return_value=None)

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only xit",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only xit",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.PASS
    assert result.evidence["verification"] == "set_observe"
    assert result.evidence["set_accepted"] is True
    assert result.evidence["handler"] == "set_and_observe"
    # restore: False is the benign default for TOGGLE_BOOL
    assert radio.set_rit_tx_status.call_count == 2
    calls = radio.set_rit_tx_status.call_args_list
    assert calls[0].args[0] is True  # test value
    assert calls[1].args[0] is False  # restore value
    assert result.evidence["restored"] is True
    assert result.evidence["restore_value"] is False


async def test_set_and_observe_bump_hz_restores_zero():
    """WRITE_ONLY_OBSERVE + BUMP_HZ: second call uses restore value 0."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"rit"}
    radio.set_rit_frequency = AsyncMock(return_value=None)

    entry = CapabilityDeclarationEntry(
        check_id="rit.set",
        capability="rit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only rit freq",
    )
    spec = CheckSpec(
        check_id="rit.set",
        capability="rit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only rit freq",
        set_op="set_rit_frequency",
        value_rule=ValueRule.BUMP_HZ,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.PASS
    calls = radio.set_rit_frequency.call_args_list
    assert radio.set_rit_frequency.call_count == 2
    assert calls[0].args[0] == 100  # BUMP_HZ test value
    assert calls[1].args[0] == 0  # restore value
    assert result.evidence["restore_value"] == 0


async def test_set_and_observe_set_timeout_fails():
    """SET raises RigTimeoutError -> FAIL with COMMAND_EXECUTION domain."""
    from rigplane.core.exceptions import TimeoutError as RigTimeoutError
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"xit"}
    radio.set_rit_tx_status = AsyncMock(side_effect=RigTimeoutError("timed out"))

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only xit timeout",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only xit timeout",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.FAIL
    assert result.failure_domain is FailureDomain.COMMAND_EXECUTION
    assert "set_error" in result.evidence
    assert result.evidence.get("set_accepted") is not True


async def test_set_and_observe_skip_read_only():
    """allow_writes=False -> SKIP via _write_gate; set_op never called."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"xit"}
    radio.set_rit_tx_status = AsyncMock(return_value=None)

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only xit skip",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only xit skip",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=False, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.SKIP
    radio.set_rit_tx_status.assert_not_called()


async def test_set_and_observe_unsupported_capability_absent():
    """Capability absent -> UNSUPPORTED via _write_gate; set_op never called."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = set()
    radio.set_rit_tx_status = AsyncMock(return_value=None)

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only xit cap absent",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only xit cap absent",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.UNSUPPORTED
    radio.set_rit_tx_status.assert_not_called()


async def test_set_and_observe_unsupported_no_set_op():
    """Capability present but radio lacks set_op attribute -> UNSUPPORTED."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"xit"}
    # Deliberately omit set_rit_tx_status so getattr falls back to None/missing.
    del radio.set_rit_tx_status

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only no set op",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only no set op",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.UNSUPPORTED
    assert "set_rit_tx_status" in str(result.evidence.get("reason", ""))


async def test_set_and_observe_no_restore_default():
    """STEP_LEVEL_255 has no benign restore default -> PASS, set called once."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"squelch"}
    radio.set_squelch = AsyncMock(return_value=None)

    entry = CapabilityDeclarationEntry(
        check_id="squelch.set",
        capability="squelch",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only squelch no restore",
    )
    spec = CheckSpec(
        check_id="squelch.set",
        capability="squelch",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only squelch no restore",
        set_op="set_squelch",
        value_rule=ValueRule.STEP_LEVEL_255,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.PASS
    assert result.evidence["restored"] is False
    assert "restore_skipped" in result.evidence
    radio.set_squelch.assert_called_once()


async def test_set_and_observe_restore_error_swallowed():
    """Restore raises RigTimeoutError -> still PASS, evidence records restore_error."""
    from rigplane.core.exceptions import TimeoutError as RigTimeoutError
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"xit"}
    radio.set_rit_tx_status = AsyncMock(
        side_effect=[None, RigTimeoutError("restore timed out")]
    )

    entry = CapabilityDeclarationEntry(
        check_id="xit.set",
        capability="xit",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="write only xit restore error",
    )
    spec = CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.WRITE_ONLY_OBSERVE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="write only xit restore error",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.PASS
    assert result.evidence["set_accepted"] is True
    assert result.evidence["restored"] is False
    assert "restore_error" in result.evidence


async def test_generic_mode_cycle_unsupported():
    """MODE_CYCLE value_rule -> UNSUPPORTED (handled by named _check_mode_set)."""
    from rigplane.validation.hardware import _check_from_spec

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = {"mode_x"}
    radio.get_x = AsyncMock(return_value="USB")
    radio.set_x = AsyncMock(return_value=None)
    entry = CapabilityDeclarationEntry(
        check_id="mode_x.set",
        capability="mode_x",
        level=ValidationLevel.CAPABILITY_MATRIX,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="mode cycle stub",
    )
    spec = CheckSpec(
        check_id="mode_x.set",
        capability="mode_x",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="mode cycle stub",
        get_op="get_x",
        set_op="set_x",
        value_rule=ValueRule.MODE_CYCLE,
    )
    result = await _check_from_spec(
        radio, entry, spec, allow_writes=True, per_check_timeout=5.0
    )
    assert result.status is CheckStatus.UNSUPPORTED
    assert "mode_cycle" in str(result.evidence.get("reason", ""))


def test_value_rule_map_correctness():
    """All 6 scalar closures produce expected outputs; MODE_CYCLE is absent."""
    from rigplane.validation.hardware import _VALUE_RULE_FNS

    assert _VALUE_RULE_FNS[ValueRule.TOGGLE_BOOL](False) is True
    assert _VALUE_RULE_FNS[ValueRule.TOGGLE_BOOL](True) is False

    assert _VALUE_RULE_FNS[ValueRule.STEP_LEVEL_255](0) == 200
    assert _VALUE_RULE_FNS[ValueRule.STEP_LEVEL_255](200) == 50

    assert _VALUE_RULE_FNS[ValueRule.NUDGE_FILTER](2600) == 2800
    assert _VALUE_RULE_FNS[ValueRule.NUDGE_FILTER](2601) == 2401

    assert _VALUE_RULE_FNS[ValueRule.PREAMP_CYCLE](0) == 1
    assert _VALUE_RULE_FNS[ValueRule.PREAMP_CYCLE](1) == 0

    assert _VALUE_RULE_FNS[ValueRule.AGC_FLIP](AgcMode.FAST) == int(AgcMode.SLOW)
    assert _VALUE_RULE_FNS[ValueRule.AGC_FLIP](AgcMode.SLOW) == int(AgcMode.FAST)

    assert _VALUE_RULE_FNS[ValueRule.BUMP_HZ](14_000_000) == 14_000_100

    assert ValueRule.MODE_CYCLE not in _VALUE_RULE_FNS


async def test_unknown_check_id_still_skips():
    """A SUPPORTED entry with an unknown check_id (no spec) -> SKIP."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "X6200"
    radio.capabilities = set()
    template = _single_entry_template(
        check_id="bogus.thing",
        capability="",
        level=ValidationLevel.CAPABILITY_MATRIX,
    )
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    check = _flatten(levels)["bogus.thing"]
    assert check.status is CheckStatus.SKIP
    assert "no hardware handler" in str(check.evidence.get("reason", ""))
