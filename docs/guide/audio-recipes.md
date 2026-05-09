# Audio Recipes (copy/paste)

Practical scenarios for the current `rigplane` audio API.
Most generic examples below use **PCM 16-bit mono 48 kHz**
(`AudioCodec.PCM_1CH_16BIT`) to avoid external codec dependencies. Radio
profiles may choose a different radio-native LAN contract when a model has a
better-known default.

## Radio-native vs browser/client audio contracts

Direct Icom LAN audio is PCM-first in `rigplane`. The radio-native contract is
the codec, sample rate, and channel count written into the Icom conninfo packet
and accepted by the radio. For direct radio LAN connections this should normally
be PCM or u-law; Opus is not assumed to be a native Icom radio codec. Opus can
still be useful as a server-to-browser or server-to-client transport when a web
consumer benefits from lower bandwidth, but that is a consumer contract layered
after the radio stream has been decoded to PCM for taps, DSP, and bridges.

The runtime tracks three related decisions:

| Contract | Meaning |
| --- | --- |
| Requested radio-native | Values selected before conninfo from explicit overrides, the radio profile, or global defaults. |
| Effective radio-native | Values actually used after conninfo. If the radio rejects a request, this can record a fallback such as stereo RX downgraded to mono. |
| Web RX emission | Browser/client codec selected from profile policy, for example PCM from the radio emitted as browser Opus. This does not change the radio-native stream. |

Use diagnostics when debugging audio negotiation. The `audio/audio.json` file in
a diagnostic report includes `radio_native.requested`,
`radio_native.effective`, and `web_rx` when those values are available or
configured.

`ICOM_AUDIO_SAMPLE_RATE` remains an operator override. Set it when you need to
force the direct LAN conninfo sample rate for testing, hardware compatibility,
or bandwidth-constrained tunnel paths:

```bash
export ICOM_AUDIO_SAMPLE_RATE=16000
uv run rigplane --host 192.168.55.40 --user USER --pass-file .rigplane-pass web
```

Explicit API or CLI/env overrides take precedence over profile defaults. If no
override is supplied, model profiles choose the best known radio-native default;
for example the IC-7610 direct LAN profile requests full-fidelity PCM at
48 kHz. On constrained VPN/cloud paths, 16 kHz is a good low-bandwidth override
because it fits each 20 ms stereo PCM frame in one UDP packet.

## Prerequisites

```bash
export ICOM_HOST=192.168.1.100
export ICOM_USER=YOUR_USER
export ICOM_PASS=YOUR_PASS
```

```bash
# Audio dependencies (opuslib, sounddevice, numpy) ship with the core
# install since v0.19 — `pip install rigplane` is enough.
pip install rigplane
```

---

## Optional OS Audio Smoke

The normal pytest suite does not open PortAudio devices. To smoke-test the
in-process bridge against real OS audio devices, opt in explicitly and select
both sides of the virtual route:

```bash
uv run rigplane --list-audio-devices

RIGPLANE_OS_AUDIO_SMOKE=1 \
RIGPLANE_OS_AUDIO_RX_DEVICE="<output/playback device id or name>" \
RIGPLANE_OS_AUDIO_TX_DEVICE="<input/capture device id or name>" \
uv run pytest tests/test_audio_pipeline_os_smoke.py -q -rs
```

`RIGPLANE_OS_AUDIO_RX_DEVICE` is where the generated test tone is played.
`RIGPLANE_OS_AUDIO_TX_DEVICE` is what rigplane captures and transmits. Use a
loopback pair such as BlackHole, Loopback, VB-Cable, or PipeWire if you want
the generated tone to feed TX capture without WSJT-X. The smoke skips unless
`RIGPLANE_OS_AUDIO_SMOKE=1` and explicit devices are provided. Set
`RIGPLANE_OS_AUDIO_SMOKE_FRAMES=<count>` to override the default 12 frames.

---

## Optional IC-7610 LAN Hardware Audio Validation

This test keys the transmitter and sends a short synthetic 1 kHz PCM tone over
the rigplane LAN audio path. Run it only with a safe RF setup, for example into
a dummy load or another controlled no-radiate configuration.

