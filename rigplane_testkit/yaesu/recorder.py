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
from collections.abc import Iterable, Iterator
from pathlib import Path

from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig

_TERMINATOR = b";"

# The longest line any profile command produces is a few dozen bytes; a
# longer unterminated run is garbage, not a line waiting for its ';'.
_MAX_LINE = 64

# A template field: ``{name}`` is one letter or digit, ``{name:09d}`` is
# nine digits. Any other format spec does not match and so never allows a line.
_FIELD = re.compile(r"\{[^{}:]*(?::0?(\d*)d)?\}")

# The only writes a capture may be allowed to forward: re-tuning the receive
# VFOs. Nothing here keys the transmitter or touches power or any setting.
RX_TUNING_WRITES = (
    "set_freq",
    "set_freq_sub",
    "set_mode",
    "set_mode_sub",
    "set_filter_width",
)


def _template_regex(template: str) -> re.Pattern[str]:
    """The regex matching exactly the wire lines one command template makes."""
    parts: list[str] = []
    pos = 0
    for field in _FIELD.finditer(template):
        parts.append(re.escape(template[pos : field.start()]))
        width = field.group(1)
        if field.group(0) == "{chan}":  # MR/MT memory slot: five digits (MOR-3628)
            parts.append("[0-9]{5}")
        else:
            parts.append(f"[0-9]{{{width}}}" if width else "[0-9A-Za-z]")
        pos = field.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts))


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

    A capture that must re-tune the receiver (a calibration clip on another
    band or mode) may additionally allow the named :data:`RX_TUNING_WRITES`;
    their templates are matched field by field (``FA{freq:09d};`` is ``FA``
    plus exactly nine digits). No other write can be allowed.
    """

    def __init__(
        self,
        read_templates: frozenset[str],
        write_templates: frozenset[str] = frozenset(),
    ) -> None:
        if not read_templates:
            raise ValueError("the profile declares no CAT read commands")
        self._allowed = tuple(
            _template_regex(tpl) for tpl in sorted(read_templates | write_templates)
        )

    @classmethod
    def from_config(
        cls, config: RigConfig, allow_writes: Iterable[str] = ()
    ) -> CatReadOnlyGuard:
        """The guard for the READ templates of one loaded rig profile.

        ``allow_writes`` names :data:`RX_TUNING_WRITES` commands to allow too;
        any other name, or a name the profile has no write template for, is a
        ``ValueError``.
        """
        writes: set[str] = set()
        for name in allow_writes:
            spec = config.commands.get(name)
            if (
                name not in RX_TUNING_WRITES
                or not isinstance(spec, CatCommandSpec)
                or not spec.write
            ):
                raise ValueError(f"{name!r} is not an allowable RX tuning write")
            writes.add(spec.write)
        return cls(
            frozenset(
                spec.read
                for spec in config.commands.values()
                if isinstance(spec, CatCommandSpec) and spec.read
            ),
            frozenset(writes),
        )

    def allows(self, line: bytes) -> bool:
        """True when *line* (with its ``;``) is exactly one allowed command."""
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
        self._discarding = False  # inside an oversized line, up to its ';'

    def feed(self, data: bytes) -> list[tuple[bytes, bool]]:
        """Consume one chunk; return ``[(line, allowed)]`` in wire order.

        A partial line is held until its ``;`` arrives. An unterminated run
        longer than a CAT line can be is returned as a blocked entry, and
        everything up to the ``;`` that ends that oversized line is blocked
        too — its tail is not a fresh line, whatever it looks like.
        """
        self._buf += data
        lines: list[tuple[bytes, bool]] = []
        while (end := self._buf.find(_TERMINATOR)) >= 0:
            line = bytes(self._buf[: end + 1])
            del self._buf[: end + 1]
            lines.append((line, not self._discarding and self._guard.allows(line)))
            self._discarding = False
        if self._buf and (self._discarding or len(self._buf) > _MAX_LINE):
            lines.append((bytes(self._buf), False))
            self._buf.clear()
            self._discarding = True
        return lines


__all__ = ["CatLineFilter", "CatReadOnlyGuard", "RX_TUNING_WRITES", "cat_lines"]
