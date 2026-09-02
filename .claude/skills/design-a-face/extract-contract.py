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
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import NoReturn

VIEW_MODEL = Path("frontend/src/semantic/radio-view-model.ts")
LAYOUT_CONTRACT = Path("frontend/src/presentation/layouts/contract.ts")
ADAPTER = Path("frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts")
PANEL_COMMANDS = Path("frontend/src/lib/runtime/commands/panel-commands.ts")
PANEL_ADAPTERS = Path("frontend/src/lib/runtime/adapters/panel-adapters.ts")
RADIO_INTENTS = Path("frontend/src/lib/runtime/commands/radio-intents.ts")
SURFACES_WIRING = Path("frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte")
SEMANTIC_DIR = Path("frontend/src/semantic")
LANGUAGES_DIR = Path("frontend/src/presentation/languages")


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


def _function_body(text: str, name: str) -> str | None:
    """The `{ ... }` body of `function <name>(...): ReturnType { ... }`,
    found by brace-depth counting from the parameter list's own closing
    paren — so a multi-line signature or a generic return type does not
    confuse it. None if no such function exists; callers must report that
    as its own outcome (Phase 1: never silently skip a group)."""
    m = re.search(rf"\bfunction {re.escape(name)}\(", text)
    if m is None:
        return None
    i = text.index("(", m.end() - 1)
    depth = 1
    j = i + 1
    while depth > 0:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
        j += 1
    brace = text.index("{", j)
    depth = 1
    k = brace + 1
    while depth > 0:
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
        k += 1
    return text[brace + 1 : k - 1]


# Shapes of a `derive<Group>` function's leading guard clause(s), tried in
# this order (most specific first; `_GATE_NULLCAPS` before the generic live
# patterns, since a bare `!caps` would otherwise also match those). Every
# pattern anchors at the body's own start (`\A`, after stripping the
# function's own leading newline) — a guard found later in the body is not
# "the first question for any block", it is exactly the scattered case.
_GATE_HASCAP = re.compile(r"\A\s*if \(!hasCap\(caps, '([a-z_]+)'\)\) return undefined;")
_GATE_OR_HASCAP = re.compile(
    r"\A\s*const (\w+) = hasCap\(caps, '([a-z_]+)'\);\s*\n"
    r"\s*const (\w+) = hasCap\(caps, '([a-z_]+)'\);\s*\n"
    r"\s*if \(!\1 && !\3\) return undefined;"
)
_GATE_NUMERIC = re.compile(
    r"\A\s*const (\w+) = caps\?\.(\w+) \?\? (\d+);\s*\n\s*if \(\1 <= (\d+)\) return undefined;"
)
_GATE_HALF = re.compile(r"\A\s*if \(!(\w+) \|\| !(\w+)\(caps\)\) return undefined;")
_GATE_EVER_REPORTED = re.compile(
    r"\A\s*const (\w+) = \[[^\]]*\];\s*\n"
    r"\s*if \(!\1\.some\(\(v\) => v !== undefined\)\) return undefined;"
)
_GATE_NULLCAPS = re.compile(r"\A\s*if \(!caps\) return undefined;")
_GATE_LIVE_MULTI = re.compile(r"\A\s*if \((!\w+(?: \|\| !\w+)+)\) return undefined;")
_GATE_LIVE_SINGLE = re.compile(r"\A\s*if \((![a-zA-Z]+)\) return undefined;")


def _match_if_return_undefined(text: str, i: int) -> str | None:
    """If `text[i:]` is `if (<balanced condition>) return undefined;` — `i`
    the index of the leading `if`, any whitespace including a newline
    between the condition and the keyword — the matched text, else None.

    The condition is matched by counting parens, not by stopping at the
    first `)`: an arrow function's parameter list or a nested call puts a
    `)` inside the condition itself, and a text scan that treats the first
    one as the close reads the whole guard as absent."""
    depth = 1
    j = i + 4  # past "if ("
    n = len(text)
    while j < n and depth > 0:
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
        j += 1
    if depth != 0:
        return None
    k = j
    while k < n and text[k] in " \t\r\n":
        k += 1
    if text.startswith("return undefined;", k):
        return text[i : k + len("return undefined;")]
    return None


def _later_return_undefined(stripped: str, after: int) -> str | None:
    """The first `if (...) return undefined;` at brace depth 0, outside a
    comment, appearing strictly after index `after` — or None.

    A top guard matching one of the STATIC patterns answers Phase 1's "first
    question for any block" only if it is the ONLY such guard. `deriveTxAux`
    is the counter-example that motivated this: a recognised `!hasCap(caps,
    'tx')` guard at the top, and a second `if (!hasEvidence) return
    undefined;` later, where `hasEvidence` is itself computed from live
    `state?.*` values — exactly the "half static, half live" shape
    `_classify_gate`'s own docstring assigns to `not-derivable`.

    Brace depth excludes a guard that belongs to a NESTED block — a local
    helper function's own body, a `.map()`/`.filter()` callback. A `//` or
    `/* */` comment is skipped outright for the same reason: text
    documenting a guard that used to exist is not a guard that still runs.
    Called only from the static-pattern branches below: `scattered` and
    `not-derivable` already read past the top guard by construction, so a
    later guard there is not news."""
    depth = 0
    i = after
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        if ch == "/" and stripped[i + 1 : i + 2] == "/":
            nl = stripped.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "/" and stripped[i + 1 : i + 2] == "*":
            end = stripped.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and stripped.startswith("if (", i):
            matched = _match_if_return_undefined(stripped, i)
            if matched is not None:
                return matched
        i += 1
    return None