```bash
export RIGPLANE_HW_IC7610_AUDIO=1
export RIGPLANE_HW_ALLOW_TX=1
export RIGPLANE_HW_ICOM_HOST=192.168.55.40
export RIGPLANE_HW_ICOM_USER=YOUR_USER
export RIGPLANE_HW_ICOM_PASS_FILE=.rigplane-pass

uv run pytest tests/hardware/test_ic7610_audio_pipeline.py -q -s
```

The test starts an embedded `RigctldServer`, replays the WSJT-X packet/PTT
commands (`M PKTUSB`, `T 1`, `T 0`), injects synthetic PCM through
`AudioBridge`, and asserts that the route policy selects DATA2/LAN without
changing DATA1 modulation input. It prints compact diagnostics for the route,
codec, frame count, PCM peak/RMS, bridge TX frames, overruns, and DATA
modulation inputs.

Optional overrides:

```bash
export RIGPLANE_HW_ICOM_RADIO_ADDR=0x98
export RIGPLANE_HW_AUDIO_FRAMES=20
```

---

## 1) RX → WAV file (10 seconds)

Saves the incoming audio stream from the radio to `rx.wav`.

```python
import asyncio
import wave

from rigplane import create_radio, LanBackendConfig, AudioCodec

SAMPLE_RATE = 48000
CHANNELS = 1
SECONDS = 10


async def main() -> None:
    frames: list[bytes] = []

    config = LanBackendConfig(
        host="192.168.1.100",
        username="YOUR_USER",
        password="YOUR_PASS",
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_sample_rate=SAMPLE_RATE,
    )

    def on_audio(pkt) -> None:
        # For PCM codec, pkt.data contains raw PCM bytes.
        frames.append(pkt.data)

    async with create_radio(config) as radio:
        await radio.start_audio_rx_opus(on_audio)
        await asyncio.sleep(SECONDS)
        await radio.stop_audio_rx_opus()

    with wave.open("rx.wav", "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))


asyncio.run(main())
```

---

## 2) WAV file → TX (high-level PCM API)

Reads `tx.wav` (16-bit mono 48 kHz PCM) and transmits it.

```python
import asyncio
import wave

from rigplane import create_radio, LanBackendConfig, AudioCodec

SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2
FRAME_MS = 20
BYTES_PER_FRAME = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * FRAME_MS // 1000  # 1920


async def main() -> None:
    config = LanBackendConfig(
        host="192.168.1.100",
        username="YOUR_USER",
        password="YOUR_PASS",
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_sample_rate=SAMPLE_RATE,
    )

    with wave.open("tx.wav", "rb") as wf:
        assert wf.getnchannels() == CHANNELS, "tx.wav must be mono"
        assert wf.getframerate() == SAMPLE_RATE, "tx.wav must be 48kHz"
        assert wf.getsampwidth() == SAMPLE_WIDTH, "tx.wav must be 16-bit"
        pcm = wf.readframes(wf.getnframes())

    async with create_radio(config) as radio:
        await radio.start_audio_tx_pcm(
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            frame_ms=FRAME_MS,
        )
        try:
            for i in range(0, len(pcm), BYTES_PER_FRAME):
                chunk = pcm[i : i + BYTES_PER_FRAME]
                if not chunk:
                    break
                await radio.push_audio_tx_pcm(chunk)
                await asyncio.sleep(FRAME_MS / 1000)
        finally:
            await radio.stop_audio_tx_pcm()


asyncio.run(main())
```

---

## 3) Full-duplex loopback test (dry-run style)

Simultaneously:
- starts RX and counts incoming packets,
- sends a 10-second test tone on TX.

