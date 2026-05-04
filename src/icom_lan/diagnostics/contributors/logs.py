"""Logs contributor — copy rotating log files into the bundle, redact line-by-line."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from icom_lan.diagnostics.redaction import (
    redact_credentials,
    redact_ips,
    redact_paths,
)

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


logger = logging.getLogger(__name__)


_LOG_BASENAMES: tuple[str, ...] = (
    "icom-lan.log",
    "icom-lan.log.1",
    "icom-lan.log.2",
)


def _redact_line(line: str) -> str:
    return redact_credentials(redact_ips(redact_paths(line)))


class LogsContributor:
    """Emits ``logs/icom-lan.log{,.1,.2}`` — copies of rotating logs, redacted line-by-line."""

    name = "logs"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        log_dir = ctx.log_dir
        if not log_dir.exists() or not log_dir.is_dir():
            return  # nothing to copy; bundle assembler records empty dir
        for basename in _LOG_BASENAMES:
            src = log_dir / basename
            if not src.exists() or not src.is_file():
                continue
            dst = output_dir / basename
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            try:
                with src.open("r", encoding="utf-8", errors="replace") as fin:
                    with tmp.open("w", encoding="utf-8") as fout:
                        for line in fin:
                            fout.write(_redact_line(line))
                os.replace(tmp, dst)
            except OSError as exc:
                # Never fall back to an unredacted copy — that would leak PII.
                # Best-effort cleanup of the partial tmp file; leave dst absent.
                logger.warning("logs contributor: failed to copy %s: %r", src, exc)
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
