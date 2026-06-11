"""Hardware-runner tests for the command-coverage families (MOR-642..645).

Drives every new registry check through ``execute_hardware_checks`` against
stateful fake radios (closures over real values — no MagicMock return-value
soup), asserting:

* RMVR checks mutate, read back, and restore the original value;
* READ_ONLY checks pass when the op exists and report UNSUPPORTED when absent;
* MANUAL checks resolve MANUAL_REQUIRED without touching the radio;
* TX-adjacent checks stay BLOCKED without operator authorization.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rigplane.core.radio_protocol import Radio
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.registry import REGISTRY_BY_ID, CheckKind
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _template_for(check_id: str) -> MatrixTemplate:
    """Build a single-entry template straight from the registry spec."""
    spec = REGISTRY_BY_ID[check_id]
    if spec.kind in (CheckKind.MANUAL, CheckKind.TX_ADJACENT_BLOCKED):
        declaration = CapabilityDeclaration.MANUAL_REQUIRED
    else:
        declaration = CapabilityDeclaration.SUPPORTED
    return MatrixTemplate(
        radio=RadioTarget(model="IC-7610", profile_id="ic7610"),
        entries=[
            CapabilityDeclarationEntry(
                check_id=spec.check_id,
                capability=spec.capability,
                level=spec.level,
                declaration=declaration,
                summary=spec.summary,
                tx_adjacent=spec.tx_adjacent,
            )
        ],
    )


def _bare_radio(capabilities: set[str]):
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = capabilities
    return radio


def _stateful_value_radio(
    *, capability: str, get_op: str, set_op: str, start, receiver_kw: bool = True
):
    """A fake radio whose ``get_op``/``set_op`` round-trip via a closure."""
    radio = _bare_radio({capability} if capability else set())
    store = {"value": start, "writes": []}

    if receiver_kw:

        async def _get(receiver: int = 0):
            return store["value"]

        async def _set(value, receiver: int = 0) -> None:
            store["value"] = value
            store["writes"].append(value)

    else:

        async def _get():  # type: ignore[misc]
            return store["value"]

        async def _set(value) -> None:  # type: ignore[misc]
            store["value"] = value
            store["writes"].append(value)

    setattr(radio, get_op, AsyncMock(side_effect=_get))
    setattr(radio, set_op, AsyncMock(side_effect=_set))
    return radio, store


async def _run(radio, check_id: str, *, safety: OperatorSafetyBlock | None = None):
    levels = await execute_hardware_checks(
        radio,
        _template_for(check_id),
        safety or OperatorSafetyBlock(),
        allow_writes=True,
    )
    return _flatten(levels)[check_id]


# ---------------------------------------------------------------------------
# T7 / MOR-642 — tone / TSQL
# ---------------------------------------------------------------------------


async def test_repeater_tone_set_rmvr_roundtrip():
    radio, store = _stateful_value_radio(
        capability="repeater_tone",
        get_op="get_repeater_tone",
        set_op="set_repeater_tone",
        start=False,
    )
    result = await _run(radio, "repeater_tone.set")
    assert result.status is CheckStatus.PASS
    assert result.evidence["restored"] is True
    assert store["value"] is False  # restored to original
    assert True in store["writes"]  # toggled on during the check


async def test_tsql_set_rmvr_roundtrip():
    radio, store = _stateful_value_radio(
        capability="tsql",
        get_op="get_repeater_tsql",
        set_op="set_repeater_tsql",
        start=True,
    )
    result = await _run(radio, "tsql.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] is True
    assert False in store["writes"]


async def test_tone_freq_set_cycles_standard_ctcss_tone():
    radio, store = _stateful_value_radio(
        capability="repeater_tone",
        get_op="get_tone_freq",
        set_op="set_tone_freq",
        start=88.5,
    )
    result = await _run(radio, "tone_freq.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] == 88.5  # restored
    # The mutated value must be a DIFFERENT standard CTCSS tone.
    changed = [v for v in store["writes"] if v != 88.5]
    assert changed and changed[0] == 100.0


async def test_tsql_freq_set_cycles_standard_ctcss_tone():
    radio, store = _stateful_value_radio(
        capability="tsql",
        get_op="get_tsql_freq",
        set_op="set_tsql_freq",
        start=123.0,
    )
    result = await _run(radio, "tsql_freq.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] == 123.0
    changed = [v for v in store["writes"] if v != 123.0]
    assert changed and changed[0] == 88.5


async def test_tone_check_unsupported_when_radio_lacks_op():
    radio = _bare_radio({"repeater_tone"})
    radio.get_repeater_tone = None
    radio.set_repeater_tone = None
    result = await _run(radio, "repeater_tone.set")
    assert result.status is CheckStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# T8 / MOR-643 — split / VFO / dual-watch
# ---------------------------------------------------------------------------


async def test_split_set_rmvr_roundtrip():
    radio, store = _stateful_value_radio(
        capability="split",
        get_op="get_split",
        set_op="set_split",
        start=False,
        receiver_kw=False,
    )
    result = await _run(radio, "split.set")
    assert result.status is CheckStatus.PASS
    assert result.evidence["restored"] is True
    assert store["value"] is False
    assert True in store["writes"]


async def test_vfo_slot_set_flips_a_b_and_restores():
    radio, store = _stateful_value_radio(
        capability="",
        get_op="get_vfo_slot",
        set_op="set_vfo_slot",
        start="A",
    )
    result = await _run(radio, "vfo_slot.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] == "A"  # restored
    assert "B" in store["writes"]


async def test_vfo_slot_set_flips_b_to_a():
    radio, store = _stateful_value_radio(
        capability="",
        get_op="get_vfo_slot",
        set_op="set_vfo_slot",
        start="B",
    )
    result = await _run(radio, "vfo_slot.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] == "B"
    assert "A" in store["writes"]


async def test_dual_watch_set_rmvr_roundtrip():
    radio, store = _stateful_value_radio(
        capability="dual_watch",
        get_op="get_dual_watch",
        set_op="set_dual_watch",
        start=False,
        receiver_kw=False,
    )
    result = await _run(radio, "dual_watch.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] is False
    assert True in store["writes"]


async def test_vfo_slot_unsupported_when_radio_lacks_op():
    radio = _bare_radio(set())
    radio.get_vfo_slot = None
    radio.set_vfo_slot = None
    result = await _run(radio, "vfo_slot.set")
    assert result.status is CheckStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# T9 / MOR-644 — memory / band-stack
# ---------------------------------------------------------------------------


async def test_bsr_select_is_manual_and_touches_nothing():
    radio = _bare_radio({"bsr"})
    radio.set_bsr = AsyncMock()
    radio.set_memory_mode = AsyncMock()
    result = await _run(radio, "bsr.select")
    assert result.status is CheckStatus.MANUAL_REQUIRED
    radio.set_bsr.assert_not_called()
    radio.set_memory_mode.assert_not_called()


def test_bsr_select_spec_is_manual_with_no_ops():
    spec = REGISTRY_BY_ID["bsr.select"]
    assert spec.kind is CheckKind.MANUAL
    assert spec.get_op is None
    assert spec.set_op is None


# ---------------------------------------------------------------------------
# T10 / MOR-645 — system
# ---------------------------------------------------------------------------


async def test_system_date_read_passes_with_tuple_value():
    radio = _bare_radio({"system_settings"})
    radio.get_system_date = AsyncMock(return_value=(2026, 6, 11))
    result = await _run(radio, "system_date.read")
    assert result.status is CheckStatus.PASS
    assert result.evidence["value"] == (2026, 6, 11)


async def test_system_time_read_passes_with_tuple_value():
    radio = _bare_radio({"system_settings"})
    radio.get_system_time = AsyncMock(return_value=(13, 37))
    result = await _run(radio, "system_time.read")
    assert result.status is CheckStatus.PASS
    assert result.evidence["value"] == (13, 37)


async def test_system_date_read_unsupported_when_op_missing():
    radio = _bare_radio({"system_settings"})
    radio.get_system_date = None
    result = await _run(radio, "system_date.read")
    assert result.status is CheckStatus.UNSUPPORTED


async def test_key_speed_set_rmvr_roundtrip_in_wpm():
    radio, store = _stateful_value_radio(
        capability="cw",
        get_op="get_key_speed",
        set_op="set_key_speed",
        start=28,
        receiver_kw=False,
    )
    result = await _run(radio, "key_speed.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] == 28  # restored
    changed = [v for v in store["writes"] if v != 28]
    assert changed, "key speed was never mutated"
    # Mutation must stay inside the keyer's real WPM range (6-48).
    assert all(6 <= v <= 48 for v in changed)


async def test_vox_read_passes():
    radio = _bare_radio({"vox"})
    radio.get_vox = AsyncMock(return_value=False)
    result = await _run(radio, "vox.read")
    assert result.status is CheckStatus.PASS
    assert result.evidence["value"] is False


async def test_vox_set_blocked_without_authorization():
    radio = _bare_radio({"vox"})
    radio.set_vox = AsyncMock()
    result = await _run(radio, "vox.set")
    assert result.status is CheckStatus.BLOCKED
    radio.set_vox.assert_not_called()


async def test_vox_gain_set_blocked_without_authorization():
    radio = _bare_radio({"vox"})
    radio.set_vox_gain = AsyncMock()
    result = await _run(radio, "vox_gain.set")
    assert result.status is CheckStatus.BLOCKED
    radio.set_vox_gain.assert_not_called()


async def test_vox_set_manual_required_when_authorized():
    """With operator TX authorization the check surfaces as MANUAL_REQUIRED —
    it still never auto-actuates VOX."""
    radio = _bare_radio({"vox"})
    radio.set_vox = AsyncMock()
    safety = OperatorSafetyBlock(tx_allowed=True, operator_id="test")
    result = await _run(radio, "vox.set", safety=safety)
    assert result.status is CheckStatus.MANUAL_REQUIRED
    radio.set_vox.assert_not_called()


async def test_dial_lock_set_rmvr_roundtrip():
    radio, store = _stateful_value_radio(
        capability="dial_lock",
        get_op="get_dial_lock",
        set_op="set_dial_lock",
        start=False,
        receiver_kw=False,
    )
    result = await _run(radio, "dial_lock.set")
    assert result.status is CheckStatus.PASS
    assert store["value"] is False
    assert True in store["writes"]


# ---------------------------------------------------------------------------
# Cross-family registry wiring
# ---------------------------------------------------------------------------


def test_new_family_levels_are_correct():
    matrix = ValidationLevel.CAPABILITY_MATRIX
    expectations = {
        "repeater_tone.set": matrix,
        "tone_freq.set": matrix,
        "tsql.set": matrix,
        "tsql_freq.set": matrix,
        "split.set": matrix,
        "vfo_slot.set": ValidationLevel.BASIC_CONTROL,
        "dual_watch.set": matrix,
        "bsr.select": matrix,
        "system_date.read": matrix,
        "system_time.read": matrix,
        "key_speed.set": matrix,
        "vox.read": matrix,
        "vox.set": ValidationLevel.STRESS_RECOVERY,
        "vox_gain.set": ValidationLevel.STRESS_RECOVERY,
        "dial_lock.set": matrix,
    }
    for check_id, level in expectations.items():
        spec = REGISTRY_BY_ID[check_id]
        assert spec.level == level, f"{check_id}: level {spec.level} != {level}"


def test_tx_adjacent_vox_checks_have_no_ops():
    for check_id in ("vox.set", "vox_gain.set"):
        spec = REGISTRY_BY_ID[check_id]
        assert spec.kind is CheckKind.TX_ADJACENT_BLOCKED
        assert spec.tx_adjacent is True
        assert spec.get_op is None
        assert spec.set_op is None
