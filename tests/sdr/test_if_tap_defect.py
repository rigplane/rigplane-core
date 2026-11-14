"""MOR-4394 piece 2: characterize SDR IF-tap behaviour (no product change).

Scenario: a radio with a panadapter tap on its first IF (69 450 000 Hz,
inverted IF spectrum), configured per today's user guide
(``docs/guide/sdr-panadapter.md`` "IF tap vs antenna tap") as
``SdrConfig(freq_offset_hz=69_450_000, invert_spectrum=True)`` with an
independently tuned source (today's Soapy source always declares
independent tuning).

Composition mirrors the runtime (``web/server.py``): the full
``SdrConfig`` reaches ``SdrScopeController`` through
``controller_factory`` (which seeds the primary VFO at construction),
and ``invert_spectrum`` reaches the FFT producer through
``IqFftSource``. No real sleeps: virtual time only.

Status: the ``test_if_tap_*`` cases assert the CORRECT panorama for a
fixed IF tap and are expected to FAIL on today's code (that is the
point); the ``test_today_*`` cases pin what today's code actually does
so a failing run still shows the whole story.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from rigplane.sdr.controller import RETUNE_DEBOUNCE_S, SdrScopeController
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.scope_source import IqFftSource
from rigplane.sdr.types import SdrConfig

IF_HZ = 69_450_000
RATE_HZ = 2_400_000
QUARTER_RATE_HZ = RATE_HZ // 4
VFO_40M_HZ = 7_100_000
VFO_40M_STEP_HZ = 7_150_000
VFO_20M_HZ = 14_200_000


class RecordingSource(FakeIqSource):
    """FakeIqSource that records every hardware retune request."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.retune_calls: list[int] = []

    def set_center_freq(self, hz: int) -> None:
        self.retune_calls.append(int(hz))
        super().set_center_freq(hz)


def make_tap_config(**overrides) -> SdrConfig:
    """Today's guide-prescribed config for the 69.45 MHz inverted IF tap."""
    values: dict = {
        "device_args": "driver=remote,remote=127.0.0.1",
        "sample_rate_hz": RATE_HZ,
        "freq_offset_hz": IF_HZ,
        "invert_spectrum": True,
    }
    values.update(overrides)
    return SdrConfig(**values)


def build_tap_source(provider, config, virtual_clock, vfo_hz) -> IqFftSource:
    """Compose controller + FFT exactly the way the runtime does it."""

    def controller_factory(sink) -> SdrScopeController:
        controller = SdrScopeController(
            provider, sink, config, clock=lambda: virtual_clock()
        )
        controller.on_radio_state(vfo_hz, False)
        return controller

    return IqFftSource(
        provider,
        invert_spectrum=config.invert_spectrum,
        controller_factory=controller_factory,
        start_worker=False,
    )


def observed_state(provider, frames) -> str:
    """One-line dump of everything a failing run needs to tell the story."""
    parts = [
        f"retune_calls={provider.retune_calls}",
        f"provider_centre={provider.center_freq_hz}",
    ]
    for frame in frames:
        pixels = frame.pixels or b""
        peak = int(np.argmax(np.frombuffer(pixels, dtype=np.uint8))) if pixels else None
        span_hz = frame.coverage.end_hz - frame.coverage.start_hz
        rf = (
            frame.coverage.start_hz + (peak + 0.5) * span_hz / len(pixels)
            if peak is not None
            else None
        )
        parts.append(
            f"frame coverage=({frame.coverage.start_hz},"
            f" {frame.coverage.end_hz}) peak_pixel={peak} peak_rf={rf}"
        )
    return "; ".join(parts)


def peak_pixel_rf(frame) -> tuple[int, float]:
    """Map the peak pixel to RF from coverage start/end and pixel count."""
    pixels = frame.pixels or b""
    peak = int(np.argmax(np.frombuffer(pixels, dtype=np.uint8)))
    span_hz = frame.coverage.end_hz - frame.coverage.start_hz
    rf_hz = frame.coverage.start_hz + (peak + 0.5) * span_hz / len(pixels)
    return peak, rf_hz


def pixel_width_hz(frame) -> float:
    """Width of one pixel in Hz for the given frame."""
    pixels = frame.pixels or b""
    return (frame.coverage.end_hz - frame.coverage.start_hz) / len(pixels)


async def pump_frame(source, provider, n_blocks: int = 2):
    """Emit blocks and step the synchronous producer; return new frames."""
    frames: list = []
    source.on_frame(frames.append)
    provider.pump(n_blocks)
    source.producer.process_pending()
    return frames


async def test_if_tap_hardware_parks_on_if(virtual_clock) -> None:
    """Correct: VFO 7.1 MHz parks the hardware on the 69.45 MHz IF."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ)
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        state = observed_state(provider, [])
        assert all(call == IF_HZ for call in provider.retune_calls), (
            f"hardware must only ever be asked for the IF: {state}"
        )
        assert provider.center_freq_hz == IF_HZ, (
            f"hardware must sit on the IF, not VFO+offset: {state}"
        )
    finally:
        await source.stop()


async def test_if_tap_vfo_steps_never_retune_and_follow_vfo(
    virtual_clock,
) -> None:
    """Correct: tuning the VFO never retunes a fixed IF tap; frames track it."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ)
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        frames = await pump_frame(source, provider)
        parked_calls = list(provider.retune_calls)
        for vfo_hz in (VFO_40M_HZ, VFO_40M_STEP_HZ, VFO_20M_HZ):
            source.on_radio_state(vfo_hz, False)
            await virtual_clock.elapse(RETUNE_DEBOUNCE_S + 0.050)
            frames = await pump_frame(source, provider)
            state = observed_state(provider, frames)
            assert provider.retune_calls == parked_calls, (
                f"fixed IF tap must see zero further retunes: {state}"
            )
            frame = frames[-1]
            centre_hz = (frame.coverage.start_hz + frame.coverage.end_hz) / 2
            assert abs(centre_hz - vfo_hz) <= pixel_width_hz(frame), (
                f"frame coverage must centre on VFO {vfo_hz}: {state}"
            )
    finally:
        await source.stop()


