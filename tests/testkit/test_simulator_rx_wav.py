"""Integration: a ``WavSource`` drives the simulated audio port's RX stream.

In-process, real UDP on loopback, reusing the client harness of
``tests/testkit/test_simulator_audio.py``. With a source set, the RX
payload is the FILE's bytes; with ``rx_source=None`` it stays on the
recorded byte ramp (regression pin). Red mutation: point
``_audio_rx_packet`` back at the ramp while a source is set.
"""

from __future__ import annotations

import asyncio
import struct
import wave
from pathlib import Path

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.rx_source import RX_BURST_BYTES, WavSource
from rigplane_testkit.icom.simulator_audio import (
    _RX_IDENT_FIRST_FRAGMENT,
    AUDIO_HEADER_SIZE,
)
from testkit.test_simulator_audio import (
    _collect_audio,
    _complete_bursts,
    _open_audio,
    _recorded_burst_shape,
    _udp,
)


def _stereo_wav(path: Path, bursts: int) -> bytes:
    """A 48 kHz stereo s16 WAV (full-range pattern); returns the raw frames."""
    n = bursts * (RX_BURST_BYTES // 4)
    samples = struct.pack(
        f"<{n * 2}h", *(((i * 37) % 65536) - 32768 for i in range(n * 2))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(samples)
    return samples


async def test_rx_stream_carries_the_wav_payload(tmp_path: Path) -> None:
    wav = tmp_path / "rx.wav"
    raw = _stereo_wav(wav, bursts=50)
    server = MockIcomRadio(enable_audio=True, rx_source=WavSource(wav))
    await server.start()
    client = await _udp()
    try:
        await _open_audio(client, server)
        packets = await asyncio.wait_for(_collect_audio(client, 0.4), 5.0)
        bursts = _complete_bursts(packets, _recorded_burst_shape())
        assert len(bursts) >= 4, f"only {len(bursts)} complete bursts captured"

        for burst in bursts:
            first = burst[0][1]  # burst start: ident 0x8126 at 0x10
            assert struct.unpack_from(">H", first, 0x10)[0] == _RX_IDENT_FIRST_FRAGMENT
            payload = b"".join(p[AUDIO_HEADER_SIZE:] for _, p in burst)
            # Mid-file capture start: the burst index follows from the
            # audio-level seq at 0x12 (3 packets per burst, from 0).
            index = struct.unpack_from(">H", first, 0x12)[0] // 3
            assert (
                payload == raw[index * RX_BURST_BYTES : (index + 1) * RX_BURST_BYTES]
            ), f"burst {index} does not match the file"

        # Header sequences still step by 1 across the captured packets.
        flat = [p for burst in bursts for _, p in burst]
        seqs = [struct.unpack_from("<H", p, 6)[0] for p in flat]
        assert all((c - p) & 0xFFFF == 1 for p, c in zip(seqs, seqs[1:]))
    finally:
        client.close()
        await server.stop()


async def test_no_rx_source_keeps_the_byte_ramp() -> None:
    server = MockIcomRadio(enable_audio=True)
    await server.start()
    client = await _udp()
    try:
        await _open_audio(client, server)
        packets = await asyncio.wait_for(_collect_audio(client, 0.15), 5.0)
        frames = [p for _, p in packets if len(p) > AUDIO_HEADER_SIZE]
        assert len(frames) >= 6
        for p in frames:  # the ramp is seeded by the inner sequence
            inner = struct.unpack_from(">H", p, 0x12)[0]
            assert p[AUDIO_HEADER_SIZE] == inner & 0xFF
            assert p[AUDIO_HEADER_SIZE + 1] == (inner + 1) & 0xFF
    finally:
        client.close()
        await server.stop()
