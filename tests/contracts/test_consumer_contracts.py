"""In-suite consumer-driven contract (CDC) check (MOR-883).

Mirrors the dedicated `consumer-contracts-gate.yml` workflow inside the normal
pytest run so a core change that breaks a fielded consumer is caught even when
the dedicated path-filtered gate does not trigger. The real assertion logic
lives in `scripts/run_consumer_contracts.py`; this test imports its public
entrypoint and asserts it exits zero against HEAD.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.web.server import WebConfig, WebServer

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_consumer_contracts.py"

pytest.importorskip("jsonschema")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_consumer_contracts", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_consumer_expectations_satisfied() -> None:
    runner = _load_runner()
    assert runner.main() == 0, "a consumer expectation is violated against HEAD"


class _StatusWriter:
    """Minimal asyncio.StreamWriter stand-in that captures the raw response."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def get_extra_info(self, name: str, default=None):
        if name == "peername":
            return ("127.0.0.1", 55555)
        return default


def _status_and_body(writer: _StatusWriter) -> tuple[int, dict]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


@pytest.mark.asyncio
async def test_readyz_status_reflects_station_readiness() -> None:
    """GET /readyz answers non-200 with no radio and 200 once the radio is ready.

    The CDC files cannot carry an HTTP status (pro/readyz declares schema
    `{}`), so this row pins the status half of the consumer's dependency: the
    consumer only polls the status. The ready stub mirrors the one
    `test_readyz_reflects_station_readiness` uses.
    """
    not_ready_srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    not_ready_writer = _StatusWriter()
    await not_ready_srv._handle_http(not_ready_writer, "GET", "/readyz")  # noqa: SLF001
    status, _ = _status_and_body(not_ready_writer)
    assert status != 200

    ready_radio = SimpleNamespace(radio_ready=True, connected=True, capabilities=set())
    ready_srv = WebServer(ready_radio, WebConfig(host="127.0.0.1", port=0))
    ready_writer = _StatusWriter()
    await ready_srv._handle_http(ready_writer, "GET", "/readyz")  # noqa: SLF001
    status, _ = _status_and_body(ready_writer)
    assert status == 200
