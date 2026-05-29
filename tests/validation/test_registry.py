"""Tests for the capability→check-spec registry (MOR-197).

Plain pytest functions — no class wrapper needed.
"""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from rigplane.core.capabilities import KNOWN_CAPABILITIES
from rigplane.validation.registry import (
    REGISTRY,
    REGISTRY_BY_ID,
    VALUE_RULES,
    CheckKind,
    CheckSpec,
    ValueRule,
    get_spec,
)
from rigplane.validation.schema import FailureDomain, ValidationLevel


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


def test_registry_has_21_entries():
    assert len(REGISTRY) == 21


def test_check_ids_unique():
    ids = [spec.check_id for spec in REGISTRY]
    assert len(ids) == len(set(ids))


def test_all_capabilities_known():
    allowed = {"", *KNOWN_CAPABILITIES}
    for spec in REGISTRY:
        assert spec.capability in allowed, (
            f"{spec.check_id!r}: capability {spec.capability!r} not in KNOWN_CAPABILITIES"
        )


def test_all_value_rules_in_closed_set():
    for spec in REGISTRY:
        assert spec.value_rule in VALUE_RULES, (
            f"{spec.check_id!r}: value_rule {spec.value_rule!r} not in VALUE_RULES"
        )


def test_tx_adjacent_blocked_implies_tx_adjacent():
    # Every TX_ADJACENT_BLOCKED must have tx_adjacent=True
    for spec in REGISTRY:
        if spec.kind is CheckKind.TX_ADJACENT_BLOCKED:
            assert spec.tx_adjacent is True, (
                f"{spec.check_id!r}: TX_ADJACENT_BLOCKED but tx_adjacent is False"
            )
    # ONLY tuner.tune and tx.ptt should have tx_adjacent=True
    tx_adjacent_ids = {spec.check_id for spec in REGISTRY if spec.tx_adjacent}
    assert tx_adjacent_ids == {"tuner.tune", "tx.ptt"}


def test_manual_and_blocked_have_no_set_op():
    blocked_kinds = {CheckKind.MANUAL, CheckKind.TX_ADJACENT_BLOCKED}
    for spec in REGISTRY:
        if spec.kind in blocked_kinds:
            assert spec.set_op is None, (
                f"{spec.check_id!r}: kind={spec.kind} but set_op={spec.set_op!r}"
            )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def test_get_spec_known_and_unknown():
    result = get_spec("freq.write")
    assert isinstance(result, CheckSpec)
    assert result.check_id == "freq.write"

    assert get_spec("nope") is None


def test_registry_by_id_contains_all():
    assert set(REGISTRY_BY_ID.keys()) == {spec.check_id for spec in REGISTRY}


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_checkspec_frozen():
    spec = get_spec("freq.write")
    assert spec is not None
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        spec.check_id = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_registry_order():
    first_four = [spec.check_id for spec in REGISTRY[:4]]
    assert first_four == [
        "discovery.identify",
        "freq.write",
        "freq.reverse_sync",
        "mode.set",
    ]


# ---------------------------------------------------------------------------
# Type correctness
# ---------------------------------------------------------------------------


def test_levels_and_domains_typed():
    for spec in REGISTRY:
        assert isinstance(spec.level, ValidationLevel), (
            f"{spec.check_id!r}: level is {type(spec.level)}"
        )
        assert isinstance(spec.failure_domain, FailureDomain), (
            f"{spec.check_id!r}: failure_domain is {type(spec.failure_domain)}"
        )


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


def test_import_guard_runs():
    import rigplane.validation.registry as registry_mod

    # Re-importing must not raise.
    importlib.reload(registry_mod)
    # Calling _validate_registry() directly must not raise.
    registry_mod._validate_registry()  # noqa: SLF001


# ---------------------------------------------------------------------------
# Spot-checks (table-driven)
# ---------------------------------------------------------------------------

