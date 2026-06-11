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
from collections.abc import Awaitable, Callable, Sequence
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
from rigplane.validation.interactive import InteractivePrompter
from rigplane.validation.registry import CheckKind, CheckSpec, ValueRule, get_spec
from rigplane.validation.runner import _is_authorized, _is_safety_gated
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
# ``ValueError``/``TypeError`` are included because command encoders raise them
# for out-of-band values (MOR-659: a 16.5 Hz tone readback aborted a whole run).
_RESTORE_ERRORS = (
    asyncio.TimeoutError,
    RigTimeoutError,
    RigplaneError,
    RigConnectionError,
    AuthenticationError,
    CommandError,
    OSError,
    ValueError,
    TypeError,
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


# Operator-perception prompts (MOR-667). These checks have NO software readback
# — only a human watching the rig can confirm them — so in ``--interactive`` mode
# the harness asks the operator a yes/no question and records PASS/FAIL instead of
# the default MANUAL_REQUIRED. A check_id without an entry here keeps
# MANUAL_REQUIRED even under ``--interactive``.
_PERCEPTION_PROMPTS: dict[str, str] = {
    "audio.rx": (
        "Listen to the radio's RX audio (speaker/headphones). "
        "Do you hear receive audio? [y/N] "
    ),
    "scope.capture": (
        "Look at the radio's panadapter/spectrum scope. "
        "Do you see the spectrum sweeping? [y/N] "
    ),
    "bsr.select": (
        "Trigger the band-stack select on the rig and watch the band display. "
        "Did the band change as expected? [y/N] "
    ),
}


# MOR-666 — TX actuation. The pre-TX gate prompt is asked ONCE per run via the
# MOR-667 ``confirm()`` primitive, which ALWAYS reads a real answer and IGNORES
# ``--assume-yes`` — an unattended run can therefore never key the transmitter.
_TX_ACTUATE_CONFIRM_PROMPT = (
    "About to TRANSMIT on the connected antenna/dummy load at MINIMUM power "
    "for ~1-2s (PTT key + tuner tune-cycle). Type YES to proceed: "
)
# Minimum TX power on the normalised 0-255 scale (lowest practical key).
_TX_MIN_POWER = 0
# Brief PTT key duration (seconds) — shortest practical key for a valid check.
_TX_PTT_KEY_SECONDS = 1.0
# Tuner status codes (mirror ``commands/system.py``): 0=off, 1=on, 2=tune.
_TUNER_STATUS_TUNE = 2
# Bounded wait for the tune-cycle to settle before reading status back.
_TUNER_SETTLE_SECONDS = 1.0

# The check_ids whose actuation transmits; gated by ``--tx-actuate`` + confirm.
# VOX is deliberately excluded (stays MANUAL — out of MOR-666 scope).
_TX_ACTUATE_CHECK_IDS = frozenset({"tx.ptt", "tuner.tune"})

# MOR-668 — RX-audio probes that run for real against a live captured RX-PCM
# window (supplied via ``audio_probe_frames``). ``audio.tx.byte_perfect`` is
# deliberately excluded: it needs a TX loopback and stays MANUAL/out of scope.
_LIVE_AUDIO_PROBE_CHECK_IDS = frozenset({"audio.rx.rms", "scope.fft.presence"})


def _template_has_tx_check(template: MatrixTemplate) -> bool:
    """True if the template contains a TX-actuatable check (tx.ptt/tuner.tune).

    Used to decide whether to ask the single pre-TX confirm at all — a run with
    no TX check never prompts the operator.
    """
    return any(entry.check_id in _TX_ACTUATE_CHECK_IDS for entry in template.entries)


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
    write_only_capabilities: frozenset[str] = frozenset(),
    prompter: InteractivePrompter | None = None,
    tx_actuate: bool = False,
    audio_probe_frames: Sequence[bytes | None] | None = None,
) -> list[LevelResult]:
    """Run a template's checks against an already-connected ``radio``.

    Iterates ``template.entries`` in order, producing one :class:`CheckResult`
    per entry, then groups results by validation level (ascending, empty
    levels omitted, original order preserved within a level). Each result is
    stamped with ISO-8601 ``started_at``/``finished_at`` timestamps and a single
    INFO log line is emitted per check. The radio is assumed connected; this
    function neither connects nor disconnects.

    When *tx_actuate* is ``True`` (the caller has already verified the full
    opt-in gate stack, MOR-666) AND a *prompter* is supplied, the TX checks
    (``tx.ptt``/``tuner.tune``) may ACTUALLY transmit — but only after the
    operator types YES to a single pre-TX ``confirm()`` gate asked ONCE here,
    before any check runs. Without ``tx_actuate``, without a prompter, or on a
    declined confirm, the TX checks keep their MANUAL_REQUIRED behaviour and
    NEVER key the transmitter.

    When *audio_probe_frames* is supplied (MOR-668), it carries a window of
    REAL RX PCM already captured from a live audio session by the caller (the
    CLI hardware path owns opening/closing that RX session). The RX-audio
    probes (``audio.rx.rms`` / ``scope.fft.presence``) then run for real
    against those captured frames instead of staying MANUAL_REQUIRED. Without
    it (dry-run/CI, or a non-AudioCapable radio) those probes keep their
    MANUAL_REQUIRED behaviour. ``audio.tx.byte_perfect`` is out of scope here
    and is never run against live hardware (it needs TX loopback).
    """
    # MOR-666: resolve the single pre-TX confirmation up front so it is asked at
    # most once per run (not per check). ``confirm()`` ignores ``--assume-yes``,
    # so an unattended run can never authorise transmission.
    tx_actuate_confirmed = False
    if tx_actuate and prompter is not None and _template_has_tx_check(template):
        tx_actuate_confirmed = prompter.confirm(_TX_ACTUATE_CONFIRM_PROMPT)

    by_level: dict[ValidationLevel, list[CheckResult]] = {}
    for entry in template.entries:
        started = _utcnow_iso()
        try:
            result = await _run_one_check(
                radio,
                entry,
                safety,
                allow_writes=allow_writes,
                per_check_timeout=per_check_timeout,
                write_only_capabilities=write_only_capabilities,
                prompter=prompter,
                tx_actuate_confirmed=tx_actuate_confirmed,
                audio_probe_frames=audio_probe_frames,
            )
        except Exception as exc:
            # Backstop (MOR-659): one raising check must never abort the
            # matrix — the artifact is ALWAYS produced. ``BaseException``
            # (KeyboardInterrupt/SystemExit/CancelledError) still propagates.
            _LOGGER.exception("validate check %s raised unexpectedly", entry.check_id)
            result = _base_result(
                entry,
                CheckStatus.FAIL,
                failure_domain=FailureDomain.COMMAND_EXECUTION,
                error=repr(exc),
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
    write_only_capabilities: frozenset[str] = frozenset(),
    prompter: InteractivePrompter | None = None,
    tx_actuate_confirmed: bool = False,
    audio_probe_frames: Sequence[bytes | None] | None = None,
) -> CheckResult:
    """Execute a single template entry, applying universal pre-gates first."""
    # Pre-gate 1: authorization for TX-adjacent checks.
    if _is_safety_gated(entry) and not _is_authorized(entry, safety):
        return _base_result(
            entry,
            CheckStatus.BLOCKED,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            evidence={"reason": "operator authorization required"},
        )

    # MOR-668: live RX-audio probes. When the caller captured a real RX-PCM
    # window (``audio_probe_frames``), run ``audio.rx.rms`` / ``scope.fft.presence``
    # for real against it instead of leaving them MANUAL_REQUIRED. Without a
    # captured window they fall through to MANUAL_REQUIRED below (today's
    # behaviour). ``audio.tx.byte_perfect`` is excluded (out of scope). This
    # measurement is pure (no radio I/O here) — the session was already opened
    # and torn down by the caller.
    if audio_probe_frames is not None and entry.check_id in _LIVE_AUDIO_PROBE_CHECK_IDS:
        return await _run_live_audio_probe(entry, audio_probe_frames)

    # MOR-666: TX actuation. ONLY when the operator affirmatively confirmed the
    # pre-TX gate AND this is a TX-actuatable check, ACTUALLY transmit (key PTT
    # at minimum power / run a tune-cycle). This runs AFTER pre-gate 1, so the
    # operator must STILL be authorized (tx_allowed/tuner_allowed). Without an
    # affirmative confirm this branch is skipped and the check falls through to
    # MANUAL_REQUIRED below — the transmitter is never keyed.
    if tx_actuate_confirmed and entry.check_id in _TX_ACTUATE_CHECK_IDS:
        if entry.check_id == "tx.ptt":
            return await _actuate_tx_ptt(
                radio, entry, per_check_timeout=per_check_timeout
            )
        if entry.check_id == "tuner.tune":
            return await _actuate_tuner_tune(
                radio, entry, per_check_timeout=per_check_timeout
            )

    # Pre-gate 2: manual-required (authorized / non-TX-adjacent).
    if entry.declaration == CapabilityDeclaration.MANUAL_REQUIRED:
        return _manual_required_result(radio, entry, prompter=prompter)

    # Pre-gate 3: declared unsupported pending evidence.
    if entry.declaration == CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE:
        evidence: dict[str, object] = {"declared": "unsupported_pending_evidence"}
        present = _capability_present(radio, entry)
        if present is not None and entry.check_id == "scope.capture":
            evidence["scope_capability_present"] = present
        elif present is not None:
            evidence["capability_present"] = present
        # MOR-660: a confirmed-present capability IS the evidence the
        # declaration was "pending" — resolve PASS, not UNSUPPORTED.
        status = CheckStatus.PASS if present is True else CheckStatus.UNSUPPORTED
        return _base_result(entry, status, evidence=evidence)

    # Per-radio write-only classification (MOR-208): controls whose capability
    # is declared write-only route through set-and-observe (no read-first),
    # overriding any named RMVR handler. Falls through if the check has no spec.
    if entry.capability and entry.capability in write_only_capabilities:
        spec = get_spec(entry.check_id)
        if spec is not None:
            gate = _write_gate(radio, entry, allow_writes=allow_writes)
            if gate is not None:
                return gate
            return await _set_and_observe(
                radio, entry, spec, per_check_timeout=per_check_timeout
            )

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
                prompter=prompter,
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


