"""MOR-3256: a TX audio marker through the real WS stack into the sink.

The production stack of ``test_tx_under_fault_matrix._stand_up_stack`` (real
``WebServer`` + ``RigctldServer`` + managed TX authority + ``IcomRadio`` over
the ``MockIcomRadio`` LAN simulator, built by ``test_multi_session_tx_lanes._Arena``
with ``capture_tx_audio=True``) is keyed over ``/api/v1/ws``, and a marker tone
with planted silence gaps (``rigplane_testkit/audio_markers.py``) is streamed
as 20 ms PCM16 frames over ``/api/v1/audio`` — while the simulator's RX byte
ramp runs on the same audio port (the RX-leak condition). The captured sink
payload must analyze clean: right frequency and level, gaps at their offsets,
no dropout, no RX leak; the sink's ``ptt`` flags and frame numbering pin the
gating (PTT off -> frames stop).

FAKE RADIO ONLY — the simulator; nothing here can key a real radio.
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

# Make tests/ importable from tests/integration/.
sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.mock_integration

from rigplane.runtime.tx_interlock import RfState  # noqa: E402
from rigplane.web.protocol import (  # noqa: E402
    AUDIO_CODEC_PCM16,
    MSG_TYPE_AUDIO_TX,
    encode_audio_frame,
)

from rigplane_testkit.audio_markers import (  # noqa: E402
    MarkerSpec,
    analyze_marker_pcm16,
    check_marker,
    render_marker_pcm16,
)

from test_multi_session_tx_lanes import _Arena  # noqa: E402
from test_tx_under_fault_matrix import (  # noqa: E402
    _GATE_TIMEOUT_S,
    _KEY_DEADLINE_S,
    _wait_ptt,
    _wait_rf,
    _WsKeyClient,
)

from rigplane_testkit.icom import MockIcomRadio  # noqa: E402

_FRAME_MS = 20  # one WS TX frame per 20 ms of PCM, paced in real time

# Latency bounds, measured on the Mac mini and set at ~4x the observed
# values, with a 250 ms floor for loaded runners (observed: WS frame sent -> sink 0.004 s; simulator PTT-on ->
# first sink frame 0.372 s — the latter is mostly the test's own
# observe-then-send path; both numbers are in the MOR-3256 PR text).
_WS_TO_SINK_BOUND_S = 0.25
_PTT_ON_TO_SINK_BOUND_S = 1.5


async def _open_tx_mic(
    arena: _Arena, key: str | None
) -> tuple[_WsKeyClient, dict[str, object]]:
    """Open ``/api/v1/audio`` and arm TX; return the mic and the
    ``audio_tx_format`` reply (the negotiated TX sample rate and codec)."""
    mic = await arena.ws(key, "/api/v1/audio")
    await mic.send({"type": "audio_start", "direction": "tx"})
    reply = await mic._until(lambda message: message.get("type") == "audio_tx_format")  # noqa: SLF001
    return mic, reply


async def _send_pcm(mic: _WsKeyClient, pcm: bytes, sample_rate: int) -> float:
    """Stream ``pcm`` as 20 ms MSG_TYPE_AUDIO_TX PCM16 frames paced in real
    time; return the monotonic time the first nonzero-payload frame went out."""
    frame_bytes = sample_rate * 2 * _FRAME_MS // 1000
    first_nonzero_at = 0.0
    start = time.monotonic()
    seq = 0
    for index, offset in enumerate(range(0, len(pcm), frame_bytes)):
        chunk = pcm[offset : offset + frame_bytes]
        if not first_nonzero_at and any(chunk):
            first_nonzero_at = time.monotonic()
        await mic.send_binary(
            encode_audio_frame(
                MSG_TYPE_AUDIO_TX,
                AUDIO_CODEC_PCM16,
                seq,
                sample_rate // 100,
                1,
                _FRAME_MS,
                chunk,
            )
        )
        seq = (seq + 1) & 0xFFFF
        await asyncio.sleep(
            max(0.0, start + (index + 1) * _FRAME_MS / 1000 - time.monotonic())
        )
    return first_nonzero_at


async def _wait_sink_bytes(sim: MockIcomRadio, payload_bytes: int) -> None:
    """Wait until the sink holds ``payload_bytes`` captured payload bytes.

    The radio's TX path chunks each WS frame to MAX_AUDIO_PAYLOAD (1364
    bytes; ``src/rigplane/audio/lan_stream.py``: ``push_tx``), so the sink
    frame count is not the WS frame count — the byte count is the exact
    one. Delivery of the last datagrams takes a loop tick; a fixed sleep
    would hope.
    """
    end = time.monotonic() + _GATE_TIMEOUT_S
    while sum(frame.payload_len for frame in sim.audio_tx_frames) < payload_bytes:
        assert time.monotonic() < end, (
            f"TX sink stuck at "
            f"{sum(frame.payload_len for frame in sim.audio_tx_frames)}"
            f"/{payload_bytes} bytes"
        )
        await asyncio.sleep(0.02)


async def test_marker_streamed_while_keyed_lands_clean_in_the_sink(
    tmp_path: Path,
) -> None:
    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        sim = arena.sim
        session = await arena.session()
        mic, tx_format = await _open_tx_mic(arena, session.key)
        rate = tx_format["sample_rate"]
        assert isinstance(rate, int) and rate > 0, tx_format
        spec = MarkerSpec(sample_rate=rate)
        pcm = render_marker_pcm16(spec)

        # PTT-gated: with nobody keyed, the WS gate drops every TX frame.
        probe_frames = 5
        await _send_pcm(mic, pcm[: probe_frames * rate * 2 * _FRAME_MS // 1000], rate)
        await asyncio.sleep(0.3)
        assert not sim.audio_tx_frames

        assert await session.primary.key("marker")
        await _wait_ptt(sim, True, _KEY_DEADLINE_S)
        # The audio gate opens on the observed TX; wait for it explicitly.
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        t_ptt_on = next(t for t, on in reversed(sim.ptt_transitions) if on)

        sent_first = await _send_pcm(mic, pcm, rate)
        # The RX byte ramp ran on the same port all along (leak condition).
        assert sim.audio_rx_frames_sent > 0
        await _wait_sink_bytes(sim, len(pcm))

        frames = sim.audio_tx_frames
        captured = sim.audio_tx_pcm()
        assert len(captured) == len(pcm)  # exactly the marker, nothing else
        assert all(frame.ptt for frame in frames)
        seqs = [frame.inner_seq for frame in frames]
        assert all((b - a) & 0xFFFF == 1 for a, b in zip(seqs, seqs[1:]))

        problems = check_marker(analyze_marker_pcm16(captured, spec), spec)
        assert problems == [], problems

        first = frames[0]
        assert first.received_at - sent_first <= _WS_TO_SINK_BOUND_S
        assert first.received_at - t_ptt_on <= _PTT_ON_TO_SINK_BOUND_S

        # PTT off -> frames stop. The audio gate follows the OBSERVED RF
        # state (the poller must read RX first, as in
        # test_multi_session_tx_lanes path4), so the freeze is pinned from
        # the observed RX, not from the unkey reply.
        await arena.unkey(session)
        await _wait_rf(arena.web, RfState.RX, _GATE_TIMEOUT_S)
        frozen = len(sim.audio_tx_frames)
        await _send_pcm(mic, pcm[:rate], rate)  # 0.5 s more tone
        assert len(sim.audio_tx_frames) == frozen


async def test_marker_check_catches_a_wrong_tone(tmp_path: Path) -> None:
    """The harness is not vacuous: the same pipeline fed a tone 200 Hz off
    the spec must make ``check_marker`` report the frequency."""
    async with _Arena(
        tmp_path, enable_audio=True, sim_options={"capture_tx_audio": True}
    ) as arena:
        session = await arena.session()
        mic, tx_format = await _open_tx_mic(arena, session.key)
        rate = tx_format["sample_rate"]
        assert isinstance(rate, int) and rate > 0, tx_format
        spec = MarkerSpec(duration_s=0.5, gaps=(), sample_rate=rate)
        sent = render_marker_pcm16(replace(spec, freq_hz=spec.freq_hz + 200.0))

        assert await session.primary.key("broken")
        await _wait_ptt(arena.sim, True, _KEY_DEADLINE_S)
        await _wait_rf(arena.web, RfState.TX, _GATE_TIMEOUT_S)
        await _send_pcm(mic, sent, rate)
        await _wait_sink_bytes(arena.sim, len(sent))

        problems = check_marker(
            analyze_marker_pcm16(arena.sim.audio_tx_pcm(), spec), spec
        )
        assert any("frequency" in problem for problem in problems)
        await arena.unkey(session)
