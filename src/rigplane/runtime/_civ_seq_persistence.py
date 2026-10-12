"""Per-radio record of the CI-V tracked sequence number (MOR-3418).

The control phase records the CI-V transport's ``send_seq`` here, deletes the
record at a clean exit, and starts a fresh login's numbering just past a record
it finds instead of at 0. ``tests/testkit/test_orphan_civ_restart.py``
characterizes the case this serves. One JSON file per ``host:port`` under the
platformdirs state directory, holding the radio's GUID from login; a record
whose GUID differs from the current login's is not used.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path
from typing import Final

import platformdirs

#: Seconds between two records.
WRITE_INTERVAL_S: Final = 5.0
#: Tracked CI-V packets per second assumed sent between the last record and death.
ADVANCE_RATE_PER_S: Final = 12
#: Packets added on top of the assumed rate; chosen, not measured.
ADVANCE_MARGIN: Final = 30
#: Upper bound on how far a fresh login steps past the recorded number.
MAX_ADVANCE: Final = 150
#: Divisor turning the step into the wait shown to the operator.
FILL_RATE_PER_S: Final = 10
#: A record older than this is not used.
MAX_RECORD_AGE_S: Final = 3600.0

_FORMAT_VERSION: Final = 1
_SEQ_MODULUS: Final = 0x10000


def _state_dir() -> Path:
    """Directory holding the records (test seam: tests redirect it per test)."""
    return Path(platformdirs.user_state_path("rigplane")) / "civ-seq"


def _record_path(host: str, port: int) -> Path:
    # Hashed: a host string can hold characters that are unsafe in a file name.
    digest = hashlib.sha256(f"{host}:{port}".encode()).hexdigest()
    return _state_dir() / f"{digest}.json"


def advance(interval_s: float | None = None) -> int:
    """Packets a fresh login starts past the record: ``interval_s`` (default
    ``WRITE_INTERVAL_S``) at ``ADVANCE_RATE_PER_S`` plus ``ADVANCE_MARGIN``,
    never above ``MAX_ADVANCE``."""
    interval = WRITE_INTERVAL_S if interval_s is None else interval_s
    return min(math.ceil(interval * ADVANCE_RATE_PER_S) + ADVANCE_MARGIN, MAX_ADVANCE)


def start_seq(recorded: int) -> int:
    """First tracked number of a fresh login that found ``recorded``."""
    return (recorded + advance()) % _SEQ_MODULUS


def wait_seconds() -> int:
    """The step over ``FILL_RATE_PER_S``, rounded up: the wait shown to the operator."""
    return math.ceil(advance() / FILL_RATE_PER_S)


def status_line(waiting_s: int | None) -> str:
    """Operator sentence for a fresh login the radio leaves unanswered.

    Without a record there is no estimate, so ``waiting_s`` is None and the wait
    is left out.
    """
    line = "radio still holds the previous session; waiting"
    return line if waiting_s is None else f"{line} ~{waiting_s} s"


def read_record(host: str, port: int, guid: bytes, now: float) -> int | None:
    """The recorded ``send_seq``, or None for a missing, unreadable or malformed
    file, a different GUID, or a record older than ``MAX_RECORD_AGE_S`` or dated
    after ``now``."""
    try:
        document = json.loads(_record_path(host, port).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or document.get("version") != _FORMAT_VERSION:
        return None
    if document.get("guid") != guid.hex():
        return None
    send_seq = document.get("send_seq")
    written_at = document.get("written_at")
    if type(send_seq) is not int or not 0 <= send_seq < _SEQ_MODULUS:
        return None
    if not isinstance(written_at, (int, float)) or isinstance(written_at, bool):
        return None
    if not 0 <= now - written_at <= MAX_RECORD_AGE_S:
        return None
    return send_seq


def write_record(host: str, port: int, guid: bytes, send_seq: int, now: float) -> None:
    """Replace the record atomically (temporary file, then rename); not fsynced."""
    path = _record_path(host, port)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    document = {
        "version": _FORMAT_VERSION,
        "guid": guid.hex(),
        "send_seq": send_seq,
        "written_at": now,
    }
    try:
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def remove_record(host: str, port: int, guid: bytes) -> None:
    """Delete this radio's record; another GUID's is left, an absent file is fine."""
    path = _record_path(host, port)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("guid") != guid.hex():
            return
    except (OSError, ValueError, AttributeError):
        pass
    path.unlink(missing_ok=True)
