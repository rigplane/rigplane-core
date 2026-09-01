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

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
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
    RmvrOutcome,
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
# MOR-2086 review round — attenuator.set: route by declared domain, not by
# radio type. An earlier version of that fix routed every profile-backed
# radio onto get_attenuator_level/set_attenuator_level unconditionally.
# ftx1.toml declares [attenuator] values = [0, 1] -- a truthy,
# profile-backed domain -- so the FTX-1 took that path too, and
# YaesuCatRadio.get_attenuator_level raises NotImplementedError("Attenuator
# level (Icom) not supported on Yaesu radios"), downgrading attenuator.set
# from PASS to UNSUPPORTED. Same shape as FIX 3's nb.set/nr.set downgrade.
# The fix: a profile declaring at most one non-zero value (FTX-1's [0, 1]
# included) makes the boolean form exactly equivalent to the level form, so
# the check stays on get_attenuator/set_attenuator and never calls the
# level API at all. Only a profile declaring more than one non-zero value
# (IC-7610's 3..45 dB steps, always Icom-backed) moves onto the level API.
# ---------------------------------------------------------------------------


def _ftx1_attenuator_mock(*, on: bool = False):
    """FTX-1-shaped attenuator: boolean get/set present and working; the
    level API exists but raises NotImplementedError, exactly like the real
    YaesuCatRadio.get_attenuator_level."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"attenuator"}
    radio.profile = SimpleNamespace(att_values=(0, 1))
    store = {"on": on}

    async def _get(receiver: int = 0) -> bool:
        return store["on"]

    async def _set(state: bool, receiver: int = 0) -> None:
        store["on"] = state

    async def _get_level(receiver: int = 0) -> int:
        raise NotImplementedError(
            "Attenuator level (Icom) not supported on Yaesu radios"
        )

    radio.get_attenuator = AsyncMock(side_effect=_get)
    radio.set_attenuator = AsyncMock(side_effect=_set)
    radio.get_attenuator_level = AsyncMock(side_effect=_get_level)
    return radio, store


async def test_attenuator_ftx1_stays_on_boolean_path():
    """FTX-1 declares a single non-zero attenuator value: the boolean form
    is exactly equivalent, so the check must PASS without ever calling
    get_attenuator_level -- which raises on the real backend."""
    radio, store = _ftx1_attenuator_mock(on=False)
    check = await _run(radio, check_id="attenuator.set", capability="attenuator")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] is False
    assert check.evidence["changed"] is True
    assert check.evidence["readback"] is True
    assert store["on"] is False  # restored
    radio.get_attenuator_level.assert_not_awaited()


def _ic7610_attenuator_mock(*, level: int = 0):
    """IC-7610-shaped attenuator: stepped domain, no bool getter/setter at
    all -- the level API is the only correct path."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"attenuator"}
    radio.profile = SimpleNamespace(
        att_values=(0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45)
    )
    del radio.get_attenuator
    del radio.set_attenuator
    store = {"level": level}

    async def _get(receiver: int = 0) -> int:
        return store["level"]

    async def _set(db: int, receiver: int = 0) -> None:
        store["level"] = db

    radio.get_attenuator_level = AsyncMock(side_effect=_get)
    radio.set_attenuator_level = AsyncMock(side_effect=_set)
    return radio, store


async def test_attenuator_ic7610_stepped_stays_on_level_path():
    """IC-7610 declares 15 non-zero steps: the boolean form cannot
    disambiguate them, so this must stay on the level API (no regression
    on the radio the stepped case actually protects)."""
    radio, store = _ic7610_attenuator_mock(level=0)
    check = await _run(radio, check_id="attenuator.set", capability="attenuator")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] != 0
    assert check.evidence["readback"] == check.evidence["changed"]
    assert store["level"] == 0  # restored


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


# ---------------------------------------------------------------------------
# MOR-2103 — write-path ``?;`` rejection: the three-way RMVR outcome
# ---------------------------------------------------------------------------


def test_rmvr_outcome_is_pinned() -> None:
    """A fourth outcome must not appear unnoticed (mirrors
    tests/test_tx_authority.py: test_engine_failure_tag_set_is_pinned)."""
    assert {member.value for member in RmvrOutcome} == {
        "rejected",
        "ignored",
        "timed_out",
    }


#
# Drives a REAL YaesuCatTransport + YaesuCatRadio (not a MagicMock(spec=Radio),
# unlike the fixtures above) against a scripted serial wire, so the fix under
# test -- YaesuCatTransport._drain_responses inspecting the drained line for
# "?;" -- runs for real. tests/tx_authority_fakes.py: ScriptedCatTransport is
# a pattern to copy, not an object to reuse here: it fakes the whole
# transport and its write() never raises. The rejection below comes from
# this fake's own scripting, independent of any shipped rigs/ftx1.toml entry
# (sibling ticket MOR-2104 fixed sql_type's CAT write-template width there,
# merged as 4efe071a).


