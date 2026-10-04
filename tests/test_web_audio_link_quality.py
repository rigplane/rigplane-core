"""Client link-quality uplink (MOR-585, ADR §3.6 — step 18 of MOR-562).

Falsification suite: the server must RECORD per-client link-quality
signals so the step-19 adaptive egress codec controller can later read
them — an inbound ``audio_stats`` message updates the per-client
snapshot, and the broadcaster's drop-oldest queue eviction increments
an observable per-client counter.

[BP] stats collection only: a client that never sends ``audio_stats``
behaves exactly as before — no error, codec unchanged.
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster, AudioHandler
from rigplane.web.protocol import AUDIO_CODEC_PCM16, AUDIO_HEADER_SIZE
from rigplane.web.websocket import WebSocketConnection

# 20 ms @ 48 kHz mono s16le.
PCM_PAYLOAD = b"\x01\x02" * 960

STATS_MSG: dict[str, Any] = {
    "type": "audio_stats",
    "underruns": 3,
    "buffer_depth_ms": 140,
    "dropped_frames": 2,
}

PLAYBACK = {
    "schemaVersion": 1,
    "contextState": "suspended",
    "sampleRate": 48000,
    "receivedFrames": 3,
    "validFrames": 2,
    "decodedFrames": 1,
    "pcmPeak": 0,
    "scheduledFrames": 0,
    "suspendedDrops": 1,
    "resumeOutcome": "rejected",
    "lastResumeError": "NotAllowedError",
}


def _make_radio() -> tuple[Any, AudioBus]:
    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = 48000
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio, bus


def _make_ws() -> MagicMock:
    ws = MagicMock(spec=WebSocketConnection)
    ws.send_text = AsyncMock()
    return ws


async def _inject(bus: AudioBus, count: int = 1) -> None:
    for _ in range(count):
        pkt = MagicMock()
        pkt.data = PCM_PAYLOAD
        bus._on_opus_packet(pkt)
    await asyncio.sleep(0.1)


class TestAudioStatsUplink:
    async def test_bounded_playback_report_survives_existing_client_snapshot(self):
        radio, _ = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()
        await handler._handle_control({**STATS_MSG, "playback": dict(PLAYBACK)})
        snapshot = broadcaster.client_link_quality(handler._frame_queue)
        assert snapshot["playback"] == PLAYBACK
        snapshot["playback"]["lastResumeError"] = "secret"
        retained = broadcaster.client_link_quality(handler._frame_queue)
        assert retained["playback"] == PLAYBACK
        await handler._handle_control(dict(STATS_MSG))
        assert "playback" not in broadcaster.client_link_quality(handler._frame_queue)

    async def test_invalid_or_unbounded_playback_is_not_retained(self):
        radio, _ = _make_radio()
        handler = AudioHandler(_make_ws(), radio, AudioBroadcaster(radio))
        for key, value in [
            ("contextState", "private text"),
            ("sampleRate", math.inf),
            ("receivedFrames", 2**53),
            ("pcmPeak", -1),
            ("lastResumeError", "token=secret"),
            ("unexpected", "secret"),
            ("validFrames", True),
            ("resumeOutcome", {"message": "secret"}),
        ]:
            await handler._handle_control(
                {**STATS_MSG, "playback": {**PLAYBACK, key: value}, "unknown_number": 7}
            )
            assert handler._link_quality == {
                "underruns": 3,
                "buffer_depth_ms": 140,
                "dropped_frames": 2,
            }

    async def test_numeric_signals_are_finite_bounded_and_allowlisted(self):
        radio, _ = _make_radio()
        handler = AudioHandler(_make_ws(), radio, AudioBroadcaster(radio))
        for value in (True, -1, math.nan, math.inf, 10**1000, 2**32):
            await handler._handle_control(
                {"type": "audio_stats", "underruns": value, "unknown": 7}
            )
            assert handler._link_quality == {}

    async def test_audio_stats_records_per_client_link_quality(self) -> None:
        """An inbound audio_stats message must update both the handler's
        snapshot and the broadcaster's per-client map (the structure the
        step-19 controller reads)."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()

        await handler._handle_control(dict(STATS_MSG))

        expected = {"underruns": 3, "buffer_depth_ms": 140, "dropped_frames": 2}
        assert handler._link_quality == expected
        snapshot = broadcaster.client_link_quality(handler._frame_queue)
        assert snapshot == {**expected, "ws_queue_drops": 0}

    async def test_latest_stats_win(self) -> None:
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()

        await handler._handle_control(dict(STATS_MSG))
        await handler._handle_control(
            {"type": "audio_stats", "underruns": 7, "buffer_depth_ms": 60}
        )

        snapshot = broadcaster.client_link_quality(handler._frame_queue)
        assert snapshot == {
            "underruns": 7,
            "buffer_depth_ms": 60,
            "ws_queue_drops": 0,
        }

    async def test_non_numeric_fields_are_dropped(self) -> None:
        """Defensive contract: only numeric signals are recorded — a
        malicious/buggy client cannot park arbitrary payloads server-side."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()

        await handler._handle_control(
            {
                "type": "audio_stats",
                "underruns": 1,
                "note": "free text",
                "flag": True,
                "blob": {"a": 1},
                "nothing": None,
            }
        )

        snapshot = broadcaster.client_link_quality(handler._frame_queue)
        assert snapshot == {"underruns": 1, "ws_queue_drops": 0}

    async def test_stats_before_rx_start_do_not_register_with_broadcaster(
        self,
    ) -> None:
        """audio_stats before audio_start must not leak entries into the
        broadcaster's per-client maps (the queue is not subscribed yet)."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)

        await handler._handle_control(dict(STATS_MSG))

        assert handler._link_quality["underruns"] == 3
        assert broadcaster._client_link_quality == {}


