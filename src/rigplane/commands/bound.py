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
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

from ._frame import decode_wire_tuple

if TYPE_CHECKING:
    from collections.abc import Callable

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


class BoundCommands:
    """A radio's command builders, pre-bound to its `CommandMap`."""

    __slots__ = ("_map",)

    def __init__(self, cmd_map: CommandMap) -> None:
        self._map = cmd_map

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
        return functools.partial(builder, cmd_map=self._map)

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
        wire = self._map.get(key)
        return decode_wire_tuple(wire)