class _ScriptedModeWire:
    """Fake serial wire understanding only the FTX-1 ``MD0`` mode command.

    Assigned as both ``_reader`` and ``_writer`` on a real
    ``YaesuCatTransport`` instance. ``behavior`` selects one of the three
    RMVR outcomes under test:

    * ``"reject"`` -- every ``MD0{code};`` SET gets ``?;`` back, regardless
      of the value, so the initial write AND the RMVR restore-write both
      hit the same rejection.
    * ``"ignore"`` -- a SET gets silence (the real accepted-write response,
      bench-measured for MOR-2103) but the stored mode code never changes.
    * ``"hang"`` -- any SET's ``drain()`` blocks until cancelled, so the
      write leg itself times out at ``_guard``'s per-check timeout.
      ``YaesuCatTransport._raw_write`` awaits the writer's ``drain()``
      before ``_drain_responses`` ever runs and with no timeout of its own
      -- unlike the post-write response read, which the transport bounds
      with its own 30 ms drain window -- so a stalled writer genuinely
      reaches ``_guard``'s per-check timeout on the write coroutine, not
      only on a read.
    """

    def __init__(self, *, behavior: str, initial_code: str) -> None:
        self.behavior = behavior
        self.code = initial_code
        self._last_write = b""
        self.closed = False

    # -- StreamWriter side ---------------------------------------------
    def write(self, data: bytes) -> None:
        self._last_write = data

    async def drain(self) -> None:
        if self.behavior == "hang" and self._last_write != b"MD0;":
            await asyncio.sleep(30)  # cut off by _guard's per-check timeout

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    # -- StreamReader side ---------------------------------------------
    async def readuntil(self, separator: bytes) -> bytes:
        cmd = self._last_write.decode("ascii")
        if cmd == "MD0;":  # GET
            return f"MD0{self.code};".encode("ascii")
        if cmd.startswith("MD0") and cmd.endswith(";") and cmd != "MD0;":  # SET
            if self.behavior == "reject":
                return b"?;"
            if self.behavior != "ignore":
                self.code = cmd[3:-1]
            raise asyncio.TimeoutError("accepted write: silence")
        raise asyncio.TimeoutError(f"unscripted CAT frame: {cmd!r}")


def _scripted_mode_radio(wire: _ScriptedModeWire) -> YaesuCatRadio:
    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    radio._transport._connected = True
    radio._transport._reader = wire
    radio._transport._writer = wire
    return radio


async def test_mode_set_rejected_reports_outcome_and_frame():
    """A Yaesu ``?;`` on the write surfaces as outcome=rejected, naming the
    rejected frame in ``check.error`` -- not the old 'control did not react'
    guess (MOR-2103)."""
    wire = _ScriptedModeWire(behavior="reject", initial_code="1")  # LSB
    radio = _scripted_mode_radio(wire)
    check = await _run(radio, check_id="mode.set", capability="")
    assert check.status is CheckStatus.FAIL
    assert check.evidence["outcome"] == "rejected"
    assert "MD02;" in check.error  # the rejected SET frame (LSB -> USB)


async def test_mode_set_ignored_reports_outcome():
    """A write that returns cleanly but never moves the readback is a
    genuinely different diagnosis from a rejection (MOR-2103)."""
    wire = _ScriptedModeWire(behavior="ignore", initial_code="1")  # LSB
    radio = _scripted_mode_radio(wire)
    check = await _run(radio, check_id="mode.set", capability="")
    assert check.status is CheckStatus.FAIL
    assert check.evidence["outcome"] == "ignored"
    assert check.error == "control did not react: readback equals original"


async def test_mode_set_timed_out_reports_outcome():
    """A per-check ``_guard`` timeout on the write coroutine itself (a
    stalled writer, MOR-2103) is the third, distinct outcome -- not the
    transport's own 30 ms post-write drain silence, which is the healthy
    accepted-write path."""
    wire = _ScriptedModeWire(behavior="hang", initial_code="1")  # LSB
    radio = _scripted_mode_radio(wire)
    template = _single_entry_template(check_id="mode.set", capability="")
    levels = await execute_hardware_checks(
        radio,
        template,
        OperatorSafetyBlock(),
        allow_writes=True,
        per_check_timeout=0.05,
    )
    check = _flatten(levels)["mode.set"]
    assert check.status is CheckStatus.FAIL
    assert check.evidence["outcome"] == "timed_out"


async def test_mode_set_local_value_error_is_not_rejected():
    """A local encoder ``ValueError`` never reached the radio at all -- it
    must not be labelled outcome=rejected (MOR-2103). Uses a
    ``MagicMock(spec=Radio)`` directly, not the scripted wire: the failure
    originates in the radio call itself, before any frame would be sent."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = set()
    radio.get_mode = AsyncMock(return_value=("LSB", None))
    radio.set_mode = AsyncMock(side_effect=ValueError("mode 'XYZ' out of range"))
    check = await _run(radio, check_id="mode.set", capability="")
    assert check.status is CheckStatus.FAIL
    assert "outcome" not in check.evidence


async def test_mode_set_readback_parse_error_is_not_rejected():
    """The worst case: the SET was accepted and the radio answered
    correctly -- a parse-template mismatch on the verify read is our own
    bug, not a radio rejection (MOR-2103). A width mismatch like this is
    the same class of defect MOR-2104 fixed for sql_type's write
    template -- this scenario is its parse-template analogue, not the
    same bug."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = set()
    radio.get_mode = AsyncMock(
        side_effect=[
            ("LSB", None),  # original read
            ValueError("Parse error for 'MD0{mode};' against 'MD;'"),  # verify read
            ("LSB", None),  # restore read
        ]
    )
    radio.set_mode = AsyncMock()  # both the changed-write and the restore succeed
    check = await _run(radio, check_id="mode.set", capability="")
    assert check.status is CheckStatus.FAIL
    assert "outcome" not in check.evidence
    assert check.evidence["restored"] is True
