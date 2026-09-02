"""Profile-derived support relations for public runtime operations.

Profiles own radio facts. Command builders own the profile keys they consume.
This module owns only the small set of public operations whose support is
derived from those two sources rather than declared directly by a profile.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeAlias

from rigplane.commands import (
    get_alc,
    get_antenna_1,
    get_antenna_2,
    get_attenuator,
    get_mode,
    get_rx_antenna_ant1,
    get_scope_main_sub,
    get_scope_single_dual,
    get_swr,
    scope_data_output,
    scope_main_sub,
    scope_on,
    send_cw,
    set_antenna_1,
    set_antenna_2,
    set_attenuator_level,
    set_dual_watch_off,
    set_dual_watch_on,
    set_rx_antenna_ant1,
    stop_cw,
)
from rigplane.profiles import RadioProfile

CommandBuilder: TypeAlias = Callable[..., object]


@dataclass(frozen=True, slots=True)
class BuilderAllOf:
    """An operation requiring every listed command builder."""

    operation: str
    builders: tuple[CommandBuilder, ...]
    kind: Literal["alias", "composite"]


@dataclass(frozen=True, slots=True)
class Capability:
    """An operation derived from one profile capability fact."""

    operation: str
    capability: str


@dataclass(frozen=True, slots=True)
class Protocol:
    """An operation derived from the profile protocol family."""

    operation: str
    protocol: str


SupportRelation: TypeAlias = BuilderAllOf | Capability | Protocol

_SCOPE_BUILDERS = (scope_on, scope_data_output)

BUILDER_RELATIONS: tuple[BuilderAllOf, ...] = (
    BuilderAllOf("disable_scope", (scope_data_output,), "alias"),
    BuilderAllOf("get_alc_meter", (get_alc,), "alias"),
    BuilderAllOf("get_antenna_1", (get_antenna_1,), "alias"),
    BuilderAllOf("get_antenna_2", (get_antenna_2,), "alias"),
    BuilderAllOf("get_attenuator_level", (get_attenuator,), "alias"),
    BuilderAllOf("get_rx_antenna_ant1", (get_rx_antenna_ant1,), "alias"),
    BuilderAllOf("get_scope_dual", (get_scope_single_dual,), "alias"),
    BuilderAllOf("get_scope_receiver", (get_scope_main_sub,), "alias"),
    BuilderAllOf("get_swr_meter", (get_swr,), "alias"),
    BuilderAllOf("send_cw_text", (send_cw,), "alias"),
    BuilderAllOf("set_antenna_1", (set_antenna_1,), "alias"),
    BuilderAllOf("set_antenna_2", (set_antenna_2,), "alias"),
    BuilderAllOf("set_attenuator_level", (set_attenuator_level,), "alias"),
    BuilderAllOf("set_rx_antenna_ant1", (set_rx_antenna_ant1,), "alias"),
    BuilderAllOf("set_scope_receiver", (scope_main_sub,), "alias"),
    BuilderAllOf("stop_cw_text", (stop_cw,), "alias"),
    BuilderAllOf("enable_scope", _SCOPE_BUILDERS, "composite"),
    BuilderAllOf("capture_scope_frame", _SCOPE_BUILDERS, "composite"),
    BuilderAllOf("capture_scope_frames", _SCOPE_BUILDERS, "composite"),
    BuilderAllOf("get_mode_info", (get_mode,), "composite"),
    BuilderAllOf(
        "set_dual_watch",
        (set_dual_watch_on, set_dual_watch_off),
        "composite",
    ),
)

AUDIO_OPERATIONS: frozenset[str] = frozenset(
    {
        "start_audio_rx_opus",
        "start_audio_rx_pcm",
        "stop_audio_rx_opus",
        "stop_audio_rx_pcm",
        "start_audio_tx_opus",
        "start_audio_tx_pcm",
        "push_audio_tx_opus",
        "push_audio_tx_pcm",
        "stop_audio_tx_opus",
        "stop_audio_tx_pcm",
    }
)

EXCLUDED_OPERATIONS: frozenset[str] = frozenset(
    {"set_scope_dual", "get_mode_enum", "get_memory_mode"}
)


def _validate_relations(
    relations: Iterable[SupportRelation],
) -> tuple[SupportRelation, ...]:
    """Validate relation shape before publishing the immutable registry."""
    checked = tuple(relations)
    operations = [relation.operation for relation in checked]
    if len(operations) != len(set(operations)):
        raise ValueError("callable-support relation operations must be unique")
    for relation in checked:
        if not isinstance(relation, BuilderAllOf):
            continue
        if not relation.builders:
            raise ValueError(f"{relation.operation} has no command builders")
        for builder in relation.builders:
            if not callable(getattr(builder, "cmd_map_key", None)):
                raise ValueError(
                    f"{relation.operation} builder {builder!r} has no callable cmd_map_key"
                )
    return checked


_RELATIONS = _validate_relations(
    (
        *BUILDER_RELATIONS,
        *(Capability(operation, "audio") for operation in AUDIO_OPERATIONS),
        Protocol("send_civ", "civ"),
    )
)
CALLABLE_RELATIONS: Mapping[str, SupportRelation] = MappingProxyType(
    {relation.operation: relation for relation in _RELATIONS}
)

if len(CALLABLE_RELATIONS) != 32:  # pragma: no cover - import-time invariant
    raise ValueError("callable-support registry must contain exactly 32 operations")
if set(CALLABLE_RELATIONS) & EXCLUDED_OPERATIONS:  # pragma: no cover
    raise ValueError("excluded operations must not have callable-support relations")


def _builder_command_name(builder: CommandBuilder, profile: RadioProfile) -> str | None:
    """Resolve a builder's profile key, failing closed on invalid metadata."""
    key_resolver = getattr(builder, "cmd_map_key", None)
    if not callable(key_resolver) or profile.command_map is None:
        return None
    try:
        name = key_resolver(profile.command_map)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return name if isinstance(name, str) and name else None


def _relation_supported(
    relation: SupportRelation,
    profile: RadioProfile,
    relation_names: set[str],
) -> bool:
    """Evaluate one relation against only profile and builder facts."""
    if isinstance(relation, BuilderAllOf):
        names = tuple(
            _builder_command_name(builder, profile) for builder in relation.builders
        )
        return all(
            name is not None
            and name not in relation_names
            and profile.supports_command(name)
            for name in names
        )
    if isinstance(relation, Capability):
        return bool(profile.supports_capability(relation.capability))
    return bool(profile.protocol_type == relation.protocol)


def supports_callable(profile: RadioProfile, operation: str) -> bool:
    """Return support from explicit profile facts or one registered relation."""
    if operation in profile.absent_command_names:
        return False
    if profile.supports_command(operation):
        return True
    relation = CALLABLE_RELATIONS.get(operation)
    if relation is None:
        return False
    return _relation_supported(relation, profile, set(CALLABLE_RELATIONS))
