"""Profile-derived support relations for public runtime operations.

Profiles own radio facts. Command builders own the profile keys they consume.
This module owns only the small set of public operations whose support is
derived from those two sources rather than declared directly by a profile.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
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


@dataclass(frozen=True, slots=True)
class RelativeVfoCommands:
    """A single-RX A/B relative write requiring profile wire commands."""

    operation: str
    commands: tuple[str, ...]


SupportRelation: TypeAlias = BuilderAllOf | Capability | Protocol | RelativeVfoCommands

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

# ``profile.split_tx_target`` values that route split-TX reads/writes
# through the declared unselected-role CI-V 0x25/0x26 commands on a
# dual-receiver profile: ``sub_receiver`` (selector 01 is the SUB band)
# and ``main_unselected`` (selector 01 is the unselected VFO inside
# MAIN; the independent SUB receiver is never touched). ``none`` — the
# default, and any unknown value — fails closed to the single-receiver
# scheme (MOR-3754).
_DUAL_SPLIT_TX_TARGETS: frozenset[str] = frozenset({"main_unselected", "sub_receiver"})

# Unselected-role commands a dual-receiver split-TX write needs. A GET
# declaration never licenses the matching SET; the single-receiver
# scheme keeps its own all-four-commands requirement.
_DUAL_SPLIT_TX_COMMANDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "write_relative_vfo_frequency": (
            "set_unselected_freq",
            "get_unselected_freq",
        ),
        "write_relative_vfo_mode": (
            "set_unselected_mode",
            "get_unselected_mode",
            "get_unselected_freq",
        ),
    }
)


def split_tx_routes_unselected_selector(profile: RadioProfile) -> bool:
    """Whether split-TX I/O routes via the unselected-role 0x25/0x26 commands.

    True only on a dual-receiver profile that explicitly declares where
    split TX lives (``split_tx_target`` in ``_DUAL_SPLIT_TX_TARGETS``).
    Never derived from the absence of another key: a missing or unknown
    value fails closed.
    """
    return (
        profile.receiver_count >= 2
        and getattr(profile, "split_tx_target", "none") in _DUAL_SPLIT_TX_TARGETS
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
        RelativeVfoCommands(
            "write_relative_vfo_frequency",
            (
                "set_selected_freq",
                "get_selected_freq",
                "set_unselected_freq",
                "get_unselected_freq",
            ),
        ),
        RelativeVfoCommands(
            "write_relative_vfo_mode",
            ("set_unselected_mode", "get_unselected_mode", "get_unselected_freq"),
        ),
    )
)
CALLABLE_RELATIONS: Mapping[str, SupportRelation] = MappingProxyType(
    {relation.operation: relation for relation in _RELATIONS}
)

if len(CALLABLE_RELATIONS) != 34:  # pragma: no cover - import-time invariant
    raise ValueError("callable-support registry must contain exactly 34 operations")
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
    if isinstance(relation, RelativeVfoCommands):
        command_map = profile.command_map
        if command_map is None:
            return False
        if split_tx_routes_unselected_selector(profile):
            dual_commands = _DUAL_SPLIT_TX_COMMANDS.get(relation.operation)
            return dual_commands is not None and all(
                command_map.has(name) and profile.supports_command(name)
                for name in dual_commands
            )
        return (
            profile.receiver_count == 1
            and profile.vfo_scheme == "ab"
            and profile.vfo_readback == "selected_unselected"
            and all(
                command_map.has(name) and profile.supports_command(name)
                for name in relation.commands
            )
        )
    return bool(profile.protocol_type == relation.protocol)


def supports_callable(profile: RadioProfile, operation: str) -> bool:
    """Return support from explicit profile facts or one registered relation."""
    if operation in profile.absent_command_names:
        return False
    # MOR-3678: typed CW text needs the profile's cw_text, not just send_cw.
    if operation == "send_cw_text" and not profile.cw_text.supported:
        return False
    if profile.supports_command(operation):
        return True
    relation = CALLABLE_RELATIONS.get(operation)
    if relation is None:
        return False
    return _relation_supported(relation, profile, set(CALLABLE_RELATIONS))


def supports_explicit_callable(
    operation: str,
    supported_operations: Collection[str],
    executable: Callable[[str], bool],
) -> bool:
    """Evaluate an external provider's explicit support declaration safely."""
    return operation in supported_operations and executable(operation)
