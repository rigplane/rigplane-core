"""Per-check crash containment for the hardware runner (MOR-659).

The first live IC-7610 validation run crashed and discarded ALL results: the
radio's repeater tone read back as 16.5 Hz (tone not configured), which is
below the encoder's settable band (67.0-254.1 Hz, ``commands/tone.py``
``_encode_tone_freq``), so the RMVR restore raised a bare ``ValueError`` that
escaped ``_guard``, the restore ``finally`` and ``execute_hardware_checks``.

These tests pin the four containment layers:

* ``_guard`` maps ``ValueError``/``TypeError`` to a per-check
  COMMAND_EXECUTION FAIL;
* ``_RESTORE_ERRORS`` includes ``ValueError``/``TypeError``;
* ``execute_hardware_checks`` has a per-entry backstop so one raising check
  can never abort the matrix or lose the artifact;
* ``tone_freq.set``/``tsql_freq.set`` SKIP without mutating when the current
  value is outside the settable band (non-destructive harness).
"""

from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.radio_protocol import Radio
from rigplane.validation import hardware
from rigplane.validation.hardware import (
    _RESTORE_ERRORS,
    _guard,
    execute_hardware_checks,
)
from rigplane.validation.registry import REGISTRY_BY_ID, CheckKind
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckStatus,
    FailureDomain,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
)

# Settable band of commands/tone.py::_encode_tone_freq (shared by
# set_tone_freq and set_tsql_freq).
_TONE_BAND_MIN = 67.0
_TONE_BAND_MAX = 254.1

# The IC-7610's readback when no repeater tone is configured: un-encodable.
_UNENCODABLE_TONE = 16.5


def _flatten(levels):
    return {check.check_id: check for level in levels for check in level.checks}


def _entry_for(check_id: str) -> CapabilityDeclarationEntry:
    spec = REGISTRY_BY_ID[check_id]
    if spec.kind in (CheckKind.MANUAL, CheckKind.TX_ADJACENT_BLOCKED):
        declaration = CapabilityDeclaration.MANUAL_REQUIRED
    else:
        declaration = CapabilityDeclaration.SUPPORTED
    return CapabilityDeclarationEntry(
        check_id=spec.check_id,
        capability=spec.capability,
        level=spec.level,
        declaration=declaration,
        summary=spec.summary,
        tx_adjacent=spec.tx_adjacent,
    )


def _template_for(*check_ids: str) -> MatrixTemplate:
    return MatrixTemplate(
        radio=RadioTarget(model="IC-7610", profile_id="ic7610"),
        entries=[_entry_for(check_id) for check_id in check_ids],
    )


def _encoder_guarded_setter(store: dict):
    """A setter that raises like commands/tone.py::_encode_tone_freq."""

    async def _set(freq_hz: float, receiver: int = 0) -> None:
        if not _TONE_BAND_MIN <= freq_hz <= _TONE_BAND_MAX:
            raise ValueError(f"Tone frequency must be 67.0-254.1 Hz, got {freq_hz}")
        store["value"] = freq_hz
        store["writes"].append(freq_hz)

    return _set


def _unconfigured_tone_radio():
    """A fake IC-7610 whose tone/TSQL frequencies read back un-encodable."""
    radio = MagicMock(spec=Radio)
    radio.connected = True
    radio.model = "IC-7610"
    radio.capabilities = {"repeater_tone", "tsql"}
    tone_store: dict = {"value": _UNENCODABLE_TONE, "writes": []}
    tsql_store: dict = {"value": _UNENCODABLE_TONE, "writes": []}

    async def _get_tone(receiver: int = 0) -> float:
        return tone_store["value"]

    async def _get_tsql(receiver: int = 0) -> float:
        return tsql_store["value"]

    radio.get_tone_freq = AsyncMock(side_effect=_get_tone)
    radio.set_tone_freq = AsyncMock(side_effect=_encoder_guarded_setter(tone_store))
    radio.get_tsql_freq = AsyncMock(side_effect=_get_tsql)
    radio.set_tsql_freq = AsyncMock(side_effect=_encoder_guarded_setter(tsql_store))

    repeater_store: dict = {"value": False, "writes": []}

    async def _get_repeater(receiver: int = 0) -> bool:
        return repeater_store["value"]

    async def _set_repeater(on: bool, receiver: int = 0) -> None:
        repeater_store["value"] = on
        repeater_store["writes"].append(on)

    radio.get_repeater_tone = AsyncMock(side_effect=_get_repeater)
    radio.set_repeater_tone = AsyncMock(side_effect=_set_repeater)

    freq_store = {"value": 14_074_000}

    async def _get_freq(receiver: int = 0) -> int:
        return freq_store["value"]

    radio.get_freq = AsyncMock(side_effect=_get_freq)
    return radio, tone_store, tsql_store


# ---------------------------------------------------------------------------
# Fix 4 — un-encodable original value: SKIP without mutating, run completes
# ---------------------------------------------------------------------------


