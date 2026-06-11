"""System checks: clock read, CW keyer speed, VOX, dial lock.

Command-coverage family T10 (MOR-645). Ops exist on ``SystemControlCapable``
(``get_system_date``/``get_system_time``, ``get/set_dial_lock``),
``CwControlCapable`` (``get/set_key_speed``, WPM) and ``VoiceControlCapable``
(``get_vox``, ``set_vox``, ``set_vox_gain``).

Safety classification:

* ``system_date.read``/``system_time.read`` are READ_ONLY — writing the rig
  clock is lossy (seconds drift between RMVR write and restore) and
  ``set_system_date``/``set_system_time`` take multi-argument tuples the
  generic single-value runner cannot drive; the SET side is deferred.
* ``vox.set``/``vox_gain.set`` are TX_ADJACENT_BLOCKED: enabling VOX (or
  raising its gain while the operator has VOX armed) can key the transmitter
  from ambient microphone audio, so both require explicit operator
  authorization and are never auto-actuated.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # system_date.read
    CheckSpec(
        check_id="system_date.read",
        capability="system_settings",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Read the rig system date and verify a plausible (year, month, day) result.",
        protocol="system_settings",
        get_op="get_system_date",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # system_time.read
    CheckSpec(
        check_id="system_time.read",
        capability="system_settings",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Read the rig system time and verify a plausible (hour, minute) result.",
        protocol="system_settings",
        get_op="get_system_time",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # key_speed.set
    CheckSpec(
        check_id="key_speed.set",
        capability="cw",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Nudge the CW keyer speed (WPM), verify readback, restore original.",
        protocol="cw",
        get_op="get_key_speed",
        set_op="set_key_speed",
        value_rule=ValueRule.KEY_SPEED_WPM,
        tolerance=1,
        hamlib_token="KEYSPD",
        tx_adjacent=False,
    ),
    # vox.read — safe readback of the VOX enable state.
    CheckSpec(
        check_id="vox.read",
        capability="vox",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Read the VOX on/off state without actuating it.",
        protocol="vox",
        get_op="get_vox",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # vox.set — TX-adjacent: enabling VOX can key TX from ambient audio.
    CheckSpec(
        check_id="vox.set",
        capability="vox",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary=(
            "Toggle VOX on/off; requires explicit operator authorization "
            "because an enabled VOX can key TX from ambient microphone audio."
        ),
        protocol="vox",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=True,
    ),
    # vox_gain.set — TX-adjacent: raising gain with VOX armed can key TX.
    CheckSpec(
        check_id="vox_gain.set",
        capability="vox",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary=(
            "Adjust VOX gain; requires explicit operator authorization "
            "because raising the gain while VOX is armed can key TX."
        ),
        protocol="vox",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=True,
    ),
    # dial_lock.set
    CheckSpec(
        check_id="dial_lock.set",
        capability="dial_lock",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the dial lock on/off and verify readback; restore original.",
        protocol="dial_lock",
        get_op="get_dial_lock",
        set_op="set_dial_lock",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