async def _run_live_audio_probe(
    entry: CapabilityDeclarationEntry,
    frames: Sequence[bytes | None],
) -> CheckResult:
    """Run a live RX-audio probe against ``frames`` (MOR-668).

    Delegates to the MOR-639/641 measurement logic, fed the LIVE captured RX
    PCM window instead of the deterministic fake backend. The probe functions
    build their own :class:`CheckResult` from the registry spec (same level /
    summary the template carries). Any unexpected error is contained into a
    FAIL result so the broader matrix is never aborted (MOR-659 backstop also
    covers this).
    """
    from rigplane.validation.audio_checks import (
        run_rx_rms_check_on_frames,
        run_scope_presence_check_on_frames,
    )

    if entry.check_id == "audio.rx.rms":
        return await run_rx_rms_check_on_frames(frames)
    return await run_scope_presence_check_on_frames(frames)


def _interactive_perception_result(
    entry: CapabilityDeclarationEntry, prompter: InteractivePrompter, prompt: str
) -> CheckResult:
    """Resolve a manual-perception check via an operator yes/no prompt (MOR-667).

    Asks the operator the perception question and records PASS (yes) or FAIL
    (no) with ``{operator_confirmed: <bool>, prompt: <text>}`` evidence. Performs
    NO actuation itself — the operator triggers and observes the rig; the harness
    only collects the verdict.
    """
    confirmed = prompter.ask(prompt)
    return _base_result(
        entry,
        CheckStatus.PASS if confirmed else CheckStatus.FAIL,
        failure_domain=None if confirmed else FailureDomain.COMMAND_EXECUTION,
        evidence={"operator_confirmed": confirmed, "prompt": prompt.strip()},
    )


