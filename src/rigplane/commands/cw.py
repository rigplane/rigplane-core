"""CW keying commands (send_cw, stop_cw).

Migrated onto the bound command map in MOR-2008 (batch 1,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N):
both builders now require ``cmd_map``, with no hardcoded fallback left.
Zero divergence rows, zero gap rows -- every profile that declares
``send_cw``/``stop_cw`` already carries the identical ``[0x17]`` tuple the
fallback built (verified by grep across ``rigs/*.toml`` before deleting
it), so ``tests/command_map_parity_divergences.txt`` stays empty of this
module's rows.
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


@expose_command_key(lambda cmd_map: "send_cw")
@require_cmd_map
def send_cw(
    text: str, to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> list[bytes]:
    """Build CI-V frames to send CW text.

    CW text is sent in chunks of up to 30 characters per frame.
    Each character is sent as ASCII byte in the data field.

    Args:
        text: CW text to send (A-Z, 0-9, and common prosigns).
        to_addr: Radio CI-V address.
        from_addr: Controller CI-V address.

    Returns:
        List of CI-V frame bytes (one per chunk).
    """
    frames = []
    text = text.upper()
    for i in range(0, len(text), 30):
        chunk = text[i : i + 30]
        data = chunk.encode("ascii")
        frames.append(
            _build_from_map(
                cmd_map, "send_cw", to_addr=to_addr, from_addr=from_addr, data=data
            )
        )
    return frames


@expose_command_key(lambda cmd_map: "stop_cw")
@require_cmd_map
def stop_cw(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to stop CW sending."""
    return _build_from_map(
        cmd_map, "stop_cw", to_addr=to_addr, from_addr=from_addr, data=b"\xff"
    )
