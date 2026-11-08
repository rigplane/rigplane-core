"""Max-hold projection of native frequency bins onto a requested view."""

from rigplane.core._optional_deps import _require_numpy

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
    _require_numpy()
    import numpy as np

    native_scale = len(pixels) / (coverage.end_hz - coverage.start_hz)
    step = (view.end_hz - view.start_hz) / width
    edges = view.start_hz + np.arange(width + 1, dtype=np.float64) * step
    starts = np.maximum(edges[:-1], coverage.start_hz)
    ends = np.minimum(edges[1:], coverage.end_hz)
    valid = starts < ends
    first = np.maximum(
        0, np.floor((starts[valid] - coverage.start_hz) * native_scale)
    ).astype(np.intp)
    last = np.minimum(
        len(pixels), np.ceil((ends[valid] - coverage.start_hz) * native_scale)
    ).astype(np.intp)
    nonempty = first < last
    result: np.ndarray[tuple[int], np.dtype[np.uint8]] = np.zeros(width, dtype=np.uint8)
    if nonempty.any():
        positions = np.flatnonzero(valid)[nonempty]
        first, last = first[nonempty], last[nonempty]
        native = np.frombuffer(pixels, dtype=np.uint8)
        # Adjacent columns meet at one edge. reduceat covers floor-to-floor;
        # ceil may include the next segment's first bin as well.
        reduced = np.maximum.reduceat(native[: last[-1]], first)
        result[positions] = np.maximum(reduced, native[last - 1])
    return bytes(result.tobytes())
