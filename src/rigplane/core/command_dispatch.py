"""Descriptor-backed command dispatch shared by every runtime drain.

Profiles remain authoritative for radio/model support and wire facts.  This
module owns only backend-neutral operation mechanics: public name, reviewed
Radio method, parameter binding, semantic target, timeout, and queue policy.
Every migrated operation is admitted, queued, invoked, and audited through the
same descriptor; drains must not add operation-specific branches.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol

from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    CommandSource,
    FieldPath,
)

__all__ = [
    "CommandDescriptor",
    "CommandUnsupportedError",
    "command_descriptor",
    "command_descriptors",
    "enqueue_command_intent",
    "execute_command_intent",
    "prepare_command_intent",
]


class CommandUnsupportedError(CommandError):
    """Raised before queueing when the active profile rejects an operation."""


class DispatchRadio(Protocol):
    def supports_command(self, command: str) -> bool: ...


class DispatchQueue(Protocol):
    def put_ordered(
        self,
        command: Any,
        *,
        future: asyncio.Future[None] | None = None,
    ) -> None: ...


Binder = Callable[[Mapping[str, Any]], dict[str, Any]]
TargetBuilder = Callable[[Mapping[str, Any]], FieldPath]


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    """One operation's backend-neutral execution mechanics."""

    name: str
    method_name: str
    bind: Binder
    target: TargetBuilder
    argument_names: tuple[str, ...]
    timeout: float = 10.0
    queue_policy: Literal["ordered"] = "ordered"

    def result(self, intent: CommandIntent) -> dict[str, Any]:
        return {name: intent.params[name] for name in self.argument_names}


def _bind_repeater_shift(params: Mapping[str, Any]) -> dict[str, Any]:
    raw_direction = params["direction"]
    raw_receiver = params.get("receiver", 0)
    if isinstance(raw_direction, bool) or not isinstance(raw_direction, int):
        raise ValueError("direction must be an integer 0-3")
    if not 0 <= raw_direction <= 3:
        raise ValueError("direction must be an integer 0-3")
    if isinstance(raw_receiver, bool) or not isinstance(raw_receiver, int):
        raise ValueError("receiver must be 0 or 1")
    if raw_receiver not in (0, 1):
        raise ValueError("receiver must be 0 or 1")
    return {
        "direction": int(raw_direction),
        "receiver": int(raw_receiver),
        "repeater_shift": int(raw_direction),
    }


def _repeater_shift_target(params: Mapping[str, Any]) -> FieldPath:
    return FieldPath.receiver(
        str(params["receiver"]), "operator_controls", "repeater_shift"
    )


_COMMAND_DESCRIPTORS: Mapping[str, CommandDescriptor] = MappingProxyType(
    {
        "set_repeater_shift": CommandDescriptor(
            name="set_repeater_shift",
            method_name="set_repeater_shift",
            bind=_bind_repeater_shift,
            target=_repeater_shift_target,
            argument_names=("direction", "receiver"),
        )
    }
)


def command_descriptors() -> Mapping[str, CommandDescriptor]:
    return _COMMAND_DESCRIPTORS


def command_descriptor(name: str) -> CommandDescriptor | None:
    return _COMMAND_DESCRIPTORS.get(name)


def enqueue_command_intent(
    queue: DispatchQueue,
    intent: CommandIntent,
    *,
    future: asyncio.Future[None],
) -> None:
    """Enqueue through the policy owned by the command descriptor."""

    descriptor = command_descriptor(intent.name)
    if descriptor is None:
        raise CommandError(f"no command descriptor for {intent.name!r}")
    if descriptor.queue_policy == "ordered":
        queue.put_ordered(intent, future=future)
        return
    raise CommandError(
        f"unsupported queue policy {descriptor.queue_policy!r} for {descriptor.name!r}"
    )


def prepare_command_intent(
    radio: DispatchRadio,
    name: str,
    params: Mapping[str, Any],
    *,
    source: CommandSource,
    command_id: str | None = None,
    session_id: str | None = None,
) -> CommandIntent:
    """Bind and admit one descriptor operation before CommandService state."""

    descriptor = command_descriptor(name)
    if descriptor is None:
        raise KeyError(f"no command descriptor for {name!r}")
    normalized = descriptor.bind(params)
    if session_id is not None:
        normalized["session_id"] = session_id
    if not radio.supports_command(descriptor.name):
        raise CommandUnsupportedError(
            f"command {descriptor.name!r} is not supported by active profile"
        )
    target = descriptor.target(normalized)
    return CommandIntent(
        id=command_id or f"{source}-{time.monotonic_ns()}",
        name=descriptor.name,
        params=normalized,
        source=source,
        target=target,
        priority="user",
        timeout=descriptor.timeout,
        pending_policy="scoped",
        expected_observations=(target,),
    )


async def execute_command_intent(radio: Any, intent: CommandIntent) -> None:
    """Invoke the reviewed Radio method for a descriptor-built intent."""

    descriptor = command_descriptor(intent.name)
    if descriptor is None:
        raise CommandError(f"no command descriptor for {intent.name!r}")
    method = getattr(radio, descriptor.method_name)
    await method(**{name: intent.params[name] for name in descriptor.argument_names})
