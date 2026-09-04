from __future__ import annotations

from typing import Any, cast

import pytest

from rigplane.runtime._poller_types import (
    SetAfLevel,
    SetAntenna1,
    SetAntenna2,
    SetCivOutputAnt,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetTunerStatus,
)
from rigplane.runtime.managed_write_intent_bridge import (
    ManagedWriteCommand,
    managed_write_intent,
)


@pytest.mark.parametrize(
    ("command", "name", "params", "target"),
    [
        (
            SetTunerStatus(0),
            "set_tuner_status",
            {"value": 0},
            "global.operator_controls.tuner_status",
        ),
        (
            SetTunerStatus(1),
            "set_tuner_status",
            {"value": 1},
            "global.operator_controls.tuner_status",
        ),
        (
            SetTunerStatus(2),
            "set_tuner_status",
            {"value": 2},
            "global.operator_controls.tuner_status",
        ),
        (
            SetAntenna1(True),
            "set_antenna_1",
            {"on": True, "rx_antenna_1": True},
            "global.slow_state.rx_antenna_1",
        ),
        (
            SetAntenna2(False),
            "set_antenna_2",
            {"on": False, "rx_antenna_2": False},
            "global.slow_state.rx_antenna_2",
        ),
        (
            SetRxAntennaAnt1(False),
            "set_rx_antenna_ant1",
            {"on": False, "rx_antenna_1": False},
            "global.slow_state.rx_antenna_1",
        ),
        (
            SetRxAntennaAnt2(True),
            "set_rx_antenna_ant2",
            {"on": True, "rx_antenna_2": True},
            "global.slow_state.rx_antenna_2",
        ),
        (
            SetCivOutputAnt(True),
            "set_civ_output_ant",
            {"on": True, "civ_output_ant": True},
            "global.slow_state.civ_output_ant",
        ),
    ],
)
def test_frozen_typed_command_maps_to_canonical_admission_intent(
    command: ManagedWriteCommand,
    name: str,
    params: dict[str, Any],
    target: str | None,
) -> None:
    intent = managed_write_intent(
        command,
        command_id="cmd-17",
        source="websocket",
        session_id="session-a",
    )

    assert intent.id == "cmd-17"
    assert intent.source == "websocket"
    assert intent.name == name
    assert intent.params == {**params, "session_id": "session-a"}
    actual_target = None if intent.target is None else str(intent.target)
    assert actual_target == target


def test_missing_session_identity_is_not_invented() -> None:
    intent = managed_write_intent(
        SetCivOutputAnt(False),
        command_id="rigctld-8",
        source="rigctld",
    )

    assert intent.id == "rigctld-8"
    assert intent.source == "rigctld"
    assert "session_id" not in intent.params


@pytest.mark.parametrize("value", [-1, 3, True, 1.0, "1"])
def test_tuner_value_outside_closed_domain_fails_before_admission(value: Any) -> None:
    with pytest.raises(ValueError, match="tuner value must be 0, 1, or 2"):
        managed_write_intent(
            SetTunerStatus(cast(Any, value)),
            command_id="cmd-invalid",
            source="test",
        )


def test_descriptor_boolean_validation_is_not_duplicated_or_loosened() -> None:
    with pytest.raises(ValueError, match="rx_antenna_1 must be a bool"):
        managed_write_intent(
            SetAntenna1(cast(Any, 1)),
            command_id="cmd-invalid",
            source="test",
        )


def test_unmapped_typed_command_fails_closed_instead_of_falling_through() -> None:
    with pytest.raises(TypeError, match="unsupported managed write command"):
        managed_write_intent(
            cast(ManagedWriteCommand, SetAfLevel(17)),
            command_id="cmd-unmapped",
            source="test",
        )
