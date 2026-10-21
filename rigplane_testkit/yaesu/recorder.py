"""Yaesu CAT recording on the existing serial bridge.

:class:`~rigplane_testkit.serial.SerialCivRecorder` is a byte pump with a
JSONL log; nothing in it is CI-V-specific. This module adds what a CAT
capture needs on top of it, without a second recorder:

* :func:`cat_lines` — the CAT sibling of
  :func:`~rigplane_testkit.serial.recorder.civ_frames`: reassembles the
  ``;``-terminated lines of one recording, per direction.
* :class:`CatReadOnlyGuard` — the RX-only safety for a capture on a real
  radio. The bridge forwards only lines the rig profile declares as READ
  templates (``ID;``, ``IF;``, ``FA;``, ``MD0;`` ...). Everything else — a
  frequency set, ``TX1;``, a setting write — never reaches the radio, and
  is logged as a ``blocked`` row. A Yaesu set draws no reply, so a dropped
  write looks to the polling client exactly like a write the radio took.
  The adversary is the polling client (the Core server under capture): its
  connect sequence or a stray control may write; the capture session must
  not depend on it behaving.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig

_TERMINATOR = b";"

# The longest line any profile command produces is a few dozen bytes; a
# longer unterminated run is garbage, not a line waiting for its ';'.
_MAX_LINE = 64

_FIELD = re.compile(r"\{[^{}]*\}")


def cat_lines(path: str | Path) -> Iterator[tuple[float, str, str]]:
    """Yield ``(t, direction, line)`` for every complete CAT line in a JSONL.

    Pure helper over a recorder file: a line split across chunks is
    re-assembled and a chunk holding several lines is split, per direction;
    each line carries the ``t`` of the chunk that completed it and keeps its
    ``;``. A trailing partial line is not yielded. Only the ``c2r`` and
    ``r2c`` rows are read — the bridge's ``blocked`` rows are not traffic.
    Non-ASCII bytes (a garbled radio reply) appear as ``\\xNN`` escapes.
    """
    buffers: dict[str, bytearray] = {}
    with Path(path).open(encoding="utf-8") as file:
        for raw in file:
            record = json.loads(raw)
            direction = str(record["dir"])
            if direction not in ("c2r", "r2c"):
                continue
            t = float(record["t"])
            buf = buffers.setdefault(direction, bytearray())
            buf += bytes.fromhex(str(record["hex"]))
            while (end := buf.find(_TERMINATOR)) >= 0:
                line = bytes(buf[: end + 1])
                del buf[: end + 1]
                yield t, direction, line.decode("ascii", errors="backslashreplace")


class CatReadOnlyGuard:
    """The set of lines a capture may forward to the radio: the profile's reads.

    A template field (``{receiver}``) matches exactly one letter or digit, so
    ``OS0;`` is a read while ``OS01;`` — the write that shares its prefix —
    is not. Build one per rig profile with :meth:`from_config`; hand
    :meth:`new_filter` to ``SerialCivRecorder(client_filter=...)``.
    """

    def __init__(self, read_templates: frozenset[str]) -> None:
        if not read_templates:
            raise ValueError("the profile declares no CAT read commands")
        self._allowed = tuple(
            re.compile("[0-9A-Za-z]".join(re.escape(p) for p in _FIELD.split(tpl)))
            for tpl in sorted(read_templates)
        )

    @classmethod
    def from_config(cls, config: RigConfig) -> CatReadOnlyGuard:
        """The guard for the READ templates of one loaded rig profile."""
        return cls(
            frozenset(
                spec.read
                for spec in config.commands.values()
                if isinstance(spec, CatCommandSpec) and spec.read
            )
        )

    def allows(self, line: bytes) -> bool:
        """True when *line* (with its ``;``) is exactly one profile read."""
        try:
            text = line.decode("ascii")
        except UnicodeDecodeError:
            return False
        return any(pattern.fullmatch(text) for pattern in self._allowed)

    def new_filter(self) -> CatLineFilter:
        """A fresh per-connection filter (it holds a partial line between reads)."""
        return CatLineFilter(self)


class CatLineFilter:
    """Splits a client byte stream into lines and marks the allowed ones."""

    def __init__(self, guard: CatReadOnlyGuard) -> None:
        self._guard = guard
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[bytes, bool]]:
        """Consume one chunk; return ``[(line, allowed)]`` in wire order.

        A partial line is held until its ``;`` arrives. An unterminated run
        longer than a CAT line can be is returned whole as one blocked entry,
        so the stream recovers at the next ``;``.
        """
        self._buf += data
        lines: list[tuple[bytes, bool]] = []
        while (end := self._buf.find(_TERMINATOR)) >= 0:
            line = bytes(self._buf[: end + 1])
            del self._buf[: end + 1]
            lines.append((line, self._guard.allows(line)))
        if len(self._buf) > _MAX_LINE:
            lines.append((bytes(self._buf), False))
            self._buf.clear()
        return lines


__all__ = ["CatLineFilter", "CatReadOnlyGuard", "cat_lines"]