async def test_unencodable_tone_freq_never_crashes_the_run():
    radio, tone_store, tsql_store = _unconfigured_tone_radio()
    template = _template_for(
        "discovery.identify", "repeater_tone.set", "tone_freq.set", "tsql_freq.set"
    )
    # (a) must not raise — one un-encodable value cannot abort the matrix.
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    results = _flatten(levels)
    # (b) every template entry produced a result (artifact producible).
    assert set(results) == {
        "discovery.identify",
        "repeater_tone.set",
        "tone_freq.set",
        "tsql_freq.set",
    }
    # (c) the freq checks SKIP non-destructively, they do not crash or FAIL.
    for check_id in ("tone_freq.set", "tsql_freq.set"):
        result = results[check_id]
        assert result.status is CheckStatus.SKIP, check_id
        assert result.evidence["original"] == _UNENCODABLE_TONE
        assert "non-destructive" in str(result.evidence["reason"])
    # (d) the radio was NEVER mutated: no write reached either setter.
    assert tone_store["writes"] == []
    assert tsql_store["writes"] == []
    # Unrelated checks still ran normally.
    assert results["discovery.identify"].status is CheckStatus.PASS
    assert results["repeater_tone.set"].status is CheckStatus.PASS


async def test_encodable_tone_freq_still_runs_rmvr():
    """The restorable gate must not affect in-band values."""
    radio, tone_store, _ = _unconfigured_tone_radio()
    tone_store["value"] = 88.5
    levels = await execute_hardware_checks(
        radio,
        _template_for("tone_freq.set"),
        OperatorSafetyBlock(),
        allow_writes=True,
    )
    result = _flatten(levels)["tone_freq.set"]
    assert result.status is CheckStatus.PASS
    assert tone_store["value"] == 88.5  # restored
    assert 100.0 in tone_store["writes"]


# ---------------------------------------------------------------------------
# Fix 1 — _guard maps ValueError/TypeError to a per-check FAIL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc_type", [ValueError, TypeError])
async def test_guard_maps_bare_value_and_type_errors_to_fail(exc_type):
    async def _raises():
        raise exc_type("Tone frequency must be 67.0-254.1 Hz, got 16.5")

    entry = _entry_for("tone_freq.set")
    value, failure = await _guard(_raises(), entry, per_check_timeout=1.0)
    assert value is None
    assert failure is not None
    assert failure.status is CheckStatus.FAIL
    assert failure.failure_domain is FailureDomain.COMMAND_EXECUTION
    assert "16.5" in (failure.error or "")


async def test_guard_does_not_swallow_keyboard_interrupt():
    async def _raises():
        raise KeyboardInterrupt

    entry = _entry_for("tone_freq.set")
    with pytest.raises(KeyboardInterrupt):
        await _guard(_raises(), entry, per_check_timeout=1.0)


# ---------------------------------------------------------------------------
# Fix 2 — _RESTORE_ERRORS covers ValueError/TypeError
# ---------------------------------------------------------------------------


def test_restore_errors_include_value_and_type_errors():
    assert ValueError in _RESTORE_ERRORS
    assert TypeError in _RESTORE_ERRORS


# ---------------------------------------------------------------------------
# Fix 3 — execute_hardware_checks backstop
# ---------------------------------------------------------------------------


async def test_backstop_contains_unexpected_check_exception(monkeypatch):
    """An exception escaping a single check handler must become an errored
    result, not abort the loop — every other entry still produces a result."""

    async def _explodes(*args, **kwargs):
        raise RuntimeError("handler blew up unexpectedly")

    monkeypatch.setitem(hardware._SUPPORTED_HANDLERS, "freq.write", _explodes)

    radio, _, _ = _unconfigured_tone_radio()
    template = _template_for("discovery.identify", "freq.write", "repeater_tone.set")
    levels = await execute_hardware_checks(
        radio, template, OperatorSafetyBlock(), allow_writes=True
    )
    results = _flatten(levels)
    assert set(results) == {"discovery.identify", "freq.write", "repeater_tone.set"}
    errored = results["freq.write"]
    assert errored.status is CheckStatus.FAIL
    assert errored.failure_domain is not None
    assert errored.error == repr(RuntimeError("handler blew up unexpectedly"))
    assert errored.started_at and errored.finished_at
    # The entries before and after the exploding one ran normally.
    assert results["discovery.identify"].status is CheckStatus.PASS
    assert results["repeater_tone.set"].status is CheckStatus.PASS


async def test_backstop_does_not_swallow_cancellation(monkeypatch):
    """CancelledError is BaseException on 3.11+ and must propagate."""

    async def _cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setitem(hardware._SUPPORTED_HANDLERS, "freq.write", _cancelled)

    radio, _, _ = _unconfigured_tone_radio()
    with pytest.raises(asyncio.CancelledError):
        await execute_hardware_checks(
            radio,
            _template_for("freq.write"),
            OperatorSafetyBlock(),
            allow_writes=True,
        )