def _manual_required_result(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    prompter: InteractivePrompter | None = None,
) -> CheckResult:
    """Build a MANUAL_REQUIRED result with read-only evidence enrichment.

    Performs NO actuation: never keys TX, never triggers a tune cycle, never
    starts a stream. When a *prompter* is supplied and this check has a
    perception prompt (``audio.rx``/``scope.capture``/``bsr.select``), resolve it
    to PASS/FAIL from the operator's answer instead of MANUAL_REQUIRED (MOR-667).
    """
    # MOR-667: operator-confirmed perception checks. TX-adjacent checks
    # (``tx.ptt``/``tuner.tune``) deliberately have NO perception prompt, so they
    # never become an auto-yes target and fall through to MANUAL_REQUIRED below.
    if prompter is not None:
        prompt = _PERCEPTION_PROMPTS.get(entry.check_id)
        if prompt is not None:
            return _interactive_perception_result(entry, prompter, prompt)

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


# ---------------------------------------------------------------------------
# TX actuation handlers (MOR-666) — reached ONLY after the full gate stack and
# an explicit interactive confirm() YES. Each guarantees the radio is left in a
# safe (un-keyed, power-restored) state via a finally that never raises.
# ---------------------------------------------------------------------------


async def _actuate_tx_ptt(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    per_check_timeout: float,
) -> CheckResult:
    """ACTUALLY key PTT briefly at minimum power, verify, then always unkey.

    Sequence: read original TX power → set power to MINIMUM → key PTT → brief
    wait → read ``radio_state.ptt`` to verify it keyed → unkey → restore power.
    The unkey AND power-restore run in a ``finally`` that ALWAYS executes and
    never raises (contained by ``_RESTORE_ERRORS``, which includes ``OSError``),
    so a mid-check exception/timeout/LAN drop can never leave the radio keyed or
    at the wrong power.
    """
    _set_ptt_attr = getattr(radio, "set_ptt", None)
    if not callable(_set_ptt_attr):
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={"reason": "radio has no set_ptt op"},
        )
    set_ptt = cast(Callable[[bool], Awaitable[None]], _set_ptt_attr)

    _get_power_attr = getattr(radio, "get_rf_power", None)
    _set_power_attr = getattr(radio, "set_rf_power", None)
    has_power = callable(_get_power_attr) and callable(_set_power_attr)
    get_power = cast(Callable[[], Awaitable[int]], _get_power_attr)
    set_power = cast(Callable[[int], Awaitable[None]], _set_power_attr)

    evidence: dict[str, object] = {"tx_actuated": True, "keyed": False}
    original_power: int | None = None
    power_set_to_min = False
    keyed = False
    verify_error: str | None = None

    if has_power:
        original_power, fail = await _guard(
            get_power(), entry, per_check_timeout=per_check_timeout
        )
        if fail is None and original_power is not None:
            evidence["original_power"] = original_power
            _, pf = await _guard(
                set_power(_TX_MIN_POWER),
                entry,
                per_check_timeout=per_check_timeout,
            )
            power_set_to_min = pf is None
            evidence["power_set_to_min"] = power_set_to_min
            if pf is not None:
                evidence["power_set_error"] = pf.error
        else:
            if fail is not None:
                evidence["power_read_error"] = fail.error

    # Harm reduction: if the radio HAS power control but we could not confirm
    # it is at minimum (read or set failed), refuse to transmit at an unknown
    # (possibly full) power. Minimum-power-first is part of the TX-actuate
    # safety contract, not just best-effort. (A radio with no power API at all
    # still actuates — power can't be controlled there; the operator opted in.)
    if has_power and not power_set_to_min:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            evidence=evidence,
            error="refusing to transmit: could not set TX power to minimum",
        )

    try:
        # Key the transmitter.
        await asyncio.wait_for(set_ptt(True), timeout=per_check_timeout)
        keyed = True
        # Brief, bounded key window.
        await asyncio.sleep(_TX_PTT_KEY_SECONDS)
        # Verify the keyed state from published radio state (best-effort).
        evidence["ptt_state"] = bool(radio.radio_state.ptt)
        keyed = bool(radio.radio_state.ptt)
        evidence["keyed"] = keyed
    except _RESTORE_ERRORS as exc:
        verify_error = str(exc)
        evidence["actuate_error"] = verify_error
    finally:
        # ALWAYS unkey, no matter what — the radio must never be left keyed.
        unkeyed = False
        try:
            await asyncio.wait_for(set_ptt(False), timeout=per_check_timeout)
            unkeyed = True
        except _RESTORE_ERRORS as exc:
            evidence["unkey_error"] = str(exc)
        evidence["unkeyed"] = unkeyed
        # ALWAYS restore the original power if we lowered it.
        power_restored = not power_set_to_min
        if has_power and power_set_to_min and original_power is not None:
            try:
                _, rf = await _guard(
                    set_power(original_power),
                    entry,
                    per_check_timeout=per_check_timeout,
                )
                power_restored = rf is None
                if rf is not None:
                    evidence["power_restore_error"] = rf.error
            except _RESTORE_ERRORS as exc:
                evidence["power_restore_error"] = str(exc)
        evidence["power_restored"] = power_restored

    if verify_error is not None:
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            evidence=evidence,
            error=f"tx actuation failed: {verify_error}",
        )
    if keyed and bool(evidence.get("unkeyed")):
        return _base_result(entry, CheckStatus.PASS, evidence=evidence)
    return _base_result(
        entry,
        CheckStatus.FAIL,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        evidence=evidence,
        error="PTT did not key/unkey cleanly",
    )


