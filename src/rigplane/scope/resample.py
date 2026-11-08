"""Max-hold projection of native frequency bins onto a requested view."""

from math import ceil, floor

from .models import Coverage, View


def max_hold(pixels: bytes, coverage: Coverage, view: View, width: int) -> bytes | None:
    if type(width) is not int or not 1 <= width <= 65535:
        raise ValueError("width must be an integer in [1, 65535]")
    if (
        not pixels
        or coverage.end_hz <= view.start_hz
        or coverage.start_hz >= view.end_hz
    ):
        return None
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
