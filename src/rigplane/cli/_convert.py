"""Pure Hamlib ``dump_caps`` → draft rigplane TOML + cross-check (MOR-203).

This module is a *pure* data transform: ``str``/dataclass in, ``str``/dataclass
out. It performs NO subprocess calls, NO disk I/O, and registers NO CLI verb —
those belong to MOR-204. It lives in ``cli/`` because only the top layer may
import BOTH ``rigplane.backends.hamlib_models`` (``HamlibCaps``) AND
``rigplane.validation.registry`` (the token map). ``validation/`` must not import
``backends/``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rigplane.backends.hamlib_models import HamlibCaps, load_hamlib_caps
from rigplane.validation.registry import REGISTRY

__all__ = [
    "CrossCheckReport",
    "add_subparser",
    "build_draft_toml",
    "caps_to_capabilities",
    "cross_check",
    "run",
]


# Hamlib mode token -> rigplane mode string. Unknown tokens pass through.
_MODE_MAP: dict[str, str] = {
    "CWR": "CW-R",
    "RTTYR": "RTTY-R",
    "PKTUSB": "USB-D",
    "PKTLSB": "LSB-D",
    "PKTFM": "FM",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _token_to_capability() -> dict[str, str]:
    """Reverse map ``hamlib_token -> rigplane capability`` from REGISTRY.

    Only specs with a non-None ``hamlib_token`` AND a non-empty ``capability``
    contribute, except the structural transmit token ``t`` which maps to the
    real ``tx`` capability and is included here. Structural ``f``/``m`` (empty
    capability) are skipped.
    """
    mapping: dict[str, str] = {}
    for spec in REGISTRY:
        if spec.hamlib_token is None:
            continue
        if spec.capability:
            mapping[spec.hamlib_token] = spec.capability
    return mapping


def caps_to_capabilities(caps: HamlibCaps) -> frozenset[str]:
    """Union of present Hamlib tokens mapped through :func:`_token_to_capability`.

    Considers ``get_funcs | set_funcs | get_levels | set_levels`` plus the
    transmit token ``t`` when ``caps.ptt_type`` is set. Tokens with no mapping
    are dropped.
    """
    token_map = _token_to_capability()
    present: set[str] = set(
        caps.get_funcs | caps.set_funcs | caps.get_levels | caps.set_levels
    )
    if caps.ptt_type:
        present.add("t")
    return frozenset(token_map[token] for token in present if token in token_map)


def _normalize_modes(modes: frozenset[str]) -> list[str]:
    """Map Hamlib mode tokens to rigplane mode strings, sorted, deduplicated."""
    return sorted({_MODE_MAP.get(m, m) for m in modes})


def _slug(model: str) -> str:
    """Lowercase *model*, replacing runs of non-alphanumerics with ``_``."""
    return _SLUG_RE.sub("_", model.lower()).strip("_")


@dataclass(frozen=True, slots=True)
class CrossCheckReport:
    """Result of comparing a profile's declared capabilities against Hamlib."""

    profile_id: str
    agreed: tuple[str, ...]
    rigplane_only: tuple[str, ...]
    hamlib_only: tuple[str, ...]
    mode_only_profile: tuple[str, ...]
    mode_only_hamlib: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly dict; bucket fields are lists, not tuples."""
        return {
            "profile_id": self.profile_id,
            "agreed": list(self.agreed),
            "rigplane_only": list(self.rigplane_only),
            "hamlib_only": list(self.hamlib_only),
            "mode_only_profile": list(self.mode_only_profile),
            "mode_only_hamlib": list(self.mode_only_hamlib),
        }

    def human_table(self) -> str:
        """Render a readable multi-line summary of every bucket."""
        lines = [f"Cross-check report for profile: {self.profile_id or '(none)'}"]
        buckets = (
            ("agreed", self.agreed),
            ("rigplane_only", self.rigplane_only),
            ("hamlib_only", self.hamlib_only),
            ("mode_only_profile", self.mode_only_profile),
            ("mode_only_hamlib", self.mode_only_hamlib),
        )
        for name, values in buckets:
            rendered = ", ".join(values) if values else "(none)"
            lines.append(f"  {name}: {rendered}")
        return "\n".join(lines)


def cross_check(
    caps: HamlibCaps,
    profile_capabilities: frozenset[str],
    *,
    profile_id: str = "",
) -> CrossCheckReport:
    """Compare declared profile capabilities against Hamlib-derived ones (ADR §8.2).

    ``agreed`` = intersection; ``rigplane_only`` = declared but no Hamlib token;
    ``hamlib_only`` = Hamlib token present but profile omits it. Mode buckets are
    empty in v1 (cross_check receives no profile modes). All tuples are sorted
    for determinism.
    """
    hamlib_caps = caps_to_capabilities(caps)
    return CrossCheckReport(
        profile_id=profile_id,
        agreed=tuple(sorted(profile_capabilities & hamlib_caps)),
        rigplane_only=tuple(sorted(profile_capabilities - hamlib_caps)),
        hamlib_only=tuple(sorted(hamlib_caps - profile_capabilities)),
        mode_only_profile=(),
        mode_only_hamlib=(),
    )


def build_draft_toml(caps: HamlibCaps, *, model: str, profile_id: str) -> str:
    """Return draft TOML text (valid per ``tomllib``) from *caps* (ADR §8.1).

    Auto-fills the loader-required sections/fields, normalizes modes and maps
    capability tokens, and marks every non-auto field with ``TODO(human):``.
    Deterministic: all lists are sorted. The string is hand-rolled — no TOML
    writer dependency.
    """
    features = sorted(caps_to_capabilities(caps))
    modes = _normalize_modes(caps.modes) or ["USB"]

    lines: list[str] = [
        "# REVIEW: auto-generated draft from Hamlib dump_caps. Human review required",
        "#         before this becomes a real profile. Do NOT auto-commit.",
        "",
        "[radio]",
        f'id = "{_slug(model)}"',
        f'model = "{model}"',
    ]
    if caps.model_id is not None:
        lines.append(f"hamlib_model_id = {caps.model_id}")
    lines.extend(
        [
            "receiver_count = 1  # TODO(human): confirm receiver count",
            "has_lan = false  # TODO(human): RigPlane-specific, not in dump_caps",
            "has_wifi = false  # TODO(human): RigPlane-specific, not in dump_caps",
            "# TODO(human): civ_addr — per-unit, not exposed by dump_caps",
            "",
            "[protocol]",
            'type = "civ"  # TODO(human): civ|kenwood_cat|yaesu_cat',
            "",
            "[capabilities]",
            "features = [" + ", ".join(f'"{f}"' for f in features) + "]",
            "",
            "[modes]",
            "list = ["
            + ", ".join(f'"{m}"' for m in modes)
            + ("]" if caps.modes else "]  # TODO(human): no modes in dump_caps"),
            "",
            "[filters]",
            'list = ["FIL1"]  # TODO(human): dump_caps exposes no filter passbands',
            "",
            "[vfo]",
            'scheme = "ab"  # TODO(human): ab|main_sub|single',
            "",
            "[commands]",
            "# TODO(human): CI-V/CAT byte maps not in dump_caps",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI verb: ``rigplane convert <model>`` (MOR-220 / ADR §8) — I/O driver.
#
# This is the thin driver wrapping the pure functions above. It shells out via
# ``load_hamlib_caps`` (the only I/O), writes the draft TOML to disk, and
# optionally runs the cross-check against an existing profile. It lives in the
# same module to keep the pure converter + its driver cohesive.
# ---------------------------------------------------------------------------


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the top-level ``convert`` subparser on ``sub``.

    Typed as ``Any`` because ``argparse._SubParsersAction`` is private and the
    surrounding parser code in ``cli/__init__.py`` follows the same convention.
    """
    p: argparse.ArgumentParser = sub.add_parser(
        "convert",
        help=(
            "Bootstrap a draft rigplane TOML profile from a Hamlib model's "
            "dump_caps (and optionally cross-check it against an existing "
            "profile). Human review required before the draft becomes real."
        ),
    )
    p.add_argument(
        "convert_model",
        metavar="MODEL",
        help=(
            "Hamlib numeric model id (e.g. 3091) or a known rigplane model "
            "name (e.g. X6200)."
        ),
    )
    p.add_argument(
        "--draft-out",
        dest="draft_out",
        default=None,
        metavar="PATH",
        help=(
            "Where to write the draft TOML. Default: <slug>.draft.toml in the "
            "current directory (NOT rigs/, to avoid auto-load)."
        ),
    )
    p.add_argument(
        "--compare-profile",
        dest="compare_profile",
        default=None,
        metavar="MODEL",
        help=(
            "Cross-check the Hamlib-derived capabilities against this existing "
            "profile and print the report."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (cross-check report) instead of human text.",
    )
    return p


def _resolve_model_id(model: str) -> tuple[int, str]:
    """Resolve *model* to ``(hamlib_model_id, display_name)``.

    Tries ``int(model)`` first; on ``ValueError`` resolves a rigplane profile
    by name via :func:`rigplane.profiles.get_radio_profile`. Raises ``KeyError``
    when the name is unknown.
    """
    try:
        model_id = int(model)
    except ValueError:
        from rigplane.profiles import get_radio_profile

        profile = get_radio_profile(model)  # may raise KeyError
        return profile.hamlib_model_id, profile.model
    return model_id, str(model_id)


def run(args: argparse.Namespace) -> int:
    """Driver for ``rigplane convert``. Returns a process exit code.

    Exit codes: ``0`` success; ``2`` unknown model name; ``3`` Hamlib
    ``dump_caps`` unavailable / degraded (cannot bootstrap a draft).
    """
    model_arg = str(getattr(args, "convert_model"))

    try:
        model_id, display_name = _resolve_model_id(model_arg)
    except KeyError as exc:
        print(f"Error: unknown model {model_arg!r}: {exc}", file=sys.stderr)
        return 2

    caps = load_hamlib_caps(model_id)
    if caps.degraded_reason:
        print(
            f"Error: cannot bootstrap draft for {model_arg!r}: "
            f"{caps.degraded_reason}.\n"
            "  dump_caps is required (check that Hamlib `rigctl` is installed "
            "and the model id is valid).",
            file=sys.stderr,
        )
        return 3

    profile_id = _slug(display_name)
    text = build_draft_toml(caps, model=display_name, profile_id=profile_id)

    draft_out = getattr(args, "draft_out", None) or f"{profile_id}.draft.toml"
    out_path = Path(draft_out)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(f"Draft written to: {out_path}", file=sys.stderr)

    compare = getattr(args, "compare_profile", None)
    if compare:
        from rigplane.profiles import get_radio_profile

        try:
            other = get_radio_profile(compare)
        except KeyError as exc:
            print(
                f"Error: unknown --compare-profile {compare!r}: {exc}", file=sys.stderr
            )
            return 2
        report = cross_check(caps, other.capabilities, profile_id=other.id)
        if getattr(args, "json", False):
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.human_table())

    return 0
