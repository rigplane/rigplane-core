"""Outcome classes for both probe protocols (MOR-4464).

One class set for Yaesu CAT and CI-V: ``data`` (the reply parses as the
answer), ``refused`` (``?;`` / CI-V NAK ``0xFA``), ``ack_only`` (a bare
CI-V ACK ``0xFB`` with no data), ``silent`` (nothing arrived), ``malformed``
(unattributable bytes), ``unsolicited`` (a push in an answer slot),
``transport_error`` (the run ends). Raw bytes are always kept; the class
is a convenience, never the only record.
"""

from __future__ import annotations

from rigplane.commands import command_carries_sub, parse_civ_frame

__all__ = [
    "cat_reply_closes",
    "civ_requested_command",
    "classify_cat",
    "classify_civ",
]

#: Command byte of a CI-V refusal and of a bare acknowledgement.
_NAK = 0xFA
_ACK = 0xFB

#: CI-V dual-receiver wrapper the probe unwraps before comparing.
_CMD29 = 0x29

#: The one recorded exception to "a data reply starts with the whole
#: request body": the radio answers the SUB S-meter read ``SM1;`` with
#: the side digit echoed as 0 (rigs/ftx1.toml: get_s_meter_sub).
_ECHO_EXCEPTIONS = {"SM1;": "SM0"}


def cat_reply_closes(request: str, reply: str) -> bool:
    """Whether *reply* (with its ``;``) can answer CAT *request*.

    A data reply starts with the whole request body (``NA1;`` ->
    ``NA1x;``) or is the ``?;`` refusal. A late reply to an earlier
    request that merely shares the two-letter verb (``NA00;`` for
    ``NA1;``) is not one.
    """
    return (
        reply == "?;"
        or reply.startswith(request[:-1])
        or reply.startswith(_ECHO_EXCEPTIONS.get(request, "\0"))
    )


def _is_text_noise_free(line: str) -> bool:
    """Whether *line* holds only bytes a CAT reply may carry."""
    return all(0x20 <= ord(char) <= 0x7E for char in line)


def classify_cat(line: str, reply: str | None) -> str:
    """Classify one Yaesu CAT answer: data / refused / silent / malformed."""
    if reply is None:
        return "silent"
    if not _is_text_noise_free(reply):
        return "malformed"
    if reply == "?;":
        return "refused"
    if cat_reply_closes(line, reply):
        return "data"
    return "malformed"


def civ_requested_command(body: bytes) -> tuple[int, int | None]:
    """The ``(command, sub)`` pair one CI-V probe body asks about."""
    inner = body[2:] if body[:1] == bytes((_CMD29,)) and len(body) >= 3 else body
    command = inner[0]
    sub: int | None = None
    if len(inner) > 1 and command_carries_sub(command):
        sub = inner[1]
    return command, sub


def classify_civ(body: bytes, reply: bytes | None, *, kind: str = "get") -> str:
    """Classify one CI-V answer: data / refused / ack_only / silent / malformed.

    A data reply must carry the requested ``(command, sub)`` pair after
    unwrapping one leading ``0x29`` prefix; anything else parseable is a
    reply to another command. ``kind="unknown"`` probes accept any
    parseable non-refusal as data: the point is what the radio does.
    """
    if reply is None:
        return "silent"
    try:
        frame = parse_civ_frame(reply)
    except ValueError:
        return "malformed"
    if frame.command == _NAK:
        return "refused"
    if frame.command == _ACK:
        return "ack_only"
    if kind == "unknown" or (frame.command, frame.sub) == civ_requested_command(body):
        return "data"
    return "malformed"
