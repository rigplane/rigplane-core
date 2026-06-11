"""Tone / tone-squelch checks: CTCSS repeater tone, TSQL, tone frequencies.

Command-coverage family T7 (MOR-642). The Icom-shaped checks drive ops that
exist on ``RepeaterControlCapable`` (``runtime/radio.py``):
``get/set_repeater_tone``, ``get/set_repeater_tsql``, ``get/set_tone_freq``,
``get/set_tsql_freq``. They are gated on the Icom-spelled ``repeater_tone`` /
``tsql`` capabilities (declared by IC-7610/X6200, NOT by the FTX-1).

The Yaesu FTX-1 exposes the same physical surface through a different CAT
abstraction (MOR-672): a single ``CT`` "SQL TYPE" select (0=off / 1=TONE /
2=TSQL) read/written by ``get/set_sql_type`` plus a read-only ``CN`` "CTCSS
TONE FREQUENCY" via ``get_ctcss_tone``. The Icom-spelled ``*_repeater_tone`` /
``*_tone_freq`` methods do NOT exist on the Yaesu backend, so the two
``sql_type``-gated checks below resolve there instead. ``ctcss_tone.read`` is a
READ_ONLY presence check: the FTX-1 ``CN`` tone-frequency surface has no setter
(``get_ctcss_tone`` is read-only) so no RMVR is possible.

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
    # ----- MOR-672: FTX-1 ``CT``/``CN`` tone surface (Yaesu abstraction) -----
    # sql_type.set — RMVR over the ``CT`` SQL-type select (0=off / 1=TONE /
    # 2=TSQL). Gated on the ``sql_type`` capability (FTX-1 only); resolves to
    # ``get_sql_type`` / ``set_sql_type`` on the Yaesu backend. The flip stays
    # between two always-valid codes (1<->2) and restores the original.
    CheckSpec(
        check_id="sql_type.set",
        capability="sql_type",
        kind=CheckKind.RMVR_SAFE_WRITE,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Cycle the SQL type (TONE<->TSQL) and verify readback; restore original.",
        protocol="sql_type",
        get_op="get_sql_type",
        set_op="set_sql_type",
        value_rule=ValueRule.SQL_TYPE_CYCLE,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
    # ctcss_tone.read — READ_ONLY presence check over the ``CN`` CTCSS tone
    # frequency. ``get_ctcss_tone`` is read-only (no setter), so this is a
    # read/presence check, NOT an RMVR. Gated on the ``sql_type`` capability.
    CheckSpec(
        check_id="ctcss_tone.read",
        capability="sql_type",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.READBACK,
        summary="Read the CTCSS tone frequency (CN); read-only surface, no setter.",
        protocol="sql_type",
        get_op="get_ctcss_tone",
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
