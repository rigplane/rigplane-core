"""Descriptor-backed command dispatch shared by every runtime drain.

Profiles remain authoritative for radio/model support and wire facts.  This
module owns only backend-neutral operation mechanics: public name, reviewed
Radio method, parameter binding, semantic target, timeout, queue policy, and
explicit TX policy.  Every migrated operation is admitted, queued, invoked,
and audited through the same descriptor; drains must not add operation-specific
branches.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
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
    "DescriptorTxPolicy",
    "ManagedWriteAdmission",
    "bind_command_intent",
    "command_descriptor",
    "command_descriptors",
    "enqueue_command_intent",
    "execute_command_intent",
    "prepare_command_intent",
]


class CommandUnsupportedError(CommandError):
    """Raised before queueing when the active profile rejects an operation."""


class DispatchRadio(Protocol):
    def supports_command(
        self, command: str, *, receiver: int | None = None
    ) -> bool: ...


class DispatchQueue(Protocol):
    def put(
        self,
        command: Any,
        *,
        command_id: str | None = None,
        source: CommandSource | None = None,
        session_id: str | None = None,
        command_service: Any | None = None,
    ) -> None: ...

    def put_ordered(
        self,
        command: Any,
        *,
        future: asyncio.Future[None] | None = None,
        command_id: str | None = None,
        source: CommandSource | None = None,
        session_id: str | None = None,
        command_service: Any | None = None,
    ) -> object: ...


class ManagedWriteAdmission(Protocol):
    async def admit_managed_write(self, intent: CommandIntent) -> bool: ...


Binder = Callable[[Mapping[str, Any]], dict[str, Any]]
TargetBuilder = Callable[[Mapping[str, Any]], FieldPath]
ExpectationProjector = Callable[[Any, Mapping[str, Any]], dict[str, Any]]


class DescriptorTxPolicy(StrEnum):
    """TX policies admitted by descriptor-backed dispatch."""

    ALWAYS_PASS = "always_pass"
    TX_SAFE = "tx_safe"
    ANTENNA_SWITCH = "antenna_switch"


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    """One operation's backend-neutral execution mechanics."""

    name: str
    method_name: str
    bind: Binder
    target: TargetBuilder
    argument_names: tuple[str, ...]
    tx_policy: DescriptorTxPolicy
    public_names: tuple[str, ...]
    timeout: float = 10.0
    queue_policy: Literal["ordered", "coalesced"] = "ordered"
    receiver_aware: bool = False
    project_expectation: ExpectationProjector | None = None

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