_EXPECTED: list[tuple[str, dict]] = [
    (
        "freq.write",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.BASIC_CONTROL,
            "get_op": "get_freq",
            "set_op": "set_freq",
            "value_rule": ValueRule.BUMP_HZ,
            "tolerance": 0,
            "hamlib_token": "f",
            "protocol": None,
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "filter_width.set",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.CAPABILITY_MATRIX,
            "get_op": "get_filter_width",
            "set_op": "set_filter_width",
            "value_rule": ValueRule.NUDGE_FILTER,
            "tolerance": 50,
            "hamlib_token": None,
            "protocol": "filter_width",
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "rf_gain.set",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.CAPABILITY_MATRIX,
            "get_op": "get_rf_gain",
            "set_op": "set_rf_gain",
            "value_rule": ValueRule.STEP_LEVEL_255,
            "tolerance": 3,
            "hamlib_token": "RF",
            "protocol": "rf_gain",
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "af_level.set",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.CAPABILITY_MATRIX,
            "get_op": "get_af_level",
            "set_op": "set_af_level",
            "value_rule": ValueRule.STEP_LEVEL_255,
            "tolerance": 3,
            "hamlib_token": "AF",
            "protocol": "af_level",
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "squelch.set",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.CAPABILITY_MATRIX,
            "get_op": "get_squelch",
            "set_op": "set_squelch",
            "value_rule": ValueRule.STEP_LEVEL_255,
            "tolerance": 3,
            "hamlib_token": "SQL",
            "protocol": "squelch",
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "rit.set",
        {
            "kind": CheckKind.RMVR_SAFE_WRITE,
            "level": ValidationLevel.CAPABILITY_MATRIX,
            "get_op": "get_rit_frequency",
            "set_op": "set_rit_frequency",
            "value_rule": ValueRule.BUMP_HZ,
            "tolerance": 10,
            "hamlib_token": None,
            "protocol": "rit",
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "meters.read",
        {
            "kind": CheckKind.READ_ONLY,
            "level": ValidationLevel.COMPATIBILITY_SURFACES,
            "get_op": "get_s_meter",
            "set_op": None,
            "value_rule": ValueRule.TOGGLE_BOOL,
            "tolerance": 0,
            "hamlib_token": "STRENGTH",
            "protocol": None,
            "failure_domain": FailureDomain.READBACK,
            "tx_adjacent": False,
        },
    ),
    (
        "tuner.tune",
        {
            "kind": CheckKind.TX_ADJACENT_BLOCKED,
            "level": ValidationLevel.STRESS_RECOVERY,
            "get_op": None,
            "set_op": None,
            "value_rule": ValueRule.TOGGLE_BOOL,
            "tolerance": 0,
            "hamlib_token": None,
            "protocol": "tuner",
            "failure_domain": FailureDomain.COMMAND_EXECUTION,
            "tx_adjacent": True,
        },
    ),
    (
        "tx.ptt",
        {
            "kind": CheckKind.TX_ADJACENT_BLOCKED,
            "level": ValidationLevel.STRESS_RECOVERY,
            "get_op": None,
            "set_op": None,
            "value_rule": ValueRule.TOGGLE_BOOL,
            "tolerance": 0,
            "hamlib_token": "t",
            "protocol": None,
            "failure_domain": FailureDomain.COMMAND_EXECUTION,
            "tx_adjacent": True,
        },
    ),
]


@pytest.mark.parametrize("check_id,expected", _EXPECTED)
def test_spot_check_values(check_id: str, expected: dict):
    spec = get_spec(check_id)
    assert spec is not None, f"{check_id!r} not found in REGISTRY"
    for field_name, expected_value in expected.items():
        actual = getattr(spec, field_name)
        assert actual == expected_value, (
            f"{check_id!r}.{field_name}: expected {expected_value!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# ValueRule string round-trip
# ---------------------------------------------------------------------------


def test_value_rule_str_roundtrip():
    assert ValueRule.TOGGLE_BOOL == "toggle_bool"
    # Default value_rule on a minimal CheckSpec is TOGGLE_BOOL
    spec = CheckSpec(
        check_id="x",
        capability="",
        kind=CheckKind.READ_ONLY,
        level=ValidationLevel.DISCOVERY,
        failure_domain=FailureDomain.DISCOVERY,
        summary="test",
    )
    assert spec.value_rule == ValueRule.TOGGLE_BOOL
    assert spec.value_rule == "toggle_bool"
