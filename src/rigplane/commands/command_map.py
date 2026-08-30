"""CommandMap — frozen lookup for CI-V wire bytes by command name."""

from __future__ import annotations

from collections.abc import Iterator

__all__ = ["CommandMap"]


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