async def _actuate_tuner_tune(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    per_check_timeout: float,
) -> CheckResult:
    """ACTUALLY run a tuner tune-cycle (``set_tuner_status(2)``) and verify.

    The tuner keys TX into the connected load; this is gated behind the same
    pre-TX confirm as ``tx.ptt``. After triggering the cycle we wait a bounded
    settle window, read the tuner status back (best-effort), and record it. A
    NAK/timeout-free trigger is the PASS signal — the radio's own readback can
    report transient/idle states once the cycle completes.
    """
    _set_tuner_attr = getattr(radio, "set_tuner_status", None)
    if not callable(_set_tuner_attr):
        return _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            evidence={"reason": "radio has no set_tuner_status op"},
        )
    set_tuner = cast(Callable[[int], Awaitable[None]], _set_tuner_attr)

    evidence: dict[str, object] = {"tx_actuated": True}
    _, fail = await _guard(
        set_tuner(_TUNER_STATUS_TUNE),
        entry,
        per_check_timeout=per_check_timeout,
    )
    if fail is not None:
        evidence["tune_error"] = fail.error
        return _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=fail.failure_domain,
            evidence=evidence,
            error=fail.error,
        )
    evidence["tune_triggered"] = True

    # Bounded settle, then best-effort readback (never the pass/fail driver).
    await asyncio.sleep(_TUNER_SETTLE_SECONDS)
    _get_tuner_attr = getattr(radio, "get_tuner_status", None)
    if callable(_get_tuner_attr):
        get_tuner = cast(Callable[[], Awaitable[int]], _get_tuner_attr)
        status, sf = await _guard(
            get_tuner(),
            entry,
            per_check_timeout=per_check_timeout,
        )
        if sf is None:
            evidence["tuner_status_readback"] = status
        else:
            evidence["tuner_status_read_error"] = sf.error
    return _base_result(entry, CheckStatus.PASS, evidence=evidence)


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
    except NotImplementedError as exc:
        # MOR-670: a radio op may deliberately ``raise NotImplementedError`` to
        # signal "this model does not implement this op" (e.g.
        # ``FtX1Radio.get_vfo_slot`` — no VFO-slot concept). That is
        # semantically UNSUPPORTED, NOT a COMMAND_EXECUTION failure: no command
        # ever reached the radio. Resolve the check to UNSUPPORTED (no
        # failure_domain) with the reason captured for the artifact.
        return None, _base_result(
            entry,
            CheckStatus.UNSUPPORTED,
            failure_domain=None,
            evidence={"reason": f"{entry.check_id} not implemented on this radio"},
            error=str(exc),
        )
    except RigplaneError as exc:
        return None, _base_result(
            entry,
            CheckStatus.FAIL,
            failure_domain=FailureDomain.COMMAND_EXECUTION,
            error=str(exc),
        )
    except (ValueError, TypeError) as exc:
        # Command encoders raise these for out-of-band values (MOR-659:
        # set_tone_freq(16.5) -> ValueError); contain them per-check.
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


# Below this value a filter-width readback is a discrete table index (0-23 on
# the FTX-1), not a width in Hz. The smallest realistic Hz width is 50 (Icom
# ``filter_width_min``), so the threshold cleanly separates the two encodings.
_FILTER_WIDTH_INDEX_MAX = 30


def _nudge_filter(width: int) -> int:
    """Mutate a filter width to a DIFFERENT value the radio will accept.

    Two encodings share this check:

    * Discrete *table index* (FTX-1, ``width <= 30``, valid range 0-23): pick a
      different in-range index by stepping toward the middle of the range. The
      old ``width + 200`` produced an out-of-range index (e.g. 19 -> 219) that
      the radio silently ignored, so the check falsely reported "did not react".
    * Width in *Hz* (Icom, ``width > 30``): keep the historical ±200 Hz nudge.
    """
    if width <= _FILTER_WIDTH_INDEX_MAX:
        # Table index: a small in-range delta toward 0 (wrap up near the floor).
        return width - 5 if width >= 5 else width + 5
    return width + 200 if width <= 2600 else width - 200


# MOR-671 — IF-shift offset is signed Hz with a symmetric settable band of
# roughly +/-1200 Hz on the FTX-1. The RMVR mutation must stay inside that band
# so the written test value is always restorable and never out of range.
_IF_SHIFT_LIMIT_HZ = 1200
_IF_SHIFT_NUDGE_HZ = 200


# MOR-679 — CW pitch is a sidetone frequency in Hz, NOT a 0-255 level. The Icom
# encoder (``commands/levels.py::_cw_pitch_to_level``) raises ValueError outside
# 300-900 Hz, and the getter snaps the readback to the nearest 5 Hz. The RMVR
# mutation must stay inside that band so the written test value is always
# restorable and never out of range.
_CW_PITCH_MIN_HZ = 300
_CW_PITCH_MAX_HZ = 900
_CW_PITCH_NUDGE_HZ = 50


