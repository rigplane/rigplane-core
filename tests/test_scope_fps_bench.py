from __future__ import annotations

import pytest
import aiohttp

from qa.bench.dogfood import scope_fps as bench
from qa.bench.dogfood.scope_fps import sequence_step, summarize, validate_frame
from rigplane.scope.models import (
    Coverage,
    GrantedSubscription,
    ScopeWireFrame,
    Subscription,
    View,
)
from rigplane.web.protocol import encode_scope_frame_v2, encode_scope_granted
from dataclasses import replace


def frame(*, width: int = 1024, seq: int = 1) -> ScopeWireFrame:
    return ScopeWireFrame(
        "radio",
        "radio",
        0,
        14_100_000,
        14_200_000,
        14_100_000,
        14_200_000,
        width,
        bytes(width),
        False,
        0,
        seq,
        None,
    )


def test_sequence_gaps_are_expected_and_uint32_wrap_is_forward() -> None:
    assert sequence_step(8, 11) == 3
    assert sequence_step(0xFFFFFFFF, 1) == 2
    for previous, current in [(5, 5), (5, 4), (0, 0x80000000)]:
        with pytest.raises(ValueError, match="sequence"):
            sequence_step(previous, current)


def test_delivery_summary_uses_received_frames_not_native_sequence_span() -> None:
    result = summarize([0.0, 0.1, 0.2], 3 * 1085, 0.3)
    assert result["frames"] == 3
    assert result["fps"] == pytest.approx(10)
    assert result["bytes_per_second"] == pytest.approx(10850)


def test_frame_validation_checks_the_granted_consumer_contract() -> None:
    validate_frame(frame(), "radio", 0, 1024)
    with pytest.raises(ValueError, match="subscription"):
        validate_frame(frame(), "radio", 0, 2800)
    with pytest.raises(ValueError, match="subscription"):
        validate_frame(frame(), "audio_fft", None, 1024)


@pytest.mark.parametrize(
    ("pixels", "granted_fps", "frame_count", "expected_error"),
    [
        (None, 10, 1, "no in-range pixel"),
        (bytes(1024), 1, 1, None),
        (bytes(1024), 10, 1, "delivery below 80%"),
        (bytes(1024), 10, 7, "delivery below 80%"),
        (bytes(1024), 10, 8, None),
    ],
    ids=[
        "null",
        "complete-low-fps",
        "under-10-percent",
        "under-70-percent",
        "boundary-80-percent",
    ],
)
async def test_collect_requires_pixel_delivery_and_reports_valid_null_states(
    pixels: bytes | None,
    granted_fps: int,
    frame_count: int,
    expected_error: str | None,
    virtual_clock,
    monkeypatch,
) -> None:
    virtual_clock.scope_to(monkeypatch, bench)
    view = View(14_100_000, 14_200_000)
    grant = GrantedSubscription(
        "radio", 0, view, Coverage(view.start_hz, view.end_hz), 1024, granted_fps
    )
    messages = [
        aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, encode_scope_granted(grant), ""),
        *(
            aiohttp.WSMessage(
                aiohttp.WSMsgType.BINARY,
                encode_scope_frame_v2(replace(frame(), pixels=pixels, seq=seq)),
                "",
            )
            for seq in range(1, frame_count + 1)
        ),
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_str(self, _text):
            pass

        async def receive(self, **_kwargs):
            if not messages:
                raise TimeoutError
            return messages.pop(0)

    class Session:
        def ws_connect(self, *_args, **_kwargs):
            return Socket()

    request = Subscription("radio", 0, view, 1024, granted_fps)
    if expected_error is not None:
        with pytest.raises(ValueError, match=expected_error):
            await bench.collect(Session(), "http://127.0.0.1", (), request, 1)
    else:
        result = await bench.collect(Session(), "http://127.0.0.1", (), request, 1)
        assert result["pixel_frames"] == frame_count
        assert result["null_frames"] == result["out_of_range_frames"] == 0


@pytest.mark.parametrize(
    "granted_width,expected_error", [(475, None), (3000, "unexpected")]
)
async def test_collect_accepts_native_capped_grant(
    granted_width, expected_error, virtual_clock, monkeypatch
) -> None:
    """A grant narrower than requested (hub native-width cap) still collects."""
    virtual_clock.scope_to(monkeypatch, bench)
    view = View(14_100_000, 14_200_000)
    grant = GrantedSubscription(
        "radio", 0, view, Coverage(view.start_hz, view.end_hz), granted_width, 10
    )
    messages = [
        aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, encode_scope_granted(grant), ""),
        *(
            aiohttp.WSMessage(
                aiohttp.WSMsgType.BINARY,
                encode_scope_frame_v2(frame(width=granted_width, seq=seq)),
                "",
            )
            for seq in range(1, 9)
        ),
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_str(self, _text):
            pass

        async def receive(self, **_kwargs):
            if not messages:
                raise TimeoutError
            return messages.pop(0)

    class Session:
        def ws_connect(self, *_args, **_kwargs):
            return Socket()

    request = Subscription("radio", 0, view, 2800, 10)
    if expected_error is not None:
        with pytest.raises(ValueError, match=expected_error):
            await bench.collect(Session(), "http://127.0.0.1", (), request, 1)
    else:
        result = await bench.collect(Session(), "http://127.0.0.1", (), request, 1)
        assert result["width"] == granted_width
        assert result["pixel_frames"] == 8


