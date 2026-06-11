"""Front-end gain and level checks: filter width, gains, preamp, attenuator.

Registry positions 5-9, plus the MOR-679 Icom level RMVR cluster (rf_power,
mic_gain, comp_level, nr_level, nb_level, cw_pitch) appended below the
historical block.
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
    # ----- MOR-679: Icom level RMVR cluster (shared CoreRadio; serves both -----
    # ----- IC-7610 and IC-7300). Append-only; the template generators ----------
    # ----- stable-sort by level so these slot into CAPABILITY_MATRIX in --------
    # ----- registry order without disturbing positions 5-9. All ranges are -----
    # ----- 0-255 (STEP_LEVEL_255) except CW pitch (300-900 Hz, CW_PITCH_HZ). ---
    # rf_power.set — RF power 14 0A
    CheckSpec(
        check_id="rf_power.set",
        capability="power_control",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the RF output power level and confirm readback within tolerance.",
        protocol="power_control",
        get_op="get_rf_power",
        set_op="set_rf_power",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # mic_gain.set — MIC gain 14 0B
    CheckSpec(
        check_id="mic_gain.set",
        capability="power_control",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the microphone gain level and confirm readback within tolerance.",
        protocol="power_control",
        get_op="get_mic_gain",
        set_op="set_mic_gain",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # comp_level.set — speech compressor level 14 0E
    CheckSpec(
        check_id="comp_level.set",
        capability="compressor",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the speech-compressor level and confirm readback within tolerance.",
        protocol="compressor",
        get_op="get_compressor_level",
        set_op="set_compressor_level",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # nr_level.set — noise-reduction level 14 06
    CheckSpec(
        check_id="nr_level.set",
        capability="nr",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the noise-reduction level and confirm readback within tolerance.",
        protocol="nr",
        get_op="get_nr_level",
        set_op="set_nr_level",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # nb_level.set — noise-blanker level 14 12
    CheckSpec(
        check_id="nb_level.set",
        capability="nb",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Step the noise-blanker level and confirm readback within tolerance.",
        protocol="nb",
        get_op="get_nb_level",
        set_op="set_nb_level",
        value_rule=ValueRule.STEP_LEVEL_255,
        tolerance=3,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # cw_pitch.set — CW sidetone pitch 14 09 (300-900 Hz)
    CheckSpec(
        check_id="cw_pitch.set",
        capability="cw",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Nudge the CW pitch in Hz and confirm readback within tolerance.",
        protocol="cw",
        get_op="get_cw_pitch",
        set_op="set_cw_pitch",
        value_rule=ValueRule.CW_PITCH_HZ,
        tolerance=5,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