class TestQueueDropCounter:
    async def test_drop_oldest_eviction_increments_per_client_counter(self) -> None:
        """Forcing the bounded per-client WS queue past its watermark must
        evict-oldest AND count it — the counter is the server-side WS
        congestion signal of ADR §3.6."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        broadcaster.HIGH_WATERMARK = 2  # tiny queue → forced evictions
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()

        await _inject(bus, count=4)  # nobody drains → 2 fill + 2 evictions

        # MOR-2743: the relay task fans the injected frames out on this same
        # loop, but under CI load its scheduling trailed the old fixed 0.1 s
        # sleep once (quick run 36339733417 attempt 1: 0 drops counted). Await
        # the eviction itself — loop-yield until both evictions are counted —
        # instead of a wall-clock delay.
        for _ in range(2000):
            snapshot = broadcaster.client_link_quality(handler._frame_queue)
            if snapshot["ws_queue_drops"] == 2 and handler._frame_queue.qsize() == 2:
                break
            await asyncio.sleep(0)
        snapshot = broadcaster.client_link_quality(handler._frame_queue)
        assert snapshot["ws_queue_drops"] == 2
        # Eviction kept the stream flowing: queue still holds newest frames.
        assert handler._frame_queue.qsize() == 2

    async def test_unsubscribe_clears_link_quality_state(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        broadcaster.HIGH_WATERMARK = 2
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()
        await handler._handle_control(dict(STATS_MSG))
        await _inject(bus, count=4)

        await broadcaster.unsubscribe(handler._frame_queue)

        assert broadcaster._client_link_quality == {}
        assert broadcaster._client_queue_drops == {}


class TestNoStatsClientUnchanged:
    async def test_client_that_never_sends_audio_stats_behaves_as_today(
        self,
    ) -> None:
        """[BP] gate: no audio_stats → no error envelope, frames still
        arrive in the unchanged default PCM16 codec, and the link-quality
        snapshot is just the zeroed server-side counter."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        await handler._handle_control({"type": "audio_start", "direction": "rx"})
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert frame[AUDIO_HEADER_SIZE:] == PCM_PAYLOAD
        sent = [json.loads(c.args[0]) for c in ws.send_text.await_args_list]
        assert [m for m in sent if m.get("type") == "error"] == []
        assert broadcaster.client_link_quality(handler._frame_queue) == {
            "ws_queue_drops": 0
        }


def test_playback_diagnostics_is_bounded_anonymous_and_detached() -> None:
    broadcaster = AudioBroadcaster(None)
    queues = [asyncio.Queue() for _ in range(10)]
    for queue in queues:
        broadcaster._clients[id(queue)] = queue
        broadcaster.record_client_stats(queue, {**STATS_MSG, "playback": PLAYBACK})
        broadcaster._client_identity[id(queue)] = "private-session-device"
        broadcaster._client_link_quality[id(queue)]["raw_error"] = "private-message"
    snapshot = broadcaster.playback_diagnostics()
    assert snapshot == {
        "schemaVersion": 1,
        "state": "available",
        "activeClients": 10,
        "reportedClients": 10,
        "omittedClients": 2,
        "clients": [PLAYBACK] * 8,
    }
    assert "private" not in json.dumps(snapshot)
    snapshot["clients"][0]["lastResumeError"] = "private-message"
    assert broadcaster.playback_diagnostics()["clients"][0] == PLAYBACK
    broadcaster.record_client_stats(queues[0], dict(STATS_MSG))
    assert snapshot["reportedClients"] == 10
    assert broadcaster.playback_diagnostics()["reportedClients"] == 9
    broadcaster._drop_client(id(queues[1]))
    assert broadcaster.playback_diagnostics()["activeClients"] == 9
    assert broadcaster.playback_diagnostics()["reportedClients"] == 8


def test_playback_diagnostics_missing_legacy_invalid_and_disconnected() -> None:
    broadcaster = AudioBroadcaster(None)
    assert broadcaster.playback_diagnostics() == {
        "schemaVersion": 1,
        "state": "no_clients",
        "activeClients": 0,
        "reportedClients": 0,
        "omittedClients": 0,
        "clients": [],
    }
    queue = asyncio.Queue()
    broadcaster._clients[id(queue)] = queue
    for stats in (
        {},
        dict(STATS_MSG),
        {"playback": {**PLAYBACK, "deviceId": "private-device"}},
        {"playback": {**PLAYBACK, "lastResumeError": "private-message"}},
        {"playback": {**PLAYBACK, "decodedFrames": 2**32}},
        {"playback": {**PLAYBACK, "sampleRate": math.inf}},
        {"playback": {**PLAYBACK, "resumeOutcome": {"raw": "private"}}},
    ):
        # Bypass ingestion deliberately: export must revalidate retained state.
        broadcaster._client_link_quality[id(queue)] = stats
        assert broadcaster.playback_diagnostics() == {
            "schemaVersion": 1,
            "state": "no_playback_reports",
            "activeClients": 1,
            "reportedClients": 0,
            "omittedClients": 0,
            "clients": [],
        }
    broadcaster.record_client_stats(queue, {"playback": PLAYBACK})
    broadcaster._drop_client(id(queue))
    broadcaster.record_client_stats(queue, {"playback": PLAYBACK})
    broadcaster._client_link_quality[id(queue)] = {"playback": PLAYBACK}
    assert broadcaster.playback_diagnostics()["state"] == "no_clients"