class GrantReplaySession:
    def __init__(self, messages, clock):
        self.messages = list(messages)
        self.clock = clock

    def ws_connect(self, *_args, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def send_str(self, _text):
        pass

    async def receive(self, **_kwargs):
        if not self.messages:
            raise TimeoutError
        delay, message = self.messages.pop(0)
        if delay:
            await self.clock.elapse(delay)
        return message


def grant_message(grant):
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, encode_scope_granted(grant), "")


def pixel_message(width, seq, view=View(14_100_000, 14_200_000)):
    native = replace(
        frame(width=width, seq=seq),
        view_start_hz=view.start_hz,
        view_end_hz=view.end_hz,
        coverage_start_hz=view.start_hz,
        coverage_end_hz=view.end_hz,
    )
    return aiohttp.WSMessage(
        aiohttp.WSMsgType.BINARY, encode_scope_frame_v2(native), ""
    )


async def test_collect_follows_observed_width_and_geometry_grant_updates(
    virtual_clock, monkeypatch
):
    virtual_clock.scope_to(monkeypatch, bench)
    view = View(14_100_000, 14_200_000)
    provisional = GrantedSubscription(
        "radio", 0, view, Coverage(view.start_hz, view.end_hz), 2800, 10
    )
    observed = replace(provisional, width=689)
    changed_view = View(14_110_000, 14_190_000)
    changed = replace(
        observed,
        width=475,
        view=changed_view,
        coverage=Coverage(changed_view.start_hz, changed_view.end_hz),
    )
    messages = [(0, grant_message(provisional)), (0, grant_message(observed))]
    messages += [(0, pixel_message(689, seq)) for seq in range(1, 5)]
    messages += [(0, grant_message(changed))]
    messages += [(0, pixel_message(475, seq, changed_view)) for seq in range(5, 9)]
    result = await bench.collect(
        GrantReplaySession(messages, virtual_clock),
        "http://127.0.0.1",
        (),
        Subscription("radio", 0, view, 2800, 10),
        1,
    )
    assert result["width"] == 475
    assert result["pixel_frames"] == 8
    assert result["view"] == {
        "start_hz": changed_view.start_hz,
        "end_hz": changed_view.end_hz,
    }
    assert result["coverage"] == result["view"]


@pytest.mark.parametrize("initial_fps,updated_fps", [(2, 10), (10, 2)])
async def test_collect_integrates_changed_granted_fps_over_measurement(
    initial_fps, updated_fps, virtual_clock, monkeypatch
):
    virtual_clock.scope_to(monkeypatch, bench)
    view = View(14_100_000, 14_200_000)
    initial = GrantedSubscription(
        "radio", 0, view, Coverage(view.start_hz, view.end_hz), 1024, initial_fps
    )
    messages = [
        (0, grant_message(initial)),
        (0.5, grant_message(replace(initial, max_fps=updated_fps))),
    ]
    messages += [(0, pixel_message(1024, seq)) for seq in range(1, 6)]
    result = await bench.collect(
        GrantReplaySession(messages, virtual_clock),
        "http://127.0.0.1",
        (),
        Subscription("radio", 0, view, 1024, 10),
        1,
    )
    assert result["max_fps"] == updated_fps
    assert result["pixel_delivery_ratio"] == pytest.approx(5 / 6)


async def test_collect_refuses_error_control_after_usable_frames(
    virtual_clock, monkeypatch
):
    virtual_clock.scope_to(monkeypatch, bench)
    view = View(14_100_000, 14_200_000)
    initial = GrantedSubscription(
        "radio", 0, view, Coverage(view.start_hz, view.end_hz), 1024, 10
    )
    messages = [(0, grant_message(initial))]
    messages += [(0, pixel_message(1024, seq)) for seq in range(1, 9)]
    messages += [
        (
            0,
            aiohttp.WSMessage(
                aiohttp.WSMsgType.TEXT,
                '{"type":"error","message":"source unavailable"}',
                "",
            ),
        )
    ]
    with pytest.raises(ValueError, match="source unavailable"):
        await bench.collect(
            GrantReplaySession(messages, virtual_clock),
            "http://127.0.0.1",
            (),
            Subscription("radio", 0, view, 1024, 10),
            1,
        )
