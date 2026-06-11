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
