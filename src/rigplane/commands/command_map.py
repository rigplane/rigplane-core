"""CommandMap — frozen lookup for CI-V wire bytes by command name.

Also hosts :class:`ReverseCommandIndex`, the inverse of the map above:
bytes off the wire to a declared command name (Z2 of
`docs/plans/2026-09-01-reverse-command-index.md`).

**One index per profile, never a union across profiles.** Icom's top-level
opcodes (the CI-V command byte, e.g. 0x1A) are stable across the family,
but the ``0x1A 0x05`` menu sub-address space is per-model: the identical
wire prefix ``1A 05 01 12`` is ``get_scope_edge1_1p6mhz`` on IC-7300,
``get_civ_transceive`` on IC-7610, and ``get_acc1_mod_level`` on IC-9700 --
three different meanings, each independently sourced from its own radio's
CI-V reference guide (verified by grepping ``rigs/ic7300.toml``,
``rigs/ic7610.toml``, ``rigs/ic9700.toml`` for that exact byte tuple).
Resolving a frame therefore requires knowing which radio sent it; nothing
here ever merges two profiles' declared names into one lookup.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from ._frame import decode_wire_tuple

__all__ = ["CommandMap", "ReverseCommandIndex", "ReverseLookupResult"]


class CommandMap:
    """Immutable mapping from command names to CI-V wire byte tuples.

    Usage::

        cm = CommandMap({"af_gain": (0x14, 0x01), "rf_gain": (0x14, 0x02)})
        cm.get("af_gain")   # (0x14, 0x01)
        cm.has("af_gain")   # True
        len(cm)             # 2
        list(cm)            # ["af_gain", "rf_gain"]
    """

    __slots__ = ("_commands",)

    def __init__(self, commands: dict[str, tuple[int, ...]]) -> None:
        self._commands: dict[str, tuple[int, ...]] = dict(commands)

    def get(self, name: str) -> tuple[int, ...]:
        """Return wire bytes for *name*, or raise ``KeyError``."""
        try:
            return self._commands[name]
        except KeyError:
            raise KeyError(
                f"Unknown command {name!r}. "
                f"Available: {', '.join(sorted(self._commands))}"
            ) from None

    def has(self, name: str) -> bool:
        """Return ``True`` if *name* is a known command."""
        return name in self._commands

    def __iter__(self) -> Iterator[str]:
        return iter(self._commands)

    def __len__(self) -> int:
        return len(self._commands)

    def __repr__(self) -> str:
        return f"CommandMap({len(self._commands)} commands)"

    def __eq__(self, other: object) -> bool:
        """Compare by contents, matching this class's ``Immutable mapping`` claim.

        Without this, two ``CommandMap`` instances built from identical
        dicts (e.g. two calls to ``profiles/rig_loader.py:
        RigConfig.to_command_map`` for the same TOML) compared unequal by
        identity -- which broke a pre-existing field-by-field equality
        sweep once ``profiles/__init__.py: RadioProfile`` gained a
        ``command_map`` field carrying one (MOR-2003 Step 3).
        """
        if not isinstance(other, CommandMap):
            return NotImplemented
        return self._commands == other._commands

    def __hash__(self) -> int:
        return hash(frozenset(self._commands.items()))


@dataclass(frozen=True, slots=True)
class ReverseLookupResult:
    """Outcome of :meth:`ReverseCommandIndex.resolve`.

    Exactly one of two shapes: ``name`` set and ``candidates`` empty means
    the frame resolved to exactly one declared command name; ``name`` is
    ``None`` and ``candidates`` holds two or more names means the frame
    matches a known ``(command, sub)`` and declared prefix but more than
    one declared name shares it and nothing here can break the tie (the
    class (c)/(d) collisions plan §2 describes -- a future annotation,
    tracked as Z3, is what would decide these, not this step). Both empty
    is CommandMap's own refuse-rather-than-guess posture applied to the
    reverse direction: no declared row explains this frame at all.
    """

    name: str | None = None
    candidates: frozenset[str] = field(default_factory=frozenset)

    @property
    def resolved(self) -> bool:
        return self.name is not None

    @property
    def ambiguous(self) -> bool:
        return self.name is None and bool(self.candidates)

    @property
    def unrecognized(self) -> bool:
        return self.name is None and not self.candidates


class ReverseCommandIndex:
    """Resolve an incoming ``(command, sub, data)`` frame to a declared name.

    Built once per profile from that profile's own :class:`CommandMap`
    (module docstring: never a union across profiles). Every declared
    name's wire tuple is decoded with :func:`commands._frame.decode_wire_tuple`
    -- the same split :func:`commands._frame.parse_civ_frame` uses on a
    received frame, via the shared :func:`commands._frame.command_carries_sub`
    predicate, so a tuple grouped here and a frame handed to :meth:`resolve`
    agree on where the sub-command byte ends and the constant prefix bytes
    begin.

    Names are grouped by their full ``(command, sub, prefix)`` key --
    deliberately not collapsed to ``(command, sub)``: on IC-7300 alone the
    ``0x1A 0x05`` menu family holds 84 distinct prefixes; a
    ``(command, sub)``-only index would collapse all of them into one
    bucket and could never name a single one back.

    Every declared prefix under the incoming ``(command, sub)`` that is a
    byte-prefix of ``data`` remains viable. A single viable name resolves;
    multiple names are returned as candidates; no viable name is
    unrecognized. This deliberately retains both a shorter getter prefix and
    a longer write prefix when the same incoming bytes can be either a getter
    reply payload or an echoed write. Direction or request context can narrow
    that set in a later consumer, but payload length alone cannot.
    """

    __slots__ = ("_buckets",)

    def __init__(self, command_map: CommandMap) -> None:
        raw: dict[tuple[int, int | None], dict[bytes, list[str]]] = {}
        for name in command_map:
            command, sub, prefix = decode_wire_tuple(command_map.get(name))
            raw.setdefault((command, sub), {}).setdefault(prefix, []).append(name)
        self._buckets: dict[tuple[int, int | None], dict[bytes, tuple[str, ...]]] = {
            key: {prefix: tuple(names) for prefix, names in group.items()}
            for key, group in raw.items()
        }

    def __eq__(self, other: object) -> bool:
        """Compare by contents -- the same MOR-2003 reason ``CommandMap.__eq__``
        above gives: two instances built from the same profile twice (as
        `tests/test_ftx1_control_domains.py:
        test_ftx1_scalar_domains_are_exact_loader_published_capabilities`
        does) must compare equal for ``RadioProfile``'s own field-by-field
        equality sweep, not by identity.
        """
        if not isinstance(other, ReverseCommandIndex):
            return NotImplemented
        return self._buckets == other._buckets

    def __hash__(self) -> int:
        return hash(
            frozenset(
                (key, frozenset(group.items())) for key, group in self._buckets.items()
            )
        )

    def resolve(
        self, command: int, sub: int | None, data: bytes
    ) -> ReverseLookupResult:
        """Resolve one incoming frame's ``(command, sub, data)`` to a name."""
        bucket = self._buckets.get((command, sub))
        if not bucket:
            return ReverseLookupResult()

        candidates = frozenset(
            name
            for prefix, names in bucket.items()
            if data.startswith(prefix)
            for name in names
        )
        if not candidates:
            return ReverseLookupResult()
        if len(candidates) == 1:
            return ReverseLookupResult(name=next(iter(candidates)))
        return ReverseLookupResult(candidates=candidates)
