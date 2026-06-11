"""Assembly of the canonical REGISTRY from the per-domain check modules.

Concatenation order is part of the public contract: it must reproduce the
historical monolithic ``registry.py`` order exactly (positions 1-21), because
the template generators stable-sort by level and preserve registry order
within each level.
"""

from __future__ import annotations

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.validation.registry._dsp import CHECKS as _DSP_CHECKS
from rigplane.validation.registry._levels import CHECKS as _LEVELS_CHECKS
from rigplane.validation.registry._memory import CHECKS as _MEMORY_CHECKS
from rigplane.validation.registry._structural import CHECKS as _STRUCTURAL_CHECKS
from rigplane.validation.registry._surfaces import CHECKS as _SURFACES_CHECKS
from rigplane.validation.registry._tone import CHECKS as _TONE_CHECKS
from rigplane.validation.registry._tuning import CHECKS as _TUNING_CHECKS
from rigplane.validation.registry._tx import CHECKS as _TX_CHECKS
from rigplane.validation.registry._types import VALUE_RULES, CheckKind, CheckSpec
from rigplane.validation.registry._vfo import CHECKS as _VFO_CHECKS

# ---------------------------------------------------------------------------
# REGISTRY
# ---------------------------------------------------------------------------

REGISTRY: tuple[CheckSpec, ...] = (
    _STRUCTURAL_CHECKS  # 1-4: discovery, freq, mode
    + _LEVELS_CHECKS  # 5-9: filter_width, rf_gain, af_level, preamp, attenuator
    + _DSP_CHECKS  # 10-13: notch, nb, nr, agc
    + _TUNING_CHECKS  # 14-16: rit, xit, squelch
    + _SURFACES_CHECKS  # 17-19: audio, scope, meters
    + _TX_CHECKS  # 20-21: tuner, tx
    # --- MOR-642..645 command-coverage families (append-only; generators
    # stable-sort by level so new checks slot in without reordering 1-21) ---
    + _TONE_CHECKS  # T7: repeater_tone, tone_freq, tsql, tsql_freq
    + _VFO_CHECKS  # T8: split, vfo_slot, dual_watch
    + _MEMORY_CHECKS  # T9: bsr (manual; CI-V memory surface is SET-only)
)


# ---------------------------------------------------------------------------
# REGISTRY_BY_ID
# ---------------------------------------------------------------------------

REGISTRY_BY_ID: dict[str, CheckSpec] = {spec.check_id: spec for spec in REGISTRY}


# ---------------------------------------------------------------------------
# get_spec
# ---------------------------------------------------------------------------


def get_spec(check_id: str) -> CheckSpec | None:
    """Return the ``CheckSpec`` for *check_id*, or ``None`` if not found."""
    return REGISTRY_BY_ID.get(check_id)


# ---------------------------------------------------------------------------
# Import-time guard
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