async def test_if_tap_inverted_tone_maps_below_vfo(virtual_clock) -> None:
    """Correct: +100 kHz above the IF appears 100 kHz BELOW the VFO."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ, tones=[(100_000, -20.0)])
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        frames = await pump_frame(source, provider)
        frame = frames[-1]
        peak, peak_rf_hz = peak_pixel_rf(frame)
        state = observed_state(provider, frames)
        assert abs(peak_rf_hz - 7_000_000) <= pixel_width_hz(frame), (
            f"inverted IF tone must map to RF 7 000 000, not 7 200 000: {state}"
        )
    finally:
        await source.stop()


def test_if_tap_mode_offsets_have_no_config_field() -> None:
    """Gap record: no SdrConfig field expresses per-mode IF offsets."""
    fields = [f.name for f in dataclasses.fields(SdrConfig)]
    assert not any("mode" in name for name in fields), (
        "no way to express LSB +1500 / USB -1500 Hz IF mode offsets: "
        f"SdrConfig fields={fields}"
    )


async def test_today_retune_targets_vfo_plus_if_offset(virtual_clock) -> None:
    """Pin today: VFO 7.1 MHz retunes hardware to VFO + IF - rate/4."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ)
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        rf_hz = VFO_40M_HZ + IF_HZ
        expected = rf_hz - QUARTER_RATE_HZ
        state = observed_state(provider, [])
        assert provider.retune_calls == [expected], (
            f"today retunes to rf_hz - rate/4 = {expected}: {state}"
        )
        assert provider.center_freq_hz == expected, (
            f"today parks hardware at {expected}: {state}"
        )
    finally:
        await source.stop()


async def test_today_vfo_steps_drag_hardware_and_coverage(
    virtual_clock,
) -> None:
    """Pin today: VFO steps drag the hardware; coverage stays in IF space."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ)
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        first = (VFO_40M_HZ + IF_HZ) - QUARTER_RATE_HZ
        third = (VFO_20M_HZ + IF_HZ) - QUARTER_RATE_HZ
        coverages = []
        for vfo_hz in (VFO_40M_HZ, VFO_40M_STEP_HZ, VFO_20M_HZ):
            source.on_radio_state(vfo_hz, False)
            await virtual_clock.elapse(RETUNE_DEBOUNCE_S + 0.050)
            frames = await pump_frame(source, provider)
            frame = frames[-1]
            coverages.append((frame.coverage.start_hz, frame.coverage.end_hz))
        state = observed_state(provider, [])
        state += f"; coverages={coverages}"
        assert provider.retune_calls == [first, third], (
            f"today retunes to {[first, third]} (7.15 MHz fits the guard band,"
            f" 14.2 MHz does not): {state}"
        )
        half_rate = RATE_HZ // 2
        assert coverages == [
            (first - half_rate, first + half_rate),
            (first - half_rate, first + half_rate),
            (third - half_rate, third + half_rate),
        ], f"today labels frames with the hardware centre: {state}"
    finally:
        await source.stop()


async def test_today_inverted_tone_peak_lands_in_hardware_space(
    virtual_clock,
) -> None:
    """Pin today: the +100 kHz tone peak maps into ~75-77 MHz IF space."""
    provider = RecordingSource(sample_rate_hz=RATE_HZ, tones=[(100_000, -20.0)])
    source = build_tap_source(provider, make_tap_config(), virtual_clock, VFO_40M_HZ)
    await source.start()
    try:
        frames = await pump_frame(source, provider)
        frame = frames[-1]
        assert frame.pixels, (
            f"expected a measured frame: {observed_state(provider, frames)}"
        )
        assert len(frame.pixels) == 689, (
            f"expected 689 native pixels: {observed_state(provider, frames)}"
        )
        centre = (VFO_40M_HZ + IF_HZ) - QUARTER_RATE_HZ
        half_rate = RATE_HZ // 2
        state = observed_state(provider, frames)
        assert (frame.coverage.start_hz, frame.coverage.end_hz) == (
            centre - half_rate,
            centre + half_rate,
        ), f"today labels frames with the hardware centre: {state}"
        peak, peak_rf_hz = peak_pixel_rf(frame)
        assert centre - half_rate <= peak_rf_hz <= centre + half_rate, (
            f"peak pixel {peak} maps into hardware IF space: {state}"
        )
        assert peak_rf_hz > 70_000_000, (
            f"peak RF {peak_rf_hz} sits in IF space, far from VFO: {state}"
        )
        assert abs(peak_rf_hz - 7_000_000) > 1_000_000, (
            f"peak RF {peak_rf_hz} is nowhere near inverted RF 7 000 000: {state}"
        )
    finally:
        await source.stop()
