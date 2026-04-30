"""Generate `file-inventory.md`, `orphan-candidates.md`, and `init-snapshot.md`.

Reads `file-inventory.json` + `import-graph.dot` and produces three
markdown summaries for the Phase 1 discovery doc. Deterministic output.

Top-level orphan-candidates bucketing heuristic
-----------------------------------------------
For every ``.py`` file directly under ``src/icom_lan/`` (i.e. not inside a
subpackage), classify into a tentative bucket using a layered set of rules:

  1. Filename prefix / suffix tokens (``audio_``, ``_audio_``, ``scope_``,
     ``radio_``, ``civ``, ``transport``, ``commander``, ``command_``,
     ``profile``, ``rig_``, ``cli``, ``rigctld``, ``proxy``, ``startup``,
     ``meter_``, ``cw_``, ``sync``, ``discovery``, ``capabilities``,
     ``env_``, ``auth``, ``exceptions``, ``ic7``, ``ic705``, ``radios``).
  2. Module summary keyword scan when filename is ambiguous.
  3. ``unsure:<best-guess>`` when neither rule fires confidently.

This is an INPUT to Phase 2, not an output decision. The ``unsure`` tag
exists so the maintainer sees the script wasn't certain.
"""

from __future__ import annotations

import json
from pathlib import Path

ARTIFACTS = Path("docs/plans/discovery-artifacts")
INVENTORY = json.loads((ARTIFACTS / "file-inventory.json").read_text())

# Build inverse edges from the DOT file.
inbound: dict[str, set[str]] = {}
outbound: dict[str, set[str]] = {}
for line in (ARTIFACTS / "import-graph.dot").read_text().splitlines():
    line = line.strip().rstrip(";")
    if "->" not in line:
        continue
    src_part, _, tgt_part = line.partition("->")
    src = src_part.strip().strip('"')
    tgt = tgt_part.strip().strip('"')
    outbound.setdefault(src, set()).add(tgt)
    inbound.setdefault(tgt, set()).add(src)

# ---------------------------------------------------------------------------
# Bucketing heuristic
# ---------------------------------------------------------------------------

BUCKET_RULES = [
    # (predicate, bucket)
    (lambda n: n in {"audio_analyzer", "audio_bridge", "audio_bus", "audio_fft_scope"}, "audio"),
    (lambda n: n.startswith("_audio_") or n == "_audio_codecs", "audio"),
    (lambda n: n == "usb_audio_resolve", "audio"),
    (lambda n: n in {"scope", "scope_render"}, "scope"),
    (lambda n: n == "_scope_runtime", "scope"),
    (lambda n: n in {"civ", "transport", "protocol", "_civ_rx", "_connection_state",
                     "_control_phase", "exceptions", "auth", "discovery"}, "core"),
    (lambda n: n in {"commander", "command_map", "command_spec"}, "commands"),
    (lambda n: n in {"capabilities", "env_config"}, "core"),
    (lambda n: n in {"profiles", "profiles_runtime", "rig_loader"}, "profiles"),
    (lambda n: n in {"radio", "radio_protocol", "radio_state", "radio_state_snapshot",
                     "radio_initial_state", "radio_reconnect", "radios", "ic705",
                     "_state_cache", "_state_queries", "_runtime_protocols",
                     "_audio_runtime_mixin", "_dual_rx_runtime", "_shared_state_runtime",
                     "_poller_types", "_queue_pressure", "_bounded_queue", "_bridge_metrics",
                     "_bridge_state", "sync", "_audio_recovery", "_audio_transcoder",
                     "meter_cal", "cw_auto_tuner", "startup_checks", "proxy"}, "runtime"),
    (lambda n: n == "cli", "cli"),
    (lambda n: n == "types", "core"),
]


def bucket_for(name: str, summary: str) -> str:
    for predicate, bucket in BUCKET_RULES:
        if predicate(name):
            return bucket
    s = summary.lower()
    if "audio" in s:
        return "unsure:audio"
    if "scope" in s:
        return "unsure:scope"
    if "command" in s:
        return "unsure:commands"
    if "profile" in s or "rig" in s:
        return "unsure:profiles"
    if "radio" in s or "state" in s or "runtime" in s:
        return "unsure:runtime"
    return "unsure"


def is_top_level(path: str) -> bool:
    parts = Path(path).parts
    # parts: ('src', 'icom_lan', '<file>.py')
    return len(parts) == 3 and parts[1] == "icom_lan" and parts[2] != "__init__.py"


# ---------------------------------------------------------------------------
# orphan-candidates.md
# ---------------------------------------------------------------------------

orphan_lines: list[str] = [
    "# Top-level orphan candidates",
    "",
    "Files directly under `src/icom_lan/` (not inside a subpackage), each tagged",
    "with a *tentative* layer bucket from a filename + summary heuristic. Buckets",
    "marked `unsure:<X>` mean the heuristic was not confident. **This is input to",
    "Phase 2, not a final assignment.**",
    "",
    "| Path | Inbound | Outbound | Bucket | Summary |",
    "|---|---:|---:|---|---|",
]

# Sort by bucket, then path.
top_level = [
    (m, e) for m, e in INVENTORY.items()
    if is_top_level(e["path"]) and not e["path"].endswith("__init__.py")
]


