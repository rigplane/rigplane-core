"""Opt-in PR-only web audio observation during a lost key-on ACK."""

from __future__ import annotations

import asyncio
import json
import os
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio.lan_stream import AudioStream, parse_audio_packet
from rigplane.audio.session import AudioSession
from rigplane.runtime.managed_tx_state import ActuationResult
from rigplane.testing.timing import virtual_clock
from rigplane.web.handlers.audio import AudioHandler, browser_tx_audio_facts
from rigplane.web.protocol import (
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from test_mor4846_release_latency_probe import (
    _drive,
    _probe_rig,
    _ptt_payload,
    _scope_clocks,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("MOR4846_PROBE") != "1", reason="set MOR4846_PROBE=1"
)


@virtual_clock
async def test_lost_ack_audio_probe(clock, monkeypatch, tmp_path):
    _scope_clocks(clock, monkeypatch)
    await _drive(clock, _lost_ack_audio_scenario(clock, tmp_path))


class _AudioWire:
    my_id = 0x00010001
    remote_id = 0xDEADBEEF

    def __init__(self, rig):
        self.rig = rig
        self.sent = []
        # Stand in for an already-open audio channel, without starting UDP RX.
        rig.sim._audio_client = (("127.0.0.1", 1), self.my_id)

    async def send_tracked(self, packet):
        parsed = parse_audio_packet(packet)
        assert parsed is not None, "production AudioStream emitted a non-audio packet"
        frame_id = int.from_bytes(parsed.data[:2], "little")
        self.sent.append({"id": frame_id, "t": self.rig.clock()})
        self.rig.sim._handle_audio_packet(
            packet,
            ("127.0.0.1", 1),
            struct.unpack_from("<H", packet, 4)[0],
            self.my_id,
            len(self.sent),
            None,
        )


async def _lost_ack_audio_scenario(clock, tmp_path):
    async with _probe_rig(clock, tmp_path, "wifi15", 0) as rig:
        wire = _AudioWire(rig)
        radio = rig.radio
        radio._audio_transport = wire
        radio._audio_stream = AudioStream(wire)
        radio._audio_session = AudioSession(radio, monotonic=clock)
        radio._audio_session.add_listener(radio._on_audio_session_event)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        attach_managed_tx_composition(server, rig.composition)
        ws = SimpleNamespace(send_text=AsyncMock(), send_binary=AsyncMock())
        handler = AudioHandler(
            ws,
            radio,
            tx_gate=server._bridge_tx_gate_open,
            managed_tx_authority=rig.authority,
        )
        assert browser_tx_audio_facts(radio).available
        # PCM is used end to end; no native codec backend is needed.
        handler._ensure_tx_transcoder = lambda: False
        await handler._handle_control({"type": "audio_start", "direction": "tx"})
        assert handler._tx_active, "audio_start did not arm the production web path"
        rig.wire.drop_key_ack = True
        offered = []
        pushes = []
        settlement = {}
        key_wait = None

        def offer(frame_id):
            entry = {"id": frame_id, "t": clock()}
            offered.append(entry)
            # 20 ms of mono PCM16 at 48 kHz, with the marker repeated so
            # both production LAN chunks can be associated with the frame.
            pcm = frame_id.to_bytes(2, "little") * 960
            payload = bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
            payload += b"\x00" * (AUDIO_HEADER_SIZE - 2) + pcm

            async def push():
                await handler._handle_tx_audio(payload)
                entry["returned_at"] = clock()

            pushes.append(asyncio.create_task(push()))

        try:
            offer(0)
            await pushes[-1]
            key = await rig.authority.submit_ptt(True, "probe")

            async def observe_settlement():
                result = await key.wait_settlement()
                settlement.update(t=clock(), result=result)

            key_wait = asyncio.create_task(observe_settlement())
            await rig.wire.key_sent.wait()
            assert not key.settlement_done, "PR-only probe needs a waiting key ACK"
            key_frame = next(
                e
                for e in rig.wire.events
                if e["direction"] == "tx" and _ptt_payload(e) == b"\x00\x01"
            )
            for frame_id in range(1, 112):
                offer(frame_id)
                await asyncio.sleep(0.020)
            await key_wait
            assert rig.sim.ptt_on
            assert settlement["result"].result is ActuationResult.ACCEPTED
            assert not rig.wire.key_ack_received.is_set(), "key ACK was not suppressed"
            offer(112)
            await asyncio.gather(*pushes)
            await asyncio.sleep(0.100)
            received = rig.sim.audio_tx_frames
            assert received, "no audio reached the simulator; check audio path setup"
            assert [int.from_bytes(f.payload[:2], "little") for f in received] == [
                frame["id"] for frame in wire.sent
            ], "audio transport submissions and simulator sink disagree"
            origin = key_frame["t"]
            settled_at = settlement["t"]
            rows = []
            for entry in offered:
                sent = [f["t"] for f in wire.sent if f["id"] == entry["id"]]
                at_radio = [
                    f
                    for f in received
                    if int.from_bytes(f.payload[:2], "little") == entry["id"]
                ]
                if not sent:
                    disposition = "dropped"
                elif sent[0] < settled_at:
                    disposition = "sent_before_settlement"
                elif entry["t"] < settled_at:
                    disposition = "held_until_settlement"
                else:
                    disposition = "sent_after_settlement"
                rows.append(
                    {
                        "id": entry["id"],
                        "disposition": disposition,
                        "offered_ms": round((entry["t"] - origin) * 1000, 6),
                        "returned_ms": round((entry["returned_at"] - origin) * 1000, 6),
                        "wire_ms": [round((t - origin) * 1000, 6) for t in sent],
                        "radio_ms": [
                            round((f.received_at - origin) * 1000, 6) for f in at_radio
                        ],
                        "radio_ptt": [f.ptt for f in at_radio],
                    }
                )
            print(
                json.dumps(
                    {
                        "probe": "MOR4846_LOST_ACK_AUDIO",
                        "revision": os.getenv("MOR4846_REVISION", "unset"),
                        "profile": "wifi15",
                        "audio_link_delay_ms": 0,
                        "key_frame_ms": 0,
                        "key_applied_ms": round(
                            (
                                next(
                                    e["t"]
                                    for e in rig.wire.events
                                    if e["direction"] == "radio"
                                    and _ptt_payload(e) == b"\x00\x01"
                                )
                                - origin
                            )
                            * 1000,
                            6,
                        ),
                        "settlement_ms": round((settled_at - origin) * 1000, 6),
                        "settlement_result": settlement["result"].result.value,
                        "response_timeout_s": radio._civ_get_timeout,
                        "gate_drop_counts": dict(handler._tx_warn_counts),
                        "frames": rows,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        finally:
            for task in [*pushes, *([key_wait] if key_wait is not None else [])]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                *pushes,
                *([key_wait] if key_wait is not None else []),
                return_exceptions=True,
            )
            await handler._handle_control({"type": "audio_stop", "direction": "tx"})
            release = await rig.authority.submit_ptt(False, "probe")
            await release.wait_settlement()
            radio._audio_transport = None
            radio._audio_stream = None
