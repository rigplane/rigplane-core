"""Hardware-execution path for the ``rigplane validate`` vertical.

Given an *already-connected* :class:`~rigplane.core.radio_protocol.Radio`,
execute the planned checks in a :class:`MatrixTemplate` against the live
radio and return per-level :class:`LevelResult` evidence.

Safety posture (read/write by default, with automatic restore):

* RX-safe write checks default to a read-modify-verify-restore (RMVR) cycle:
  read the original value, write a different value, verify the readback, then
  always restore the original in a ``finally`` block that never raises.
* When ``allow_writes`` is ``False`` (the CLI ``--read-only`` flag) every write
  check is SKIPPED — only pure-read checks (``discovery.identify``,
  ``freq.reverse_sync``) and observation-only checks run.
* TX-adjacent checks (``tx.ptt``, ``tuner.tune``) are BLOCKED without operator
  authorization and only ever reported as ``MANUAL_REQUIRED`` when authorized
  — they are NEVER actuated (no PTT keying, no tune cycle).

This module imports only the standard library, ``rigplane.core.*`` and
``rigplane.validation.*`` — it must not depend on the CLI, backends, or
runtime layers. The caller owns connection lifecycle.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from rigplane.core.exceptions import (
    AuthenticationError,
    CommandError,
    ConnectionError as RigConnectionError,
    RigplaneError,
    TimeoutError as RigTimeoutError,
)
from rigplane.core.radio_protocol import (
    AntennaControlCapable,
    AudioCapable,
    DspControlCapable,
    LevelsCapable,
    Radio,
    RitXitCapable,
    ScopeCapable,
    SystemControlCapable,
    UsbAudioCapable,
)
from rigplane.core.types import AgcMode
from rigplane.validation.registry import CheckKind, CheckSpec, ValueRule, get_spec
from rigplane.validation.runner import _is_authorized
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckResult,
    CheckStatus,
    FailureDomain,
    LevelResult,
    MatrixTemplate,
    OperatorSafetyBlock,
    ValidationLevel,
)

__all__ = ["execute_hardware_checks", "DEFAULT_PER_CHECK_TIMEOUT"]

DEFAULT_PER_CHECK_TIMEOUT = 5.0

_LOGGER = logging.getLogger("rigplane.validation")

T = TypeVar("T")

# Exceptions handled by the best-effort restore path; never escapes ``finally``.
_RESTORE_ERRORS = (
    asyncio.TimeoutError,
    RigTimeoutError,
    RigplaneError,
    RigConnectionError,
    AuthenticationError,
    CommandError,
    OSError,
)

# Capability tag -> capability Protocol used for runtime ``isinstance`` checks.
# ``tx`` intentionally has no protocol (tag-only).
_CAP_PROTOCOL: dict[str, type] = {
    "audio": AudioCapable,
    "scope": ScopeCapable,
    "tuner": SystemControlCapable,
    "rf_gain": LevelsCapable,
    "af_level": LevelsCapable,
    "squelch": LevelsCapable,
    "nb": LevelsCapable,
    "nr": LevelsCapable,
    "attenuator": AntennaControlCapable,
    "preamp": AntennaControlCapable,
    "antenna": AntennaControlCapable,
    "rx_antenna": AntennaControlCapable,
    "filter_width": DspControlCapable,
    "agc": DspControlCapable,
    "notch": DspControlCapable,
    "apf": DspControlCapable,
    "pbt": DspControlCapable,
    "digisel": DspControlCapable,
    "ip_plus": DspControlCapable,
    "rit": RitXitCapable,
    "xit": RitXitCapable,
}


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 millisecond ``...Z`` string."""
    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