def _classify_gate(body: str) -> tuple[str, str]:
    """(classification, detail) for one group's `derive*` body.

    Three classifications, matching Phase 1's own "first question for any
    block": `static` — a single, legible condition read purely from `caps`,
    at the top of the function, AND no other `if (...) return undefined;` —
    that exact, unbraced shape, the only one `_later_return_undefined`
    looks for — at the function's own top-level control flow (brace depth
    0, outside a comment).
    `scattered` — the only top-of-function guard is a generic `!caps`, and
    the real decision (an array length, an OR of several capability flags
    computed further down) is further into the body; read the function.
    `not-derivable` — the guard reads a LIVE object (runtime state, a
    TX/audio/scope snapshot) or an accumulated "was this ever reported"
    check, neither of which static source can answer; this also covers a
    guard that is HALF a static capability test and half a live check (its
    detail says which half is which) — including a static top guard
    followed by an unrelated later guard, since a function that returns
    early twice is not "a single, legible condition" no matter how clean
    the first return looks alone."""
    stripped = body.lstrip("\n")

    m = _GATE_HASCAP.match(stripped)
    if m:
        later = _later_return_undefined(stripped, m.end())
        if later:
            return "not-derivable", (
                f"!hasCap(caps, '{m.group(1)}') at the top looks static, "
                f"but the body has a later guard too — `{later}` — so the "
                "top guard alone is not the real gate; read the function"
            )
        return "static", f"!hasCap(caps, '{m.group(1)}')"

    m = _GATE_OR_HASCAP.match(stripped)
    if m:
        detail = f"!hasCap(caps, '{m.group(2)}') && !hasCap(caps, '{m.group(4)}')"
        later = _later_return_undefined(stripped, m.end())
        if later:
            return "not-derivable", (
                f"{detail} at the top looks static, but the body has a "
                f"later guard too — `{later}` — so the top guard alone is "
                "not the real gate; read the function"
            )
        return "static", detail

    m = _GATE_NUMERIC.match(stripped)
    if m:
        ident, field, _default, limit = m.groups()
        later = _later_return_undefined(stripped, m.end())
        if later:
            return "not-derivable", (
                f"{ident} <= {limit} (where {ident} = caps?.{field}) at the "
                f"top looks static, but the body has a later guard too — "
                f"`{later}` — so the top guard alone is not the real gate; "
                "read the function"
            )
        return "static", f"{ident} <= {limit}, where {ident} = caps?.{field}"

    m = _GATE_HALF.match(stripped)
    if m:
        live, cap_fn = m.groups()
        return "not-derivable", (
            f"half static, half live: !{live} || !{cap_fn}(caps) — the "
            f"capability half is checkable, the live `{live}` half is not"
        )

    m = _GATE_EVER_REPORTED.match(stripped)
    if m:
        return "not-derivable", (
            f"gated on whether any of `{m.group(1)}` was ever reported — an "
            "accumulated-observation check, not a capability tag"
        )

    if _GATE_NULLCAPS.match(stripped):
        return "scattered", "only a generic `!caps` guard at the top; the real gate is further into the body"

    m = _GATE_LIVE_MULTI.match(stripped) or _GATE_LIVE_SINGLE.match(stripped)
    if m:
        return "not-derivable", f"gated on a live runtime object: {m.group(1)}"

    return "scattered", "no recognised single-guard shape at the top of the function"


