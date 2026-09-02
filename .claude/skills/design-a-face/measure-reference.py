#!/usr/bin/env python3
"""Measure a design reference by ink density, and report proportions.

Estimating a proportion by eye and writing it down like a measurement is the
failure this exists to prevent. Project the image onto each axis, read the
boundaries off the profile, and report shares of the measured box — never
pixels, because the stage scales as one block and a pixel figure is true at
one size only.

    ./measure-reference.py bands   <image> [--crop L,T,R,B] [--min-run 4]
    ./measure-reference.py columns <image> [--crop L,T,R,B] [--min-run 4]
    ./measure-reference.py selftest

`bands` splits along the vertical axis (horizontal bands), `columns` along the
horizontal (left/right divisions). For a fine element — glyph cells, segment
pitch — pass `--crop` and measure it in its own resolution: precision comes
from the crop, not from a cleverer algorithm.

`selftest` renders an image with known boundaries and checks they are
recovered. Run it before trusting any result on a real image; a profile that
finds nothing and a profile that is broken look identical in a summary.

Requires Pillow and numpy.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover - environment, not logic
    print(f"measure-reference: needs Pillow and numpy ({exc})", file=sys.stderr)
    raise SystemExit(1)


VARIANCE = [False]

# `--by colour` profiles distance from grey (chroma). Most of any panel is
# its own ground, so the MEDIAN chroma over a measured box is, in practice,
# the ground's own chroma. If the ground itself is tinted (this reference's
# amber LCD ground, not merely an accent on a neutral ground), that median is
# already high, and "distance from grey" cannot separate "ground" from
# "signal" — the run(s) it reports profile the tint, not a meaning.
#
# Chosen by measuring both sides on real fixtures, not by guessing a round
# number first: median chroma on `/var/tmp/ftx1-reference.png` (a genuinely
# single-hue amber panel, `ink` vs `colour` corrcoef -0.63) is 0.463; on two
# fixtures where a real accent sits on a near-neutral ground — this file's
# own `selftest` fixture (white ground, one red patch) and a constructed
# dark-grey-ground-plus-red-accent image — it is 0.0 and 0.02 respectively.
# 0.15 sits roughly a third of the way from the reference's value toward
# zero, and 7-20x above either genuine-accent fixture: a real accent against
# a neutral ground will not trip it, and any ground that is even moderately
# tinted will.
DEGENERATE_CHROMA = 0.15


def die(msg: str) -> NoReturn:
    print(f"measure-reference: {msg}", file=sys.stderr)
    raise SystemExit(1)


def colour_degeneracy(colour: np.ndarray) -> float | None:
    """The median chroma of a `--by colour` field, if it exceeds
    DEGENERATE_CHROMA, else None.

    A separate, callable check (rather than inlined where it is used) so
    `selftest` can assert on it directly instead of scraping printed text.
    """
    median = float(np.median(colour))
    return median if median > DEGENERATE_CHROMA else None


def load(path: str, crop: str | None, by: str = "ink") -> np.ndarray:
    """A 2-D field to profile.

    `ink` — darkness, 0 = white, 1 = black. Finds elements against a ground.
    `colour` — distance from grey (max channel minus min, normalised). Finds
    what is COLOURED regardless of how dark it is. On a monochrome panel where
    colour is reserved for one meaning — a transmit group, an alarm — this
    locates that meaning without being told where to look, and separates a
    dimmed indicator (same hue, less ink) from a differently-coloured one.
    Warns (see DEGENERATE_CHROMA) when the measured box's ground itself is
    tinted enough that this separation cannot hold.
    """
    try:
        img = Image.open(path).convert("RGB")
    except OSError as exc:
        die(f"cannot read {path}: {exc}")
    if crop:
        try:
            box = tuple(int(v) for v in crop.split(","))
        except ValueError:
            die("--crop wants four integers: L,T,R,B")
        if len(box) != 4:
            die("--crop wants four integers: L,T,R,B")
        img = img.crop(box)
    rgb = np.asarray(img, dtype=np.float64) / 255.0
    if rgb.size == 0:
        die("empty image after crop")
    if by == "colour":
        colour = rgb.max(axis=2) - rgb.min(axis=2)
        degenerate = colour_degeneracy(colour)
        if degenerate is not None:
            print(
                f"measure-reference: WARNING — median chroma {degenerate:.2f} "
                f"exceeds {DEGENERATE_CHROMA}. The GROUND itself reads as "
                "coloured here, not only an accent: --by colour cannot tell "
                "'ground' from 'signal' on this image, and any run reported "
                "below profiles the ground, not a colour-coded meaning."
            )
        return colour
    return 1.0 - rgb.mean(axis=2)


def profile(a: np.ndarray, axis: str, smooth: int = 0) -> np.ndarray:
    """Mean ink per row (axis='rows') or per column (axis='columns').

    `smooth` is a moving-average window. Design references often carry a
    regular texture — scanlines on an LCD imitation, a dot grid — whose period
    is small and whose amplitude rivals a real element's. Untreated, the
    detector returns one run per texture stripe and buries the bands. A window
    a few times the texture's period flattens it and leaves the bands, because
    a band is wide and a stripe is not.

    Choose it from the texture, not from taste: measure the stripe spacing in
    the raw profile first, then smooth past it.
    """
    ax = 1 if axis == "rows" else 0
    p = a.std(axis=ax) if VARIANCE[0] else a.mean(axis=ax)
    if smooth > 1:
        k = np.ones(smooth) / smooth
        p = np.convolve(p, k, mode="same")
    return p


def runs(p: np.ndarray, min_run: int) -> list[tuple[int, int]]:
    """Contiguous stretches whose ink exceeds a threshold set just above the
    profile's floor.

    The threshold sits just above the profile's own FLOOR, not at its midpoint.
    A midpoint fails on any image holding both a heavy element and a faint one:
    a bright readout raises the ceiling until a row of dimmed indicators falls
    below the middle and vanishes. Panels of this kind always contain both, so
    the midpoint version silently loses exactly the bands hardest to see.

    Bands are separated by gutters, so the signal that marks a boundary is
    departure from emptiness rather than absolute darkness. An image with no
    contrast yields no runs, which is reported rather than hidden.
    """
    lo, hi = float(p.min()), float(p.max())
    if hi - lo < 1e-9:
        return []
    inked = p > lo + max(0.02, (hi - lo) * 0.08)
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(inked):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_run:
                out.append((start, i))
            start = None
    if start is not None and len(inked) - start >= min_run:
        out.append((start, len(inked)))
    return out


def report(a: np.ndarray, axis: str, min_run: int, label: str,
           smooth: int = 0) -> None:
    p = profile(a, axis, smooth)
    total = len(p)
    found = runs(p, min_run)
    h, w = a.shape
    print(f"# {label}")
    print(f"measured box: {w} x {h} px; profile over {total} {axis}")
    if not found:
        print("no runs found — the image has no usable contrast on this axis,")
        print("or --min-run is larger than every band. Not a clean result.")
        return
    print(f"{len(found)} run(s), as share of the {axis[:-1]} extent:\n")
    print(f"  {'#':>3}  {'start':>7}  {'end':>7}  {'extent':>7}   px")
    for i, (s, e) in enumerate(found, 1):
        print(f"  {i:>3}  {s/total:>6.1%}  {e/total:>6.1%}  {(e-s)/total:>6.1%}"
              f"   {s}-{e}")
    gaps = [(found[i][1], found[i + 1][0]) for i in range(len(found) - 1)]
    if gaps:
        print("\n  gaps between runs:")
        for i, (s, e) in enumerate(gaps, 1):
            print(f"  {i:>3}  {s/total:>6.1%}  {e/total:>6.1%}  {(e-s)/total:>6.1%}"
                  f"   {s}-{e}")
    print("\nReport these as shares, not pixels, and state the image and its")
    print("dimensions. A share survives the stage's uniform scaling; a pixel")
    print("figure is true at one size only.")


def selftest() -> None:
    """Recover known boundaries, and prove the threshold is load-bearing.

    An earlier version planted near-black bands on white. That case passes at
    almost any threshold, so it did NOT verify the instrument: deliberately
    breaking the threshold left it green. A self-test on an ideal case tests
    nothing, because an ideal case survives being measured badly.

    So one band here is LOW CONTRAST — close enough to the ground that only a
    correct threshold separates it. Break the threshold and this fails, which
    is the whole point of running it.

    Every check below calls `load()` — the same function `bands`/`columns`
    use — against a real file written to disk, rather than recomputing ink
    and colour inline. An earlier version did the latter: a reviewer mutated
    `load()` so `by="colour"` silently returned the ink field, and this
    self-test still exited 0, because it never called the function it
    claimed to check. Going through `load()` closes that gap and exercises
    image loading and `--crop` along the way, which were exercised nowhere
    else in this file.
    """
    H, W = 400, 200
    img = np.full((H, W, 3), 255, dtype=np.uint8)
    strong = [(40, 80), (300, 320)]
    faint = (160, 260)
    for s_, e_ in strong:
        img[s_:e_, :, :] = 20
    img[faint[0]:faint[1], :, :] = 150          # ~41% ink: needs a real midpoint
    img[210:240, 60:140] = (220, 40, 40)        # the only colour on the page

    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "selftest.png")
        Image.fromarray(img).save(path)

        want = [strong[0], faint, strong[1]]
        got = runs(profile(load(path, None, "ink"), "rows"), 4)
        print("selftest — planted bands vs recovered (one is low-contrast):")
        ok = len(got) == len(want)
        for i, w in enumerate(want):
            h = got[i] if i < len(got) else None
            m = h == w
            ok = ok and m
            tag = " (low contrast)" if w == faint else ""
            print(f"  planted {w}{tag}   recovered {h}   {'ok' if m else 'MISMATCH'}")

        # --smooth must still find the same three bands, only wider (a
        # moving-average window blurs a boundary, it does not move its
        # centre). A mutation that zeroes the smoothing branch collapses
        # every run to none here; the smooth=0 checks above never exercise
        # `smooth > 1` at all, so they cannot catch that.
        smoothed = runs(profile(load(path, None, "ink"), "rows", smooth=5), 4)
        smooth_ok = len(smoothed) == len(want) and all(
            gs <= ws and ge >= we for (gs, ge), (ws, we) in zip(smoothed, want)
        )
        print(f"  smooth=5 recovers {smoothed}, containing planted {want}"
              f"   {'ok' if smooth_ok else 'MISMATCH'}")

        cruns = runs(profile(load(path, None, "colour"), "rows"), 4)
        c_ok = cruns == [(210, 240)]
        print(f"  colour profile finds {cruns} — expected [(210, 240)]"
              f"   {'ok' if c_ok else 'MISMATCH'}")

        crop_runs = runs(profile(load(path, "0,200,200,250", "colour"), "rows"), 4)
        crop_ok = crop_runs == [(10, 40)]
        print(
            f"  --crop 0,200,200,250 colour profile finds {crop_runs} — "
            f"expected [(10, 40)]   {'ok' if crop_ok else 'MISMATCH'}"
        )

        control = runs(profile(np.zeros((H, W)), "rows"), 4)
        print(f"  flat image yields {len(control)} run(s) — expected 0"
              f"   {'ok' if not control else 'MISMATCH'}")

        # DEGENERATE_CHROMA must discriminate both ways: silent on a genuine
        # accent (this fixture — white ground, one red patch), firing on a
        # ground that is itself tinted with no accent at all.
        silent_flag = colour_degeneracy(load(path, None, "colour"))
        silent_ok = silent_flag is None
        print(f"  degenerate-colour check, genuine accent: flagged={silent_flag}"
              f" — expected None   {'ok' if silent_ok else 'MISMATCH'}")

        tinted = np.full((40, 40, 3), (196, 156, 64), dtype=np.uint8)
        tinted_path = str(Path(tmp) / "tinted.png")
        Image.fromarray(tinted).save(tinted_path)
        fires_flag = colour_degeneracy(load(tinted_path, None, "colour"))
        fires_ok = fires_flag is not None
        print(f"  degenerate-colour check, tinted ground: flagged={fires_flag}"
              f" — expected a value   {'ok' if fires_ok else 'MISMATCH'}")

        # --signal variation: a scanline ground that inks every row about
        # equally (alternating level, period 2) so LEVEL can never separate
        # a band from a gutter — every "high" stripe is only 1 row deep,
        # below --min-run, so LEVEL finds nothing at all. Rows 80-120 carry
        # a real band: a mean-preserving checkerboard, so its LEVEL is
        # unchanged but its per-row spread is not. A mutation that makes the
        # VARIANCE dispatch unconditionally compute the mean (ignoring
        # VARIANCE[0]) makes `variation` collapse to the same "no runs" as
        # `level` here, which this catches.
        vH, vW = 200, 160
        v_ink = np.zeros((vH, vW), dtype=np.float64)
        for y in range(vH):
            v_ink[y, :] = 0.55 if (y // 2) % 2 == 0 else 0.45
        v_band = (80, 120)
        v_cols = np.arange(vW)
        for y in range(*v_band):
            base = v_ink[y, 0]
            v_ink[y, :] = np.clip(np.where((v_cols % 4) < 2, base + 0.12, base - 0.12), 0, 1)
        v_gray = np.clip((1.0 - v_ink) * 255.0, 0, 255).astype(np.uint8)
        v_path = str(Path(tmp) / "variation.png")
        Image.fromarray(np.stack([v_gray] * 3, axis=-1)).save(v_path)

        v_loaded = load(v_path, None, "ink")
        VARIANCE[0] = False
        level_runs = runs(profile(v_loaded, "rows"), 4)
        level_ok = level_runs == []
        VARIANCE[0] = True
        variation_runs = runs(profile(v_loaded, "rows"), 4)
        variation_ok = variation_runs == [v_band]
        VARIANCE[0] = False
        print(f"  --signal level on a scanline ground finds {level_runs} — "
              f"expected []   {'ok' if level_ok else 'MISMATCH'}")
        print(f"  --signal variation on the same ground finds {variation_runs} — "
              f"expected [{v_band}]   {'ok' if variation_ok else 'MISMATCH'}")

        if (not ok or not c_ok or not crop_ok or control or not silent_ok
                or not fires_ok or not smooth_ok or not level_ok or not variation_ok):
            die("selftest failed — do not trust measurements from this build")
    print("\nselftest passed. The low-contrast band is the load-bearing case:")
    print("it is what makes a broken threshold visible, and an all-ideal")
    print("self-test would have reported the same green either way.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["bands", "columns", "selftest"])
    ap.add_argument("image", nargs="?")
    ap.add_argument("--crop", help="L,T,R,B in pixels, for a fine element")
    ap.add_argument("--min-run", type=int, default=4,
                    help="ignore runs shorter than this many px (default 4)")
    ap.add_argument("--smooth", type=int, default=0,
                    help="moving-average window, to flatten a regular texture")
    ap.add_argument("--by", choices=["ink", "colour"], default="ink",
                    help="profile darkness (default) or distance from grey")
    ap.add_argument("--signal", choices=["level", "variation"], default="level",
                    help="mean along the axis (default), or spread. Use "
                         "`variation` when the reference carries a regular "
                         "texture: a scanline ground has ink everywhere, so "
                         "level cannot separate a band from a gutter, while "
                         "spread can — a band varies across its width, an "
                         "empty row does not.")
    args = ap.parse_args()

    if args.mode == "selftest":
        selftest()
        return
    if not args.image:
        die("an image path is required for bands/columns")

    VARIANCE[0] = args.signal == "variation"
    a = load(args.image, args.crop, args.by)
    what = "ink" if args.by == "ink" else "colour"
    if args.mode == "bands":
        report(a, "rows", args.min_run, f"horizontal {what} bands of {args.image}", args.smooth)
    else:
        report(a, "columns", args.min_run, f"vertical {what} divisions of {args.image}", args.smooth)


if __name__ == "__main__":
    main()
