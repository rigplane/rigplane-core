from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from icom_lan.runtime._connection_state import RadioConnectionState
from icom_lan.radio import IcomRadio
from icom_lan.web.handlers import ControlHandler
from icom_lan.web.protocol import decode_json


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def _assert_doc_patterns(rel_path: str, patterns: list[str]) -> None:
    text = _read(rel_path)
    missing = [p for p in patterns if re.search(p, text, flags=re.IGNORECASE) is None]
    if missing:
        formatted = "\n".join(f"- {pat}" for pat in missing)
        raise AssertionError(
            f"docs/runtime drift: {rel_path} is missing runtime invariant(s):\n{formatted}"
        )


def test_runtime_invariant_radio_ready_tracks_civ_health() -> None:
    radio = IcomRadio("127.0.0.1")
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._civ_transport = SimpleNamespace(_udp_error_count=0)
    radio._civ_stream_ready = True
    radio._civ_recovering = False
    radio._last_civ_data_received = time.monotonic()

    assert radio.radio_ready is True

    radio._civ_recovering = True
    assert radio.radio_ready is False

    radio._civ_recovering = False
    radio._last_civ_data_received = time.monotonic() - (
        radio._civ_ready_idle_timeout + 0.1
    )
    assert radio.radio_ready is False


@pytest.mark.asyncio
async def test_runtime_invariant_control_rejects_manual_connect_during_backend_recovery() -> (
    None
):
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    sent: list[str] = []

    async def _send_text(payload: str) -> None:
        sent.append(payload)

    ws.send_text = _send_text
    radio = SimpleNamespace(
        connected=True,
        radio_ready=False,
        conn_state=SimpleNamespace(value="reconnecting"),
    )
    handler = ControlHandler(ws, radio, "test", "IC-7610", server=None)

    await handler._handle_radio_connect({"id": "busy"})

    assert sent, "expected backend_recovering response"
    msg = decode_json(sent[-1])
    assert msg["ok"] is False
    assert msg["error"] == "backend_recovering"


def test_docs_sync_radio_contract_invariants() -> None:
    _assert_doc_patterns(
        "docs/api/radio.md",
        [
            r"radio_ready",
            r"backend[- ]managed",
            r"connected.*radio_ready",
            r"radio_addr:\s*int\s*\|\s*None\s*=\s*None|Optional CI-V address override",
        ],
    )


def test_docs_sync_connection_recovery_invariants() -> None:
    _assert_doc_patterns(
        "docs/guide/connection.md",
        [
            r"radio_ready",
            r"partial connectivity|connected\s*=\s*true[^\n]*radio_ready\s*=\s*false",
            r"readiness gate|gated by radio_ready|defer",
        ],
    )


def test_docs_sync_web_ui_invariants() -> None:
    _assert_doc_patterns(
        "docs/guide/web-ui.md",
        [
            r"backend_recovering",
            r"backend (manages|owns) (reconnect|recovery)",
            r"scope.*(defer|deferred).*radio_ready",
        ],
    )