async def execute_hardware_checks(
    radio: Radio,
    template: MatrixTemplate,
    safety: OperatorSafetyBlock,
    *,
    allow_writes: bool,
    per_check_timeout: float = DEFAULT_PER_CHECK_TIMEOUT,
) -> list[LevelResult]:
    """Run a template's checks against an already-connected ``radio``.

    Iterates ``template.entries`` in order, producing one :class:`CheckResult`
    per entry, then groups results by validation level (ascending, empty
    levels omitted, original order preserved within a level). Each result is
    stamped with ISO-8601 ``started_at``/``finished_at`` timestamps and a single
    INFO log line is emitted per check. The radio is assumed connected; this
    function neither connects nor disconnects.
    """
    by_level: dict[ValidationLevel, list[CheckResult]] = {}
    for entry in template.entries:
        started = _utcnow_iso()
        result = await _run_one_check(
            radio,
            entry,
            safety,
            allow_writes=allow_writes,
            per_check_timeout=per_check_timeout,
        )
        finished = _utcnow_iso()
        result = dataclasses.replace(result, started_at=started, finished_at=finished)
        _LOGGER.info(
            "validate check %s -> %s (%s)",
            result.check_id,
            result.status.value,
            finished,
        )
        if result.status in {CheckStatus.FAIL, CheckStatus.BLOCKED}:
            _LOGGER.info(
                "validate check %s failure domain=%s error=%s",
                result.check_id,
                result.failure_domain.value if result.failure_domain else None,
                result.error,
            )
        by_level.setdefault(entry.level, []).append(result)
    return [
        LevelResult(level=level, checks=by_level[level]) for level in sorted(by_level)
    ]


def _capability_present(radio: Radio, entry: CapabilityDeclarationEntry) -> bool | None:
    """Return whether ``entry``'s capability is present, or None if untagged."""
    if not entry.capability:
        return None
    if entry.capability in radio.capabilities:
        return True
    proto = _CAP_PROTOCOL.get(entry.capability)
    if proto is not None and isinstance(radio, proto):
        return True
    return False


def _base_result(
    entry: CapabilityDeclarationEntry,
    status: CheckStatus,
    *,
    failure_domain: FailureDomain | None = None,
    evidence: dict[str, object] | None = None,
    error: str | None = None,
) -> CheckResult:
    """Build a :class:`CheckResult` carrying ``entry``'s static fields."""
    return CheckResult(
        check_id=entry.check_id,
        capability=entry.capability,
        level=entry.level,
        status=status,
        declaration=entry.declaration,
        summary=entry.summary,
        failure_domain=failure_domain,
        evidence=evidence or {},
        error=error,
    )


async def _run_one_check(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    safety: OperatorSafetyBlock,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    """Execute a single template entry, applying universal pre-gates first."""
    # Pre-gate 1: authorization for TX-adjacent checks.
    if entry.tx_adjacent and not _is_authorized(entry, safety):
        return _base_result(
            entry,
            CheckStatus.BLOCKED,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            evidence={"reason": "operator authorization required"},
        )

    # Pre-gate 2: manual-required (authorized / non-TX-adjacent).
    if entry.declaration == CapabilityDeclaration.MANUAL_REQUIRED:
        return _manual_required_result(radio, entry)

    # Pre-gate 3: declared unsupported pending evidence.
    if entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE:
        evidence: dict[str, object] = {"declared": "unsupported_pending_evidence"}
        present = _capability_present(radio, entry)
        if present is not None and entry.check_id == "scope.capture":
            evidence["scope_capability_present"] = present
        elif present is not None:
            evidence["capability_present"] = present
        return _base_result(entry, CheckStatus.UNSUPPORTED, evidence=evidence)

    # Pre-gate 4: SUPPORTED -> check-specific logic.
    handler = _SUPPORTED_HANDLERS.get(entry.check_id)
    if handler is None:
        spec = get_spec(entry.check_id)
        if spec is not None:
            return await _check_from_spec(
                radio,
                entry,
                spec,
                allow_writes=allow_writes,
                per_check_timeout=per_check_timeout,
            )
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={"reason": f"no hardware handler for check_id '{entry.check_id}'"},
        )
    return await handler(
        radio,
        entry,
        allow_writes=allow_writes,
        per_check_timeout=per_check_timeout,
    )


