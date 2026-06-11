"""Tuning-offset and squelch checks: RIT, XIT, squelch threshold.

Registry positions 14-16.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
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
)