def presence_gates(adapter_text: str, groups: list[str]) -> list[dict[str, str]]:
    """Per optional group, does a block exist at all for a given radio —
    "the first question for any block" (Phase 1) — answered, or said why it
    cannot be, for EVERY group. Emitting only the easy ones would produce a
    derived list that is silently incomplete, worse than none.

    Derives the classification per group by reading its own `derive<Group>`
    function; does not hand-maintain which group falls in which bucket."""
    out: list[dict[str, str]] = []
    for group in groups:
        fn = "derive" + group[0].upper() + group[1:]
        body = _function_body(adapter_text, fn)
        if body is None:
            out.append({
                "group": group, "function": fn, "classification": "not-found",
                "detail": f"no `function {fn}` found in {ADAPTER}",
            })
            continue
        classification, detail = _classify_gate(body)
        out.append({"group": group, "function": fn, "classification": classification, "detail": detail})
    return out


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

    A surface with NO shared component still might reach the design
    language directly, by calling `renderSlot()` itself and spreading the
    result's `.attributes` onto its own markup. "No shared component" alone
    answers nothing about cost; whether design-language attributes land on
    ANY markup here is the question that does. This reports only whether
    that machinery is invoked at all — the per-selector answer for what is
    actually addressable is
    `components-v2/wiring/__tests__/design-language-selector-reachability
    .component.test.ts`, not duplicated here.
    """
    render_slot = re.compile(r"renderSlot\('(\w+)'")
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
        out[f.stem] = {
            "components": comps,
            "rendersSlots": sorted(set(render_slot.findall(text))),
        }
    if not out:
        die("no *Surface.svelte found — run from the repository root")
    return out


def radio_intent_names(text: str) -> list[str]:
    """The closed set `dispatchRadioIntent` accepts — `radio-intents.ts`'s
    own `intentSpecs` table. PTT is excluded by that module's own contract
    ("Only a known non-PTT radio intent may be dispatched"); a callback that
    keys or unkeys the radio reaches no name in this set by construction,
    not by an omission of this parser."""
    m = re.search(r"const intentSpecs = \[(.*?)\]\s*as const satisfies", text, re.S)
    if m is None:
        die(f"intentSpecs not found in {RADIO_INTENTS}")
    names: list[str] = []
    for block in re.finditer(r"names:\s*\[(.*?)\]", m.group(1), re.S):
        names += re.findall(r"'([a-z_0-9]+)'", block.group(1))
    if not names:
        die("intentSpecs parsed to zero intent names")
    return sorted(set(names))


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _balanced(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index just past the `close_ch` matching the `open_ch` at `text[i]`,
    or -1 if the text runs out unbalanced."""
    depth = 0
    n = len(text)
    while i < n:
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _decl_body(text: str, name: str) -> str | None:
    """Body of `function <name>(...) {...}`, found anywhere in `text` —
    module scope or nested inside another function — by paren- then
    brace-balancing from the parameter list. None if no such declaration
    exists. Matches the FIRST occurrence only; two same-named `function`
    declarations in one file would make this ambiguous, and none exist in
    any of the three source files this is run against today —
    `panel-commands.ts`, `panel-adapters.ts`, `SemanticRadioSurfaces.svelte`
    (checked by grepping `function \\w+` in each and diffing against its own
    `sort | uniq -c`)."""
    m = re.search(rf"\bfunction {re.escape(name)}\s*\(", text)
    if m is None:
        return None
    paren_open = text.index("(", m.end() - 1)
    paren_end = _balanced(text, paren_open, "(", ")")
    if paren_end < 0:
        return None
    brace_open = text.find("{", paren_end)
    if brace_open < 0:
        return None
    brace_end = _balanced(text, brace_open, "{", "}")
    if brace_end < 0:
        return None
    return text[brace_open + 1 : brace_end - 1]


def _value_span(text: str, i: int) -> str:
    """From index `i`, the value text up to the next top-level `,`/`;` or
    the enclosing bracket's own close. Bracket-depth-aware over `(){}[]`
    only — deliberately not a JS-grammar parser — so it finds where one
    object property or `const` value ends whether that value is an arrow
    function, a bare reference, or an IIFE (`onFilterWidthChange`'s
    debounce wrapper in `panel-commands.ts`, which a stricter "(params) =>
    body" pattern does not match at all)."""
    n = len(text)
    while i < n and text[i] in " \t\r\n":
        i += 1
    start = i
    depth = 0
    while i < n:
        c = text[i]
        if c in "({[":
            depth += 1
        elif c in ")}]":
            if depth == 0:
                break
            depth -= 1
        elif c in ",;" and depth == 0:
            break
        i += 1
    return text[start:i].strip()


def _prop_value(text: str, prop: str) -> str | None:
    """Value text of the first `<prop>: <value>` in `text`. The negative
    lookbehind excludes `x.<prop>:` (a property ACCESS, not a definition)
    and a longer identifier ending in `<prop>`."""
    m = re.search(rf"(?<![\w.]){re.escape(prop)}\s*:\s*", text)
    return None if m is None else _value_span(text, m.end())


def _const_value(text: str, name: str) -> str | None:
    m = re.search(rf"\bconst {re.escape(name)}\s*=\s*", text)
    return None if m is None else _value_span(text, m.end())


