"""Structural checks: discovery plus core frequency/mode control.

Registry positions 1-4. These checks have ``capability == ""`` — they are
always emitted as SUPPORTED regardless of the profile's capability set.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
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
)
