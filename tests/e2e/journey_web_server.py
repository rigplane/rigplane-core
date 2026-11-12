"""Local WebServer targets for the operator-journey Chromium tests."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import struct
import sys
import time
from pathlib import Path
from typing import Any

from rigplane.audio.bus import AudioBus
from rigplane.audio.lan_stream import SYNTHETIC_RX_IDENT, AudioPacket
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[2]
# Non-shipping simulator helpers live at the repository root (as in pytest).
sys.path.insert(0, str(ROOT))
# SerialMockRadio is a non-shipping test double that lives under tests/.
sys.path.insert(0, str(ROOT / "tests"))
from serial_stub import SerialMockRadio  # noqa: E402


class JourneyRadio(SerialMockRadio):
    """SerialMockRadio plus the active-receiver observation the frequency entry needs."""

    # Production counterpart: CivRuntime._observations_from_frame in src/rigplane/runtime/_civ_rx.py (CI-V 0x07 0xD2 reply -> slow_state.active).
    def baseline_observations(self) -> tuple[Observation, ...]:
        active_receiver = Observation(
            path=FieldPath.global_("slow_state", "active"),
            value="MAIN",
            source=SourceMetadata(
                source="state_poller", provider="serial_mock", native_id="baseline"
            ),
            timestamp_monotonic=time.monotonic(),
            max_age=None,
        )
        return (*super().baseline_observations(), active_receiver)


# RX tone of JourneyAudioRadio: 20 ms PCM16 mono frames at 48 kHz, the
# relay's default format for a radio that names no ``audio_codec`` (web
# handlers/audio.py: AudioBroadcaster). 1 kHz puts a sample on each crest,
# so a frame's sample peak is the tone amplitude itself.
_TONE_RATE_HZ = 48_000
_TONE_HZ = 1_000
_TONE_FRAME_SAMPLES = 960


def _tone_amplitude(level_file: Path) -> float:
    """The tone amplitude for the dBFS number in ``level_file``; 0 (silence)
    while the file is missing or empty."""
    try:
        text = level_file.read_text().strip()
    except FileNotFoundError:
        return 0.0
    return 10 ** (float(text) / 20) if text else 0.0


class JourneyAudioRadio(JourneyRadio):
    """JourneyRadio plus the minimum RX-audio stand-in the LIVE journey needs.

    ``has_usb_audio`` (class attribute, the UsbAudioCapable sentinel) keeps
    the profile's ``audio`` tag through web runtime_helpers.runtime_capabilities;
    ``audio_bus`` returns the real AudioBus the web relay subscribes to, kept
    in the ``_audio_bus`` slot web server._runtime_audio_bus_payload reads;
    ``start_audio_rx_opus`` accepts the ``jitter_depth`` keyword
    audio.bus.AudioBus._begin_rx passes (the inherited SerialMockRadio
    signature does not). Without ``tone_level_file`` it emits nothing —
    audio.bus.AudioSubscription.__anext__ returns None while active, so the
    relay keeps running on an idle bus. With it, RX delivers a 1 kHz tone
    whose level (dBFS) the spec writes to that file; ``stop_audio_rx_opus``
    ends it.
    """

    has_usb_audio = True
    _audio_bus: AudioBus | None = None

    def __init__(self, *, model: str, tone_level_file: Path | None = None) -> None:
        super().__init__(model=model)
        self._tone_level_file = tone_level_file
        self._tone_task: asyncio.Task[None] | None = None

    @property
    def audio_bus(self) -> AudioBus:
        if self._audio_bus is None:
            self._audio_bus = AudioBus(self)
        return self._audio_bus

    async def start_audio_rx_opus(
        self, callback: Any, *, jitter_depth: int = 5
    ) -> None:
        if self._tone_level_file is not None and self._tone_task is None:
            self._tone_task = asyncio.get_running_loop().create_task(
                self._emit_tone(callback, self._tone_level_file)
            )

    async def stop_audio_rx_opus(self) -> None:
        task, self._tone_task = self._tone_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _emit_tone(self, callback: Any, level_file: Path) -> None:
        loop = asyncio.get_running_loop()
        frame_s = _TONE_FRAME_SAMPLES / _TONE_RATE_HZ
        wave = [
            math.sin(2 * math.pi * _TONE_HZ * n / _TONE_RATE_HZ)
            for n in range(_TONE_FRAME_SAMPLES)
        ]
        due = loop.time()
        seq = 0
        while True:
            scale = min(1.0, _tone_amplitude(level_file)) * 32767
            data = struct.pack(
                f"<{_TONE_FRAME_SAMPLES}h", *(round(scale * v) for v in wave)
            )
            callback(AudioPacket(ident=SYNTHETIC_RX_IDENT, send_seq=seq, data=data))
            seq = (seq + 1) & 0xFFFF
            due += frame_s
            await asyncio.sleep(max(0.0, due - loop.time()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18766, type=int)
    parser.add_argument("--static-dir", default=ROOT / "frontend" / "dist", type=Path)
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="start the WebServer with WebConfig.read_only=True",
    )
    parser.add_argument(
        "--freq",
        default=None,
        type=int,
        help="set the radio's MAIN frequency in Hz before serving (default: SerialMockRadio baseline)",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="serve with the RX-audio-capable JourneyAudioRadio instead of JourneyRadio",
    )
    parser.add_argument(
        "--model",
        default="IC-7610",
        help="rig profile the fake resolves (default: IC-7610)",
    )
    parser.add_argument(
        "--rx-tone-level-file",
        default=None,
        type=Path,
        help="with --audio: RX carries a 1 kHz tone at the dBFS level this file holds",
    )
    parser.add_argument(
        "--panorama-audio",
        action="store_true",
        help="use the loopback IC-7610 simulator with mixed scope and synthetic PCM",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    if not args.static_dir.is_dir():
        raise SystemExit(f"frontend build is missing: {args.static_dir}")
    simulator = None
    if args.panorama_audio:
        if args.host != "127.0.0.1":
            raise SystemExit("--panorama-audio requires --host 127.0.0.1")
        from rigplane.core.types import AudioCodec
        from rigplane.runtime.radio import IcomRadio
        from rigplane_testkit.icom.scope_control import ScopeControlSimulator

        simulator = ScopeControlSimulator(
            host="127.0.0.1",
            scope_stream=True,
            enable_audio=True,
            dual_receiver=True,
        )
        # The mixed journey exercises a refused pinned view while the
        # existing MAIN/SUB streams and PCM keep advancing.
        simulator.refused_civ_commands = {(0x27, 0x1E)}
        # Recorded headers/cadence remain intact; their all-zero amplitude
        # payloads cannot witness rendering. Give only this opt-in simulator a
        # synthetic nonconstant wave, carried through its normal CI-V sender.
        for receiver, templates in simulator._scope_templates.items():
            shaped = []
            for template in templates:
                assert len(template) == 711 and template[7:9] == b"\x01\x01"
                assert template[-1] == 0xFD
                for phase in range(2):
                    amplitude = bytes(
                        15 + phase * 25 + ((index + receiver * 17) % 90) // 3
                        for index in range(689)
                    )
                    shaped.append(template[:21] + amplitude + template[-1:])
            simulator._scope_templates[receiver] = shaped
        await simulator.start()
        radio = IcomRadio(
            host="127.0.0.1",
            port=simulator.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
            audio_codec=AudioCodec.PCM_2CH_16BIT,
            audio_sample_rate=48000,
        )
    else:
        radio = (
            JourneyAudioRadio(model=args.model, tone_level_file=args.rx_tone_level_file)
            if args.audio
            else JourneyRadio(model=args.model)
        )
    try:
        await radio.connect()
        if args.freq is not None:
            await radio.set_freq(args.freq)
        server = WebServer(
            radio,
            WebConfig(
                host=args.host,
                port=args.port,
                static_dir=args.static_dir,
                radio_model=radio.model,
                discovery=False,
                read_only=args.read_only,
            ),
        )
        await server.serve_forever(
            on_started=lambda: print(
                f"{radio.model} journey fake ready at http://{args.host}:{args.port}/?ui=v2",
                flush=True,
            )
        )
    finally:
        if simulator is not None:
            try:
                await radio.disconnect()
            finally:
                await simulator.stop()


if __name__ == "__main__":
    asyncio.run(run())