def _manual_required_result(
    radio: Radio, entry: CapabilityDeclarationEntry
) -> CheckResult:
    """Build a MANUAL_REQUIRED result with read-only evidence enrichment.

    Performs NO actuation: never keys TX, never triggers a tune cycle, never
    starts a stream.
    """
    if entry.check_id == "audio.rx":
        present = (
            isinstance(radio, AudioCapable | UsbAudioCapable)
            or "audio" in radio.capabilities
        )
        return _base_result(
            entry,
            CheckStatus.MANUAL_REQUIRED,
            evidence={"audio_capability_present": present},
        )
    if entry.check_id == "tuner.tune":
        return _base_result(
            entry,
            CheckStatus.MANUAL_REQUIRED,
            evidence={
                "tuner_status": radio.radio_state.tuner_status,
                "note": "tune cycle not auto-run (keys TX)",
            },
        )
    if entry.check_id == "tx.ptt":
        return _base_result(
            entry,
            CheckStatus.MANUAL_REQUIRED,
            evidence={
                "ptt": radio.radio_state.ptt,
                "note": "PTT not auto-keyed",
            },
        )
    if entry.check_id == "scope.capture":
        scope_present = _capability_present(radio, entry)
        evidence: dict[str, object] = {}
        if scope_present is not None:
            evidence["scope_capability_present"] = scope_present
        return _base_result(entry, CheckStatus.MANUAL_REQUIRED, evidence=evidence)
    return _base_result(entry, CheckStatus.MANUAL_REQUIRED)


async def _guard(
    coro: Awaitable[T],
    entry: CapabilityDeclarationEntry,
    *,
    per_check_timeout: float,
) -> tuple[T | None, CheckResult | None]:
    """Await a radio coroutine, mapping failures to a :class:`CheckResult`.

    Returns ``(value, None)`` on success or ``(None, failure_result)`` on any
    handled error. Never swallows ``KeyboardInterrupt``/``SystemExit``.
    """
    try:
        value = await asyncio.wait_for(coro, timeout=per_check_timeout)
        return value, None
    except (asyncio.TimeoutError, RigTimeoutError):
        return None, _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            error=f"timeout after {per_check_timeout}s",
        )
    except (RigConnectionError, AuthenticationError) as exc:
        return None, _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.TRANSPORT,
            error=str(exc),
        )
    except CommandError as exc:
        return None, _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            error=str(exc),
        )
    except RigplaneError as exc:
        return None, _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            error=str(exc),
        )


def _default_equal(a: T, b: T) -> bool:
    """Default equality comparator for RMVR readbacks."""
    return bool(a == b)


def _tolerant_equal(tol: int) -> Callable[[int, int], bool]:
    """Build a comparator treating values within ``tol`` as equal.

    Used for analog level controls where a real radio may quantize a written
    value, so an exact readback is not guaranteed. Evidence still records the
    exact original/changed/readback values.
    """

    def _eq(a: int, b: int) -> bool:
        return abs(int(a) - int(b)) <= tol

    return _eq


async def _read_modify_verify_restore(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    read: Callable[[], Awaitable[T]],
    write: Callable[[T], Awaitable[None]],
    make_changed: Callable[[T], T],
    per_check_timeout: float,
    equal: Callable[[T, T], bool] = _default_equal,
    extra_evidence: dict[str, object] | None = None,
) -> CheckResult:
    """Read-modify-verify-restore an RX-safe control and report the outcome.

    Reads the original value, writes a different value, verifies the readback
    reacted, and always restores the original in a ``finally`` that never
    raises. The original is restored even if the write or readback failed.
    """
    original, fail = await _guard(read(), entry, per_check_timeout=per_check_timeout)
    if fail is not None:
        return fail
    # ``original`` is a valid value (bool/0 included); cast for typing.
    original = cast(T, original)

    changed = make_changed(original)
    evidence: dict[str, object] = {"original": original, "changed": changed}
    if extra_evidence:
        evidence.update(extra_evidence)

    reacted = False
    readback: T | None = None
    restored = False
    outcome: CheckResult | None = None

    try:
        _, w_fail = await _guard(
            write(changed), entry, per_check_timeout=per_check_timeout
        )
        if w_fail is not None:
            evidence["write_error"] = w_fail.error
            outcome = w_fail
        else:
            readback, r_fail = await _guard(
                read(), entry, per_check_timeout=per_check_timeout
            )
            if r_fail is not None:
                evidence["readback_error"] = r_fail.error
                outcome = r_fail
            else:
                evidence["readback"] = readback
                reacted = equal(cast(T, readback), changed)
    finally:
        # Best-effort restore that must never raise.
        try:
            _, restore_fail = await _guard(
                write(original), entry, per_check_timeout=per_check_timeout
            )
            if restore_fail is not None:
                evidence["restore_error"] = restore_fail.error
            else:
                ctrl, ctrl_fail = await _guard(
                    read(), entry, per_check_timeout=per_check_timeout
                )
                if ctrl_fail is not None:
                    evidence["restore_read_error"] = ctrl_fail.error
                else:
                    restored = equal(cast(T, ctrl), original)
                    evidence["restore_readback"] = ctrl
        except _RESTORE_ERRORS as exc:
            evidence["restore_error"] = str(exc)
        evidence["restored"] = restored

    if outcome is not None:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=outcome.failure_domain,
            evidence=evidence,
            error=outcome.error,
        )
    if reacted and restored:
        return _base_result(entry, CheckStatus.PASS, evidence=evidence)
    if not reacted:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.READBACK,
            evidence=evidence,
            error="control did not react: readback equals original",
        )
    return _base_result(
        entry,
        CheckStatus.FAIL,
        failure_domain=FailureDomain.READBACK,
        evidence=evidence,
        error="control reacted but restore failed",
    )


