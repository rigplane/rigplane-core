"""Tone / tone-squelch checks: CTCSS repeater tone, TSQL, tone frequencies.

Command-coverage family T7 (MOR-642). All four checks drive ops that exist on
``RepeaterControlCapable`` (``runtime/radio.py``): ``get/set_repeater_tone``,
``get/set_repeater_tsql``, ``get/set_tone_freq``, ``get/set_tsql_freq``.

DTCS/DCS code select is deliberately absent: the Radio protocol has no
``get_dtcs_code``/``set_dtcs_code`` yet (capability tag ``dtcs`` exists but is
method-less) — deferred as add-method-then-add-check.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # repeater_tone.set
    CheckSpec(
        check_id="repeater_tone.set",
        capability="repeater_tone",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle the CTCSS repeater tone on/off and verify readback.",
        protocol="repeater_tone",
        get_op="get_repeater_tone",
        set_op="set_repeater_tone",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # tone_freq.set
    CheckSpec(
        check_id="tone_freq.set",
        capability="repeater_tone",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Cycle the CTCSS tone frequency to another standard tone and verify readback.",
        protocol="repeater_tone",
        get_op="get_tone_freq",
        set_op="set_tone_freq",
        value_rule=ValueRule.TONE_FREQ_CYCLE,
        tolerance=1,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # tsql.set
    CheckSpec(
        check_id="tsql.set",
        capability="tsql",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Toggle tone squelch (TSQL) on/off and verify readback.",
        protocol="tsql",
        get_op="get_repeater_tsql",
        set_op="set_repeater_tsql",
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # tsql_freq.set
    CheckSpec(
        check_id="tsql_freq.set",
        capability="tsql",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Cycle the TSQL frequency to another standard tone and verify readback.",
        protocol="tsql",
        get_op="get_tsql_freq",
        set_op="set_tsql_freq",
        value_rule=ValueRule.TONE_FREQ_CYCLE,
        tolerance=1,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
