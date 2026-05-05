"""Errors contributor — snapshot of in-process exception ring."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

from icom_lan.diagnostics._error_ring import get_ring
from icom_lan.diagnostics.redaction import (
    redact_credentials,
    redact_ips,
    redact_paths,
)

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import BundleContext


def _redact_traceback_lines(lines: list[str]) -> list[str]:
    return [redact_credentials(redact_ips(redact_paths(line))) for line in lines]


def _redact_message(msg: str) -> str:
    return redact_credentials(redact_ips(redact_paths(msg)))


class ErrorsContributor:
    """Emits ``errors/recent-tracebacks.json`` from the global exception ring."""

    name = "errors"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        snapshot = get_ring().snapshot()
        items = []
        for item in snapshot:
            d = dataclasses.asdict(item)
            d["message"] = _redact_message(d["message"])
            d["traceback_lines"] = _redact_traceback_lines(d["traceback_lines"])
            items.append(d)
        payload = {"count": len(items), "items": items}
        text = json.dumps(payload, indent=2, sort_keys=True)
        (output_dir / "recent-tracebacks.json").write_text(
            text + "\n", encoding="utf-8"
        )
