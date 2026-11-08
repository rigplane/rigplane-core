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
