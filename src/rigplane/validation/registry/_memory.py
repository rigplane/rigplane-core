"""Memory / band-stack checks.

Command-coverage family T9 (MOR-644). This family is read-mostly by charter,
but ``CoreRadio.get_memory_mode`` and ``CoreRadio.get_memory_contents`` raise
``NotImplementedError`` (pinned by the ``*_still_raises_not_implemented``
tests in tests/test_memory_commands.py), so ``set_memory_mode`` and
``memory_to_vfo`` have no readback and memory-channel checks stay deferred.
``CoreRadio.get_bsr`` works (MOR-681); ``bsr.select`` stays MANUAL because it
asks the operator to confirm the band display changed, and whether an
automated BSR check should exist is an open decision — issue #2910.
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    CheckSpec(
        check_id="bsr.select",
        capability="bsr",
        kind=CheckKind.MANUAL,
        level=ValidationLevel.CAPABILITY_MATRIX,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary=(
            "Operator verifies band-stack register select/recall on the rig; "
            "the prompt confirms the rig's band display changed, which a "
            "register readback does not confirm."
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
