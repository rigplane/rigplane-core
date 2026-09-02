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
import tomllib
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
    """How absence is expressed by `Availability` and the named-reason unions.

    These are two of THREE mechanisms in the state model. The third — whether
    a `RadioViewModel` group exists at all — lives on fields this function
    never looks at; `view_models()` reports it, since that is where the
    `readonly <name>?:` optionality is parsed. Reporting any one of the three
    as universal produces a false contract.

    An earlier version of this function took the FIRST `reason:` union it found
    and printed it as the model-wide vocabulary. It occurs once, on one type,
    and the resulting document told a designer to distinguish four states that
    twelve of fourteen surfaces cannot express — `RxTxSurface` and `VfoSurface`
    are the two that read `txTarget`. Find every occurrence and say which type
    carries it.
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


def _count_top_level_members(body: str) -> int:
    """Independently count interface members, to catch a field pattern that
    silently drops or double-counts some of them.

    Strips `/** ... */` and `//` comments — prose inside them can contain a
    `;` that would otherwise be miscounted — then counts `;` at bracket depth
    zero. That is a different signal from the FIELD pattern: it does not know
    what a member declaration looks like, only where nesting closes. The two
    agreeing across every `*ViewModel` interface in this file (verified by
    hand before this check was added) is real evidence the pattern parsed
    every member; disagreeing means it didn't, which is exactly the shape of
    bug that let `RadioViewModel` read as 10 fields when the source declared
    24 — the FIELD pattern silently skipped every `readonly <name>?:` line.
    """
    stripped = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    depth = 0
    count = 0
    for ch in stripped:
        if ch in "{[(<":
            depth += 1
        elif ch in "}])>":
            depth = max(0, depth - 1)
        elif ch == ";" and depth == 0:
            count += 1
    return count


def view_models(text: str) -> dict[str, list[dict[str, object]]]:
    """Every `*ViewModel` interface, its declared fields, and whether each is
    optional.

    Optionality decides whether the field's whole block can be absent at all
    — Phase 1's first question about any block — so it has to survive into
    the output, not just the field's presence. `RadioViewModel` is the only
    interface in this file that declares `readonly` fields (checked across
    every `export interface` block, not only the ones named `*ViewModel`);
    a pattern that does not accept the modifier matches none of them and
    drops the `?` along with it.
    """
    out: dict[str, list[dict[str, object]]] = {}
    for block in re.finditer(
        r"export interface (\w+ViewModel)\s*\{(.*?)\n\}", text, re.S
    ):
        name, body = block.group(1), block.group(2)
        fields = [
            {
                "name": f.group(1),
                "optional": f.group(2) == "?",
                "type": f.group(3).strip(),
            }
            for f in re.finditer(
                r"^\s{2}(?:readonly\s+)?(\w+)(\??):\s*([^;]+);", body, re.M
            )
        ]
        expected = _count_top_level_members(body)
        if len(fields) != expected:
            die(
                f"{name}: field pattern matched {len(fields)} member(s) but "
                f"an independent bracket-depth count found {expected} "
                f"top-level `;` — the parse cannot be trusted"
            )
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
    # Parse it, do not scrape it. A regex over the text picks up features that
    # are commented out — `ftx1.toml` disables `monitor` with the reason
    # "rejected by FTX-1 (not supported via CAT)" and a text scan reports it as
    # declared. A profile is TOML; read it as TOML.
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        die(f"{path} is not valid TOML: {exc}")
    feats = doc.get("features")
    if feats is None:
        feats = doc.get("capabilities", {}).get("features")
    if not isinstance(feats, list):
        die(f"no `features` list in {path} — the profile shape moved")
    return [str(f) for f in feats], str(path)


def surface_components() -> dict[str, dict]:
    """Per surface: the shared components it mounts, and how far a design
    language reaches each.

    This is the lookup a recogniser needs. Seeing an S-meter in a reference is
    only useful if it answers "how does an S-meter connect HERE" — which field
    feeds it, what already draws it, and whether its appearance is reachable
    from a stylesheet at all. Written by hand it would rot; derived, it cannot.

    Tiers, decided by which custom-property vocabulary the component reads:
      language  — reads `--dl-*`; a stylesheet can restyle it.
      theme     — reads `--v2-*` only; a change lands in EVERY skin.
      code      — reads neither; the form is in the source.
    """
    out: dict[str, dict] = {}
    for f in sorted(Path("frontend/src/semantic").glob("*Surface.svelte")):
        text = f.read_text(encoding="utf-8")
        mounted = sorted({
            m.group(1) for m in re.finditer(
                r"import ([A-Z]\w+) from '([^']+\.svelte)'", text)
        })
        comps = []
        for name in mounted:
            m = re.search(rf"import {name} from '([^']+)'", text)
            rel = (f.parent / m.group(1)).resolve() if m else None
            info = {"name": name, "tier": "unknown", "dl": 0, "v2": 0}
            if rel and rel.is_file():
                body = rel.read_text(encoding="utf-8")
                info["dl"] = len(re.findall(r"var\(--dl-", body))
                info["v2"] = len(re.findall(r"var\(--v2-", body))
                info["tier"] = ("language" if info["dl"]
                                else "theme" if info["v2"] else "code")
            comps.append(info)
        out[f.stem] = {"components": comps}
    if not out:
        die("no *Surface.svelte found — run from the repository root")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="ftx1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    vm_text = read(VIEW_MODEL)
    data = {
        "surfaces": surfaces(),
        "absence": absence_model(vm_text),
        "viewModels": view_models(vm_text),
        "fieldShapes": field_shapes(vm_text),
        "radio": args.radio,
        "radioFeatures": radio_declares(args.radio)[0],
        "surfaceComponents": surface_components(),
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
    rvm = data["viewModels"].get("RadioViewModel", [])
    optional_groups = [f["name"] for f in rvm if f["optional"]]
    print(
        "**Per-group optionality — whether a block exists at all.** "
        f"`RadioViewModel` declares {len(optional_groups)} of its "
        f"{len(rvm)} fields optional; an absent group is not the same "
        "fact as every field in it being unsupported. This is the first "
        "question for any block, before any question about its "
        "contents:\n"
    )
    print(
        (
            ", ".join(f"`{n}?`" for n in optional_groups)
            if optional_groups
            else "_none parsed_"
        )
        + "\n"
    )
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
        for f in fields:
            marker = "?" if f["optional"] else ""
            print(f"- `{f['name']}{marker}`: `{f['type']}`")
        print()

    print("## What already draws each surface\n")
    print("The lookup for a recognised element: which shared component draws it,")
    print("and whether a design language can restyle it. **language** = reads")
    print("`--dl-*`; **theme** = reads `--v2-*` only, so a change lands in every")
    print("skin; **code** = reads neither, the form is in the source. A surface")
    print("with no component draws its own markup.\n")
    for name, info in sorted(data["surfaceComponents"].items()):
        comps = info["components"]
        if not comps:
            print(f"- `{name}` — no shared component; draws its own markup")
            continue
        parts = ", ".join(f"`{c['name']}` (**{c['tier']}**, "
                          f"--dl- {c['dl']} / --v2- {c['v2']})" for c in comps)
        print(f"- `{name}` — {parts}")
    print()

    feats = data["radioFeatures"]
    print(f"## What `{args.radio}` declares ({len(feats)} features)\n")
    print("A field in the view model does not mean this radio reports it.\n")
    print(", ".join(f"`{f}`" for f in feats) if feats else "_none parsed_")
    print()


if __name__ == "__main__":
    main()
