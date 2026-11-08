from __future__ import annotations

import pytest

from qa.bench.dogfood.scope_fps import sequence_step, summarize, validate_frame
from rigplane.scope.models import ScopeWireFrame


def frame(*, width: int = 1024, seq: int = 1) -> ScopeWireFrame:
    return ScopeWireFrame(
        "radio", "radio", 0, 14_100_000, 14_200_000,
        14_100_000, 14_200_000, width, bytes(width), False, 0, seq, None,
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
