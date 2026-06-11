"""Split / VFO-slot / dual-watch checks.

Command-coverage family T8 (MOR-643). Ops exist on ``SplitCapable``
(``get/set_split``), ``VfoSlotCapable`` (``get/set_vfo_slot`` — implemented on
``CoreRadio`` via ``DualRxRuntimeMixin``) and ``SystemControlCapable``
(``get/set_dual_watch``).

``swap_vfo_ab`` is deliberately absent: it is a zero-argument *action* op and
the generic runner only drives value-bearing get/set pairs — deferred until a
runner action-check kind exists. ``vfo_slot.set`` covers A/B select with full
RMVR readback, which subsumes the swap path's observable effect.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # split.set
    CheckSpec(
        check_id="split.set",
        capability="split",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle split operation on/off and verify readback; restore original.",
        protocol="split",
        get_op="get_split",
        set_op="set_split",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # vfo_slot.set — structural (capability ""): VFO A/B select is core
    # control like freq/mode; radios lacking the op resolve UNSUPPORTED.
    CheckSpec(
        check_id="vfo_slot.set",
        capability="",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.BASIC_CONTROL,
        failure_domain=FailureDomain.READBACK,
        summary="Flip the active VFO slot (A/B), verify readback, restore original.",
        protocol=None,
        get_op="get_vfo_slot",
        set_op="set_vfo_slot",
        value_rule=ValueRule.VFO_AB_FLIP,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # dual_watch.set
    CheckSpec(
        check_id="dual_watch.set",
        capability="dual_watch",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle dual watch on/off and verify readback; restore original.",
        protocol="dual_watch",
        get_op="get_dual_watch",
        set_op="set_dual_watch",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
