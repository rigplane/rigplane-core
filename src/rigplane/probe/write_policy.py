"""Send-time write gate for the probe write checks (MOR-4464 B1).

The allow-lists and never-lists are DEFINED once in
``rigplane.profiles.probe_checks`` (the loader enforces them at load,
and the import layers forbid ``profiles`` from importing ``probe``):
this module re-exports them for the send path and checks every body
the engine sends, including the restore bodies it computes at run
time. Adding a verb to an allow-list is a one-line change to the
frozensets in ``profiles.probe_checks`` (``YAESU_WRITE_ALLOW`` /
``CIV_WRITE_ALLOW``); this module needs no edit.

The adversary is a wrong or careless profile entry written by a
developer or a future profile author, never the radio or its owner.
"""

from __future__ import annotations

from rigplane.commands import command_carries_sub
from rigplane.profiles.probe_checks import (
    CIV_CONDITIONAL_VFO,
    CIV_NEVER_ANY,
    CIV_NEVER_WRITE,
    CIV_WRITE_ALLOW,
    YAESU_NEVER,
    YAESU_WRITE_ALLOW,
)

__all__ = [
    "CIV_CONDITIONAL_VFO",
    "CIV_NEVER_ANY",
    "CIV_NEVER_WRITE",
    "CIV_WRITE_ALLOW",
    "YAESU_NEVER",
    "YAESU_WRITE_ALLOW",
    "check_raw_read_allowed",
    "check_send_allowed",
]


def _civ_parts(body: bytes) -> tuple[int, int | None, bool]:
    """The (command, sub, is-write-form) triple of one CI-V body."""
    command = body[0]
    sub = body[1] if len(body) >= 2 and command_carries_sub(command) else None
    prefix = 2 if sub is not None else 1
    return command, sub, len(body) > prefix


def check_send_allowed(
    protocol: str, body: bytes, *, vfo_pair_restored: bool = False
) -> None:
    """Refuse a body the engine must never send; the verb is named.

    Raises :class:`ValueError` when the body is off its protocol's
    allow-list or on its never-list. The conditional VFO
    exchange/equalize writes (CI-V ``07 A0``/``07 B1``) additionally
    need ``vfo_pair_restored``: the check restores both VFOs.
    """
    if protocol == "yaesu_cat":
        try:
            line = body.decode("ascii")
        except UnicodeDecodeError:
            raise ValueError(f"refusing {body!r}: not a CAT line") from None
        verb = line[:2].upper() if len(line) >= 2 else ""
        if verb in YAESU_NEVER:
            raise ValueError(f"refusing CAT write {verb!r}: never-listed verb")
        if verb not in YAESU_WRITE_ALLOW:
            raise ValueError(f"refusing CAT write {verb!r}: not on the allow-list")
        return
    if protocol == "civ":
        if not body:
            raise ValueError("refusing empty CI-V body")
        command, sub, is_write = _civ_parts(body)
        if (command, sub) in CIV_NEVER_ANY or (command, None) in CIV_NEVER_ANY:
            raise ValueError(f"refusing CI-V 0x{command:02X}: never-listed command")
        if (command, sub) in CIV_CONDITIONAL_VFO and not vfo_pair_restored:
            raise ValueError(
                f"refusing CI-V 0x{command:02X} {sub:02X}: "
                "needs both VFOs in the restore list"
            )
        if is_write and (
            (command, sub) in CIV_NEVER_WRITE or (command, None) in CIV_NEVER_WRITE
        ):
            raise ValueError(f"refusing CI-V 0x{command:02X} write: never-listed")
        if command not in CIV_WRITE_ALLOW:
            raise ValueError(
                f"refusing CI-V 0x{command:02X}: not on the allow-list"
            )
        return
    raise ValueError(f"refusing send: probe checks need USB CI-V or Yaesu CAT")


def check_raw_read_allowed(protocol: str, body: bytes) -> None:
    """Refuse a raw read on the never-list; allow-list is not required.

    Raw reads are queries for verbs the profile does not declare, so
    any verb passes except the never-list backstop (reads never write,
    but the backstop still names the verb).
    """
    if protocol == "yaesu_cat":
        try:
            line = body.decode("ascii")
        except UnicodeDecodeError:
            raise ValueError(f"refusing {body!r}: not a CAT line") from None
        verb = line[:2].upper() if len(line) >= 2 else ""
        if verb in YAESU_NEVER:
            raise ValueError(f"refusing CAT raw read {verb!r}: never-listed verb")
        return
    if protocol == "civ":
        if not body:
            raise ValueError("refusing empty CI-V body")
        command, sub, _is_write = _civ_parts(body)
        if (command, sub) in CIV_NEVER_ANY or (command, None) in CIV_NEVER_ANY:
            raise ValueError(f"refusing CI-V 0x{command:02X}: never-listed command")
        if (command, sub) in CIV_CONDITIONAL_VFO:
            raise ValueError(
                f"refusing CI-V 0x{command:02X} {sub:02X}: never-listed command"
            )
        return
    raise ValueError(f"refusing send: probe checks need USB CI-V or Yaesu CAT")