def _nudge_cw_pitch(pitch_hz: int) -> int:
    """Mutate a CW pitch (Hz) to a DIFFERENT in-range value on the 5 Hz grid.

    Nudge by +50 Hz, but if that would exceed the 900 Hz ceiling, step the other
    way (-50 Hz) instead. The original is assumed in-band (300-900 Hz), so the
    result is always within 300-900 Hz, on the radio's 5 Hz grid, and always
    differs from the original. NEVER writes out of range.
    """
    if int(pitch_hz) + _CW_PITCH_NUDGE_HZ <= _CW_PITCH_MAX_HZ:
        return int(pitch_hz) + _CW_PITCH_NUDGE_HZ
    return int(pitch_hz) - _CW_PITCH_NUDGE_HZ


# MOR-695 — level RMVR value rules must respect the control's settable range.
# The historical ``200 if v < 128 else 50`` nudge assumes a 0-255 ICOM scale;
# on the Yaesu FTX-1 comp/nr/nb levels have SMALL ranges (nr/nb 0-10, comp
# 0-100), so the fixed nudge lands out of range, the radio ignores the write,
# and the readback equals the original -> false FAIL. We resolve each level
# check's ``[min, max]`` band from ``radio.profile.controls`` and nudge inside
# it, defaulting to 0-255 when no range is declared.
_DEFAULT_LEVEL_RANGE: tuple[int, int] = (0, 255)

# Per level check, the ORDERED candidate control keys to look up in
# ``radio.profile.controls``. The IC-7610 uses ``nr_level``/``nb_level``/
# ``compressor_level``; the FTX-1 uses the shorter ``nr``/``nb`` (and a
# ``compressor_level`` block added by MOR-695). First key whose control table
# carries a min/max wins; otherwise the default 0-255 applies.
_LEVEL_RANGE_CANDIDATE_KEYS: dict[str, tuple[str, ...]] = {
    "rf_gain.set": ("rf_gain",),
    "af_level.set": ("af_level",),
    "rf_power.set": ("rf_power", "power_control"),
    "mic_gain.set": ("mic_gain",),
    "comp_level.set": ("compressor_level", "compressor", "comp"),
    "nr_level.set": ("nr_level", "nr"),
    "nb_level.set": ("nb_level", "nb"),
}


def _control_range(control: object) -> tuple[int, int] | None:
    """Extract a ``(min, max)`` band from one raw control table, or None.

    Accepts both key conventions found in the rig TOMLs: ``range_min``/
    ``range_max`` (FTX-1) and ``raw_min``/``raw_max`` (IC-7610). A control that
    declares neither pair (or only one half) yields None so the caller can fall
    through to the next candidate key.
    """
    if not isinstance(control, dict):
        return None
    for lo_key, hi_key in (("range_min", "range_max"), ("raw_min", "raw_max")):
        lo = control.get(lo_key)
        hi = control.get(hi_key)
        if lo is not None and hi is not None:
            try:
                lo_i, hi_i = int(lo), int(hi)
            except (TypeError, ValueError):
                continue
            if lo_i <= hi_i:
                return (lo_i, hi_i)
    return None


def _resolve_level_range(radio: Radio, check_id: str) -> tuple[int, int]:
    """Resolve a level check's settable band from the radio's profile.

    Walks the ordered candidate keys for ``check_id`` against
    ``radio.profile.controls`` and returns the first declared ``(min, max)``
    band. Falls back to 0-255 when the radio has no profile, the control is not
    declared, or no range is present — keeping the historical ICOM behaviour for
    radios (and tests) that declare nothing.
    """
    profile = getattr(radio, "profile", None)
    controls = getattr(profile, "controls", None)
    if not isinstance(controls, dict):
        return _DEFAULT_LEVEL_RANGE
    for key in _LEVEL_RANGE_CANDIDATE_KEYS.get(check_id, ()):
        rng = _control_range(controls.get(key))
        if rng is not None:
            return rng
    return _DEFAULT_LEVEL_RANGE


