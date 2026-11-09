"""MOR-3690: blank simulator waves decode to all-zero frames; watchdog stays quiet.

The testkit Icom simulator replays recorded ``27 00`` scope waves. With
``scope_blank_waves`` it zeroes the pixel payload while the stream keeps
flowing — the menu-open radio from the MOR-3690 report. The waves travel
the real UDP path, decode through the production CI-V frame parser and
``ScopeAssembler``, and land in the web scope-health watchdog, which must
not write scope-enable for them.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.scope import ScopeAssembler, ScopeFrame
from rigplane.scope.models import Subscription, View
from rigplane.testing.timing import VirtualClock
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from test_web_server_coverage import _scope_radio
from testkit.test_simulator_scope_stream import _ACK, _client, _open, _waves


def _set(sim: MockIcomRadio, payload: bytes) -> None:
    assert sim._dispatch_civ(0x27, payload, 0xE0) == _ACK  # noqa: SLF001


def _decode(frame: bytes) -> ScopeFrame:
    """Decode one simulator wave through the production scope path."""
    parsed = parse_civ_frame(frame)
    assert parsed.sub == 0x00
    receiver = parsed.data[0]
    assembled = ScopeAssembler().feed(parsed.data[1:], receiver)
    assert assembled is not None
    assert len(assembled.pixels) > 0
    return assembled


async def _collect_blank_waves() -> list[ScopeFrame]:
    clock = VirtualClock(start=0)
    sim = MockIcomRadio(scope_stream=True, scope_blank_waves=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        decoded = []
        for _ in range(10):
            clock.advance(0.13)
            assert sim.pump_scope_stream() >= 0
            for wave in await _waves(client):
                decoded.append(_decode(wave))
            if len(decoded) >= 2:
                break
        return decoded
    finally:
        client.close()
        await sim.stop()


async def test_blank_waves_decode_to_all_zero_frames() -> None:
    """Simulator blank waves keep framing but carry no pixel data."""
    decoded = await _collect_blank_waves()

    assert len(decoded) >= 2
    for frame in decoded:
        assert all(b == 0 for b in frame.pixels)


def _watchdog_server(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WebServer, list[EnableScope]]:
    srv = WebServer(_scope_radio(), WebConfig(radio_model="IC-7610"))
    srv._scope_health_interval = 0.05  # noqa: SLF001
    queued: list[EnableScope] = []
    put_ordered = srv.command_queue.put_ordered  # noqa: SLF001

    def observe_and_complete(command: object, **kwargs: Any) -> Any:
        entry = put_ordered(command, **kwargs)
        if isinstance(command, EnableScope):
            queued.append(command)
        future = kwargs.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(None)
        return entry

    monkeypatch.setattr(srv.command_queue, "put_ordered", observe_and_complete)
    return srv, queued


async def test_watchdog_ignores_simulator_blank_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3690: decoded blank simulator frames never trigger a write."""
    from rigplane.core.state_pipeline_contracts import FieldPath

    frames = await _collect_blank_waves()
    assert frames, "simulator emitted no waves"
    srv, queued = _watchdog_server(monkeypatch)

    async with VirtualClock.on_running_loop() as clock:
        frame = frames[0]
        srv._radio_state.main.freq = (frame.start_freq_hz + frame.end_freq_hz) // 2
        subscription = await srv.scope_hub.subscribe(
            Subscription(
                "radio",
                0,
                View(frame.start_freq_hz, frame.end_freq_hz),
                1024,
                30,
            )
        )
        queued.clear()
        source = srv._radio_scope_source
        assert source is not None
        from rigplane.radio import IcomRadio

        publisher = IcomRadio("127.0.0.1", model="IC-7610")
        publisher._control_scope_session = srv._radio_scope_session
        publisher.on_scope_data(source.feed_frame)
        monitor = asyncio.create_task(srv._scope_health_monitor())
        try:
            for index in range(6):
                publisher._civ_runtime._publish_scope_frame(frames[index % len(frames)])
                await clock.elapse(srv._scope_health_interval / 2)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await subscription.close()

    assert queued == []
    hidden_path = FieldPath.parse("health.health.scope_hidden")
    values = [
        field.value
        for field in srv.command_state_store.snapshot().fields
        if field.path == hidden_path
    ]
    assert values == [False]


def test_blank_scope_wave_preserves_real_assembler_geometry():
    from dataclasses import replace
    from rigplane_testkit.icom.simulator import _blank_scope_wave

    sim = MockIcomRadio(scope_stream=True)
    for wave in sim._scope_templates[0]:
        original = _decode(wave)
        blank = _decode(_blank_scope_wave(wave))
        assert blank == replace(original, pixels=bytes(len(original.pixels)))


def test_blank_scope_wave_keeps_multipart_metadata_and_zeros_continuations():
    from rigplane.commands import build_civ_frame
    from rigplane_testkit.icom.simulator import _blank_scope_wave
    from test_scope import _seq1_payload

    metadata = build_civ_frame(
        0xE0,
        0x98,
        0x27,
        sub=0,
        data=b"\x00"
        + _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=0,
            start_hz=14_100_000,
            end_hz=50_000,
            oor=False,
        ),
    )
    assert _blank_scope_wave(metadata) == metadata
    assembler = ScopeAssembler()
    for sequence, pixels in ((1, metadata), (2, b"\x01\x02"), (3, b"\x03\x04")):
        wave = (
            metadata
            if sequence == 1
            else build_civ_frame(
                0xE0, 0x98, 0x27, sub=0, data=bytes((0, sequence, 3)) + pixels
            )
        )
        parsed = parse_civ_frame(_blank_scope_wave(wave))
        result = assembler.feed(parsed.data[1:], parsed.data[0])
    assert result == ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(4), False)


def test_blank_scope_wave_leaves_out_of_range_and_controls_unchanged():
    from rigplane.commands import build_civ_frame
    from rigplane_testkit.icom.simulator import _blank_scope_wave
    from test_scope import _seq1_payload

    oor = build_civ_frame(
        0xE0,
        0x98,
        0x27,
        sub=0,
        data=b"\x00"
        + _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=0,
            start_hz=14_100_000,
            end_hz=50_000,
            oor=True,
        ),
    )
    control = build_civ_frame(0xE0, 0x98, 0x27, sub=0x11, data=b"\x01")
    assert _blank_scope_wave(oor) == oor
    assert _blank_scope_wave(control) == control
