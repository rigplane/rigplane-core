"""FTX-1-flavoured hardware-validator regressions (MOR-499).

The FTX-1 (``YaesuCatRadio``) reacts to filter-width, manual-notch, noise
blanker / noise reduction, AGC, and XIT changes via documented CAT commands,
but the universal validation harness reported false FAIL / UNSUPPORTED
verdicts because the read-modify-verify-restore (RMVR) mutations assumed
Icom-shaped value encodings:

* ``filter_width.set`` mutated a table *index* (0-23) as if it were Hz, so the
  changed value fell out of range and the radio ignored it.
* ``notch.set`` toggled/compared the whole ``(bool, int)`` compound returned by
  ``get_manual_notch`` instead of the on/off bool component.
* ``nb.set`` / ``nr.set`` looked up ``get_nb`` / ``get_nr`` (Icom bool getters)
  that the Yaesu backend does not expose — it has ``get_nb_level`` /
  ``get_nr_level`` — so the harness downgraded to UNSUPPORTED.

These tests lock the FTX-1 encodings AND the unchanged Icom-shaped behaviour
(bool notch, bool nb/nr, Hz filter width) so the per-encoding branching cannot
regress the Icom radios that share the same checks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rigplane.core.radio_protocol import Radio
from rigplane.core.types import AgcMode
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    ValidationLevel,
)


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _single_entry_template(*, check_id: str, capability: str) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="FTX-1", profile_id="ftx1"),
        entries=[
            CapabilityDeclarationEntry(
                check_id=check_id,
                capability=capability,
                level=ValidationLevel.CAPABILITY_MATRIX,
                declaration=CapabilityDeclaration.SUPPORTED,
                summary="single",
            )
        ],
    )


async def _run(radio, *, check_id: str, capability: str):
    template = _single_entry_template(check_id=check_id, capability=capability)
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    return _flatten(levels)[check_id]


# ---------------------------------------------------------------------------
# FIX 1 — filter_width.set table-index vs Hz
# ---------------------------------------------------------------------------


def _stateful_filter_mock(*, start: int):
    """A radio whose filter-width set/get round-trips, but rejects (no-op)
    a write that falls outside the FTX-1 table-index range 0-23."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"filter_width"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(value: int, receiver: int = 0) -> None:
        # FTX-1 ignores an out-of-range table index (the original bug).
        if 0 <= value <= 23:
            store["value"] = value

    radio.get_filter_width = AsyncMock(side_effect=_get)
    radio.set_filter_width = AsyncMock(side_effect=_set)
    return radio, store


async def test_filter_width_table_index_reacts():
    """A small table index (19) must mutate to a DIFFERENT in-range index so the
    FTX-1 accepts the change instead of ignoring an out-of-range Hz value."""
    radio, store = _stateful_filter_mock(start=19)
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 19
    assert 0 <= int(check.evidence["changed"]) <= 23
    assert check.evidence["changed"] != 19
    assert check.evidence["readback"] == check.evidence["changed"]
    assert check.evidence["restored"] is True


async def test_filter_width_table_index_low_value_reacts():
    """A table index of 0 must still mutate to a valid in-range alternate."""
    radio, _store = _stateful_filter_mock(start=0)
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert 0 <= int(check.evidence["changed"]) <= 23
    assert check.evidence["changed"] != 0


