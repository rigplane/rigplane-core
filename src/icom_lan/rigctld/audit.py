"""Structured per-command audit logging for the rigctld server.

Emits one JSON line per executed command to a dedicated audit logger
(``icom_lan.rigctld.audit``), kept separate from the main server logger
so operators can route it to a file independently.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

__all__ = [
    "AuditRecord",
    "AUDIT_LOGGER_NAME",
    "RigctldAuditFormatter",
    "log_command",
]

AUDIT_LOGGER_NAME = "icom_lan.rigctld.audit"


@dataclass(slots=True)
class AuditRecord:
    """Immutable record capturing a single rigctld command execution.

    Attributes:
        timestamp: ISO 8601 UTC timestamp of when the command was received.
        client_id: Monotonically increasing server-assigned client identifier.
        peername: ``"host:port"`` string of the connected TCP client.
        cmd: Short (single-char or ``\\name``) command string.
        long_cmd: Long-form command name (e.g. ``"get_freq"``).
        args: Tuple of string arguments supplied with the command.
        vfo: Leading VFO token (``"VFOA"``, ``"VFOB"``, ``"currVFO"`` …)
            captured by the parser when the client uses ``chk_vfo=1``
            mode, else ``None``. Distinguishes ``F VFOB 14080000`` from
            ``F VFOA 14080000`` from bare-form ``F 14080000`` in audit
            logs (issue #1346).
        duration_ms: Wall-clock time from execute-start to response-sent, ms.
        rprt: Hamlib ``RPRT`` code (0 = success, negative = error).
        is_set: ``True`` if this was a write/set command.
    """

    timestamp: str
    client_id: int
    peername: str
    cmd: str
    long_cmd: str
    args: tuple[str, ...]
    duration_ms: float
    rprt: int
    is_set: bool
    vfo: str | None = None


class RigctldAuditFormatter(logging.Formatter):
    """Formats :class:`AuditRecord` log entries as a single JSON line.

    Install on a :class:`logging.Handler` attached to
    :data:`AUDIT_LOGGER_NAME` to get structured audit output::

        fh = logging.FileHandler("audit.jsonl")
        fh.setFormatter(RigctldAuditFormatter())
        logging.getLogger(AUDIT_LOGGER_NAME).addHandler(fh)
    """

    def format(self, record: logging.LogRecord) -> str:
        audit: AuditRecord = record.msg  # type: ignore[assignment]
        return json.dumps(
            {
                "timestamp": audit.timestamp,
                "client_id": audit.client_id,
                "peername": audit.peername,
                "cmd": audit.cmd,
                "long_cmd": audit.long_cmd,
                "args": list(audit.args),
                "vfo": audit.vfo,
                "duration_ms": audit.duration_ms,
                "rprt": audit.rprt,
                "is_set": audit.is_set,
            }
        )


def log_command(record: AuditRecord) -> None:
    """Emit *record* to the audit logger at INFO level.

    If no handler is attached to :data:`AUDIT_LOGGER_NAME` (the default),
    the record is silently discarded — enabling audit logging requires
    explicitly configuring a handler on that logger.
    """
    logging.getLogger(AUDIT_LOGGER_NAME).info(record)