def _write_gate(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
) -> CheckResult | None:
    """Apply the allow_writes + capability-present gate for write checks.

    Returns a short-circuit :class:`CheckResult` (SKIP/UNSUPPORTED) or ``None``
    if the RMVR cycle should proceed.
    """
    if not allow_writes:
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={
                "reason": (
                    "writes disabled (--read-only); pass without --read-only "
                    f"to exercise {entry.check_id}"
                )
            },
        )
    present = _capability_present(radio, entry)
    if present is False:
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={"capability_present": False},
        )
    return None


# ---------------------------------------------------------------------------
# Read-only handlers
# ---------------------------------------------------------------------------


async def _check_discovery_identify(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    if not radio.connected:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.TRANSPORT,
            evidence={"connected": False, "model": radio.model},
            error="radio reports not connected",
        )
    freq, failure = await _guard(
        radio.get_freq(), entry, per_check_timeout=per_check_timeout
    )
    if failure is not None:
        return failure
    return _base_result(
        entry,
        CheckStatus.PASS,
        evidence={"connected": True, "model": radio.model, "freq_hz": freq},
    )


async def _check_freq_reverse_sync(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    command_freq, failure = await _guard(
        radio.get_freq(), entry, per_check_timeout=per_check_timeout
    )
    if failure is not None:
        return failure
    assert command_freq is not None
    state_freq = radio.radio_state.main.freq
    if state_freq == command_freq:
        return _base_result(
            entry,
            CheckStatus.PASS,
            evidence={
                "command_freq_hz": command_freq,
                "state_freq_hz": state_freq,
                "delta_hz": 0,
            },
        )
    return _base_result(
        entry,
        CheckStatus.FAIL,
        failure_domain=FailureDomain.STATE_PUBLISHING,
        evidence={
            "command_freq_hz": command_freq,
            "state_freq_hz": state_freq,
            "delta_hz": command_freq - state_freq,
        },
        error="core state frequency does not match the command-path read",
    )


# ---------------------------------------------------------------------------
# Write handlers (RMVR)
# ---------------------------------------------------------------------------


async def _check_freq_write(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    # ``freq.write`` is untagged (cap ""): gate on allow_writes only.
    if not allow_writes:
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={
                "reason": (
                    "writes disabled (--read-only); pass without --read-only "
                    "to exercise freq.write"
                )
            },
        )
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: radio.get_freq(0),
        write=lambda value: radio.set_freq(value, 0),
        make_changed=lambda f: f + 1000,
        per_check_timeout=per_check_timeout,
    )


