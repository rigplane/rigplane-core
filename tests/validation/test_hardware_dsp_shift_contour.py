"""RMVR validation for FTX-1 IF-shift and contour DSP controls (MOR-671).

``if_shift.set`` and ``contour.set`` previously had only synthetic
``<cap>.presence`` entries. These tests lock the new read-modify-verify-restore
behaviour driven entirely by the generic registry dispatch:

* ``if_shift.set`` nudges the signed-Hz offset by a clamped delta that NEVER
  leaves the +/-1200 Hz band, verifies the readback reacted, and restores.
* ``contour.set`` flips the on/off (0 / >0) DSP state, verifies, and restores.

A radio that *declares* the capability but lacks the get/set op must degrade to
UNSUPPORTED (not crash / FAIL), cross-radio-safe for Icom radios that share the
DSP surface but never expose these Yaesu-only ops.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rigplane.core.radio_protocol import Radio
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
# if_shift.set — signed Hz, clamped RMVR
# ---------------------------------------------------------------------------


def _stateful_if_shift_mock(*, start: int):
    """FTX-1-shaped IF-shift: round-trips any value in the +/-1200 Hz band."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"if_shift"}
    store = {"value": start}
    writes: list[int] = []

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(offset: int, receiver: int = 0) -> None:
        writes.append(offset)
        store["value"] = offset

    radio.get_if_shift = AsyncMock(side_effect=_get)
    radio.set_if_shift = AsyncMock(side_effect=_set)
    return radio, store, writes


async def test_if_shift_rmvr_passes_and_restores():
    """A mid-band offset nudges to a DIFFERENT in-range value, reacts, restores."""
    radio, store, writes = _stateful_if_shift_mock(start=0)
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 200
    assert check.evidence["readback"] == 200
    assert check.evidence["restored"] is True
    # Restored to the original value.
    assert store["value"] == 0
    # Every written value stayed strictly inside the +/-1200 Hz band.
    assert all(-1200 <= w <= 1200 for w in writes)


async def test_if_shift_rmvr_clamps_near_upper_bound():
    """Near the +1200 ceiling the nudge must flip downward, never exceeding it."""
    radio, store, writes = _stateful_if_shift_mock(start=1100)
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.PASS
    # +200 would overshoot 1200, so the rule steps -200 instead.
    assert check.evidence["changed"] == 900
    assert check.evidence["changed"] != 1100
    assert store["value"] == 1100
    # NEVER write out of range, even at the boundary.
    assert all(-1200 <= w <= 1200 for w in writes)
    assert max(writes) <= 1200


async def test_if_shift_rmvr_at_ceiling_clamps_down():
    """Exactly at +1200 the rule still produces an in-range, different value."""
    radio, _store, writes = _stateful_if_shift_mock(start=1200)
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.PASS
    assert check.evidence["changed"] == 1000
    assert all(-1200 <= w <= 1200 for w in writes)


# ---------------------------------------------------------------------------
# contour.set — off <-> on RMVR
# ---------------------------------------------------------------------------


def _stateful_contour_mock(*, start: int):
    """FTX-1-shaped contour: 0=off, >0=on; round-trips any value."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "FTX-1"
    radio.capabilities = {"contour"}
    store = {"value": start}

    async def _get(receiver: int = 0) -> int:
        return store["value"]

    async def _set(val: int, receiver: int = 0) -> None:
        store["value"] = val

    radio.get_contour = AsyncMock(side_effect=_get)
    radio.set_contour = AsyncMock(side_effect=_set)
    return radio, store


async def test_contour_rmvr_off_to_on_passes_and_restores():
    """Contour off (0) flips to a valid on level (1), reacts, restores to 0."""
    radio, store = _stateful_contour_mock(start=0)
    check = await _run(radio, check_id="contour.set", capability="contour")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 0
    assert check.evidence["changed"] == 1
    assert check.evidence["readback"] == 1
    assert check.evidence["restored"] is True
    assert store["value"] == 0


async def test_contour_rmvr_on_to_off_passes_and_restores():
    """Contour on (1) flips to off (0), reacts, restores to the original on value."""
    radio, store = _stateful_contour_mock(start=1)
    check = await _run(radio, check_id="contour.set", capability="contour")
    assert check.status is CheckStatus.PASS
    assert check.evidence["original"] == 1
    assert check.evidence["changed"] == 0
    assert check.evidence["readback"] == 0
    assert check.evidence["restored"] is True
    assert store["value"] == 1


# ---------------------------------------------------------------------------
# Cross-radio safety: declares the cap but lacks the op -> UNSUPPORTED
# ---------------------------------------------------------------------------


async def test_if_shift_declared_but_missing_op_is_unsupported():
    """A radio declaring ``if_shift`` but exposing no get/set op degrades to
    UNSUPPORTED, never crashing or FAILing (Icom shares the DSP surface but has
    no IF-shift op)."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"if_shift"}
    # No get_if_shift / set_if_shift: spec(Radio) excludes the Yaesu-only ops.
    check = await _run(radio, check_id="if_shift.set", capability="if_shift")
    assert check.status is CheckStatus.UNSUPPORTED


async def test_contour_declared_but_missing_op_is_unsupported():
    """A radio declaring ``contour`` but exposing no get/set op degrades to
    UNSUPPORTED, never crashing or FAILing."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"contour"}
    check = await _run(radio, check_id="contour.set", capability="contour")
    assert check.status is CheckStatus.UNSUPPORTED
