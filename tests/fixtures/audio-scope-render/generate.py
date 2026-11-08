"""Generate RF-mapped audio render fixtures through the actual PCM and hub path."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, TypedDict
from unittest.mock import patch

import numpy as np

from rigplane.audio import fft_scope
from rigplane.audio.scope_source import AudioFftSource
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.scope.hub import ScopeHub
from rigplane.scope.models import ScopeWireFrame, Subscription, View
from rigplane.testing.timing import ManualClock
from rigplane.web.protocol import encode_scope_frame_v2
from rigplane.web.scope_sources import WebScopePcmTap
from rigplane.web.server import WebConfig, WebServer


class AudioPassbandJson(TypedDict):
    tunedHz: float
    carrierOffsetHz: float
    sideband: Literal["upper", "lower", "both"]
    lowOffsetHz: float
    highOffsetHz: float


class InputJson(TypedDict):
    mode: str
    cwPitchHz: int | None
    sampleRateHz: int
    fftSize: int
    toneHz: int


class RenderSidecar(TypedDict):
    frame: dict[str, Any]
    audioPassband: AudioPassbandJson
    input: InputJson


class PcmInput:
    callback: Callable[[bytes], None] | None = None

    async def acquire(self, callback: Callable[[bytes], None]) -> None:
        self.callback = callback

    async def release(self) -> None:
        self.callback = None


async def generate(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, mode in (
        ("audio-usb-tone", "USB"),
        ("audio-lsb-tone", "LSB"),
        ("audio-cwu-tone", "CW-U"),
        ("audio-cwl-tone", "CW-L"),
    ):
        server = WebServer(None, WebConfig(radio_model="ic7300"))
        pitch = 700 if mode.startswith("CW-") else None
        inputs: InputJson = {
            "mode": mode,
            "cwPitchHz": pitch,
            "sampleRateHz": 48000,
            "fftSize": 2048,
            "toneHz": 1000,
        }
        observed = [
            (FieldPath.active("0", "freq_mode", field), value)
            for field, value in (
                ("freq_hz", 14_100_000),
                ("mode", mode),
                ("filter_width", 2400),
            )
        ]
        if pitch is not None:
            observed.append((FieldPath.global_("operator_controls", "cw_pitch"), pitch))
        for path, value in observed:
            server.command_state_store.apply_current(
                Observation(
                    path=path,
                    value=value,
                    source=SourceMetadata(source="poll_response", provider="test"),
                    timestamp_monotonic=1,
                )
            )
        mapping = WebScopePcmTap(server).facts
        facts = mapping()
        tap = PcmInput()
        source = AudioFftSource(
            tap, mapping, fft_size=inputs["fftSize"], sample_rate=inputs["sampleRateHz"]
        )
        frames: list[ScopeWireFrame] = []

        def encode(frame: ScopeWireFrame) -> bytes:
            frames.append(frame)
            return bytes(encode_scope_frame_v2(frame))

        hub = ScopeHub(encoder=encode)
        hub.register("audio_fft", source)
        view = View(
            facts.tuned_hz + facts.low_offset_hz, facts.tuned_hz + facts.high_offset_hz
        )
        sub = await hub.subscribe(Subscription("audio_fft", None, view, 1024, 20))
        try:
            pcm = (
                (
                    16000
                    * np.sin(
                        2
                        * np.pi
                        * inputs["toneHz"]
                        * np.arange(inputs["fftSize"])
                        / inputs["sampleRateHz"]
                    )
                )
                .astype("<i2")
                .tobytes()
            )
            assert tap.callback is not None
            with patch.object(
                fft_scope, "time", SimpleNamespace(monotonic=ManualClock(1))
            ):
                tap.callback(pcm)
            assert len(frames) == 1
            frame = frames[0]
            wire = await sub.receive()
            assert frame.width == source.capabilities().native_width
            fields = asdict(frame)
            fields["pixels"] = list(frame.pixels) if frame.pixels is not None else None
            sidecar: RenderSidecar = {
                "frame": fields,
                "audioPassband": {
                    "tunedHz": facts.tuned_hz,
                    "carrierOffsetHz": facts.carrier_offset_hz,
                    "sideband": facts.sideband,
                    "lowOffsetHz": facts.low_offset_hz,
                    "highOffsetHz": facts.high_offset_hz,
                },
                "input": inputs,
            }
            destination.joinpath(name + ".bin").write_bytes(wire)
            destination.joinpath(name + ".json").write_text(
                json.dumps(sidecar, separators=(",", ":")) + "\n"
            )
        finally:
            await sub.close()


if __name__ == "__main__":
    asyncio.run(generate(Path(__file__).parent))