async def _check_mode_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    # ``mode.set`` is untagged (cap ""): gate on allow_writes only.
    if not allow_writes:
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={
                "reason": (
                    "writes disabled (--read-only); pass without --read-only "
                    "to exercise mode.set"
                )
            },
        )

    original, fail = await _guard(
        radio.get_mode(0), entry, per_check_timeout=per_check_timeout
    )
    if fail is not None:
        return fail
    assert original is not None
    orig_mode, orig_filter = original
    changed_mode = next(
        candidate for candidate in ("USB", "LSB", "CW", "AM") if candidate != orig_mode
    )

    evidence: dict[str, object] = {
        "original_mode": orig_mode,
        "original_filter": orig_filter,
        "changed_mode": changed_mode,
    }
    reacted = False
    restored = False
    outcome: CheckResult | None = None

    try:
        _, w_fail = await _guard(
            radio.set_mode(changed_mode, None, 0),
            entry,
            per_check_timeout=per_check_timeout,
        )
        if w_fail is not None:
            evidence["write_error"] = w_fail.error
            outcome = w_fail
        else:
            readback, r_fail = await _guard(
                radio.get_mode(0), entry, per_check_timeout=per_check_timeout
            )
            if r_fail is not None:
                evidence["readback_error"] = r_fail.error
                outcome = r_fail
            else:
                assert readback is not None
                evidence["readback_mode"] = readback[0]
                reacted = readback[0] == changed_mode
    finally:
        try:
            _, restore_fail = await _guard(
                radio.set_mode(orig_mode, orig_filter, 0),
                entry,
                per_check_timeout=per_check_timeout,
            )
            if restore_fail is not None:
                evidence["restore_error"] = restore_fail.error
            else:
                ctrl, ctrl_fail = await _guard(
                    radio.get_mode(0), entry, per_check_timeout=per_check_timeout
                )
                if ctrl_fail is not None:
                    evidence["restore_read_error"] = ctrl_fail.error
                else:
                    assert ctrl is not None
                    restored = ctrl[0] == orig_mode
        except _RESTORE_ERRORS as exc:
            evidence["restore_error"] = str(exc)
        evidence["restored"] = restored

    if outcome is not None:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=outcome.failure_domain,
            evidence=evidence,
            error=outcome.error,
        )
    if reacted and restored:
        return _base_result(entry, CheckStatus.PASS, evidence=evidence)
    if not reacted:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.READBACK,
            evidence=evidence,
            error="control did not react: readback equals original",
        )
    return _base_result(
        entry,
        CheckStatus.FAIL,
        failure_domain=FailureDomain.READBACK,
        evidence=evidence,
        error="control reacted but restore failed",
    )


async def _check_filter_width_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    dsp = cast(DspControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: dsp.get_filter_width(0),
        write=lambda value: dsp.set_filter_width(value, 0),
        make_changed=lambda w: w + 200 if w <= 2600 else w - 200,
        equal=_tolerant_equal(50),
        per_check_timeout=per_check_timeout,
    )


async def _check_rf_gain_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    levels = cast(LevelsCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: levels.get_rf_gain(0),
        write=lambda value: levels.set_rf_gain(value, 0),
        make_changed=lambda v: 200 if v < 128 else 50,
        equal=_tolerant_equal(3),
        per_check_timeout=per_check_timeout,
    )


async def _check_af_level_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    levels = cast(LevelsCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: levels.get_af_level(0),
        write=lambda value: levels.set_af_level(value, 0),
        make_changed=lambda v: 200 if v < 128 else 50,
        equal=_tolerant_equal(3),
        per_check_timeout=per_check_timeout,
    )


async def _check_preamp_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    antenna = cast(AntennaControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: antenna.get_preamp(0),
        write=lambda value: antenna.set_preamp(value, 0),
        make_changed=lambda v: 1 if v == 0 else 0,
        per_check_timeout=per_check_timeout,
    )


async def _check_attenuator_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    antenna = cast(AntennaControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: antenna.get_attenuator(0),
        write=lambda value: antenna.set_attenuator(value, 0),
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


async def _check_notch_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    dsp = cast(DspControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: dsp.get_manual_notch(0),
        write=lambda value: dsp.set_manual_notch(value, 0),
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


async def _check_nb_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    read_fn = getattr(radio, "get_nb", None)
    if not callable(read_fn):
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={"reason": "radio exposes set_nb but no get_nb readback"},
        )
    write_fn = cast(Callable[[bool], Awaitable[None]], getattr(radio, "set_nb"))
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: cast(Awaitable[bool], read_fn()),
        write=write_fn,
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


async def _check_nr_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    read_fn = getattr(radio, "get_nr", None)
    if not callable(read_fn):
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={"reason": "radio exposes set_nr but no get_nr readback"},
        )
    write_fn = cast(Callable[[bool], Awaitable[None]], getattr(radio, "set_nr"))
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: cast(Awaitable[bool], read_fn()),
        write=write_fn,
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