def _stateful_hz_filter_mock(*, start: int):
    """An Icom-shaped radio whose filter width is in Hz (round-trips any value)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"filter_width"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(value: int, receiver: int = 0) -> None:
        store["value"] = value

    radio.get_filter_width = AsyncMock(side_effect=_get)
    radio.set_filter_width = AsyncMock(side_effect=_set)
    return radio, store


async def test_filter_width_hz_icom_unchanged_low():
    """Icom Hz width below 2600 keeps the +200 nudge (no regression)."""
    radio, _store = _stateful_hz_filter_mock(start=2400)
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 2400
    assert check.evidence["changed"] == 2600


async def test_filter_width_hz_icom_unchanged_high():
    """Icom Hz width above 2600 keeps the -200 nudge (no regression)."""
    radio, _store = _stateful_hz_filter_mock(start=3000)
    check = await _run(radio, check_id="filter_width.set", capability="filter_width")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 3000
    assert check.evidence["changed"] == 2800


# ---------------------------------------------------------------------------
# FIX 2 — notch.set compound (bool, int) vs bool
# ---------------------------------------------------------------------------


def _stateful_compound_notch_mock(*, on: bool, freq: int = 17):
    """FTX-1-shaped notch: get returns (bool, int); set takes a bool only."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"notch"}
    store = {"on": on, "freq": freq}

    async def _get(receiver: int = 0) -> tuple[bool, int]:
        return store["on"], store["freq"]

    async def _set(state: bool, receiver: int = 0) -> None:
        store["on"] = state

    radio.get_manual_notch = AsyncMock(side_effect=_get)
    radio.set_manual_notch = AsyncMock(side_effect=_set)
    return radio, store


async def test_notch_compound_toggles_bool_only():
    """The compound (bool, int) notch must toggle/compare only the bool."""
    radio, store = _stateful_compound_notch_mock(on=False, freq=17)
    check = await _run(radio, check_id="notch.set", capability="notch")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] is False
    assert check.evidence["changed"] is True
    assert check.evidence["readback"] is True
    assert check.evidence["restored"] is True
    # The freq index must be preserved across the cycle.
    assert store["freq"] == 17
    # Restored back to original bool.
    assert store["on"] is False


async def test_notch_compound_does_not_react_fails_readback():
    """A compound notch whose bool never changes -> FAIL (READBACK)."""
    from rigplane.validation.schema import FailureDomain

    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"notch"}

    async def _get(receiver: int = 0) -> tuple[bool, int]:
        return False, 17  # bool never moves

    async def _set(state: bool, receiver: int = 0) -> None:
        return None  # no-op

    radio.get_manual_notch = AsyncMock(side_effect=_get)
    radio.set_manual_notch = AsyncMock(side_effect=_set)
    check = await _run(radio, check_id="notch.set", capability="notch")
    assert check.status is CheckStatus.FAIL
    assert check.failure_domain is FailureDomain.READBACK


def _stateful_bool_notch_mock(*, on: bool):
    """Icom-shaped notch: get/set are plain bool."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"notch"}
    store = {"on": on}

    async def _get(receiver: int = 0) -> bool:
        return store["on"]

    async def _set(state: bool, receiver: int = 0) -> None:
        store["on"] = state

    radio.get_manual_notch = AsyncMock(side_effect=_get)
    radio.set_manual_notch = AsyncMock(side_effect=_set)
    return radio, store


async def test_notch_plain_bool_icom_unchanged():
    """A plain-bool notch (Icom) still toggles and passes (no regression)."""
    radio, store = _stateful_bool_notch_mock(on=False)
    check = await _run(radio, check_id="notch.set", capability="notch")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] is False
    assert check.evidence["changed"] is True
    assert check.evidence["readback"] is True
    assert check.evidence["restored"] is True
    assert store["on"] is False


# ---------------------------------------------------------------------------
# FIX 3 — nb.set / nr.set level-getter fallback
# ---------------------------------------------------------------------------


def _level_nb_mock(*, start: int = 0):
    """FTX-1-shaped NB: only the level getter/setter exist (no get_nb)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"nb"}
    store = {"value": start}
    del radio.get_nb  # no bool getter on the Yaesu backend
    del radio.set_nb

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(level: int, receiver: int = 0) -> None:
        store["value"] = level

    radio.get_nb_level = AsyncMock(side_effect=_get)
    radio.set_nb_level = AsyncMock(side_effect=_set)
    return radio, store


def _level_nr_mock(*, start: int = 0):
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"nr"}
    store = {"value": start}
    del radio.get_nr
    del radio.set_nr

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(level: int, receiver: int = 0) -> None:
        store["value"] = level

    radio.get_nr_level = AsyncMock(side_effect=_get)
    radio.set_nr_level = AsyncMock(side_effect=_set)
    return radio, store


