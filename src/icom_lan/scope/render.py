"""Scope/waterfall frame rendering for Icom transceivers.

Renders ScopeFrame data as PNG images: spectrum plot and waterfall display.

Requires Pillow (optional dependency)::

    pip install icom-lan[scope]

Example::

    from icom_lan.scope_render import render_scope_image
    img = render_scope_image(frames, output="waterfall.png")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from icom_lan._optional_deps import _require_pillow

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

    from icom_lan.scope import ScopeFrame


class _ThemeSpec(TypedDict):
    background: tuple[int, int, int]
    spectrum_line: tuple[int, int, int]
    grid: tuple[int, int, int]
    label_color: tuple[int, int, int]
    colormap: list[tuple[int, int, int]]


__all__ = [
    "THEMES",
    "amplitude_to_color",
    "render_spectrum",
    "render_waterfall",
    "render_scope_image",
]

# ---------------------------------------------------------------------------
# Color themes
# ---------------------------------------------------------------------------

# Anchor points: (amplitude_0_160, R, G, B)
_CLASSIC_ANCHORS: list[tuple[int, int, int, int]] = [
    (0, 0, 0, 40),  # noise floor: dark blue/black
    (40, 0, 0, 200),  # weak: blue
    (80, 0, 200, 200),  # medium: cyan
    (120, 200, 200, 0),  # strong: yellow
    (160, 255, 50, 50),  # max: red
]

_GRAYSCALE_ANCHORS: list[tuple[int, int, int, int]] = [
    (0, 0, 0, 0),  # black
    (160, 255, 255, 255),  # white
]


def _build_colormap(
    anchors: list[tuple[int, int, int, int]],
) -> list[tuple[int, int, int]]:
    """Build a 161-entry colormap from anchor points by linear interpolation.

    Args:
        anchors: List of (amplitude, R, G, B) tuples in ascending amplitude order.

    Returns:
        List of 161 RGB tuples covering amplitudes 0-160.
    """
    colormap: list[tuple[int, int, int]] = []
    for i in range(161):
        lo = anchors[0]
        hi = anchors[-1]
        for j in range(len(anchors) - 1):
            if anchors[j][0] <= i <= anchors[j + 1][0]:
                lo = anchors[j]
                hi = anchors[j + 1]
                break
        span = hi[0] - lo[0]
        t = 0.0 if span == 0 else (i - lo[0]) / span
        r = int(lo[1] + t * (hi[1] - lo[1]))
        g = int(lo[2] + t * (hi[2] - lo[2]))
        b = int(lo[3] + t * (hi[3] - lo[3]))
        colormap.append((r, g, b))
    return colormap


THEMES: dict[str, _ThemeSpec] = {
    "classic": {
        "background": (0, 0, 0),
        "spectrum_line": (0, 255, 0),
        "grid": (30, 30, 30),
        "label_color": (180, 180, 180),
        "colormap": _build_colormap(_CLASSIC_ANCHORS),
    },
    "grayscale": {
        "background": (0, 0, 0),
        "spectrum_line": (200, 200, 200),
        "grid": (40, 40, 40),
        "label_color": (200, 200, 200),
        "colormap": _build_colormap(_GRAYSCALE_ANCHORS),
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def amplitude_to_color(value: int, theme: str = "classic") -> tuple[int, int, int]:
    """Map amplitude 0-160 to an RGB color using the selected theme.

    Args:
        value: Amplitude value 0-160.
        theme: Color theme name ("classic" or "grayscale").

    Returns:
        RGB tuple (R, G, B), each 0-255.
    """
    colormap = THEMES[theme]["colormap"]
    idx = max(0, min(160, value))
    return colormap[idx]


def render_spectrum(
    frame: "ScopeFrame",
    width: int = 800,
    height: int = 200,
    theme: str = "classic",
) -> "PILImage":
    """Render a single scope frame as a spectrum (amplitude vs frequency) plot.

    X axis = frequency, Y axis = amplitude.
    Includes frequency labels on X axis and a dB scale on Y axis.
    Draws a filled green line graph on a black background.

    Args:
        frame: ScopeFrame to render.
        width: Image width in pixels.
        height: Image height in pixels.
        theme: Color theme name ("classic" or "grayscale").

    Returns:
        PIL Image object.

    Raises:
        ImportError: If Pillow is not installed.
    """
    _require_pillow()
    from PIL import Image, ImageDraw

    t = THEMES[theme]
    bg: tuple[int, int, int] = t["background"]
    line_color: tuple[int, int, int] = t["spectrum_line"]
    grid_color: tuple[int, int, int] = t["grid"]
    label_color: tuple[int, int, int] = t["label_color"]

    # Layout
    LABEL_HEIGHT = 25
    MARGIN_LEFT = 45
    MARGIN_RIGHT = 8

    img: PILImage = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    plot_x = MARGIN_LEFT
    plot_w = width - MARGIN_LEFT - MARGIN_RIGHT
    plot_y = 5
    plot_h = height - LABEL_HEIGHT - 5

    # Vertical grid lines (5 divisions)
    N_GRID = 5
    for i in range(N_GRID + 1):
        gx = plot_x + i * plot_w // N_GRID
        draw.line([(gx, plot_y), (gx, plot_y + plot_h)], fill=grid_color)

    # Horizontal grid lines (4 divisions)
    for i in range(5):
        gy = plot_y + i * plot_h // 4
        draw.line([(plot_x, gy), (plot_x + plot_w, gy)], fill=grid_color)

    pixels = frame.pixels
    n_pixels = len(pixels)

    if n_pixels > 0 and not frame.out_of_range:
        # Build polyline points
        points: list[tuple[int, int]] = []
        for col in range(plot_w):
            src_idx = col * n_pixels // max(plot_w, 1)
            src_idx = min(src_idx, n_pixels - 1)
            amp = min(pixels[src_idx], 160)
            y = plot_y + plot_h - amp * plot_h // 160
            points.append((plot_x + col, y))

        if len(points) > 1:
            # Fill area under line (semi-transparent dark green)
            fill_poly = (
                [(plot_x, plot_y + plot_h)]
                + points
                + [(plot_x + plot_w - 1, plot_y + plot_h)]
            )
            draw.polygon(fill_poly, fill=(0, 70, 0))
            # Draw spectrum line on top
            draw.line(points, fill=line_color, width=1)

    # Frequency labels on X axis
    start_hz = frame.start_freq_hz
    end_hz = frame.end_freq_hz
    span_hz = end_hz - start_hz
    if span_hz > 0:
        label_y = plot_y + plot_h + 5
        for i in range(N_GRID + 1):
            freq_hz = start_hz + i * span_hz // N_GRID
            freq_mhz = freq_hz / 1_000_000
            label = f"{freq_mhz:.3f}"
            gx = plot_x + i * plot_w // N_GRID
            draw.text((gx - 14, label_y), label, fill=label_color)

    # Y axis labels (amplitude scale 0-160)
    for i in range(5):
        gy = plot_y + i * plot_h // 4
        amp_val = 160 - i * 40
        draw.text((2, gy - 5), str(amp_val), fill=label_color)

    return img


def render_waterfall(
    frames: list["ScopeFrame"],
    width: int = 800,
    height: int = 400,
    theme: str = "classic",
) -> "PILImage":
    """Render multiple scope frames as a waterfall display.

    Each row = one frame.  Top = newest frame, bottom = oldest.
    Pixel color encodes amplitude via the theme colormap.
    Frequency labels are drawn on the X axis.

    Args:
        frames: List of ScopeFrames (oldest first, newest last).
        width: Image width in pixels.
        height: Image height in pixels.
        theme: Color theme name ("classic" or "grayscale").

    Returns:
        PIL Image object.

    Raises:
        ImportError: If Pillow is not installed.
    """
    _require_pillow()
    from PIL import Image, ImageDraw

    t = THEMES[theme]
    bg: tuple[int, int, int] = t["background"]
    colormap: list[tuple[int, int, int]] = t["colormap"]
    label_color: tuple[int, int, int] = t["label_color"]

    LABEL_HEIGHT = 25
    MARGIN_LEFT = 45
    MARGIN_RIGHT = 8

    img: PILImage = Image.new("RGB", (width, height), bg)

    plot_x = MARGIN_LEFT
    plot_w = width - MARGIN_LEFT - MARGIN_RIGHT
    plot_h = height - LABEL_HEIGHT

    if not frames:
        draw = ImageDraw.Draw(img)
        return img

    # Newest at top → reverse the list
    display_frames = list(reversed(frames))
    n_frames = len(display_frames)

    # If more frames than available rows, downsample
    if n_frames > plot_h:
        indices = [i * n_frames // plot_h for i in range(plot_h)]
        display_frames = [display_frames[i] for i in indices]
        n_frames = plot_h

    row_height = max(1, plot_h // n_frames)

    # Direct pixel access for performance (~5-10× faster than draw.point/line)
    img_pixels = img.load()

    for row_idx, frame in enumerate(display_frames):
        if frame.out_of_range or not frame.pixels:
            continue
        y_start = row_idx * row_height
        if y_start >= plot_h:
            break

        pixels = frame.pixels
        n_pix = len(pixels)
        y_end = min(y_start + row_height, plot_h)

        # Pre-compute row colors (one per column)
        row_colors: list[tuple[int, int, int]] = []
        for col in range(plot_w):
            src_idx = min(col * n_pix // max(plot_w, 1), n_pix - 1)
            row_colors.append(colormap[min(pixels[src_idx], 160)])

        # Fill all rows for this frame
        for y in range(y_start, y_end):
            for col in range(plot_w):
                img_pixels[plot_x + col, y] = row_colors[col]

    # Frequency labels from most-recent frame
    draw = ImageDraw.Draw(img)
    ref_frame = frames[-1]
    start_hz = ref_frame.start_freq_hz
    end_hz = ref_frame.end_freq_hz
    span_hz = end_hz - start_hz
    if span_hz > 0:
        label_y = plot_h + 5
        N_LABELS = 5
        for i in range(N_LABELS + 1):
            freq_hz = start_hz + i * span_hz // N_LABELS
            freq_mhz = freq_hz / 1_000_000
            label = f"{freq_mhz:.3f}"
            gx = plot_x + i * plot_w // N_LABELS
            draw.text((gx - 14, label_y), label, fill=label_color)

    return img


def render_scope_image(
    frames: list["ScopeFrame"],
    width: int = 800,
    spectrum_height: int = 200,
    waterfall_height: int = 400,
    theme: str = "classic",
    output: str | Path | None = None,
) -> "PILImage":
    """Render a combined spectrum + waterfall image.

    Top half: spectrum plot of the most-recent frame.
    Bottom half: waterfall of all frames (newest at top).

    Args:
        frames: List of ScopeFrames (oldest first, newest last).
        width: Image width in pixels.
        spectrum_height: Height of the spectrum section in pixels.
        waterfall_height: Height of the waterfall section in pixels.
        theme: Color theme name ("classic" or "grayscale").
        output: If given, save the combined image as PNG to this path.

    Returns:
        PIL Image (spectrum on top, waterfall below).

    Raises:
        ImportError: If Pillow is not installed.
    """
    _require_pillow()
    from PIL import Image

    total_height = spectrum_height + waterfall_height
    bg: tuple[int, int, int] = THEMES[theme]["background"]
    combined: PILImage = Image.new("RGB", (width, total_height), bg)

    if frames:
        spec = render_spectrum(
            frames[-1], width=width, height=spectrum_height, theme=theme
        )
        combined.paste(spec, (0, 0))

        wf = render_waterfall(frames, width=width, height=waterfall_height, theme=theme)
        combined.paste(wf, (0, spectrum_height))

    if output is not None:
        combined.save(str(output), "PNG")

    return combined
