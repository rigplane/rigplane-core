from math import ceil, floor, nextafter

import pytest

from rigplane.scope.models import Coverage, View
from rigplane.scope.resample import max_hold


def _scalar_projection(pixels, coverage, view, width):
    result = bytearray(width)
    native_scale = len(pixels) / (coverage.end_hz - coverage.start_hz)
    step = (view.end_hz - view.start_hz) / width
    for column in range(width):
        start = max(view.start_hz + column * step, coverage.start_hz)
        end = min(view.start_hz + (column + 1) * step, coverage.end_hz)
        if start < end:
            first = max(0, floor((start - coverage.start_hz) * native_scale))
            last = min(len(pixels), ceil((end - coverage.start_hz) * native_scale))
            result[column] = max(pixels[first:last], default=0)
    return bytes(result)


@pytest.mark.parametrize("native_width", [1, 3, 475, 689, 4096])
@pytest.mark.parametrize("width", [1, 2, 1024, 2800, 65535])
def test_vector_projection_matches_scalar_frequency_intervals(native_width, width):
    pixels = bytes((i * 37) % 256 for i in range(native_width))
    coverage = Coverage(14_000_000, 14_200_000)
    for view in (
        View(14_000_000, 14_200_000),
        View(13_900_000, 14_300_000),
        View(14_000_123.25, 14_199_765.25),
        View(nextafter(coverage.end_hz, 0), 14_300_000),
        View(14_000_000.1, 14_000_000.1000001),
        View(0, 1e308),
        View(0, 1e30),
        View(14_000_123.25, 1e308),
    ):
        assert max_hold(pixels, coverage, view, width) == _scalar_projection(
            pixels, coverage, view, width
        )


def test_decimation_preserves_each_peak():
    native = bytes([0, 0, 160, 0]) * 1024
    assert (
        max_hold(native, Coverage(0, 4096), View(0, 4096), 1024) == bytes([160]) * 1024
    )


def test_upsampling_475_to_2800_never_invents_values():
    native = bytes(i % 161 for i in range(475))
    result = max_hold(native, Coverage(0, 475), View(0, 475), 2800)
    assert len(result) == 2800
    assert result[0] == native[0] and result[-1] == native[-1]
    assert max(result) == 160


def test_partial_coverage_is_positioned_without_stretching():
    assert max_hold(bytes([40, 160]), Coverage(2, 4), View(0, 8), 8) == bytes(
        [0, 0, 40, 160, 0, 0, 0, 0]
    )
    assert max_hold(bytes([40, 160]), Coverage(2, 4), View(8, 16), 8) is None


def test_downsample_uses_all_overlapping_native_bins():
    assert max_hold(bytes([1, 160, 3]), Coverage(0, 3), View(0, 3), 2) == bytes(
        [160, 160]
    )


@pytest.mark.parametrize("width", [0, -1, 65536, True, 1.5])
def test_invalid_width_is_rejected(width):
    with pytest.raises(ValueError, match="width"):
        max_hold(b"ab", Coverage(0, 2), View(0, 2), width)


def test_empty_pixels_and_touching_edges_have_no_coverage():
    assert max_hold(b"", Coverage(0, 2), View(0, 2), 4) is None
    assert max_hold(b"ab", Coverage(0, 2), View(2, 4), 4) is None
    assert max_hold(b"ab", Coverage(2, 4), View(0, 2), 4) is None