async def test_nb_falls_back_to_level_getter():
    """Absent get_nb -> fall back to get_nb_level/set_nb_level (set nonzero)."""
    radio, store = _level_nb_mock(start=0)
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert int(check.evidence["changed"]) > 0
    assert check.evidence["readback"] == check.evidence["changed"]
    assert check.evidence["restored"] is True
    assert store["value"] == 0  # restored


async def test_nr_falls_back_to_level_getter():
    """Absent get_nr -> fall back to get_nr_level/set_nr_level (set nonzero)."""
    radio, store = _level_nr_mock(start=0)
    check = await _run(radio, check_id="nr.set", capability="nr")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert int(check.evidence["changed"]) > 0
    assert check.evidence["readback"] == check.evidence["changed"]
    assert check.evidence["restored"] is True
    assert store["value"] == 0


async def test_nb_level_already_on_still_reacts():
    """An NB already at a nonzero level still mutates to a DIFFERENT level."""
    radio, store = _level_nb_mock(start=5)
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 5
    assert int(check.evidence["changed"]) != 5
    assert check.evidence["readback"] == check.evidence["changed"]
    assert store["value"] == 5  # restored


async def test_nb_no_level_or_bool_getter_unsupported():
    """Neither get_nb nor get_nb_level -> UNSUPPORTED (honest, not a crash)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"nb"}
    del radio.get_nb
    del radio.set_nb
    del radio.get_nb_level
    del radio.set_nb_level
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.UNSUPPORTED


def _bool_nb_mock(*, on: bool = False):
    """Icom-shaped NB: bool getter present (must keep the bool path)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"nb"}
    store = {"on": on}

    async def _get(receiver: int = 0) -> bool:
        return store["on"]

    async def _set(state: bool, receiver: int = 0) -> None:
        store["on"] = state

    radio.get_nb = AsyncMock(side_effect=_get)
    radio.set_nb = AsyncMock(side_effect=_set)
    return radio, store


async def test_nb_bool_getter_icom_unchanged():
    """Icom NB with a bool getter still uses the bool toggle path (no regression)."""
    radio, store = _bool_nb_mock(on=False)
    check = await _run(radio, check_id="nb.set", capability="nb")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] is False
    assert check.evidence["changed"] is True
    assert check.evidence["readback"] is True
    assert store["on"] is False


# ---------------------------------------------------------------------------
# FIX 4 — agc.set / xit.set named handlers exercise FTX-1 correctly
# ---------------------------------------------------------------------------


def _stateful_agc_mock(*, start: int):
    """FTX-1-shaped AGC: get returns 0-6, set accepts 0-4 (5/6 read-only)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"agc"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(mode: int, receiver: int = 0) -> None:
        # The FTX-1 AGC SET only accepts 0-4; 5/6 are read-only reflections.
        if 0 <= mode <= 4:
            store["value"] = mode

    radio.get_agc = AsyncMock(side_effect=_get)
    radio.set_agc = AsyncMock(side_effect=_set)
    return radio, store


async def test_agc_set_targets_settable_manual_mode():
    """AGC mutation flips between settable manual modes (1<->3), never 5/6."""
    radio, store = _stateful_agc_mock(start=int(AgcMode.FAST))  # 1
    check = await _run(radio, check_id="agc.set", capability="agc")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == int(AgcMode.FAST)
    assert check.evidence["changed"] == int(AgcMode.SLOW)
    assert int(check.evidence["changed"]) <= 4
    assert check.evidence["readback"] == int(AgcMode.SLOW)
    assert check.evidence["restored"] is True
    assert store["value"] == int(AgcMode.FAST)


def test_agc_flip_never_targets_readonly_auto():
    """The AGC mutation must never target a read-only auto mode (5/6),
    regardless of the value read back from the radio."""
    from rigplane.validation.hardware import _VALUE_RULE_FNS
    from rigplane.validation.registry import ValueRule

    flip = _VALUE_RULE_FNS[ValueRule.AGC_FLIP]
    for current in range(0, 7):
        assert int(flip(current)) <= 4, f"AGC flip from {current} must stay settable"


def _stateful_xit_mock(*, on: bool):
    """FTX-1-shaped XIT: get/set rit_tx_status are plain bool."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"xit"}
    store = {"on": on}

    async def _get() -> bool:
        return store["on"]

    async def _set(state: bool) -> None:
        store["on"] = state

    radio.get_rit_tx_status = AsyncMock(side_effect=_get)
    radio.set_rit_tx_status = AsyncMock(side_effect=_set)
    return radio, store


