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

from rigplane.core.radio_protocol import Radio
from rigplane.core.radio_state import RadioState
from rigplane.validation.hardware import execute_hardware_checks
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
    assert checks["scope.capture"].status is CheckStatus.UNSUPPORTED
    assert checks["tuner.tune"].status is CheckStatus.BLOCKED
    assert checks["tx.ptt"].status is CheckStatus.BLOCKED

    # filter_width is declared unsupported_pending_evidence on the X6200.
    assert checks["filter_width.set"].status is CheckStatus.UNSUPPORTED

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
