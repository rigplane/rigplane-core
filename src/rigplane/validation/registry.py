"""Capability→check-spec registry for the universal validation matrix.

Defines the closed set of check kinds and value-mutation rules, the
``CheckSpec`` dataclass (pure data, no hardware dependencies), and
``REGISTRY`` — the canonical 21-entry tuple that drives every validation
run regardless of radio model or backend.

Layer rule: imports only stdlib, ``rigplane.core.capabilities``, and
``rigplane.validation.schema``.  No backends, profiles, transports, or
hardware protocols.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.validation.schema import FailureDomain, ValidationLevel


# ---------------------------------------------------------------------------
# 2. CheckKind
# ---------------------------------------------------------------------------


class CheckKind(StrEnum):
    """How a check exercises the radio under test."""

    READ_ONLY = "read_only"
    RMVR_SAFE_WRITE = "rmvr_safe_write"
    WRITE_ONLY_OBSERVE = "write_only_observe"
    TX_ADJACENT_BLOCKED = "tx_adjacent_blocked"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# 3. ValueRule
# ---------------------------------------------------------------------------


class ValueRule(StrEnum):
    """How the runner mutates a value for the write-then-read-back cycle."""

    TOGGLE_BOOL = "toggle_bool"
    STEP_LEVEL_255 = "step_level_255"
    BUMP_HZ = "bump_hz"
    NUDGE_FILTER = "nudge_filter"
    PREAMP_CYCLE = "preamp_cycle"
    AGC_FLIP = "agc_flip"
    MODE_CYCLE = "mode_cycle"


# ---------------------------------------------------------------------------
# 4. VALUE_RULES
# ---------------------------------------------------------------------------

VALUE_RULES: frozenset[str] = frozenset(ValueRule)


# ---------------------------------------------------------------------------
# 5. CheckSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """Immutable specification for a single validation check."""

    check_id: str
    capability: str
    kind: CheckKind
    level: ValidationLevel
    failure_domain: FailureDomain
    summary: str
    protocol: str | None = None
    get_op: str | None = None
    set_op: str | None = None
    value_rule: str = ValueRule.TOGGLE_BOOL
    tolerance: int = 0
    hamlib_token: str | None = None
    tx_adjacent: bool = False


# ---------------------------------------------------------------------------
# 6. REGISTRY
# ---------------------------------------------------------------------------

REGISTRY: tuple[CheckSpec, ...] = (
    # 1 — discovery.identify
    CheckSpec(
        check_id="discovery.identify",
        capability="",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.DISCOVERY,
        failure_domain=FailureDomain.DISCOVERY,
        summary="Confirm the radio responds to a frequency query at connect time.",
        protocol=None,
        get_op="get_freq",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 2 — freq.write
    CheckSpec(
        check_id="freq.write",
        capability="",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.BASIC_CONTROL,
        failure_domain=FailureDomain.READBACK,
        summary="Write a frequency offset and read it back; restore original.",
        protocol=None,
        get_op="get_freq",
        set_op="set_freq",
        value_rule=ValueRule.BUMP_HZ,
        tolerance=0,
        hamlib_token="f",
        tx_adjacent=False,
    ),
    # 3 — freq.reverse_sync
    CheckSpec(
        check_id="freq.reverse_sync",
        capability="",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.BASIC_CONTROL,
        failure_domain=FailureDomain.STATE_PUBLISHING,
        summary="Verify the radio state matches the frequency reported by the command layer.",
        protocol=None,
        get_op="get_freq",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 4 — mode.set
    CheckSpec(
        check_id="mode.set",
        capability="",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.BASIC_CONTROL,
        failure_domain=FailureDomain.READBACK,
        summary="Cycle through at least two modes and confirm readback matches.",
        protocol=None,
        get_op="get_mode",
        set_op="set_mode",
        value_rule=ValueRule.MODE_CYCLE,
        tolerance=0,
        hamlib_token="m",
        tx_adjacent=False,
    ),
    # 5 — filter_width.set
    CheckSpec(
        check_id="filter_width.set",
        capability="filter_width",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Nudge the IF filter width and verify the radio accepts the change.",
        protocol="filter_width",
        get_op="get_filter_width",
        set_op="set_filter_width",
        value_rule=ValueRule.NUDGE_FILTER,
        tolerance=50,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 6 — rf_gain.set
    CheckSpec(
        check_id="rf_gain.set",
        capability="rf_gain",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the RF gain level and confirm readback within tolerance.",
        protocol="rf_gain",
        get_op="get_rf_gain",
        set_op="set_rf_gain",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token="RF",
        tx_adjacent=False,
    ),
    # 7 — af_level.set
    CheckSpec(
        check_id="af_level.set",
        capability="af_level",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the AF volume level and confirm readback within tolerance.",
        protocol="af_level",
        get_op="get_af_level",
        set_op="set_af_level",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token="AF",
        tx_adjacent=False,
    ),
    # 8 — preamp.set
    CheckSpec(
        check_id="preamp.set",
        capability="preamp",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Cycle the preamplifier to the next level and verify readback.",
        protocol="preamp",
        get_op="get_preamp",
        set_op="set_preamp",
        value_rule=ValueRule.PREAMP_CYCLE,
        tolerance=0,
        hamlib_token="PREAMP",
        tx_adjacent=False,
    ),
    # 9 — attenuator.set
    CheckSpec(
        check_id="attenuator.set",
        capability="attenuator",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the attenuator on then off and confirm both readbacks.",
        protocol="attenuator",
        get_op="get_attenuator",
        set_op="set_attenuator",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="ATT",
        tx_adjacent=False,
    ),
    # 10 — notch.set
    CheckSpec(
        check_id="notch.set",
        capability="notch",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the manual notch filter and verify readback.",
        protocol="notch",
        get_op="get_manual_notch",
        set_op="set_manual_notch",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 11 — nb.set
    CheckSpec(
        check_id="nb.set",
        capability="nb",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the noise blanker and confirm the readback matches.",
        protocol="nb",
        get_op="get_nb",
        set_op="set_nb",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="NB",
        tx_adjacent=False,
    ),
    # 12 — nr.set
    CheckSpec(
        check_id="nr.set",
        capability="nr",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the noise reduction function and confirm the readback matches.",
        protocol="nr",
        get_op="get_nr",
        set_op="set_nr",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="NR",
        tx_adjacent=False,
    ),
    # 13 — agc.set
    CheckSpec(
        check_id="agc.set",
        capability="agc",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Flip the AGC speed setting and verify the readback differs.",
        protocol="agc",
        get_op="get_agc",
        set_op="set_agc",
        value_rule=ValueRule.AGC_FLIP,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 14 — rit.set
    CheckSpec(
        check_id="rit.set",
        capability="rit",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Bump the RIT offset and confirm readback is within tolerance.",
        protocol="rit",
        get_op="get_rit_frequency",
        set_op="set_rit_frequency",
        value_rule=ValueRule.BUMP_HZ,
        tolerance=10,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 15 — xit.set
    CheckSpec(
        check_id="xit.set",
        capability="xit",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the XIT transmit offset and verify readback.",
        protocol="xit",
        get_op="get_rit_tx_status",
        set_op="set_rit_tx_status",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 16 — squelch.set
    CheckSpec(
        check_id="squelch.set",
        capability="squelch",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the squelch threshold and confirm readback within tolerance.",
        protocol="squelch",
        get_op="get_squelch",
        set_op="set_squelch",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token="SQL",
        tx_adjacent=False,
    ),
    # 17 — audio.rx
    CheckSpec(
        check_id="audio.rx",
        capability="audio",
        kind=CheckKind.MANUAL,
        level=ValidationLevel.COMPATIBILITY_SURFACES,
        failure_domain=FailureDomain.AUDIO,
        summary="Operator confirms receive audio is present and artifact-free.",
        protocol="audio",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 18 — scope.capture
    CheckSpec(
        check_id="scope.capture",
        capability="scope",
        kind=CheckKind.MANUAL,
        level=ValidationLevel.COMPATIBILITY_SURFACES,
        failure_domain=FailureDomain.SCOPE_WATERFALL,
        summary="Operator confirms the scope/waterfall renders a live spectrum trace.",
        protocol="scope",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # 19 — meters.read
    CheckSpec(
        check_id="meters.read",
        capability="meters",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.COMPATIBILITY_SURFACES,
        failure_domain=FailureDomain.READBACK,
        summary="Read the S-meter value and verify a plausible numeric result.",
        protocol=None,
        get_op="get_s_meter",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="STRENGTH",
        tx_adjacent=False,
    ),
    # 20 — tuner.tune
    CheckSpec(
        check_id="tuner.tune",
        capability="tuner",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary="Trigger the antenna tuner; requires explicit operator authorization.",
        protocol="tuner",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=True,
    ),
    # 21 — tx.ptt
    CheckSpec(
        check_id="tx.ptt",
        capability="tx",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary="Key the transmitter via PTT; requires explicit operator authorization.",
        protocol=None,
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="t",
        tx_adjacent=True,
    ),
)


# ---------------------------------------------------------------------------
# 7. REGISTRY_BY_ID
# ---------------------------------------------------------------------------

REGISTRY_BY_ID: dict[str, CheckSpec] = {spec.check_id: spec for spec in REGISTRY}


# ---------------------------------------------------------------------------
# 8. get_spec
# ---------------------------------------------------------------------------


def get_spec(check_id: str) -> CheckSpec | None:
    """Return the ``CheckSpec`` for *check_id*, or ``None`` if not found."""
    return REGISTRY_BY_ID.get(check_id)


# ---------------------------------------------------------------------------
# 9. Import-time guard
# ---------------------------------------------------------------------------


def _validate_registry() -> None:
    """Raise ``ValueError`` if any registry invariant is violated."""
    ids = [spec.check_id for spec in REGISTRY]
    if len(set(ids)) != len(ids):
        raise ValueError("REGISTRY contains duplicate check_ids")

    blocked_kinds = {CheckKind.MANUAL, CheckKind.TX_ADJACENT_BLOCKED}

    for spec in REGISTRY:
        if spec.capability and spec.capability not in KNOWN_CAPABILITIES:
            raise ValueError(
                f"check_id {spec.check_id!r}: capability {spec.capability!r} "
                "is not in KNOWN_CAPABILITIES"
            )
        if spec.value_rule not in VALUE_RULES:
            raise ValueError(
                f"check_id {spec.check_id!r}: value_rule {spec.value_rule!r} "
                "is not in VALUE_RULES"
            )
        if spec.kind is CheckKind.TX_ADJACENT_BLOCKED and not spec.tx_adjacent:
            raise ValueError(
                f"check_id {spec.check_id!r}: kind is TX_ADJACENT_BLOCKED "
                "but tx_adjacent is False"
            )
        if spec.kind in blocked_kinds and spec.set_op is not None:
            raise ValueError(
                f"check_id {spec.check_id!r}: kind={spec.kind} "
                f"but set_op={spec.set_op!r} (must be None)"
            )


_validate_registry()


# ---------------------------------------------------------------------------
# 10. __all__
# ---------------------------------------------------------------------------

__all__ = [
    "CheckKind",
    "ValueRule",
    "VALUE_RULES",
    "CheckSpec",
    "REGISTRY",
    "REGISTRY_BY_ID",
    "get_spec",
]
