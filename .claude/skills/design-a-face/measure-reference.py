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
# WHOLE-IMAGE fixtures where a real accent sits on a near-neutral ground —
# this file's own `selftest` fixture (white ground, one red patch) and a
# constructed dark-grey-ground-plus-red-accent image — it is 0.0 and 0.02
# respectively. 0.15 sits roughly a third of the way from the reference's
# value toward zero, well above both of those two whole-image medians.
#
# This is a statistic over whatever box is measured, not a judgement about
# "the ground": crop tightly enough around an accent and the accent IS most
# of the box, and the same threshold fires on it. `selftest` only exercises
# the whole-image case above; it does not claim the threshold behaves any
# particular way on a crop.
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
    Warns (see DEGENERATE_CHROMA) when the measured box's own median chroma
    is high enough that this separation cannot hold — whether that is
    because the ground itself is tinted, or because the box is cropped
    tight enough that the accent IS most of the box.
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
                f"measure-reference: WARNING — median chroma over the "
                f"measured box is {degenerate:.2f}, above "
                f"{DEGENERATE_CHROMA}: most of the box already reads as "
                "coloured, not only a small accent within it. --by colour "
                "separates an accent from what surrounds it by contrast "
                "between the two; a box that is already mostly coloured has "
                "none left to separate on. Check whether that is because "
                "the ground itself is tinted or because this crop is "
                "mostly the accent, before reporting the run below as a "
                "colour-coded meaning."
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
           smooth: int = 0) -> list[tuple[int, int]]:
    """As well as printing, returns the runs found — so a caller (selftest,
    routing a check through `main()`) can assert on the real result instead
    of re-deriving it or scraping printed text."""
    p = profile(a, axis, smooth)
    total = len(p)
    found = runs(p, min_run)
    h, w = a.shape
    print(f"# {label}")
    print(f"measured box: {w} x {h} px; profile over {total} {axis}")
    if not found:
        print("no runs found — the image has no usable contrast on this axis,")
        print("or --min-run is larger than every band. Not a clean result.")
        return found
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
    return found


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

        # --smooth must still find the same three bands, STRICTLY wider (a
        # moving-average window blurs a boundary outward, it does not move
        # its centre): `gs <= ws` is also satisfied by no widening at all,
        # so a mutation that no-ops the smoothing branch would pass a
        # non-strict containment check silently — the smooth=0 checks above
        # never exercise `smooth > 1`, so they cannot catch that either.
        # Routed through `main()`, not `profile()` directly: calling
        # `profile()` here exercises the function but never the `--smooth`
        # flag that is supposed to reach it, so a CLI wiring bug (the flag
        # parsed but dropped before it reaches `report()`) would pass too.
        smoothed = main(["bands", path, "--smooth", "5"])
        smooth_ok = smoothed is not None and len(smoothed) == len(want) and all(
            gs < ws and ge > we for (gs, ge), (ws, we) in zip(smoothed, want)
        )
        print(f"  smooth=5 recovers {smoothed}, strictly containing planted "
              f"{want}   {'ok' if smooth_ok else 'MISMATCH'}")

        # A hardcoded window (e.g. always size 3, ignoring the requested
        # value) still widens the bands relative to no smoothing, so the
        # check above alone would not catch it — it would just under-widen
        # and still pass. A larger requested window has to widen the same
        # bands MORE, or the size the flag carries is not the size applied.
        smoothed_more = main(["bands", path, "--smooth", "9"])
        window_scales_ok = smoothed_more is not None and smoothed_more != smoothed
        print(f"  smooth=9 recovers {smoothed_more}, different from smooth=5's "
              f"{smoothed}   {'ok' if window_scales_ok else 'MISMATCH'}")

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
        # `level` here, which this catches. Both routed through `main()`,
        # not `profile()` directly with `VARIANCE[0]` set by hand: this
        # module's own `--signal` flag is what a caller actually uses, and
        # a mutation that inverts its dispatch in `main()` (e.g. `variation`
        # mapping to level's behaviour) reaches neither `level_ok` nor
        # `variation_ok` unless the flag itself is exercised.
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

        level_runs = main(["bands", v_path])
        level_ok = level_runs == []
        variation_runs = main(["bands", v_path, "--signal", "variation"])
        variation_ok = variation_runs == [v_band]
        VARIANCE[0] = False
        print(f"  --signal level on a scanline ground finds {level_runs} — "
              f"expected []   {'ok' if level_ok else 'MISMATCH'}")
        print(f"  --signal variation on the same ground finds {variation_runs} — "
              f"expected [{v_band}]   {'ok' if variation_ok else 'MISMATCH'}")

        if (not ok or not c_ok or not crop_ok or control or not silent_ok
                or not fires_ok or not smooth_ok or not window_scales_ok
                or not level_ok or not variation_ok):
            die("selftest failed — do not trust measurements from this build")
    print("\nselftest passed. The low-contrast band is the load-bearing case:")
    print("it is what makes a broken threshold visible, and an all-ideal")
    print("self-test would have reported the same green either way.")


def main(argv: list[str] | None = None) -> list[tuple[int, int]] | None:
    """`argv` defaults to `sys.argv[1:]` (real CLI use). selftest passes an
    explicit list instead, so `--smooth` and `--signal` are exercised through
    the same argument parsing and dispatch a real invocation goes through —
    not just the functions they eventually call — and a wiring bug between
    the flag and `report()`/`VARIANCE[0]` fails the self-test rather than
    passing silently."""
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
    args = ap.parse_args(argv)

    if args.mode == "selftest":
        selftest()
        return None
    if not args.image:
        die("an image path is required for bands/columns")

    VARIANCE[0] = args.signal == "variation"
    a = load(args.image, args.crop, args.by)
    what = "ink" if args.by == "ink" else "colour"
    if args.mode == "bands":
        return report(a, "rows", args.min_run, f"horizontal {what} bands of {args.image}", args.smooth)
    else:
        return report(a, "columns", args.min_run, f"vertical {what} divisions of {args.image}", args.smooth)


if __name__ == "__main__":
    main()
