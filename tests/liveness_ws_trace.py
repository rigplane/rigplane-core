"""Capture HTTP-prewarmed liveness deliveries for the real frontend chain."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.web import runtime_helpers
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[1]
# test_web_server pulls in tests/_timing.py, which imports the non-shipping
# rigplane_testkit package from the repository root. Under pytest that root
# is importable via pythonpath=["."]; when this file runs as a script
# (vitest, via `uv run python tests/liveness_ws_trace.py`) only tests/ is
# on sys.path, so add the root the same way tests/e2e/*_web_server.py do.
sys.path.insert(0, str(ROOT))

from test_web_server import (  # noqa: E402
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)


async def capture_liveness_deliveries() -> list[dict]:
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    radio.last_valid_frame_monotonic = 1000.0
    server = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, radio_model="IC-7610")
    )
    age = 0.0
    with patch.object(
        runtime_helpers, "time", SimpleNamespace(monotonic=lambda: 1000 + age)
    ):
        listener = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
        async with listener:
            port = listener.sockets[0].getsockname()[1]
            # HTTP advances its delivery domain before any WS encoding.
            for i in range(5):
                server.command_state_store.apply(
                    Observation(
                        path=FieldPath.active("0", "freq_mode", "freq_hz"),
                        value=14_074_000,
                        source=SourceMetadata(
                            source="poll_response", provider="icom_civ"
                        ),
                        timestamp_monotonic=1000.0 + i,
                        provider_generation=server.command_state_store.provider_generation,
                    )
                )
                status, _, _ = await _http_get("127.0.0.1", port, "/api/v1/state")
                assert status == 200
            reader, writer, _ = await _ws_connect("127.0.0.1", port, "/api/v1/ws")
            deliveries = []
            try:

                async def receive_state() -> dict:
                    async with asyncio.timeout(5):
                        while True:
                            _, payload = await _ws_recv_frame(reader)
                            event = json.loads(payload)
                            if event["type"] == "state_update":
                                return event

                deliveries.append(await receive_state())
                for age in (0.5, 1.0):
                    await _ws_send_text(
                        writer, json.dumps({"type": "subscribe", "streams": ["state"]})
                    )
                    deliveries.append(await receive_state())
                return deliveries
            finally:
                await _close_ws(writer)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(capture_liveness_deliveries())))