def _raw_int_level_from_param(value: Any) -> int:
    """Coerce a raw-only level command param (MOR-1579).

    ``set_rf_gain``/``set_sql``/``set_squelch``: both the web frontend
    (``radio-intents.ts`` declares ``'integer'``) and the documented
    HTTP/WS command catalog agree the wire value is always a raw 0-255
    integer, never a normalized float. Dispatch on the JSON *type*, not
    magnitude — a value in ``[0, 1]`` used to be silently reinterpreted as
    normalized (MOR-1579's headline bug: raw level ``1`` became raw
    ``255``). A non-int or an out-of-range int is a caller bug, not an
    alternate encoding, so it raises instead of being coerced.

    This value feeds both the StateStore readback expectation (via
    :func:`_expected_value_for_path`) *and*, on the ``public_api`` sync
    ingress (:mod:`rigplane.runtime.sync`), the actual value sent to the
    radio (``_SyncCommandExecutor`` reads ``intent.params["squelch"]``
    directly) — so this function is the actuation path there, not just
    bookkeeping.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            f"level {value!r} must be a raw integer 0-255, not {type(value).__name__}"
        )
    if not (0 <= value <= 255):
        raise ValueError(f"level {value!r} is out of the raw 0-255 domain")
    return int(value)


def _af_level_from_param(value: Any) -> int:
    """Coerce ``set_af_level``'s type-dispatched level param (MOR-1579).

    Two documented wire contracts coexist for this one intent: the
    HTTP/WS command catalog (``docs/api/command-catalog.md``) declares
    ``level: int`` on the raw 0-255 scale (see the live-hardware
    validation recipe's ``level:35`` example, which expects raw BCD
    ``0035``); the web frontend (``radio-intents.ts`` declares
    ``'normalized'``) sends a JSON float in 0.0-1.0. Dispatch on JSON
    type, never magnitude: an int is always raw, a float is always
    normalized, matching MOR-334's original coercion for float input
    while restoring int input to a true no-op. Out-of-domain values for
    either type raise rather than being reinterpreted as the other.
    """
    if isinstance(value, bool):
        raise ValueError(f"level {value!r} must be an int or a normalized float")
    if isinstance(value, int):
        if not (0 <= value <= 255):
            raise ValueError(f"level {value!r} is out of the raw 0-255 domain")
        return value
    if isinstance(value, float):
        if not (0.0 <= value <= 1.0):
            raise ValueError(f"level {value!r} is out of the normalized 0.0-1.0 domain")
        return max(0, min(255, round(value * 255)))
    raise ValueError(f"level {value!r} must be an int or a normalized float")


def _bind_level(field: str, params: Mapping[str, Any]) -> dict[str, Any]:
    normalize = (
        _af_level_from_param if field == "af_level" else _raw_int_level_from_param
    )
    level = normalize(params["level"])
    return {"level": level, field: level, "receiver": int(params.get("receiver", 0))}


def _level_target(field: str, params: Mapping[str, Any]) -> FieldPath:
    return FieldPath.receiver(str(params["receiver"]), "operator_controls", field)


def _bind_attenuator(params: Mapping[str, Any]) -> dict[str, Any]:
    raw_db = params["level"] if "level" in params else params.get("db", 0)
    db = int(raw_db)
    return {
        "db": db,
        "att": db,
        "receiver": int(params.get("receiver", 0)),
    }


def _attenuator_target(params: Mapping[str, Any]) -> FieldPath:
    return FieldPath.receiver(str(params["receiver"]), "operator_controls", "att")


def _project_attenuator_expectation(
    radio: Any, params: Mapping[str, Any]
) -> dict[str, Any]:
    projector = getattr(radio, "project_attenuator_observation_value", None)
    if not callable(projector):
        raise CommandError(
            "active radio does not provide attenuator observation projection"
        )
    projected = projector(params["db"])
    if type(projected) is not int:
        raise CommandError("attenuator observation projection must return an exact int")
    normalized = dict(params)
    normalized["att"] = projected
    return normalized


def _bind_boolean(field: str, params: Mapping[str, Any]) -> dict[str, Any]:
    value = params["on"] if "on" in params else params["enabled"]
    if type(value) is not bool:
        raise ValueError(f"{field} must be a bool")
    return {"enabled": value, "on": value, field: value}


def _global_slow_state_target(field: str, _params: Mapping[str, Any]) -> FieldPath:
    return FieldPath.global_("slow_state", field)


_COMMAND_DESCRIPTORS: Mapping[str, CommandDescriptor] = MappingProxyType(
    {
        "set_repeater_shift": CommandDescriptor(
            name="set_repeater_shift",
            method_name="set_repeater_shift",
            bind=_bind_repeater_shift,
            target=_repeater_shift_target,
            argument_names=("direction", "receiver"),
            tx_policy=DescriptorTxPolicy.TX_SAFE,
            public_names=("set_repeater_shift",),
        ),
        "set_af_level": CommandDescriptor(
            name="set_af_level",
            method_name="set_af_level",
            bind=partial(_bind_level, "af_level"),
            target=partial(_level_target, "af_level"),
            argument_names=("level", "receiver"),
            tx_policy=DescriptorTxPolicy.ALWAYS_PASS,
            public_names=("set_af_level",),
            queue_policy="coalesced",
            receiver_aware=True,
        ),
        "set_rf_gain": CommandDescriptor(
            name="set_rf_gain",
            method_name="set_rf_gain",
            bind=partial(_bind_level, "rf_gain"),
            target=partial(_level_target, "rf_gain"),
            argument_names=("level", "receiver"),
            tx_policy=DescriptorTxPolicy.ALWAYS_PASS,
            public_names=("set_rf_gain",),
            queue_policy="coalesced",
            receiver_aware=True,
        ),
        "set_squelch": CommandDescriptor(
            name="set_squelch",
            method_name="set_squelch",
            bind=partial(_bind_level, "squelch"),
            target=partial(_level_target, "squelch"),
            argument_names=("level", "receiver"),
            tx_policy=DescriptorTxPolicy.ALWAYS_PASS,
            public_names=("set_sql", "set_squelch"),
            queue_policy="coalesced",
            receiver_aware=True,
        ),
        "set_att": CommandDescriptor(
            name="set_att",
            method_name="set_attenuator_level",
            bind=_bind_attenuator,
            target=_attenuator_target,
            argument_names=("db", "receiver"),
            tx_policy=DescriptorTxPolicy.ALWAYS_PASS,
            public_names=("set_att", "set_attenuator"),
            queue_policy="coalesced",
            receiver_aware=True,
            project_expectation=_project_attenuator_expectation,
        ),
        "set_antenna_1": CommandDescriptor(
            name="set_antenna_1",
            method_name="set_antenna_1",
            bind=partial(_bind_boolean, "rx_antenna_1"),
            target=partial(_global_slow_state_target, "rx_antenna_1"),
            argument_names=("enabled",),
            tx_policy=DescriptorTxPolicy.ANTENNA_SWITCH,
            public_names=("set_antenna", "set_antenna_1"),
        ),
        "set_antenna_2": CommandDescriptor(
            name="set_antenna_2",
            method_name="set_antenna_2",
            bind=partial(_bind_boolean, "rx_antenna_2"),
            target=partial(_global_slow_state_target, "rx_antenna_2"),
            argument_names=("enabled",),
            tx_policy=DescriptorTxPolicy.ANTENNA_SWITCH,
            public_names=("set_antenna_2",),
        ),
        "set_rx_antenna_ant1": CommandDescriptor(
            name="set_rx_antenna_ant1",
            method_name="set_rx_antenna_ant1",
            bind=partial(_bind_boolean, "rx_antenna_1"),
            target=partial(_global_slow_state_target, "rx_antenna_1"),
            argument_names=("enabled",),
            tx_policy=DescriptorTxPolicy.ANTENNA_SWITCH,
            public_names=("set_rx_antenna", "set_rx_antenna_ant1"),
        ),
        "set_rx_antenna_ant2": CommandDescriptor(
            name="set_rx_antenna_ant2",
            method_name="set_rx_antenna_ant2",
            bind=partial(_bind_boolean, "rx_antenna_2"),
            target=partial(_global_slow_state_target, "rx_antenna_2"),
            argument_names=("enabled",),
            tx_policy=DescriptorTxPolicy.ANTENNA_SWITCH,
            public_names=("set_rx_antenna_ant2",),
        ),
        "set_civ_output_ant": CommandDescriptor(
            name="set_civ_output_ant",
            method_name="set_civ_output_ant",
            bind=partial(_bind_boolean, "civ_output_ant"),
            target=partial(_global_slow_state_target, "civ_output_ant"),
            argument_names=("enabled",),
            tx_policy=DescriptorTxPolicy.TX_SAFE,
            public_names=("set_civ_output_ant",),
        ),
    }
)


_ADMITTED_DESCRIPTOR_TX_POLICIES = frozenset(
    {
        DescriptorTxPolicy.ALWAYS_PASS,
        DescriptorTxPolicy.TX_SAFE,
        DescriptorTxPolicy.ANTENNA_SWITCH,
    }
)


def _require_descriptor_policy_seat(descriptor: CommandDescriptor) -> None:
    """Reject values outside the descriptor-local admitted vocabulary."""

    policy = descriptor.tx_policy
    if not isinstance(policy, DescriptorTxPolicy) or policy not in (
        _ADMITTED_DESCRIPTOR_TX_POLICIES
    ):
        raise CommandError(
            f"descriptor {descriptor.name!r} policy {policy!r} is not admitted"
        )


def _validate_descriptor_table() -> None:
    for descriptor in _COMMAND_DESCRIPTORS.values():
        _require_descriptor_policy_seat(descriptor)


_validate_descriptor_table()


def command_descriptors() -> Mapping[str, CommandDescriptor]:
    _validate_descriptor_table()
    return _COMMAND_DESCRIPTORS


def command_descriptor(name: str) -> CommandDescriptor | None:
    descriptor = _COMMAND_DESCRIPTORS.get(name)
    if descriptor is None:
        descriptor = next(
            (
                descriptor
                for descriptor in _COMMAND_DESCRIPTORS.values()
                if name in descriptor.public_names or name == descriptor.method_name
            ),
            None,
        )
    if descriptor is not None:
        _require_descriptor_policy_seat(descriptor)
    return descriptor


def enqueue_command_intent(
    queue: DispatchQueue,
    intent: CommandIntent,
    *,
    future: asyncio.Future[None] | None,
    command_id: str,
    source: CommandSource,
    session_id: str | None,
    command_service: Any,
    timeout: float | None,
) -> None:
    """Enqueue with descriptor policy and exact lifecycle identity preserved."""

    descriptor = command_descriptor(intent.name)
    if descriptor is None:
        raise CommandError(f"no command descriptor for {intent.name!r}")
    _require_descriptor_policy_seat(descriptor)
    intent_session_id = intent.params.get("session_id")
    intent_session_id = None if intent_session_id is None else str(intent_session_id)
    if (
        command_id != intent.id
        or source != intent.source
        or session_id != intent_session_id
        or timeout != intent.timeout
    ):
        raise CommandError(f"queue metadata does not match intent {intent.id!r}")
    if command_service is None:
        raise CommandError(f"command service is required for intent {intent.id!r}")
    if descriptor.queue_policy == "coalesced" and future is None:
        queue.put(
            intent,
            command_id=command_id,
            source=source,
            session_id=session_id,
            command_service=command_service,
        )
        return
    if descriptor.queue_policy in ("ordered", "coalesced"):
        queue.put_ordered(
            intent,
            future=future,
            command_id=command_id,
            source=source,
            session_id=session_id,
            command_service=command_service,
        )
        return
    raise CommandError(
        f"unsupported queue policy {descriptor.queue_policy!r} for {descriptor.name!r}"
    )


def bind_command_intent(
    name: str,
    params: Mapping[str, Any],
    *,
    source: CommandSource,
    command_id: str | None = None,
    session_id: str | None = None,
    timeout: float | None = 10.0,
) -> CommandIntent:
    """Bind one descriptor operation without consulting a provider."""

    descriptor = command_descriptor(name)
    if descriptor is None:
        raise KeyError(f"no command descriptor for {name!r}")
    _require_descriptor_policy_seat(descriptor)
    normalized = descriptor.bind(params)
    if session_id is not None:
        normalized["session_id"] = session_id
    target = descriptor.target(normalized)
    return CommandIntent(
        id=command_id or f"{source}-{time.monotonic_ns()}",
        name=descriptor.method_name,
        params=normalized,
        source=source,
        target=target,
        priority="user",
        timeout=timeout,
        pending_policy="scoped",
        expected_observations=(target,),
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
    descriptor = command_descriptor(name)
    if descriptor is None:
        raise KeyError(f"no command descriptor for {name!r}")
    intent = bind_command_intent(
        name,
        params,
        source=source,
        command_id=command_id,
        session_id=session_id,
        timeout=descriptor.timeout,
    )
    supported = (
        radio.supports_command(
            descriptor.method_name, receiver=intent.params["receiver"]
        )
        if descriptor.receiver_aware
        else radio.supports_command(descriptor.method_name)
    )
    if not supported:
        raise CommandUnsupportedError(
            f"command {descriptor.method_name!r} is not supported by active profile"
        )
    if descriptor.project_expectation is not None:
        intent = replace(
            intent,
            params=descriptor.project_expectation(radio, intent.params),
        )
    return intent


async def execute_command_intent(
    radio: Any,
    intent: CommandIntent,
    *,
    managed_tx_authority: ManagedWriteAdmission | None = None,
) -> None:
    """Invoke the reviewed Radio method for a descriptor-built intent."""

    descriptor = command_descriptor(intent.name)
    if descriptor is None:
        raise CommandError(f"no command descriptor for {intent.name!r}")
    _require_descriptor_policy_seat(descriptor)
    method = getattr(radio, descriptor.method_name)
    if (
        managed_tx_authority is not None
        and not await managed_tx_authority.admit_managed_write(intent)
    ):
        raise CommandError(
            f"managed transmit authority refused command {descriptor.name!r}"
        )
    await method(**{name: intent.params[name] for name in descriptor.argument_names})