async def test_xit_set_toggles_bool():
    """xit.set toggles get_rit_tx_status/set_rit_tx_status and restores."""
    radio, store = _stateful_xit_mock(on=False)
    check = await _run(radio, check_id="xit.set", capability="xit")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] is False
    assert check.evidence["changed"] is True
    assert check.evidence["readback"] is True
    assert check.evidence["restored"] is True
    assert store["on"] is False


# ---------------------------------------------------------------------------
# MOR-672 — FTX-1 tone surface: sql_type (CT) RMVR + ctcss_tone (CN) read-only
# ---------------------------------------------------------------------------


def _stateful_sql_type_mock(*, start: int):
    """FTX-1-shaped SQL type: get/set_sql_type round-trip a 0/1/2 code.

    Mirrors the Yaesu ``CT`` SQL-type select (0=off / 1=TONE / 2=TSQL). The
    setter rejects (no-ops) any code outside 0-2 so an invalid mutation can
    never be restored from."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"sql_type"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(type_code: int, receiver: int = 0) -> None:
        if 0 <= type_code <= 2:
            store["value"] = type_code

    radio.get_sql_type = AsyncMock(side_effect=_get)
    radio.set_sql_type = AsyncMock(side_effect=_set)
    return radio, store


async def test_sql_type_rmvr_passes_and_restores():
    """sql_type.set is a real RMVR: flips TONE<->TSQL, verifies, restores."""
    radio, store = _stateful_sql_type_mock(start=1)  # TONE
    check = await _run(radio, check_id="sql_type.set", capability="sql_type")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 1
    assert check.evidence["changed"] == 2  # TSQL
    assert check.evidence["readback"] == 2
    assert check.evidence["restored"] is True
    assert store["value"] == 1  # restored


async def test_sql_type_flip_stays_in_valid_range():
    """The sql_type mutation must always land on a valid 0/1/2 code, never OOR."""
    from rigplane.validation.hardware import _VALUE_RULE_FNS
    from rigplane.validation.registry import ValueRule

    flip = _VALUE_RULE_FNS[ValueRule.SQL_TYPE_CYCLE]
    for current in range(0, 3):
        changed = int(flip(current))
        assert 0 <= changed <= 2, f"sql_type flip from {current} must stay 0-2"
        assert changed != current, f"sql_type flip from {current} must change"


async def test_sql_type_from_off_reacts():
    """Starting at OFF (0), the flip lands on a valid active code and restores."""
    radio, store = _stateful_sql_type_mock(start=0)  # off
    check = await _run(radio, check_id="sql_type.set", capability="sql_type")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert int(check.evidence["changed"]) in (1, 2)
    assert check.evidence["readback"] == check.evidence["changed"]
    assert store["value"] == 0  # restored


async def test_ctcss_tone_read_resolves_read_only():
    """ctcss_tone.read is a READ_ONLY check via get_ctcss_tone — no setter used."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"sql_type"}
    radio.get_ctcss_tone = AsyncMock(return_value=8850)  # 88.5 Hz in centiHz
    # No set_ctcss_tone exists on the backend; the read check must not need it.
    check = await _run(radio, check_id="ctcss_tone.read", capability="sql_type")
    assert check.status is CheckStatus.PASS
    assert check.evidence["value"] == 8850
    assert check.evidence["op"] == "get_ctcss_tone"
    radio.get_ctcss_tone.assert_awaited_once()


async def test_ctcss_tone_read_unsupported_without_getter():
    """Absent get_ctcss_tone -> UNSUPPORTED (honest), not a crash."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"sql_type"}
    del radio.get_ctcss_tone
    check = await _run(radio, check_id="ctcss_tone.read", capability="sql_type")
    assert check.status is CheckStatus.UNSUPPORTED