class _SendSideIndex:
    """The svelte-wiring and panel-commands symbol tables needed to trace a
    surface's callback prop to the `RADIO_INTENT_NAMES` it can reach — built
    once from the three files that actually wire surfaces to commands, never
    hand-typed. See `send_side()`."""

    def __init__(self, svelte_text: str, panel_text: str, panel_adapters_text: str) -> None:
        self.panel_text = panel_text
        self.aliases: dict[str, list[str]] = {}
        for m in re.finditer(r"const (\w+) = semanticHandlers\.(\w+);", svelte_text):
            self.aliases[m.group(1)] = [m.group(2)]
        for m in re.finditer(
            r"const (\w+) = \{\s*\.\.\.semanticHandlers\.(\w+),\s*\.\.\.semanticHandlers\.(\w+)\s*\};",
            svelte_text,
        ):
            self.aliases[m.group(1)] = [m.group(2), m.group(3)]

        self.records: dict[str, str] = {}
        for m in re.finditer(r"const ([A-Z][A-Z0-9_]+):\s*Record<", svelte_text):
            eq = svelte_text.index("=", m.end())
            brace = svelte_text.index("{", eq)
            end = _balanced(svelte_text, brace, "{", "}")
            self.records[m.group(1)] = svelte_text[brace + 1 : end - 1]

        self.local_fns: dict[str, str] = {}
        for m in re.finditer(r"\bfunction (\w+)\s*\(", svelte_text):
            body = _decl_body(svelte_text, m.group(1))
            if body is not None:
                self.local_fns.setdefault(m.group(1), body)
        for m in re.finditer(r"\bconst (\w+)\s*=\s*\(", svelte_text):
            name = m.group(1)
            if name in self.local_fns:
                continue
            value = _const_value(svelte_text, name)
            if value is not None and "=>" in value[:200]:
                self.local_fns[name] = value

        self.panel_fns: dict[str, str] = {}
        for m in re.finditer(r"\bfunction (\w+)\s*\(", panel_text):
            name = m.group(1)
            if name not in self.panel_fns:
                body = _decl_body(panel_text, name)
                if body is not None:
                    self.panel_fns[name] = body

        bind_body = _decl_body(panel_adapters_text, "bindSemanticSurfaceHandlers")
        if bind_body is None:
            die(f"bindSemanticSurfaceHandlers not found in {PANEL_ADAPTERS}")
        self.family_factory: dict[str, str] = dict(re.findall(r"(\w+):\s*(make\w+)\(\)", bind_body))
        if not self.family_factory:
            die(f"bindSemanticSurfaceHandlers parsed to zero families in {PANEL_ADAPTERS}")
        self._factory_body_cache: dict[str, str | None] = {}

    def _factory_body(self, factory: str) -> str | None:
        if factory not in self._factory_body_cache:
            self._factory_body_cache[factory] = _decl_body(self.panel_text, factory)
        return self._factory_body_cache[factory]

    def resolve_panel(
        self, text: str, visited: set[tuple], unresolved: list[str], depth: int = 0
    ) -> set[str]:
        """Radio intents reachable from `text` (panel-commands.ts territory):
        literal `dispatchRadioIntent({name: '<x>', ...})` calls, plus one
        level of recursion per reference — call OR bare identifier — to a
        `function` declared anywhere in `panel-commands.ts` that `text`
        names. Two distinct shapes need this: `tuningAccumulator()` /
        `activateReceiver()`, a CALL where the literal dispatch sits in a
        helper rather than in the exposed handler; and `onVoxToggle:
        toggleVox` (`makeTxHandlers`/`makeVoxHandlers`), a BARE reference —
        the property's whole value text IS the function's name, no `(`
        anywhere in it, so a call-only pattern silently drops it and the
        surface that binds it prints as though it dispatches nothing. A
        `name:` that is not a string literal (`onModInputChange`'s
        `modInputCommand(dataMode)` — the one dynamic call site inside THIS
        file; `adapters/mod-input-auto.svelte.ts` has a second, out of this
        function's reach) is reported via `unresolved`, never guessed."""
        intents: set[str] = set()
        if depth > 12:
            unresolved.append("resolution depth exceeded 12 — likely a reference cycle")
            return intents
        text = _strip_comments(text)
        for m in re.finditer(r"dispatchRadioIntent\(\{\s*name:\s*", text):
            i = m.end()
            if i < len(text) and text[i] == "'":
                j = text.index("'", i + 1)
                intents.add(text[i + 1 : j])
                continue
            unresolved.append(
                "dispatchRadioIntent's name is computed, not a literal: `"
                + _value_span(text, i) + "`"
            )
        for name, body in self.panel_fns.items():
            if re.search(rf"\b{re.escape(name)}\b", text) is None:
                continue
            key = ("panel", name)
            if key in visited:
                continue
            visited.add(key)
            intents |= self.resolve_panel(body, visited, unresolved, depth + 1)
        return intents

    def resolve_svelte(
        self, text: str, visited: set[tuple], unresolved: list[str], depth: int = 0
    ) -> set[str]:
        """Radio intents reachable from `text` (svelte-wiring territory):
        follows `alias.method` / `semanticHandlers.<family>.<method>`
        references into their factory in `panel-commands.ts`, `RECORD[field]`
        maps (unioned over every field, since the field is chosen at
        runtime), and local wrapper functions — recursively, since one
        wrapper can call another (`selectBand` -> `tuneFrequency`)."""
        intents: set[str] = set()
        if depth > 12:
            unresolved.append("resolution depth exceeded 12 — likely a reference cycle")
            return intents
        text = _strip_comments(text)

        def follow(family: str, method: str) -> bool:
            """True if `method` was located (and resolved) in `family`'s
            factory. A merged alias (`txAuxIntents` = vox + tx) means only
            ONE of several tried families is expected to declare a given
            method — so the caller decides whether to report `unresolved`,
            only once no family in the whole set found it."""
            factory = self.family_factory.get(family)
            if factory is None:
                return False
            body = self._factory_body(factory)
            if body is None:
                return False
            value = _prop_value(body, method)
            if value is None:
                return False
            intents.update(self.resolve_panel(value, visited, unresolved, depth + 1))
            return True

        for alias, families in self.aliases.items():
            for m in re.finditer(rf"\b{re.escape(alias)}\.(\w+)", text):
                method = m.group(1)
                key = ("alias", alias, method)
                if key in visited:
                    continue
                visited.add(key)
                # Not `any(...)`: a short-circuiting generator would skip
                # calling `follow` for the second family once the first
                # resolves, silently dropping that family's intents if the
                # method existed (rarely) in both.
                if not any([follow(family, method) for family in families]):
                    unresolved.append(
                        f"`{alias}.{method}` — no `{method}:` property found in any of "
                        + ", ".join(self.family_factory.get(f, f"<no factory for {f}>") for f in families)
                    )
        for m in re.finditer(r"\bsemanticHandlers\.(\w+)\.(\w+)", text):
            family, method = m.group(1), m.group(2)
            key = ("direct", family, method)
            if key in visited:
                continue
            visited.add(key)
            if not follow(family, method):
                factory = self.family_factory.get(family)
                unresolved.append(
                    f"`semanticHandlers.{family}.{method}` — "
                    + (f"no factory registered for family `{family}`" if factory is None
                       else f"no `{method}:` property found in `{factory}`")
                )
        for rname, rbody in self.records.items():
            if re.search(rf"\b{re.escape(rname)}\b", text) is None:
                continue
            key = ("record", rname)
            if key in visited:
                continue
            visited.add(key)
            intents |= self.resolve_svelte(rbody, visited, unresolved, depth + 1)
        for fname, fbody in self.local_fns.items():
            if re.search(rf"\b{re.escape(fname)}\b", text) is None:
                continue
            key = ("localfn", fname)
            if key in visited:
                continue
            visited.add(key)
            intents |= self.resolve_svelte(fbody, visited, unresolved, depth + 1)
        return intents


