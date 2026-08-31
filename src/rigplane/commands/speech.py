"""Speech announcement command (0x13).

Migrated onto the bound command map in MOR-2008 (batch 1,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N):
``get_speech`` now requires ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows, zero gap rows -- every profile that declares
``get_speech``/``set_speech`` already carries the identical ``[0x13]``
tuple the fallback built (verified by grep across ``rigs/*.toml`` before
deleting it), so ``tests/command_map_parity_divergences.txt`` stays empty
of this module's rows.

The pre-migration ``cmd_map`` branch never validated ``what`` -- only the
fallback branch did, so a caller reaching the map branch with an
out-of-range value got a frame built anyway, not a ``ValueError``. Folding
the two branches into one (there is only one left now) applies the
validation unconditionally, closing that gap rather than carrying it
forward silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._frame import (
    CONTROLLER_ADDR,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


def _speech_key(cmd_map: CommandMap) -> str:
    """The command-map key `get_speech` resolves for *cmd_map*.

    A profile may expose ``set_speech`` (wfview Set-only) instead of
    ``get_speech``; this is the one probe among the exposed builders in
    MOR-2003 Step 3 whose key is a function of the map rather than a fixed
    literal (`docs/plans/2026-08-29-profile-driven-command-bytes.md` §3.1,
    "the fourth case"). Shared between `get_speech`'s own body and its
    `@expose_command_key` decoration so the two cannot drift apart --
    `tests/test_profile_command_binding.py`'s drift test pins that they
    agree regardless.
    """
    return "set_speech" if cmd_map.has("set_speech") else "get_speech"


@expose_command_key(_speech_key)
@require_cmd_map
def get_speech(
    what: int = 0,
    *,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    cmd_map: CommandMap,
) -> bytes:
    """Build a speech announcement CI-V command (0x13).

    Fire-and-forget.  Triggers the IC-7610 voice synthesizer.

    Args:
        what: 0 = all (S-meter, frequency, mode),
              1 = frequency + S-meter,
              2 = mode.
    """
    if what not in (0, 1, 2):
        raise ValueError(f"speech 'what' must be 0, 1, or 2, got {what}")
    return _build_from_map(
        cmd_map,
        _speech_key(cmd_map),
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([what]),
    )


# Backward-compat alias
speech = get_speech
