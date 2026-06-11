"""Front-end gain and level checks: filter width, gains, preamp, attenuator.

Registry positions 5-9.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
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
)