def _extract_open_tag(block: str, tag_name: str) -> str | None:
    """The attribute text of the first `<tag_name ...>` or `<tag_name .../>`
    in `block`, brace-depth-aware so a `>` inside a `{...}` expression prop
    does not end the tag early."""
    m = re.search(rf"<{re.escape(tag_name)}\b", block)
    if m is None:
        return None
    i = m.end()
    n = len(block)
    depth = 0
    j = i
    while j < n:
        c = block[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            break
        j += 1
    tag_text = block[i:j]
    return tag_text[:-1] if tag_text.endswith("/") else tag_text


def _parse_tag_props(tag_text: str) -> dict[str, str]:
    """`name={expr}`, `name="literal"` and svelte shorthand `{name}` props,
    as a name -> value-text dict. `{name}` shorthand stores the same text as
    both key and value, matching how `<VfoSurface ... {pendingFrequencyHz}
    />` binds a prop whose name equals the variable it carries."""
    props: dict[str, str] = {}
    i = 0
    n = len(tag_text)
    while i < n:
        while i < n and tag_text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        if tag_text[i] == "{":
            end = _balanced(tag_text, i, "{", "}")
            if end < 0:
                break
            content = tag_text[i + 1 : end - 1].strip()
            props[content] = content
            i = end
            continue
        m = re.match(r"[A-Za-z][\w-]*", tag_text[i:])
        if m is None:
            i += 1
            continue
        name = m.group(0)
        i += len(name)
        while i < n and tag_text[i] in " \t\r\n":
            i += 1
        if i < n and tag_text[i] == "=":
            i += 1
            while i < n and tag_text[i] in " \t\r\n":
                i += 1
            if i < n and tag_text[i] == "{":
                end = _balanced(tag_text, i, "{", "}")
                if end < 0:
                    break
                props[name] = tag_text[i + 1 : end - 1].strip()
                i = end
            elif i < n and tag_text[i] in "\"'":
                q = tag_text[i]
                j = i + 1
                while j < n and tag_text[j] != q:
                    j += 1
                props[name] = tag_text[i + 1 : j]
                i = j + 1
            else:
                i += 1
        else:
            props[name] = "true"
    return props


def send_side(svelte_text: str, panel_text: str, panel_adapters_text: str) -> list[dict]:
    """Per surface, in `SEMANTIC_SURFACE_NAMES` order (all 14 — a surface
    with no callback props reports zero `callbacks`; a surface whose mount
    site cannot be found reports `notDerivable` — never silently dropped):
    every callback prop `SemanticRadioSurfaces.svelte` binds on its mount
    tag, the `RADIO_INTENT_NAMES` it can reach, and — when the trace cannot
    resolve part of the chain — exactly why not, in `unresolved`."""
    index = _SendSideIndex(svelte_text, panel_text, panel_adapters_text)
    out: list[dict] = []
    for surface in surfaces():
        tag = surface[0].upper() + surface[1:] + "Surface"
        tag_path = SEMANTIC_DIR / f"{tag}.svelte"
        if not tag_path.is_file():
            die(f"{tag_path} does not exist — the surface-name-to-tag convention broke")
        snippet = surface + "Surface"
        m = re.search(rf"\{{#snippet {re.escape(snippet)}\(\)\}}(.*?)\{{/snippet\}}", svelte_text, re.S)
        if m is None:
            out.append({
                "surface": surface, "tag": tag, "callbacks": [],
                "notDerivable": f"no `{{#snippet {snippet}()}}` block found in {SURFACES_WIRING}",
            })
            continue
        tag_text = _extract_open_tag(m.group(1), tag)
        if tag_text is None:
            out.append({
                "surface": surface, "tag": tag, "callbacks": [],
                "notDerivable": f"no `<{tag}` mount found inside `{{#snippet {snippet}()}}`",
            })
            continue
        props = _parse_tag_props(tag_text)
        callback_props = {k: v for k, v in props.items() if re.match(r"^on[A-Z]", k)}
        # `pending*` (e.g. `pendingFrequencyHz`) and `*Feedback` (e.g.
        # `breakInDelayFeedback`, `cwKeyer`'s prop for
        # `getBreakInDelayControlFeedback()`) — a naming check over the
        # props this specific surface's mount tag actually receives, not a
        # hand-typed per-surface list.
        pending_props = sorted(
            k for k in props if re.match(r"^pending[A-Za-z]*$", k) or k.endswith("Feedback")
        )
        callbacks = []
        for prop, value in sorted(callback_props.items()):
            visited: set[tuple] = set()
            unresolved: list[str] = []
            intents = index.resolve_svelte(value, visited, unresolved)
            callbacks.append({
                "prop": prop, "expression": value,
                "intents": sorted(intents), "unresolved": unresolved,
            })
        out.append({
            "surface": surface, "tag": tag, "callbacks": callbacks, "pendingProps": pending_props,
        })
    return out


def feedback_adoption() -> dict[str, dict]:
    """Per surface: does its own component file adopt `pressedOf`
    (`semantic/pressed-of.ts`) or `control-feedback-presentation`
    (`primitives/control-feedback/`), does it render a `<button>`, and with
    the shared `v2-control-button` class (`components-v2/controls/
    control-button.css`) or its own markup. Replaces the hand-typed counts
    the SKILL.md's deleted Reference section used to carry."""
    out: dict[str, dict] = {}
    for surface in surfaces():
        tag = surface[0].upper() + surface[1:] + "Surface"
        path = SEMANTIC_DIR / f"{tag}.svelte"
        if not path.is_file():
            die(f"{path} does not exist — the surface-name-to-tag convention broke")
        text = path.read_text(encoding="utf-8")
        out[surface] = {
            "pressedOf": "pressed-of'" in text or 'pressed-of"' in text,
            "controlFeedbackPresentation": "control-feedback-presentation" in text,
            "rendersButton": bool(re.search(r"<button\b", text)),
            "sharedButtonClass": "v2-control-button" in text,
        }
    return out


def stylesheet_dl_selectors() -> dict[str, bool]:
    """Which design-language stylesheets under `LANGUAGES_DIR` select on
    `data-dl-*` (the attribute channel `annotate()` in `semantic/design-
    language-renderers.ts` writes) — distinct from merely consuming
    `--dl-*` custom properties, which `surface_components()` already
    reports per component."""
    paths = sorted(LANGUAGES_DIR.glob("*/*.css"))
    if not paths:
        die(f"no stylesheets found under {LANGUAGES_DIR}")
    return {str(p): "data-dl-" in p.read_text(encoding="utf-8") for p in paths}


def _checklist_keys() -> tuple[list[str], list[str]]:
    """(field keys, intent keys) a face-design proposal must account for —
    every field of every `*ViewModel` interface, and every name in
    `RADIO_INTENT_NAMES`. Radio-agnostic by design: the checklist is filled
    out per-proposal, and a proposal may legitimately draw a field or an
    intent the CURRENT `--radio` does not declare, marking it `unavailable`."""
    field_keys = sorted(
        f"{name}.{f['name']}" for name, fields in view_models(read(VIEW_MODEL)).items() for f in fields
    )
    intent_keys = radio_intent_names(read(RADIO_INTENTS))
    return field_keys, intent_keys


def checklist_skeleton() -> str:
    field_keys, intent_keys = _checklist_keys()
    lines = [
        "# Buildability checklist (MOR-2217) — fill every line; do not delete any.",
        "# FIELD <name>: the drawn element, or `unavailable: <reason>`.",
        "# INTENT <name>: the feedback mechanism, or `display-only`.",
    ]
    lines += [f"FIELD {k}: TODO" for k in field_keys]
    lines += [f"INTENT {k}: TODO" for k in intent_keys]
    return "\n".join(lines) + "\n"


def validate_checklist(path: Path) -> list[str]:
    """`FIELD <key>`/`INTENT <key>` entries the filled proposal at `path` is
    missing — present with an empty value counts as missing too. Empty list
    means every field and every intent this contract knows about was named."""
    field_keys, intent_keys = _checklist_keys()
    text = path.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {"FIELD": set(), "INTENT": set()}
    for m in re.finditer(r"^(FIELD|INTENT) ([\w.]+):[ \t]*(.*)$", text, re.M):
        kind, key, value = m.group(1), m.group(2), m.group(3).strip()
        if value:
            found[kind].add(key)
    missing = [f"FIELD {k}" for k in field_keys if k not in found["FIELD"]]
    missing += [f"INTENT {k}" for k in intent_keys if k not in found["INTENT"]]
    return missing


def _run_selftest() -> int:
    """Proves the checklist validator discriminates: a good proposal (every
    expected key present with a non-empty value) must exit 0; the same
    proposal with one FIELD line and one INTENT line struck must exit
    non-zero and name both. Both runs go through this script's own `argv`
    entry point via `subprocess` — not a direct call into
    `validate_checklist` — so a flag wired to nothing still fails this, the
    same discipline `measure-reference.py`'s `selftest` uses."""
    import tempfile

    field_keys, intent_keys = _checklist_keys()
    good_lines = [f"FIELD {k}: backed by <element>" for k in field_keys]
    good_lines += [f"INTENT {k}: display-only" for k in intent_keys]
    bad_lines = good_lines[1:-1]  # drops the first FIELD line and the last INTENT line

    script = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        good_path = Path(tmp) / "good.txt"
        bad_path = Path(tmp) / "bad.txt"
        good_path.write_text("\n".join(good_lines) + "\n", encoding="utf-8")
        bad_path.write_text("\n".join(bad_lines) + "\n", encoding="utf-8")
        good = subprocess.run(
            [sys.executable, script, "--checklist", "--validate", str(good_path)],
            capture_output=True, text=True,
        )
        bad = subprocess.run(
            [sys.executable, script, "--checklist", "--validate", str(bad_path)],
            capture_output=True, text=True,
        )
    print(f"selftest: good proposal ({len(good_lines)} lines) exit={good.returncode} (want 0)")
    print(f"selftest: bad proposal ({len(bad_lines)} lines, one FIELD + one INTENT dropped) "
          f"exit={bad.returncode} (want non-zero)")
    if bad.stderr.strip():
        print(bad.stderr.strip())
    ok = good.returncode == 0 and bad.returncode != 0
    print("selftest: PASS" if ok else "selftest: FAIL")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="ftx1")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--checklist", action="store_true",
                     help="emit a buildability-checklist skeleton instead of the contract")
    ap.add_argument("--validate", metavar="PATH",
                     help="with --checklist: check a filled-in checklist file instead of emitting one")
    ap.add_argument("--selftest", action="store_true",
                     help="prove the checklist validator discriminates, then exit")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_run_selftest())

    if args.checklist:
        if args.validate:
            missing = validate_checklist(Path(args.validate))
            if missing:
                for line in missing:
                    print(f"missing: {line}", file=sys.stderr)
                raise SystemExit(1)
            print("checklist: complete")
            return
        print(checklist_skeleton(), end="")
        return

    vm_text = read(VIEW_MODEL)
    view_models_data = view_models(vm_text)
    optional_groups = [
        f["name"] for f in view_models_data.get("RadioViewModel", []) if f["optional"]
    ]
    panel_text = read(PANEL_COMMANDS)
    panel_adapters_text = read(PANEL_ADAPTERS)
    svelte_text = read(SURFACES_WIRING)
    data = {
        "surfaces": surfaces(),
        "absence": absence_model(vm_text),
        "viewModels": view_models_data,
        "fieldShapes": field_shapes(vm_text),
        "radio": args.radio,
        "radioFeatures": radio_declares(args.radio)[0],
        "surfaceComponents": surface_components(),
        "presenceGates": presence_gates(read(ADAPTER), optional_groups),
        "sendSide": send_side(svelte_text, panel_text, panel_adapters_text),
        "feedbackAdoption": feedback_adoption(),
        "stylesheetDlSelectors": stylesheet_dl_selectors(),
        "sources": [
            str(VIEW_MODEL), str(LAYOUT_CONTRACT), str(ADAPTER), radio_declares(args.radio)[1],
            str(PANEL_COMMANDS), str(PANEL_ADAPTERS), str(RADIO_INTENTS), str(SURFACES_WIRING),
        ],
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

    print(f"## Whether each group exists on this radio ({len(data['presenceGates'])} groups)\n")
    print(
        "The first question for any block (above), answered per group — or "
        f"said why it cannot be, for every group; never silently for only "
        f"some. Derived by reading each group's `derive<Group>` function in "
        f"`{ADAPTER}`:\n"
    )
    gate_label = {
        "static": "static guard",
        "scattered": "not a single guard",
        "not-derivable": "not derivable from source",
        "not-found": "function not found",
    }
    for gate in data["presenceGates"]:
        label = gate_label[gate["classification"]]
        print(f"- `{gate['group']}` (`{gate['function']}`) — **{label}**: {gate['detail']}")
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
    print("with no shared component draws its own markup — the question that")
    print("decides cost is whether design-language attributes reach ANY of it,")
    print("not whether a component exists. The per-selector answer either way")
    print("is `components-v2/wiring/__tests__/design-language-selector-")
    print("reachability.component.test.ts`.\n")
    for name, info in sorted(data["surfaceComponents"].items()):
        comps = info["components"]
        slots = info["rendersSlots"]
        # `rendersSlots` is printed for EVERY row, not only rows with no
        # shared component: MetersSurface and VfoSurface have both a shared
        # component AND their own `renderSlot()` call, and skipping the
        # slots half there made them read as "does not reach" — the two
        # rows this partial derived list was silently wrong for.
        if comps:
            component_part = ", ".join(f"`{c['name']}` (**{c['tier']}**, "
                                       f"--dl- {c['dl']} / --v2- {c['v2']})" for c in comps)
        else:
            component_part = "no shared component"
        if slots:
            named = ", ".join(f"`{s}`" for s in slots)
            slot_part = (f"calls `renderSlot()` itself for {named}; "
                         "design-language attributes DO reach this "
                         "surface's own markup")
        else:
            slot_part = ("no `renderSlot()` call found; no design-language "
                         "attribute reaches this surface's markup from here "
                         "(see the reachability test above)")
        print(f"- `{name}` — {component_part}; {slot_part}")
    print()

    feats = data["radioFeatures"]
    print(f"## What `{args.radio}` declares ({len(feats)} features)\n")
    print("A field in the view model does not mean this radio reports it.\n")
    print(", ".join(f"`{f}`" for f in feats) if feats else "_none parsed_")
    print()

    print(f"## Send side: what each surface can dispatch ({len(data['sendSide'])} surfaces)\n")
    print(
        "Per surface: every callback prop `SemanticRadioSurfaces.svelte` binds "
        "on its mount tag, traced to the names in `RADIO_INTENT_NAMES` "
        f"(`{RADIO_INTENTS}`) it can reach. A surface with no callback props "
        "is display-only; where the trace cannot resolve part of the chain, "
        "the reason is printed instead of a guess.\n"
    )
    pending_by_surface = {row["surface"]: row.get("pendingProps", []) for row in data["sendSide"]}
    for row in data["sendSide"]:
        print(f"### `{row['surface']}` (`{row['tag']}`)\n")
        if row.get("notDerivable"):
            print(f"**not derivable from source**: {row['notDerivable']}\n")
            continue
        if row["pendingProps"]:
            print("Pending/confirmation props on its mount tag: "
                  + ", ".join(f"`{p}`" for p in row["pendingProps"]) + "\n")
        if not row["callbacks"]:
            print("_display only — no callback props on its mount tag._\n")
            continue
        for cb in row["callbacks"]:
            if cb["unresolved"]:
                for reason in cb["unresolved"]:
                    print(f"- `{cb['prop']}` — **not derivable from source**: {reason}")
            if cb["intents"]:
                print(f"- `{cb['prop']}` -> " + ", ".join(f"`{i}`" for i in cb["intents"]))
            elif not cb["unresolved"]:
                print(f"- `{cb['prop']}` — no `RADIO_INTENT_NAMES` intent reached "
                      f"(expression: `{cb['expression']}`)")
        print()

    print("## Feedback adoption per surface\n")
    print(
        "`pressedOf` (`semantic/pressed-of.ts`) reflects the last OBSERVED "
        "radio reading; `control-feedback-presentation` "
        "(`primitives/control-feedback/`) projects the in-flight command "
        "phase; a `pending*`/`*Feedback` prop on the mount tag (from the "
        "'Send side' section above) is the pending/confirmation contract "
        "where neither of those is adopted. None of the three is "
        "universal — check per surface, not per intent.\n"
    )
    fa = data["feedbackAdoption"]
    pressed_n = sum(1 for v in fa.values() if v["pressedOf"])
    cfp_n = sum(1 for v in fa.values() if v["controlFeedbackPresentation"])
    button_n = sum(1 for v in fa.values() if v["rendersButton"])
    shared_button_n = sum(1 for v in fa.values() if v["sharedButtonClass"])
    pending_n = sum(1 for p in pending_by_surface.values() if p)
    print(f"`pressedOf`: {pressed_n} of {len(fa)}. "
          f"`control-feedback-presentation`: {cfp_n} of {len(fa)}. "
          f"Shared `v2-control-button` class: {shared_button_n} of {button_n} "
          f"surfaces that render a `<button>`. Pending/confirmation props: "
          f"{pending_n} of {len(fa)}.\n")
    for name, info in sorted(fa.items()):
        marks = []
        if info["pressedOf"]:
            marks.append("pressedOf")
        if info["controlFeedbackPresentation"]:
            marks.append("control-feedback-presentation")
        if info["rendersButton"]:
            marks.append("shared button class" if info["sharedButtonClass"] else "own button markup")
        pending = pending_by_surface.get(name, [])
        if pending:
            marks.append("pending props: " + ", ".join(f"`{p}`" for p in pending))
        print(f"- `{name}` — " + (", ".join(marks) if marks else "none of the above"))
    print()

    print("## Design-language stylesheets selecting on `data-dl-*`\n")
    for path, selects in sorted(data["stylesheetDlSelectors"].items()):
        print(f"- `{path}` — {'selects on `data-dl-*`' if selects else 'does not'}")
    print()


if __name__ == "__main__":
    main()
