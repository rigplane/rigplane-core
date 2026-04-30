"""Command specification types for multi-protocol radio control.

Supports both CI-V (wire bytes) and Yaesu CAT (text templates) in a unified schema.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "CivCommandSpec",
    "CatCommandSpec",
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


# Union type for command specs (CI-V or CAT)
CommandSpec = CivCommandSpec | CatCommandSpec