def sort_key(item: tuple[str, dict]) -> tuple[str, str]:
    name = Path(item[1]["path"]).stem
    bucket = bucket_for(name, item[1]["summary"] or "")
    return (bucket, item[1]["path"])


top_level.sort(key=sort_key)

bucket_counts: dict[str, int] = {}
for module, entry in top_level:
    name = Path(entry["path"]).stem
    bucket = bucket_for(name, entry["summary"] or "")
    bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
    inb = len(inbound.get(module, set()))
    outb = len(outbound.get(module, set()))
    summary = (entry["summary"] or "").replace("|", "\\|").strip()
    if len(summary) > 90:
        summary = summary[:87] + "..."
    orphan_lines.append(
        f"| `{entry['path']}` | {inb} | {outb} | `{bucket}` | {summary} |"
    )

orphan_lines += [
    "",
    "## Bucket distribution",
    "",
]
for bucket, count in sorted(bucket_counts.items()):
    orphan_lines.append(f"- `{bucket}`: {count}")
orphan_lines.append("")
orphan_lines += [
    f"Total top-level orphan files: **{len(top_level)}**.",
    "",
]

(ARTIFACTS / "orphan-candidates.md").write_text("\n".join(orphan_lines))

# ---------------------------------------------------------------------------
# init-snapshot.md
# ---------------------------------------------------------------------------

init_lines: list[str] = [
    "# `__init__.py` snapshot",
    "",
    "Verbatim public surface (`__all__`) and side-effect / PEP 562 status of",
    "every package init under `src/icom_lan/`. The Phase 2 re-export shim plan",
    "must preserve every name listed here at its current dotted path.",
    "",
]

inits = sorted(
    [(m, e) for m, e in INVENTORY.items() if e["path"].endswith("__init__.py")],
    key=lambda kv: kv[1]["path"],
)

for module, entry in inits:
    init_lines.append(f"## `{entry['path']}` — `{module}`")
    init_lines.append("")
    summary = entry["summary"] or "*(no docstring)*"
    init_lines.append(f"- **Summary:** {summary}")
    if entry["all"] is None:
        init_lines.append("- **`__all__`:** *(absent)*")
    else:
        names = entry["all"]
        init_lines.append(f"- **`__all__`** ({len(names)} names):")
        for n in names:
            init_lines.append(f"  - `{n}`")
    init_lines.append(
        f"- **PEP 562 `__getattr__`:** "
        f"{'yes (lazy-loaded names — Tier 2)' if entry['has_pep562_getattr'] else 'no'}"
    )
    side = entry.get("init_side_effects") or []
    if side:
        init_lines.append("- **Top-level statements outside the import-only allowlist:**")
        for s in side:
            init_lines.append(f"  - `{s}`")
    else:
        init_lines.append("- **Top-level statements outside the import-only allowlist:** none")
    dyn = entry.get("dynamic_imports") or []
    if dyn:
        init_lines.append(f"- **Dynamic-import call sites:** {', '.join(dyn)}")
    init_lines.append("")

(ARTIFACTS / "init-snapshot.md").write_text("\n".join(init_lines))

# ---------------------------------------------------------------------------
# file-inventory.md — full inline table grouped by current location
# ---------------------------------------------------------------------------


def _location_group(path: str) -> str:
    """Bucket every file by its current source location for the table groupings."""
    parts = Path(path).parts
    # parts: ('src', 'icom_lan', ...)
    if len(parts) <= 2:
        return "(unknown)"
    if len(parts) == 3:
        return "top-level (`src/icom_lan/`)"
    return f"`src/icom_lan/{parts[2]}/` (and below)"


inv_lines: list[str] = [
    "# File inventory",
    "",
    "Every `.py` file under `src/icom_lan/`, ordered by current location, with",
    "a one-line responsibility summary derived from the module docstring (or",
    "the top-level class/function names where no docstring is present).",
    "",
    "Rendered from [`file-inventory.json`](./file-inventory.json) by",
    "[`build_orphan_report.py`](./build_orphan_report.py).",
    "",
]

by_group: dict[str, list[tuple[str, dict]]] = {}
for module, entry in INVENTORY.items():
    by_group.setdefault(_location_group(entry["path"]), []).append((module, entry))

group_order = sorted(
    by_group,
    key=lambda g: (0 if g.startswith("top-level") else 1, g),
)

for group in group_order:
    items = sorted(by_group[group], key=lambda kv: kv[1]["path"])
    inv_lines.append(f"## {group} — {len(items)} files")
    inv_lines.append("")
    inv_lines.append("| Path | Module | Summary |")
    inv_lines.append("|---|---|---|")
    for module, entry in items:
        summary = (entry["summary"] or "").replace("|", "\\|").replace("\n", " ").strip()
        if len(summary) > 110:
            summary = summary[:107] + "..."
        path = entry["path"]
        inv_lines.append(f"| `{path}` | `{module}` | {summary} |")
    inv_lines.append("")

(ARTIFACTS / "file-inventory.md").write_text("\n".join(inv_lines))

print(f"Wrote {ARTIFACTS / 'file-inventory.md'}")
print(f"Wrote {ARTIFACTS / 'orphan-candidates.md'}")
print(f"Wrote {ARTIFACTS / 'init-snapshot.md'}")
print(f"Bucket distribution: {bucket_counts}")
