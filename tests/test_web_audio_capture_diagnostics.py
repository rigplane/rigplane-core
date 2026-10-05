"""Safe microphone reports reuse audio_stats; no radio/capture is invoked."""

import asyncio
import json
import logging

import pytest

from rigplane.web.handlers.audio import AudioBroadcaster, AudioHandler
from test_web_audio_link_quality import _make_radio, _make_ws


@pytest.mark.parametrize("category", [
    "NotAllowedError", "NotFoundError", "NotReadableError", "OverconstrainedError",
    "AbortError", "SecurityError", "InvalidStateError", "TypeError", "unknown",
])
async def test_capture_category_is_retained_and_logged_without_identity(category, caplog):
    radio, _ = _make_radio()
    broadcaster = AudioBroadcaster(radio)
    handler = AudioHandler(_make_ws(), radio, broadcaster)
    await handler._start_rx()
    with caplog.at_level(logging.INFO, logger="rigplane.web.handlers.audio"):
        for _ in range(2):
            await handler._handle_control({
                "type": "audio_stats", "microphoneCaptureError": category,
                "deviceId": "private-device", "message": "private-exception",
            })
    expected = {"schemaVersion": 1, "lastFailureCategory": category}
    assert broadcaster.capture_diagnostics() == expected
    assert sum("microphone capture rejected" in r.message for r in caplog.records) == 1
    assert category in caplog.text
    assert "private" not in caplog.text + json.dumps(expected)
    assert handler._tx_active is False
    await handler._stop_rx()
    assert broadcaster.capture_diagnostics() == expected  # survives disconnect
    expected["lastFailureCategory"] = "private"
    assert broadcaster.capture_diagnostics()["lastFailureCategory"] == category


@pytest.mark.parametrize("value", [None, "private-device", "NotFoundError\nprivate", {}, [], 1, True])
def test_invalid_or_legacy_reports_cannot_overwrite_retained_failure(value):
    broadcaster = AudioBroadcaster(None)
    queue = asyncio.Queue()
    broadcaster._clients[id(queue)] = queue
    broadcaster.record_client_stats(queue, {"microphoneCaptureError": "NotReadableError"})
    broadcaster.record_client_stats(queue, {"microphoneCaptureError": value})
    broadcaster.record_client_stats(queue, {"underruns": 0})
    assert broadcaster.capture_diagnostics() == {
        "schemaVersion": 1, "lastFailureCategory": "NotReadableError",
    }


def test_no_report_and_unsubscribed_report_do_not_invent_failure():
    broadcaster = AudioBroadcaster(None)
    broadcaster.record_client_stats(asyncio.Queue(), {"microphoneCaptureError": "NotAllowedError"})
    assert broadcaster.capture_diagnostics() == {"schemaVersion": 1, "lastFailureCategory": None}
