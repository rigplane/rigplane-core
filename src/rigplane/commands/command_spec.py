"""Command specification types for multi-protocol radio control.

Supports both CI-V (wire bytes) and Yaesu CAT (text templates) in a unified schema.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CivCommandSpec",
    "CatCommandSpec",
    "AbsentCommandSpec",
    "CommandSpec",
]


@dataclass(frozen=True, slots=True)
class CivCommandSpec:
    """CI-V command specification (Icom radios).

    Example TOML:
        get_freq = [0x03]
        set_mode = [0x06]
    """

    bytes: tuple[int, ...]
    """CI-V wire bytes (e.g., (0x03,) for get_freq)."""


@dataclass(frozen=True, slots=True)
class CatCommandSpec:
    """Yaesu CAT command specification (text-based protocol).

    Example TOML:
        get_freq = { cat = { read = "FA;", parse = "FA{freq:09d};" } }
        set_mode = { cat = { write = "MD0{mode};" } }
    """

    read: str | None = None
    """Template for READ command (e.g., "FA;" for get_freq)."""

    write: str | None = None
    """Template for WRITE command (e.g., "FA{freq:09d};" for set_freq)."""

    parse: str | None = None
    """Template for parsing response (e.g., "FA{freq:09d};" for get_freq).
    
    If omitted, defaults to `read` template (echo-based response).
    """

    def __post_init__(self) -> None:
        """Validate CAT command spec."""
        if self.read is None and self.write is None:
            raise ValueError("CatCommandSpec must have at least one of read/write")


@dataclass(frozen=True, slots=True)
class AbsentCommandSpec:
    """Declares that a radio does not have a given command (MOR-2005 step 4a).

    Plan `docs/plans/2026-08-29-profile-driven-command-bytes.md` §8.1 D1/D2:
    D1 needs a way to distinguish "this radio does not have the command,
    confirmed" from "nobody has looked yet"; D2 requires every filled TOML
    entry to record where its value came from. This spec is both at once —
    an entry with a source and no bytes.

    Example TOML:
        get_dual_watch = { absent = "IC-7300 Full Manual (A7292-4EX), no dual-watch item" }

    ``profiles/rig_loader.py: RigConfig.to_profile`` excludes names bound to
    this spec from ``RadioProfile.command_names`` and records them in
    ``RadioProfile.absent_command_names`` instead; ``RigConfig.to_command_map``
    excludes them from the ``CommandMap`` the same way it already excludes
    ``CatCommandSpec`` entries.

    This spec only makes the state representable. The refusal policy that
    reads ``absent_command_names`` to distinguish "declared absent" from
    "declared nowhere" is a later step (plan §4 Step 4, D1 states 2/3) and
    is not implemented by this dataclass.
    """

    source: str
    """The named authority this absence is confirmed against (a manual,
    a wfview rig definition, a live-hardware capture, ...) — never empty;
    see D2."""

    def __post_init__(self) -> None:
        """Validate the D2-required source is present."""
        if not self.source.strip():
            raise ValueError("AbsentCommandSpec.source must be a non-empty string")


# Union type for command specs (CI-V, CAT, or declared-absent)
CommandSpec = CivCommandSpec | CatCommandSpec | AbsentCommandSpec