async def _check_agc_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    dsp = cast(DspControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: dsp.get_agc(0),
        write=lambda value: dsp.set_agc(value, 0),
        make_changed=lambda m: (
            int(AgcMode.SLOW) if m != AgcMode.SLOW else int(AgcMode.FAST)
        ),
        per_check_timeout=per_check_timeout,
    )


async def _check_rit_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    rit = cast(RitXitCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=rit.get_rit_frequency,
        write=rit.set_rit_frequency,
        make_changed=lambda v: v + 100,
        equal=_tolerant_equal(10),
        per_check_timeout=per_check_timeout,
    )


async def _check_xit_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate
    rit = cast(RitXitCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=rit.get_rit_tx_status,
        write=rit.set_rit_tx_status,
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


# ---------------------------------------------------------------------------
# Generic dispatch: value-rule map + _check_from_spec
# ---------------------------------------------------------------------------

# Standalone test value to SET for a write-only control (no original is readable).
_WRITE_ONLY_TEST_VALUES: dict[str, Any] = {
    ValueRule.TOGGLE_BOOL: True,
    ValueRule.BUMP_HZ: 100,
    ValueRule.STEP_LEVEL_255: 100,
    ValueRule.NUDGE_FILTER: 2800,
    ValueRule.PREAMP_CYCLE: 1,
    ValueRule.AGC_FLIP: int(AgcMode.FAST),
}

# Benign value to restore a write-only control to afterwards (best-effort).
# Only defined where a clear neutral exists; absence => skip restore honestly.
_WRITE_ONLY_RESTORE: dict[str, Any] = {
    ValueRule.TOGGLE_BOOL: False,
    ValueRule.BUMP_HZ: 0,
}


# Maps each scalar ValueRule to a mutation lambda.
# MODE_CYCLE is deliberately absent: it is tuple-valued and handled exclusively
# by the bespoke _check_mode_set named handler.
_VALUE_RULE_FNS: dict[str, Callable[[Any], Any]] = {
    ValueRule.TOGGLE_BOOL: lambda b: not b,
    ValueRule.STEP_LEVEL_255: lambda v: 200 if v < 128 else 50,
    ValueRule.NUDGE_FILTER: lambda w: w + 200 if w <= 2600 else w - 200,
    ValueRule.PREAMP_CYCLE: lambda v: 1 if v == 0 else 0,
    ValueRule.AGC_FLIP: lambda m: (
        int(AgcMode.SLOW) if m != AgcMode.SLOW else int(AgcMode.FAST)
    ),
    ValueRule.BUMP_HZ: lambda v: v + 100,
}


async def _set_and_observe(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    spec: CheckSpec,
    *,
    per_check_timeout: float,
) -> CheckResult:
    """Verify a write-only control: SET a test value (no read-first), treat a
    NAK/timeout-free SET as success, best-effort restore to a benign default."""
    set_fn = getattr(radio, spec.set_op, None) if spec.set_op else None
    if not callable(set_fn):
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={
                "reason": f"radio has no set op {spec.set_op!r} for write-only check"
            },
        )

    test_value = _WRITE_ONLY_TEST_VALUES.get(spec.value_rule)
    if test_value is None:
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={
                "reason": f"no write-only test value for value_rule {spec.value_rule!r}"
            },
        )

    evidence: dict[str, object] = {
        "verification": "set_observe",
        "readback": "unavailable",
        "test_value": test_value,
        "handler": "set_and_observe",
        "value_rule": str(spec.value_rule),
    }

    _, fail = await _guard(
        cast(Awaitable[None], set_fn(test_value)),
        entry,
        per_check_timeout=per_check_timeout,
    )
    if fail is not None:
        evidence["set_error"] = fail.error
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=fail.failure_domain,
            evidence=evidence,
            error=fail.error,
        )

    evidence["set_accepted"] = True

    restore_value = _WRITE_ONLY_RESTORE.get(spec.value_rule)
    if restore_value is not None:
        _, r_fail = await _guard(
            cast(Awaitable[None], set_fn(restore_value)),
            entry,
            per_check_timeout=per_check_timeout,
        )
        evidence["restored"] = r_fail is None
        if r_fail is not None:
            evidence["restore_error"] = r_fail.error
        else:
            evidence["restore_value"] = restore_value
    else:
        evidence["restored"] = False
        evidence["restore_skipped"] = (
            f"no benign default for value_rule {spec.value_rule!r}"
        )

    return _base_result(entry, CheckStatus.PASS, evidence=evidence)