```python
import asyncio
import math
import struct

from rigplane import create_radio, LanBackendConfig, AudioCodec

SAMPLE_RATE = 48000
CHANNELS = 1
SAMPLE_WIDTH = 2
FRAME_MS = 20
BYTES_PER_FRAME = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * FRAME_MS // 1000
FREQ = 1000.0
SECONDS = 10


def make_tone_frame(phase: float) -> tuple[bytes, float]:
    samples = int(SAMPLE_RATE * FRAME_MS / 1000)
    out = bytearray()
    step = 2 * math.pi * FREQ / SAMPLE_RATE
    for _ in range(samples):
        v = int(0.2 * 32767 * math.sin(phase))
        out += struct.pack("<h", v)
        phase += step
    return bytes(out), phase


async def main() -> None:
    rx_packets = 0

    config = LanBackendConfig(
        host="192.168.1.100",
        username="YOUR_USER",
        password="YOUR_PASS",
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_sample_rate=SAMPLE_RATE,
    )

    def on_audio(_pkt) -> None:
        nonlocal rx_packets
        rx_packets += 1

    async with create_radio(config) as radio:
        await radio.start_audio_rx_opus(on_audio)
        await radio.start_audio_tx_pcm(
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            frame_ms=FRAME_MS,
        )

        phase = 0.0
        frames = int(SECONDS * 1000 / FRAME_MS)
        for _ in range(frames):
            chunk, phase = make_tone_frame(phase)
            await radio.push_audio_tx_pcm(chunk)
            await asyncio.sleep(FRAME_MS / 1000)

        await radio.stop_audio_tx_pcm()
        await radio.stop_audio_rx_opus()

    print({"rx_packets": rx_packets})


asyncio.run(main())
```

---

## 4) AudioBus — multi-consumer pub/sub

Route the same RX audio to multiple consumers simultaneously.

```python
import asyncio
from rigplane import create_radio, LanBackendConfig

async def main() -> None:
    config = LanBackendConfig(
        host="192.168.1.100",
        username="YOUR_USER",
        password="YOUR_PASS",
    )

    async with create_radio(config) as radio:
        bus = radio.audio_bus

        # Consumer 1: count packets
        count = 0
        async def consumer_1():
            nonlocal count
            async with bus.subscribe(name="counter") as sub:
                async for pkt in sub:
                    count += 1
                    if count >= 100:
                        break

        # Consumer 2: save first 50 packets
        packets = []
        async def consumer_2():
            async with bus.subscribe(name="saver") as sub:
                async for pkt in sub:
                    packets.append(pkt.data)
                    if len(packets) >= 50:
                        break

        await asyncio.gather(consumer_1(), consumer_2())
        print(f"Counted {count}, saved {len(packets)}")

asyncio.run(main())
```

---

## 5) WSJT-X all-in-one (CLI)

Run Web UI + audio bridge + rigctld in a single command:

```bash
# Install (audio-bridge deps ship with the core install since v0.19)
pip install rigplane
brew install blackhole-2ch  # macOS

# Start everything
rigplane --host 192.168.1.100 --user USER --pass PASS \
    web --bridge "BlackHole 2ch"

# WSJT-X settings:
#   Radio: Hamlib NET rigctl, localhost:4532
#   Audio Input/Output: BlackHole 2ch
```

---

## Heavy usage and deployment

The audio bridge runs the TX path (reading from the virtual device and sending to the radio) in a thread, using the event loop’s default thread pool via `run_in_executor`. Under heavy load or with **multiple bridge instances or many concurrent clients** (e.g. web UI + rigctld + bridge in one process), that shared pool can become a bottleneck.

**Recommendations:**

1. **Tuning:** Pass a dedicated `ThreadPoolExecutor` for TX I/O so bridge traffic does not compete with other work:
   ```python
   from concurrent.futures import ThreadPoolExecutor
   executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bridge-tx")
   bridge = AudioBridge(radio, device_name="BlackHole 2ch", tx_executor=executor)
   ```
   You can use `max_workers=1` or `2`; the bridge only runs one TX read at a time.

2. **Deployment:** For heavy scenarios, run the bridge in a **separate process** (e.g. a dedicated `rigplane web --bridge ...` instance or a small script that only runs the bridge). That isolates CPU and I/O and avoids contention with web/rigctld in the same process.

3. **Scale:** Prefer **one bridge (and ideally one radio connection) per process** when you need stable low-latency audio; limit the number of simultaneous bridge clients if they share the same executor or process.

---

## Troubleshooting (audio)

See the dedicated playbook:

- [Troubleshooting](troubleshooting.md)

Especially useful sections:
- Handshake / port negotiation (CI-V/audio port = 0)
- Timeout / retry / reconnect recovery
- Structured logging for integration tests
