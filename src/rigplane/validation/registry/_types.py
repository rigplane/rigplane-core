"""Core types for the validation check registry.

Defines the closed set of check kinds and value-mutation rules plus the
``CheckSpec`` dataclass (pure data, no hardware dependencies).

Layer rule: imports only stdlib and ``rigplane.validation.schema``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rigplane.validation.schema import FailureDomain, ValidationLevel


# ---------------------------------------------------------------------------
# CheckKind
# ---------------------------------------------------------------------------


class CheckKind(StrEnum):
    """How a check exercises the radio under test."""

    READ_ONLY = "read_only"
    RMVR_SAFE_WRITE = "rmvr_safe_write"
    WRITE_ONLY_OBSERVE = "write_only_observe"
    TX_ADJACENT_BLOCKED = "tx_adjacent_blocked"
    MANUAL = "manual"
    # Automated audio-pipeline probe (GH #1650, MOR-639/640/641): executes in
    # CI against the deterministic audio fakes via
    # ``rigplane.validation.audio_checks`` — never auto-run on a live radio
    # (hardware templates carry these checks as MANUAL_REQUIRED).
    AUDIO_PROBE = "audio_probe"


# ---------------------------------------------------------------------------
# ValueRule
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
# VALUE_RULES
# ---------------------------------------------------------------------------

VALUE_RULES: frozenset[str] = frozenset(ValueRule)


# ---------------------------------------------------------------------------
# CheckSpec
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
