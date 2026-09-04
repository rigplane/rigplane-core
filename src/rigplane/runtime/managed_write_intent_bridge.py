"""Canonical admission intents for the remaining managed typed writes."""

from __future__ import annotations

from typing import TypeAlias

from rigplane.core.command_dispatch import bind_command_intent
from rigplane.core.state_pipeline_contracts import CommandIntent, CommandSource
from rigplane.runtime._poller_types import (
    SetAntenna1,
    SetAntenna2,
    SetCivOutputAnt,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetTunerStatus,
)

__all__ = ["ManagedWriteCommand", "managed_write_intent"]


ManagedWriteCommand: TypeAlias = (
    SetTunerStatus
    | SetAntenna1
    | SetAntenna2
    | SetRxAntennaAnt1
    | SetRxAntennaAnt2
    | SetCivOutputAnt
)


def managed_write_intent(
    command: ManagedWriteCommand,
    *,
    command_id: str,
    source: CommandSource,
    session_id: str | None = None,
) -> CommandIntent:
    """Map one frozen typed write to its canonical managed admission intent."""
    name: str
    params: dict[str, object]
    match command:
        case SetTunerStatus(value=value):
            if type(value) is not int or value not in (0, 1, 2):
                raise ValueError(f"tuner value must be 0, 1, or 2; got {value!r}")
            name = "set_tuner_status"
            params = {"value": value}
        case SetAntenna1(on=on):
            name = "set_antenna_1"
            params = {"on": on}
        case SetAntenna2(on=on):
            name = "set_antenna_2"
            params = {"on": on}
        case SetRxAntennaAnt1(on=on):
            name = "set_rx_antenna_ant1"
            params = {"on": on}
        case SetRxAntennaAnt2(on=on):
            name = "set_rx_antenna_ant2"
            params = {"on": on}
        case SetCivOutputAnt(on=on):
            name = "set_civ_output_ant"
            params = {"on": on}
        case _:
            raise TypeError(
                f"unsupported managed write command: {type(command).__name__}"
            )

    return bind_command_intent(
        name,
        params,
        source=source,
        command_id=command_id,
        session_id=session_id,
        timeout=2.0,
    )
