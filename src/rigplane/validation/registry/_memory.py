"""Memory / band-stack checks.

Command-coverage family T9 (MOR-644). This family is read-mostly by charter,
but the Icom CI-V memory surface is SET-only on the IC-7610: ``get_memory_mode``,
``get_memory_contents`` and ``get_bsr`` exist on the runtime but raise
``NotImplementedError`` (commands 0x08 / 0x1A 0x00 / 0x1A 0x01 have no GET
variant per the CI-V Reference Manual), and the write ops (``set_memory_mode``,
``memory_to_vfo``, ``set_bsr``) cannot be restored without a readback — i.e.
they are not RMVR-safe. The only honest automated representation is a MANUAL
check; the rest of the family is deferred until a radio with memory readback
lands (see PR body).
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # bsr.select — manual: CI-V band-stack write is SET-only (no readback).
    CheckSpec(
        check_id="bsr.select",
        capability="bsr",
        kind=CheckKind.MANUAL,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary=(
            "Operator verifies band-stack register select/recall on the rig; "
            "the CI-V command is SET-only (no GET variant) so automated "
            "readback is impossible."
        ),
        protocol="bsr",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=False,
    ),
)
