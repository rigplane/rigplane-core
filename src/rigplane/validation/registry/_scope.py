"""Scope-control SET-command checks: the Command 0x27 control surface.

Command-coverage family T11 (MOR-646). All ops live on ``ScopeCapable``
(``rigplane.core.radio_protocol``) and are implemented for the IC-7610 in
``rigplane.runtime._scope_runtime``.

Safety classification:

* Every SET here only changes what the spectrum scope DISPLAYS — receiver
  selection, display mode, span/edge presets, reference level, sweep speed,
  hold, RBW/VBW, dual display, and the during-TX display flag. None can key
  the transmitter, so none is TX-adjacent. All are RMVR-safe: each setter has
  a matching getter, so the runner reads the original, writes a test value,
  verifies the readback, and restores.
* ``scope_fixed_edge.read`` is READ_ONLY: ``set_scope_fixed_edge`` exists but
  takes multi-keyword arguments (``edge``, ``start_hz``, ``end_hz``) the
  generic single-value runner cannot drive, so its SET side is deferred.
* ``enable_scope``/``disable_scope`` are stream-lifecycle ops, not SET-value
  commands; the scope surface itself is covered by ``scope.capture``.

Value encodings (IC-7610 runtime):

* receiver: 0=MAIN, 1=SUB · mode: 0=center, 1=fixed, 2/3=scroll
* span: preset index 0..7 · edge: 1..4 (1-based) · speed: 0..2 · rbw: 0..2
* center_type: 0..2 · ref: dB float on a 0.5 dB grid · the rest are bools
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel


def _rmvr(check_id: str, op: str, summary: str, value_rule: ValueRule) -> CheckSpec:
    """Build one RMVR scope-control spec; only the op stem and rule vary."""
    return CheckSpec(
        check_id=check_id,
        capability="scope",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary=summary,
        protocol="scope",
        get_op=f"get_{op}",
        set_op=f"set_{op}",
        value_rule=value_rule,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    )


CHECKS: tuple[CheckSpec, ...] = (
    # scope_receiver.set — MAIN/SUB scope source selection (0/1).
    _rmvr(
        "scope_receiver.set",
        "scope_receiver",
        "Flip the scope receiver MAIN/SUB, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_dual.set — single/dual scope display.
    _rmvr(
        "scope_dual.set",
        "scope_dual",
        "Toggle dual scope display on/off and verify readback; restore original.",
        ValueRule.TOGGLE_BOOL,
    ),
    # scope_mode.set — center/fixed display mode (flips 0<->1).
    _rmvr(
        "scope_mode.set",
        "scope_mode",
        "Flip the scope mode center/fixed, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_span.set — span preset index 0..7 (flips 0<->1, always in range).
    _rmvr(
        "scope_span.set",
        "scope_span",
        "Nudge the scope span preset index, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_edge.set — fixed-edge selection, 1-based (cycles within 1..4).
    _rmvr(
        "scope_edge.set",
        "scope_edge",
        "Cycle the fixed-edge selection (1..4), verify readback, restore original.",
        ValueRule.SCOPE_EDGE_CYCLE,
    ),
    # scope_hold.set — scope hold on/off.
    _rmvr(
        "scope_hold.set",
        "scope_hold",
        "Toggle scope hold on/off and verify readback; restore original.",
        ValueRule.TOGGLE_BOOL,
    ),
    # scope_ref.set — reference level in dB on a 0.5 dB grid.
    _rmvr(
        "scope_ref.set",
        "scope_ref",
        "Move the scope reference level (dB), verify readback, restore original.",
        ValueRule.SCOPE_REF_DB,
    ),
    # scope_speed.set — sweep speed preset 0..2 (flips 0<->1, always in range).
    _rmvr(
        "scope_speed.set",
        "scope_speed",
        "Flip the scope sweep-speed preset, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_during_tx.set — display-only flag; never keys TX, not TX-adjacent.
    _rmvr(
        "scope_during_tx.set",
        "scope_during_tx",
        "Toggle the scope-during-TX display flag and verify readback; restore.",
        ValueRule.TOGGLE_BOOL,
    ),
    # scope_center_type.set — center display type 0..2 (flips 0<->1).
    _rmvr(
        "scope_center_type.set",
        "scope_center_type",
        "Flip the scope center display type, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_vbw.set — narrow video bandwidth on/off.
    _rmvr(
        "scope_vbw.set",
        "scope_vbw",
        "Toggle narrow scope VBW on/off and verify readback; restore original.",
        ValueRule.TOGGLE_BOOL,
    ),
    # scope_rbw.set — resolution bandwidth preset 0..2 (flips 0<->1).
    _rmvr(
        "scope_rbw.set",
        "scope_rbw",
        "Flip the scope RBW preset, verify readback, restore original.",
        ValueRule.SCOPE_INDEX_FLIP,
    ),
    # scope_fixed_edge.read — READ_ONLY: set_scope_fixed_edge takes multi-kwarg
    # (edge, start_hz, end_hz) the generic single-value runner cannot drive;
    # the SET side is deferred (cf. system_date/system_time in _system.py).
    CheckSpec(
        check_id="scope_fixed_edge.read",
        capability="scope",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Read the fixed-edge scope bounds and verify a parsed result.",
        protocol="scope",
        get_op="get_scope_fixed_edge",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