def _range_aware_level_nudge(lo: int, hi: int) -> Callable[[int], int]:
    """Build a ``make_changed`` that nudges a level inside ``[lo, hi]``.

    Steps by ``max(1, (hi - lo) // 10)``; if stepping up would exceed ``hi`` it
    steps DOWN instead. The result always differs from the original and always
    stays within ``[lo, hi]`` (the original is assumed in-band — the caller's
    ``restorable`` predicate SKIPs an out-of-band original before any write).
    """
    step = max(1, (hi - lo) // 10)

    def _nudge(orig: int) -> int:
        value = int(orig)
        if value + step <= hi:
            return value + step
        return value - step

    return _nudge


def _nudge_if_shift(offset: int) -> int:
    """Mutate an IF-shift offset to a DIFFERENT in-range value.

    Nudge by +200 Hz, but if that would exceed the +1200 Hz ceiling, step the
    other way (-200 Hz) instead. The original is assumed in-band (|offset| <=
    1200), so the result is always within +/-1200 Hz and always differs from
    the original. NEVER writes out of range.
    """
    if int(offset) + _IF_SHIFT_NUDGE_HZ <= _IF_SHIFT_LIMIT_HZ:
        return int(offset) + _IF_SHIFT_NUDGE_HZ
    return int(offset) - _IF_SHIFT_NUDGE_HZ


def _filter_width_equal(a: int, b: int) -> bool:
    """Encoding-aware filter-width comparator.

    A table index (FTX-1) reads back exactly, so require an exact match; an Hz
    width (Icom) may be quantized by the radio, so allow a 50 Hz tolerance.
    Both operands are small (index) only when neither exceeds the index ceiling.
    """
    if int(a) <= _FILTER_WIDTH_INDEX_MAX and int(b) <= _FILTER_WIDTH_INDEX_MAX:
        return int(a) == int(b)
    return abs(int(a) - int(b)) <= 50


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
    restorable: Callable[[T], bool] | None = None,
) -> CheckResult:
    """Read-modify-verify-restore an RX-safe control and report the outcome.

    Reads the original value, writes a different value, verifies the readback
    reacted, and always restores the original in a ``finally`` that never
    raises. The original is restored even if the write or readback failed.

    When ``restorable`` is given and rejects the original value (it is outside
    the control's settable band, so it could never be written back), the check
    SKIPs BEFORE any write — a non-destructive harness must never mutate the
    radio to a state it cannot restore from (MOR-659).
    """
    original, fail = await _guard(read(), entry, per_check_timeout=per_check_timeout)
    if fail is not None:
        return fail
    # ``original`` is a valid value (bool/0 included); cast for typing.
    original = cast(T, original)

    if restorable is not None and not restorable(original):
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={
                "original": original,
                "reason": (
                    "current value outside settable range; not mutated to "
                    "keep the run non-destructive"
                ),
            },
        )

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
        make_changed=_nudge_filter,
        equal=_filter_width_equal,
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
    lo, hi = _resolve_level_range(radio, entry.check_id)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: levels.get_rf_gain(0),
        write=lambda value: levels.set_rf_gain(value, 0),
        make_changed=_range_aware_level_nudge(lo, hi),
        equal=_tolerant_equal(3),
        per_check_timeout=per_check_timeout,
        restorable=lambda v: lo <= v <= hi,
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
    lo, hi = _resolve_level_range(radio, entry.check_id)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: levels.get_af_level(0),
        write=lambda value: levels.set_af_level(value, 0),
        make_changed=_range_aware_level_nudge(lo, hi),
        equal=_tolerant_equal(3),
        per_check_timeout=per_check_timeout,
        restorable=lambda v: lo <= v <= hi,
    )


def _merge_evidence(result: CheckResult, extra: dict[str, object]) -> CheckResult:
    """Return ``result`` with ``extra`` merged into its evidence dict."""
    if not extra:
        return result
    merged = dict(result.evidence)
    merged.update(extra)
    return dataclasses.replace(result, evidence=merged)


async def _preamp_rmvr(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    per_check_timeout: float,
) -> CheckResult:
    """The plain PREAMP read-modify-verify-restore cycle (no prerequisites)."""
    antenna = cast(AntennaControlCapable, radio)
    return await _read_modify_verify_restore(
        radio,
        entry,
        read=lambda: antenna.get_preamp(0),
        write=lambda value: antenna.set_preamp(value, 0),
        make_changed=lambda v: 1 if v == 0 else 0,
        per_check_timeout=per_check_timeout,
    )


async def _check_preamp_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    allow_writes: bool,
    per_check_timeout: float,
) -> CheckResult:
    """RMVR the PREAMP, clearing the DIGI-SEL prerequisite first (MOR-665).

    The IC-7610 enforces PREAMP/DIGI-SEL mutual exclusion: setting PREAMP while
    DIGI-SEL (IP+) is ON raises ``CommandError``. When DIGI-SEL is ON the
    harness temporarily disables it, runs the normal PREAMP RMVR, then restores
    DIGI-SEL to its original ON state in a best-effort step that never raises.
    If DIGI-SEL is already OFF — or the radio does not support reading it —
    behaviour is identical to the plain RMVR.
    """
    gate = _write_gate(radio, entry, allow_writes=allow_writes)
    if gate is not None:
        return gate

    get_digisel = getattr(radio, "get_digisel", None)
    set_digisel = getattr(radio, "set_digisel", None)
    if not callable(get_digisel) or not callable(set_digisel):
        # Radio cannot report DIGI-SEL state — fall back to today's behaviour.
        return await _preamp_rmvr(radio, entry, per_check_timeout=per_check_timeout)

    digisel_on: bool | None = None
    ds_fail: CheckResult | None = None
    try:
        digisel_on, ds_fail = await _guard(
            cast(Awaitable[bool], get_digisel()),
            entry,
            per_check_timeout=per_check_timeout,
        )
    except Exception:  # noqa: BLE001 — any DIGI-SEL read error -> safe fallback.
        ds_fail = _base_result(entry, CheckStatus.FAIL)
    if ds_fail is not None:
        # DIGI-SEL read unsupported/failed — fall back, don't crash the check.
        return await _preamp_rmvr(radio, entry, per_check_timeout=per_check_timeout)

    if not digisel_on:
        result = await _preamp_rmvr(radio, entry, per_check_timeout=per_check_timeout)
        return _merge_evidence(result, {"digisel_was_on": False})

    # DIGI-SEL is ON: clear it, run the PREAMP RMVR, then always restore it.
    extra: dict[str, object] = {"digisel_was_on": True}
    _, clear_fail = await _guard(
        cast(Awaitable[None], set_digisel(False)),
        entry,
        per_check_timeout=per_check_timeout,
    )
    if clear_fail is not None:
        # Could not clear the prerequisite — fall back so PREAMP still runs.
        extra["digisel_clear_error"] = clear_fail.error
        result = await _preamp_rmvr(radio, entry, per_check_timeout=per_check_timeout)
        return _merge_evidence(result, extra)

    try:
        result = await _preamp_rmvr(radio, entry, per_check_timeout=per_check_timeout)
    finally:
        # Best-effort restore that must never raise (mirrors
        # ``_read_modify_verify_restore``): ``_guard`` maps the rig-error family,
        # and the surrounding ``except _RESTORE_ERRORS`` additionally contains a
        # bare ``OSError`` from the UDP send path so a mid-restore LAN drop can
        # never escape and leave DIGI-SEL in the wrong state.
        restored = False
        try:
            _, restore_fail = await _guard(
                cast(Awaitable[None], set_digisel(True)),
                entry,
                per_check_timeout=per_check_timeout,
            )
            if restore_fail is not None:
                extra["digisel_restore_error"] = restore_fail.error
            else:
                ctrl, ctrl_fail = await _guard(
                    cast(Awaitable[bool], get_digisel()),
                    entry,
                    per_check_timeout=per_check_timeout,
                )
                if ctrl_fail is not None:
                    extra["digisel_restore_read_error"] = ctrl_fail.error
                else:
                    restored = bool(ctrl)
        except _RESTORE_ERRORS as exc:
            extra["digisel_restore_error"] = str(exc)
        extra["digisel_restored"] = restored

    return _merge_evidence(result, extra)


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

    async def _read_notch_bool() -> bool:
        # The Yaesu/FTX-1 backend returns a compound ``(state, freq_index)``;
        # the Icom backend returns a plain bool. The check only toggles the
        # on/off state, so collapse the read to its bool component. ``freq`` is
        # preserved because ``set_manual_notch`` writes only the on/off state.
        value = await dsp.get_manual_notch(0)
        if isinstance(value, tuple):
            return bool(value[0])
        return bool(value)

    return await _read_modify_verify_restore(
        radio,
        entry,
        read=_read_notch_bool,
        write=lambda value: dsp.set_manual_notch(value, 0),
        make_changed=lambda b: not b,
        per_check_timeout=per_check_timeout,
    )


