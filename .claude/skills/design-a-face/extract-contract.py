#!/usr/bin/env python3
"""Emit the data contract a face design must respect, derived from the code.

Never hand-maintain the output of this script. A contract written by hand at a
layer boundary rots silently, and a design that consumed it goes on being
confidently wrong.

Usage:
    ./extract-contract.py [--radio ftx1] [--json]

Run from the repository root. Exits non-zero if a source file it needs is
missing or yields nothing, because an empty contract is indistinguishable from
a clean one and the second is never true.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NoReturn

VIEW_MODEL = Path("frontend/src/semantic/radio-view-model.ts")
LAYOUT_CONTRACT = Path("frontend/src/presentation/layouts/contract.ts")


def die(msg: str) -> NoReturn:
    print(f"extract-contract: {msg}", file=sys.stderr)
    raise SystemExit(1)


def read(path: Path) -> str:
    if not path.is_file():
        die(f"missing {path} — run from the repository root")
    return path.read_text(encoding="utf-8")


def surfaces() -> list[str]:
    """The closed set of surfaces a layout may mount."""
    text = read(LAYOUT_CONTRACT)
    m = re.search(r"SEMANTIC_SURFACE_NAMES\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        die(f"SEMANTIC_SURFACE_NAMES not found in {LAYOUT_CONTRACT}")
    names = re.findall(r"'([A-Za-z]+)'", m.group(1))
    if not names:
        die("SEMANTIC_SURFACE_NAMES parsed to zero entries")
    return names


def absence_model(text: str) -> dict:
    """How absence is expressed. There are TWO mechanisms and they are not the
    same; reporting either one as universal produces a false contract.

    An earlier version of this function took the FIRST `reason:` union it found
    and printed it as the model-wide vocabulary. It occurs once, on one type,
    and the resulting document told a designer to distinguish four states that
    thirteen of fourteen surfaces cannot express. Find every occurrence and say
    which type carries it.
    """
    general = re.search(r"export interface Availability\s*\{(.*?)\n\}", text, re.S)
    if not general:
        die("no `Availability` interface found — the state model moved")
    flags = re.findall(r"^\s{2}(\w+):\s*([^;]+);", general.group(1), re.M)
    if not flags:
        die("Availability parsed to zero members")

    # Every named-reason union, with the type that declares it.
    named: dict[str, list[str]] = {}
    for m in re.finditer(
        r"export type (\w+)\s*=(.*?);\s*$", text, re.S | re.M
    ):
        body = m.group(2)
        r = re.search(r"status:\s*'unknown';\s*reason:\s*([^}]+)", body)
        if r:
            named[m.group(1)] = re.findall(r"'([a-z-]+)'", r.group(1))

    return {"availability": [{"name": n, "type": t.strip()} for n, t in flags],
            "namedReasons": named}


def view_models(text: str) -> dict[str, list[tuple[str, str]]]:
    """Every `*ViewModel` interface and its declared fields."""
    out: dict[str, list[tuple[str, str]]] = {}
    for block in re.finditer(
        r"export interface (\w+ViewModel)\s*\{(.*?)\n\}", text, re.S
    ):
        name, body = block.group(1), block.group(2)
        fields = [
            (f.group(1), f.group(2).strip())
            for f in re.finditer(r"^\s{2}(\w+)\??:\s*([^;]+);", body, re.M)
        ]
        out[name] = fields
    if not out:
        die("no *ViewModel interfaces parsed — the file shape moved")
    return out


def field_shapes(text: str) -> dict[str, str]:
    """The reading/availability pair shapes, by declared name."""
    out = {}
    # A type body may contain `;` inside braces, so stop at a `;` that ends a
    # line rather than at the first one. A truncated shape reads as a real one.
    for m in re.finditer(
        r"export type (\w*(?:Reading|Field)\w*)\s*(?:<[^>]*>)?\s*=\s*(.*?);\s*$",
        text, re.S | re.M,
    ):
        out[m.group(1)] = " ".join(m.group(2).split())
    for m in re.finditer(r"export interface (\w*Field)\s*\{(.*?)\n\}", text, re.S):
        members = re.findall(r"^\s{2}(\w+):", m.group(2), re.M)
        out[m.group(1)] = "{ " + ", ".join(members) + " }"
    return out


def radio_declares(radio: str) -> tuple[list[str], str]:
    """What this particular radio's profile declares. A field in the view model
    does not mean this radio reports it."""
    path = Path("rigs") / f"{radio}.toml"
    if not path.is_file():
        available = sorted(p.stem for p in Path("rigs").glob("*.toml"))
        die(f"no profile {path}; available: {', '.join(available)}")
    text = path.read_text(encoding="utf-8")
    feats: list[str] = []
    m = re.search(r"^features\s*=\s*\[(.*?)\]", text, re.S | re.M)
    if m:
        feats = re.findall(r'"([^"]+)"', m.group(1))
    return feats, str(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="ftx1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    vm_text = read(VIEW_MODEL)
    data = {
        "surfaces": surfaces(),
        "absence": absence_model(vm_text),
        "viewModels": {k: dict(v) for k, v in view_models(vm_text).items()},
        "fieldShapes": field_shapes(vm_text),
        "radio": args.radio,
        "radioFeatures": radio_declares(args.radio)[0],
        "sources": [str(VIEW_MODEL), str(LAYOUT_CONTRACT), radio_declares(args.radio)[1]],
    }

    if args.json:
        print(json.dumps(data, indent=2))
        return

    print("# Data contract for a face design\n")
    print("Generated by `.claude/skills/design-a-face/extract-contract.py`.")
    print("Do not edit; re-run it.\n")
    print("Sources read: " + ", ".join(f"`{s}`" for s in data["sources"]) + "\n")

    print(f"## Surfaces a layout may mount ({len(data['surfaces'])})\n")
    print(", ".join(f"`{s}`" for s in data["surfaces"]) + "\n")

    print("## How absence is expressed\n")
    ab = data["absence"]
    print("**The general mechanism, carried by every field.** A reading is "
          "`known` with a value or `unknown` with none, and `Availability` "
          "carries these flags alongside it:\n")
    for f in ab["availability"]:
        print(f"- `{f['name']}`: `{f['type']}`")
    print("\nSo the states a design must draw are: known; unknown but the "
          "radio has the capability; and unknown because it does not. Not "
          "five — three, plus whatever the flags distinguish.\n")
    if ab["namedReasons"]:
        print("**Named-reason unions, which are NOT general.** Only these "
              "types carry an explicit reason. Do not generalise them:\n")
        for t, rs in sorted(ab["namedReasons"].items()):
            print(f"- `{t}`: " + ", ".join(f"`{r}`" for r in rs))
    else:
        print("_No type carries a named-reason union._")
    print()

    print("## Field shapes\n")
    for name, shape in sorted(data["fieldShapes"].items()):
        print(f"- `{name}` = {shape}")
    print()

    print("## Per surface\n")
    for name, fields in sorted(data["viewModels"].items()):
        print(f"### `{name}` ({len(fields)} fields)\n")
        for fname, ftype in fields.items():
            print(f"- `{fname}`: `{ftype}`")
        print()

    feats = data["radioFeatures"]
    print(f"## What `{args.radio}` declares ({len(feats)} features)\n")
    print("A field in the view model does not mean this radio reports it.\n")
    print(", ".join(f"`{f}`" for f in feats) if feats else "_none parsed_")
    print()


if __name__ == "__main__":
    main()
