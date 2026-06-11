"""DSP and noise-processing checks: notch, NB, NR, AGC.

Registry positions 10-13.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
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
)
