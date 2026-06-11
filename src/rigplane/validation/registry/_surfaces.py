"""Compatibility-surface checks: receive audio, scope/waterfall, meters.

Registry positions 17-19.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
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
)