# A representative "on" level used when toggling a level-encoded NB/NR control
# whose original value is 0 (off). Within range for both NB (0-10) and NR (0-15).
_NB_NR_TEST_LEVEL = 5


async def _check_blanker_reduction_set(
    radio: Radio,
    entry: CapabilityDeclarationEntry,
    *,
    name: str,
    per_check_timeout: float,
) -> CheckResult:
    """RMVR a noise blanker / noise reduction control.

    Prefers the boolean getter/setter (``get_nb``/``set_nb`` on Icom). When the
    radio exposes only the level variants (``get_nb_level``/``set_nb_level`` on
    the Yaesu/FTX-1 backend), fall back to those: toggle the level between off
    (0) and a representative on level, treating ``level > 0`` as "on" for the
    reaction comparison. Restores the original level on the way out.
    """
    bool_read = getattr(radio, f"get_{name}", None)
    bool_write = getattr(radio, f"set_{name}", None)
    if callable(bool_read) and callable(bool_write):
        return await _read_modify_verify_restore(
            radio,
            entry,
            read=lambda: cast(Awaitable[bool], bool_read()),
            write=cast(Callable[[bool], Awaitable[None]], bool_write),
            make_changed=lambda b: not b,
            per_check_timeout=per_check_timeout,
        )

    level_read = getattr(radio, f"get_{name}_level", None)
    level_write = getattr(radio, f"set_{name}_level", None)
    if callable(level_read) and callable(level_write):
        return await _read_modify_verify_restore(
            radio,
            entry,
            read=lambda: cast(Awaitable[int], level_read()),
            write=cast(Callable[[int], Awaitable[None]], level_write),
            make_changed=lambda v: 0 if v > 0 else _NB_NR_TEST_LEVEL,
            per_check_timeout=per_check_timeout,
            extra_evidence={"readback_via": f"get_{name}_level"},
        )

    return _base_result(
        entry,
        CheckStatus.UNSUPPORTED,
        evidence={
            "reason": (
                f"radio exposes set_{name} but no get_{name} or "
                f"get_{name}_level readback"
            )
        },
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
    return await _check_blanker_reduction_set(
        radio, entry, name="nb", per_check_timeout=per_check_timeout
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
    return await _check_blanker_reduction_set(
        radio, entry, name="nr", per_check_timeout=per_check_timeout
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
    ValueRule.TONE_FREQ_CYCLE: 88.5,
    ValueRule.VFO_AB_FLIP: "A",
    ValueRule.KEY_SPEED_WPM: 20,
    # T11 / MOR-646 — scope controls: index 1 and edge 1 are valid for every
    # scope preset surface; 0.0 dB is the neutral scope reference level.
    ValueRule.SCOPE_INDEX_FLIP: 1,
    ValueRule.SCOPE_EDGE_CYCLE: 1,
    ValueRule.SCOPE_REF_DB: 0.0,
    # MOR-671 — IF-shift in-band test value; contour on-state.
    ValueRule.SHIFT_HZ: 600,
    ValueRule.CONTOUR_FLIP: 1,
    # MOR-678 — MOD-input routing: LAN (index 3) is the documented digital
    # source and always a valid setting on DATA-OFF/1/2/3.
    ValueRule.MOD_SRC_FLIP: 3,
    # MOR-679 — CW pitch: a mid-band 600 Hz is always in 300-900 and on-grid.
    ValueRule.CW_PITCH_HZ: 600,
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
    ValueRule.NUDGE_FILTER: _nudge_filter,
    ValueRule.PREAMP_CYCLE: lambda v: 1 if v == 0 else 0,
    ValueRule.AGC_FLIP: lambda m: (
        int(AgcMode.SLOW) if m != AgcMode.SLOW else int(AgcMode.FAST)
    ),
    ValueRule.BUMP_HZ: lambda v: v + 100,
    # MOR-642..645 command-coverage families.
    # Flip between two standard CTCSS tones (Hz).
    ValueRule.TONE_FREQ_CYCLE: lambda f: 100.0 if float(f) == 88.5 else 88.5,
    # VFO slot select: "A" <-> "B".
    ValueRule.VFO_AB_FLIP: lambda s: "B" if str(s).upper() == "A" else "A",
    # CW keyer speed in WPM (real range 6-48): pick a DIFFERENT in-range value.
    ValueRule.KEY_SPEED_WPM: lambda w: 24 if int(w) != 24 else 20,
    # T11 / MOR-646 — scope controls.
    # Small 0-based preset/enum (receiver 0-1, mode 0-3, span 0-7, speed 0-2,
    # center_type 0-2, rbw 0-2): flip 0 <-> 1, in range for ALL of them.
    ValueRule.SCOPE_INDEX_FLIP: lambda v: 1 if int(v) == 0 else 0,
    # Fixed-edge selection is 1-BASED (1..4): cycle 1 <-> 2, never write 0.
    ValueRule.SCOPE_EDGE_CYCLE: lambda e: 2 if int(e) != 2 else 1,
    # Reference level in dB on the radio's 0.5 dB grid: hop between two
    # exact grid values so the readback comparison can stay exact.
    ValueRule.SCOPE_REF_DB: lambda r: 5.0 if float(r) != 5.0 else 0.0,
    # MOR-671 — IF-shift: nudge +/-200 Hz, clamped to +/-1200 (never OOR).
    ValueRule.SHIFT_HZ: _nudge_if_shift,
    # MOR-671 — contour on/off: flip 0 <-> 1 (off <-> a valid on level).
    ValueRule.CONTOUR_FLIP: lambda v: 1 if int(v) == 0 else 0,
    # MOR-678 — MOD-input routing source select (enumerated, range 0-5).
    # Flip between two always-valid digital sources: USB (2) <-> LAN (3).
    # Never writes an invalid source; restores the original afterwards.
    ValueRule.MOD_SRC_FLIP: lambda v: 3 if int(v) != 3 else 2,
    # MOR-679 — CW pitch: nudge +/-50 Hz, clamped to 300-900 (never OOR).
    ValueRule.CW_PITCH_HZ: _nudge_cw_pitch,
    # MOR-672 — FTX-1 SQL-type select (CAT ``CT``): 0=off / 1=TONE / 2=TSQL.
    # Flip between the two always-valid active codes TONE (1) <-> TSQL (2);
    # never writes an invalid code; restores the original afterwards.
    ValueRule.SQL_TYPE_CYCLE: lambda v: 2 if int(v) != 2 else 1,
}

# Restore-safety predicates (MOR-659): when the CURRENT value of an RMVR
# control is outside the encoder's settable band, the check must SKIP without
# writing — a test value could never be restored from. Bounds mirror the real
# encoder: ``commands/tone.py::_encode_tone_freq`` (67.0-254.1 Hz, shared by
# ``set_tone_freq`` and ``set_tsql_freq``). The live IC-7610 read back 16.5 Hz
# (tone not configured), which aborted an entire validation run.
_VALUE_RULE_RESTORABLE: dict[str, Callable[[Any], bool]] = {
    ValueRule.TONE_FREQ_CYCLE: lambda v: 67.0 <= float(v) <= 254.1,
    # MOR-672 — only the valid ``CT`` SQL-type codes (0=off / 1=TONE / 2=TSQL)
    # are restorable; an out-of-range read must SKIP rather than write.
    ValueRule.SQL_TYPE_CYCLE: lambda v: 0 <= int(v) <= 2,
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
    prompter: InteractivePrompter | None = None,
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
        restorable = _VALUE_RULE_RESTORABLE.get(spec.value_rule)
        extra_evidence: dict[str, object] = {
            "handler": "generic",
            "value_rule": str(spec.value_rule),
            "kind": str(spec.kind),
        }
        # MOR-695 — level controls (STEP_LEVEL_255) must nudge inside the
        # control's settable band, not the fixed 0-255 ICOM scale. Resolve the
        # band from the radio profile and SKIP an out-of-band original rather
        # than risk an unrestorable write.
        if spec.value_rule == ValueRule.STEP_LEVEL_255:
            lo, hi = _resolve_level_range(radio, entry.check_id)
            make_changed = _range_aware_level_nudge(lo, hi)
            restorable = lambda v: lo <= int(v) <= hi  # noqa: E731
            extra_evidence["range_min"] = lo
            extra_evidence["range_max"] = hi
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
            extra_evidence=extra_evidence,
            restorable=restorable,
        )

    if spec.kind is CheckKind.WRITE_ONLY_OBSERVE:
        gate = _write_gate(radio, entry, allow_writes=allow_writes)
        if gate is not None:
            return gate
        return await _set_and_observe(
            radio, entry, spec, per_check_timeout=per_check_timeout
        )

    if spec.kind is CheckKind.MANUAL:
        return _manual_required_result(radio, entry, prompter=prompter)

    if spec.kind is CheckKind.AUDIO_PROBE:
        # Automated audio probes run in CI against FakeAudioBackend via
        # ``rigplane.validation.audio_checks`` — never auto-run on a live
        # radio. Generated templates declare them MANUAL_REQUIRED (pre-gate 2
        # above); this branch only fires for custom templates that mark an
        # AUDIO_PROBE check as supported.
        return _base_result(
            entry,
            CheckStatus.SKIP,
            evidence={
                "reason": (
                    "automated audio probe runs in CI via "
                    "rigplane.validation.audio_checks"
                )
            },
        )

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
