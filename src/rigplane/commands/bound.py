"""Bind a radio's `CommandMap` once, at construction.

Implements Candidate A of
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §3.1 ("a bound
command object"), per Step 3 of the same plan (§4). This step only builds
the binder and makes it reachable from `runtime/radio.py:
CoreRadio.__init__`; no production call site moves onto it -- that is
Steps 5..N.

The owner ruling on MOR-2003 (recorded against §3.1's design fork) settles
how a builder exposes its command-map key: every builder that exposes one
does so as ``Callable[[CommandMap], str]``, uniformly -- `speech.py:
get_speech` is not split into two functions; its per-map probe
(``"set_speech" if cmd_map.has("set_speech") else "get_speech"``) lives
inside the callable instead. See `commands/_frame.py: expose_command_key`,
which attaches that callable to a builder as its ``cmd_map_key``
attribute.

`BoundCommands` is constructed once per radio, from that radio's
`CommandMap`. It supplies two things a future call site needs (Steps
5..N), both derived from one map entry so they cannot drift apart:

- attribute access (`__getattr__`) returns the named builder from
  `rigplane.commands`'s public namespace with the map already bound as
  ``cmd_map=``, so a call site reads
  ``self._commands.set_mic_gain(178, self._radio_addr)``.
- `expect(builder)` returns the ``(command, sub, prefix)`` triple the
  matching reply must have, decoded from the SAME map entry by the SAME
  decoder (`commands/_frame.py: decode_wire_tuple`) the builder itself
  uses via `_build_from_map` -- see plan §3.1, "Why `expect` takes the
  builder and not a name".

Per `commands/LAYER.md`, this module imports only `commands` internals and
`core`; it holds no module-level mutable state and performs no I/O. The
map is injected by the caller (`runtime/radio.py`), never looked up here.

The import of `rigplane.commands` inside `__getattr__` is deliberately
deferred to call time rather than done at module level: `bound.py` is
itself a module of the `commands` package, so an eager, module-level
``import rigplane.commands`` here would need the package's own
``__init__.py`` to have finished executing already -- true today because
nothing in that file imports `bound.py`, but not a fact this module should
depend on. Resolving lazily means it works either way.

**Step 4 (MOR-2005): the undeclared-command policy, D1
(`docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1).** Same
behaviour in development and production -- the only asymmetry lives in
`tests/test_profile_command_coverage.py`, never here. Three states: (1)
Declared -- send the profile's bytes, unchanged from Step 3 (pinned by
`tests/test_profile_command_binding.py`). (2) Declared absent (confirmed
not present, per a named source) -- refuse with
`core.exceptions.CommandError` quoting that source, the same *shape* of
refusal `runtime/radio.py: CoreRadio.set_filter_width` already raises for
its one production ``supports_command`` caller. (3) Neither declared nor
declared absent -- must not exist at release; if reached anyway, refuse
the same way as (2) and also invoke the optional ``on_undeclared`` hook.
Both are pinned by `tests/test_undeclared_command_policy.py`.

Both surfaces here consult the same distinction: `__getattr__`'s wrapper
classifies a ``CommandMap.get`` miss when the builder is actually called
(not every key is known without invoking it -- see
``_missing_command_name``), and `expect` classifies before decoding, since
it already knows the key via ``cmd_map_key``.

This module still performs no I/O itself (`commands/LAYER.md`):
``on_undeclared`` is a plain callable the caller supplies or omits, never
a ``logging`` call made from here -- `runtime/radio.py: CoreRadio.__init__`
wires it to a real logger, and reads ``RadioProfile.absent_command_sources``
(plain ``dict[str, str]`` data) off the profile.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from ..core.exceptions import CommandError
from ._frame import decode_wire_tuple

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ..command_map import CommandMap

__all__ = ["BoundCommands"]


def _takes_cmd_map(value: Any) -> bool:
    """True if *value*'s callable signature has a ``cmd_map`` parameter.

    Deliberately not gated on ``inspect.isfunction``: several test files
    (`tests/_command_test_helpers.py: bind_default_addr_module`) rebind a
    builder on the shared ``rigplane.commands`` package namespace into a
    ``functools.partial`` with a default ``to_addr`` bound, for the
    remainder of that test worker's session -- a real interaction in the
    full suite, not a production one, since nothing outside tests ever
    calls that helper. ``inspect.signature`` still reports the ``cmd_map``
    parameter correctly on such a partial, so checking the signature
    directly, the same way `_command_test_helpers.py:
    CommandModuleProxy.__getattr__` does, is what tolerates it.
    """
    try:
        signature = inspect.signature(value)
    except (TypeError, ValueError):
        return False
    return "cmd_map" in signature.parameters


def _missing_command_name(exc: KeyError) -> str:
    """Recover the command name from a ``CommandMap.get`` miss.

    ``CommandMap.get`` (`commands/command_map.py`) raises with a single
    formatted message, ``f"Unknown command {name!r}. Available: ..."`` --
    no structured attribute carries the bare name, so this reuses the same
    parsing `tests/test_command_map_parity.py: _report` already does
    against the identical exception, rather than a second, independent one
    that could silently disagree. A message not matching the expected
    shape is a different failure, re-raised rather than swallowed.
    """
    text = str(exc)
    marker = "Unknown command "
    if marker not in text:
        raise exc
    return text.split(marker, 1)[1].split(".", 1)[0].strip("'\"")


class BoundCommands:
    """A radio's command builders, pre-bound to its `CommandMap`.

    ``absent_command_sources`` and ``on_undeclared`` implement D1's
    undeclared-command policy (module docstring, "Step 4"); both are
    optional so existing direct construction
    (`tests/test_profile_command_binding.py`) is unaffected.
    """

    __slots__ = ("_absent", "_map", "_on_undeclared")

    def __init__(
        self,
        cmd_map: CommandMap,
        absent_command_sources: Mapping[str, str] = {},  # noqa: B006 -- read-only
        *,
        on_undeclared: Callable[[str], None] | None = None,
    ) -> None:
        self._map = cmd_map
        self._absent = dict(absent_command_sources)
        self._on_undeclared = on_undeclared

    def _refusal_for(self, name: str) -> CommandError:
        """Build the D1 refusal for *name*, classifying states 2 vs 3.

        State 2 (declared absent) never calls ``on_undeclared`` -- D1 is
        explicit that it is "not log-and-continue": a confirmed fact needs
        no warning. State 3 (unknown) does, once, right before raising.
        """
        source = self._absent.get(name)
        if source is not None:
            return CommandError(
                f"{name} is not supported by this radio "
                f"(declared absent by this profile, per {source})"
            )
        if self._on_undeclared is not None:
            self._on_undeclared(name)
        return CommandError(
            f"{name} is not supported by this radio "
            "(not declared by this profile, and not recorded as absent)"
        )

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        import rigplane.commands as _commands

        try:
            builder = getattr(_commands, name)
        except AttributeError:
            raise AttributeError(
                f"{type(self).__name__!r} has no builder named {name!r} "
                "(not exported from rigplane.commands)"
            ) from None
        if not callable(builder) or not _takes_cmd_map(builder):
            raise AttributeError(
                f"{type(self).__name__!r} has no builder named {name!r} "
                f"({name!r} in rigplane.commands does not take cmd_map)"
            )

        def _bound_builder(*args: Any, **kwargs: Any) -> bytes:
            kwargs.setdefault("cmd_map", self._map)
            try:
                return builder(*args, **kwargs)  # type: ignore[no-any-return]
            except KeyError as exc:
                raise self._refusal_for(_missing_command_name(exc)) from None

        _bound_builder.__name__ = name
        _bound_builder.__qualname__ = f"{type(self).__name__}.{name}"
        return _bound_builder

    def expect(self, builder: Callable[..., bytes]) -> tuple[int, int | None, bytes]:
        """Return the ``(command, sub, prefix)`` triple *builder*'s reply must match.

        Computed from the same map entry, via the same decoder
        (`_frame.decode_wire_tuple`), that *builder* itself resolves through
        `_build_from_map` when called with this bound map -- see the module
        docstring and plan §3.1's "Why `expect` takes the builder and not a
        name".

        Raises:
            AttributeError: *builder* has no command-map key exposed yet.
                Exposure is added module by module in Steps 5..N of
                `docs/plans/2026-08-29-profile-driven-command-bytes.md`
                (§4); until a builder's module migrates, `expect` refuses
                rather than guess.
            CommandError: *builder*'s key is not declared by this map --
                D1 states 2/3 (module docstring, "Step 4"), classified the
                same way `__getattr__`'s wrapper classifies a miss reached
                by calling the builder.
        """
        key_fn = getattr(builder, "cmd_map_key", None)
        if key_fn is None:
            name = getattr(builder, "__qualname__", repr(builder))
            raise AttributeError(
                f"{name} has no command-map key exposed yet -- expose one "
                "with @expose_command_key when this builder's module "
                "migrates in Steps 5..N of "
                "docs/plans/2026-08-29-profile-driven-command-bytes.md "
                "(§4); BoundCommands.expect refuses rather than guess."
            )
        key = key_fn(self._map)
        if not self._map.has(key):
            raise self._refusal_for(key)
        wire = self._map.get(key)
        return decode_wire_tuple(wire)