async def _check_from_spec(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    spec: CheckSpec,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    """Execute a check using a registry CheckSpec when no named handler exists."""
    if spec.kind is CheckKind.READ_ONLY:
        if spec.get_op is None:
            return _base_result(
                entry,
                CheckStatus.UNSUPPORTED,
                evidence={"reason": f"radio has no readable op {spec.get_op!r}"},
            )
        read_fn = getattr(radio, spec.get_op, None)
        if not callable(read_fn):
            return _base_result(
                entry,
                CheckStatus.UNSUPPORTED,
                evidence={"reason": f"radio has no readable op {spec.get_op!r}"},
            )
        value, fail = await _guard(
            cast(Awaitable[Any], read_fn()),
            entry,
            per_check_timeout=per_check_timeout,
        )
        if fail is not None:
            return fail
        return _base_result(
            entry,
            CheckStatus.PASS,
            evidence={
                "value": value,
                "op": spec.get_op,
                "handler": "generic",
                "kind": str(spec.kind),
            },
        )

    if spec.kind is CheckKind.RMVR_SAFE_WRITE:
        gate = _write_gate(radio, entry, allow_writes=allow_writes)
        if gate is not None:
            return gate
        read_fn = getattr(radio, spec.get_op, None) if spec.get_op else None
        write_fn = getattr(radio, spec.set_op, None) if spec.set_op else None
        if not callable(read_fn) or not callable(write_fn):
            return _base_result(
                entry,
                CheckStatus.UNSUPPORTED,
                evidence={
                    "reason": f"radio is missing get/set op for {entry.check_id}"
                },
            )
        make_changed = _VALUE_RULE_FNS.get(spec.value_rule)
        if make_changed is None:
            return _base_result(
                entry,
                CheckStatus.UNSUPPORTED,
                evidence={
                    "reason": f"value_rule {spec.value_rule!r} not supported by generic handler"
                },
            )
        equal: Callable[[Any, Any], bool] = (
            _tolerant_equal(spec.tolerance) if spec.tolerance else _default_equal
        )
        _read_fn = read_fn
        _write_fn = write_fn
        return await _read_modify_verify_restore(
            radio,
            entry,
            read=lambda: cast(Awaitable[Any], _read_fn()),
            write=lambda v: cast(Awaitable[None], _write_fn(v)),
            make_changed=make_changed,
            equal=equal,
            per_check_timeout=per_check_timeout,
            extra_evidence={
                "handler": "generic",
                "value_rule": str(spec.value_rule),
                "kind": str(spec.kind),
            },
        )

    if spec.kind is CheckKind.WRITE_ONLY_OBSERVE:
        gate = _write_gate(radio, entry, allow_writes=allow_writes)
        if gate is not None:
            return gate
        return await _set_and_observe(
            radio, entry, spec, per_check_timeout=per_check_timeout
        )

    if spec.kind is CheckKind.MANUAL:
        return _manual_required_result(radio, entry)

    # CheckKind.TX_ADJACENT_BLOCKED (defensive)
    return _base_result(
        entry,
        CheckStatus.BLOCKED,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        evidence={"reason": "tx-adjacent blocked"},
    )


# Dispatch table for SUPPORTED check-specific handlers.
_SUPPORTED_HANDLERS: dict[
    str,
    Callable[..., Awaitable[CheckResult]],
] = {
    "discovery.identify": _check_discovery_identify,
    "freq.write": _check_freq_write,
    "freq.reverse_sync": _check_freq_reverse_sync,
    "mode.set": _check_mode_set,
    "filter_width.set": _check_filter_width_set,
    "rf_gain.set": _check_rf_gain_set,
    "af_level.set": _check_af_level_set,
    "preamp.set": _check_preamp_set,
    "attenuator.set": _check_attenuator_set,
    "notch.set": _check_notch_set,
    "nb.set": _check_nb_set,
    "nr.set": _check_nr_set,
    "agc.set": _check_agc_set,
    "rit.set": _check_rit_set,
    "xit.set": _check_xit_set,
}
