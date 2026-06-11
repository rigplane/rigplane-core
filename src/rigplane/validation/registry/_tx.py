"""TX-adjacent checks: antenna tuner and PTT.

Registry positions 20-21. Both require explicit operator authorization
(``CheckKind.TX_ADJACENT_BLOCKED``, ``tx_adjacent=True``).
"""

from __future__ import annotations

from rigplane.validation.registry._types import CheckKind, CheckSpec, ValueRule
from rigplane.validation.schema import FailureDomain, ValidationLevel

CHECKS: tuple[CheckSpec, ...] = (
    # 20 — tuner.tune
    CheckSpec(
        check_id="tuner.tune",
        capability="tuner",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary="Trigger the antenna tuner; requires explicit operator authorization.",
        protocol="tuner",
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token=None,
        tx_adjacent=True,
    ),
    # 21 — tx.ptt
    CheckSpec(
        check_id="tx.ptt",
        capability="tx",
        kind=CheckKind.TX_ADJACENT_BLOCKED,
        level=ValidationLevel.STRESS_RECOVERY,
        failure_domain=FailureDomain.COMMAND_EXECUTION,
        summary="Key the transmitter via PTT; requires explicit operator authorization.",
        protocol=None,
        get_op=None,
        set_op=None,
        value_rule=ValueRule.TOGGLE_BOOL,
        tolerance=0,
        hamlib_token="t",
        tx_adjacent=True,
    ),
)
