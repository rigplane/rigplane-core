"""Comparison dimensions helper for native vs Hamlib validation artifacts.

Operates entirely on plain ``dict`` objects (the output of
``ValidationArtifact.to_dict()`` or a JSON-loaded equivalent).  Imports only
``rigplane.validation.schema`` enums and stdlib — no backends, no CLI.

ADR reference: docs/plans/2026-04-29-modularization-plan.md §7 (or the
validation-matrix ADR that defines the three comparison dimensions).
"""

from __future__ import annotations

from rigplane.validation.schema import CapabilityDeclaration, CheckStatus

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Cache .value references once to avoid repeated attribute access in loops.
_PASS = CheckStatus.PASS.value
_FAIL = CheckStatus.FAIL.value
_SKIP = CheckStatus.SKIP.value
_UNSUPPORTED = CheckStatus.UNSUPPORTED.value
_MANUAL_REQUIRED_STATUS = CheckStatus.MANUAL_REQUIRED.value
_BLOCKED = CheckStatus.BLOCKED.value

_SUPPORTED = CapabilityDeclaration.SUPPORTED.value
_UNSUPPORTED_PENDING = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE.value
_MANUAL_REQUIRED_DECL = CapabilityDeclaration.MANUAL_REQUIRED.value

# Statuses that are never PASS/FAIL for cross-impl comparison.
_OPAQUE_STATUSES: frozenset[str] = frozenset(
    {_UNSUPPORTED, _SKIP, _MANUAL_REQUIRED_STATUS, _BLOCKED}
)


def _index(artifact: dict[str, object]) -> dict[str, tuple[str, str]]:
    """Return ``{check_id: (declaration, status)}`` from an artifact dict.

    Walks ``artifact["levels"][*]["checks"][*]``.  Missing or malformed entries
    are skipped defensively so callers always get a plain mapping without
    raising on partial data.
    """
    result: dict[str, tuple[str, str]] = {}
    levels_obj = artifact.get("levels", [])
    if not isinstance(levels_obj, list):
        return result
    for level in levels_obj:
        if not isinstance(level, dict):
            continue
        checks_obj = level.get("checks", [])
        if not isinstance(checks_obj, list):
            continue
        for check in checks_obj:
            if not isinstance(check, dict):
                continue
            check_id = check.get("check_id")
            declaration = check.get("declaration")
            status = check.get("status")
            if (
                isinstance(check_id, str)
                and check_id
                and isinstance(declaration, str)
                and isinstance(status, str)
            ):
                result[check_id] = (declaration, status)
    return result


# ---------------------------------------------------------------------------
# Shared decl × status classification used by both profile_vs_reality and
# hamlib_vs_reality.  Returns "agree", "differ", or "na".
# ---------------------------------------------------------------------------


def _classify_pvr(declaration: str, status: str) -> str:
    """Classify one (declaration, status) pair for the profile-vs-reality dim.

    Rules (ADR §7.1):
    - SUPPORTED + pass          → agree
    - SUPPORTED + {fail|unsupported} → differ
    - UNSUPPORTED_PENDING + unsupported → agree
    - UNSUPPORTED_PENDING + pass        → differ
    - everything else (manual_required decl, manual_required/skip/blocked
      status, UNSUPPORTED_PENDING+fail) → na
    """
    if declaration == _SUPPORTED:
        if status == _PASS:
            return "agree"
        if status in (_FAIL, _UNSUPPORTED):
            return "differ"
        # skip / manual_required / blocked → na
        return "na"
    if declaration == _UNSUPPORTED_PENDING:
        if status == _UNSUPPORTED:
            return "agree"
        if status == _PASS:
            return "differ"
        # fail / skip / manual_required / blocked → na
        return "na"
    # MANUAL_REQUIRED declaration (or any unknown value) → na
    return "na"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_comparison_dimensions(
    native_artifact: dict[str, object],
    hamlib_artifact: dict[str, object],
) -> dict[str, object]:
    """Roll up a native and a Hamlib ``ValidationArtifact`` dict into three
    comparison dimensions.

    Parameters
    ----------
    native_artifact:
        ``ValidationArtifact.to_dict()`` (or equivalent loaded JSON) from the
        native rigplane backend validation run.
    hamlib_artifact:
        Same shape from the Hamlib/rigctld backend validation run.

    Returns
    -------
    A dict with three keys:

    ``profile_vs_reality``
        How well the native backend's declared support matches observed outcomes.
        Keys: ``agree`` (int), ``differ`` (int), ``differing`` (sorted list of
        check_ids where declaration and outcome disagreed).  No ``na`` key.

    ``hamlib_vs_reality``
        Same classification applied to the Hamlib artifact.
        Keys: ``agree`` (int), ``differ`` (int), ``na`` (int).  No ``differing``
        list.

    ``cross_impl``
        Per-check status comparison between native and Hamlib (aligned on
        check_id).  Only PASS/FAIL checks on both sides are classified as
        agree/differ; everything else (opaque status, missing in one artifact)
        is na.
        Keys: ``agree`` (int), ``differ`` (int), ``na`` (int).
    """
    native_idx = _index(native_artifact)
    hamlib_idx = _index(hamlib_artifact)

    # ------------------------------------------------------------------
    # (a) profile_vs_reality — from native index
    # ------------------------------------------------------------------
    pvr_agree = 0
    pvr_differ = 0
    pvr_differing: list[str] = []

    for check_id, (declaration, status) in native_idx.items():
        verdict = _classify_pvr(declaration, status)
        if verdict == "agree":
            pvr_agree += 1
        elif verdict == "differ":
            pvr_differ += 1
            pvr_differing.append(check_id)

    pvr_differing.sort()

    profile_vs_reality: dict[str, object] = {
        "agree": pvr_agree,
        "differ": pvr_differ,
        "differing": pvr_differing,
    }

    # ------------------------------------------------------------------
    # (b) hamlib_vs_reality — from hamlib index (same classification)
    # ------------------------------------------------------------------
    hvr_agree = 0
    hvr_differ = 0
    hvr_na = 0

    for _check_id, (declaration, status) in hamlib_idx.items():
        verdict = _classify_pvr(declaration, status)
        if verdict == "agree":
            hvr_agree += 1
        elif verdict == "differ":
            hvr_differ += 1
        else:
            hvr_na += 1

    hamlib_vs_reality: dict[str, object] = {
        "agree": hvr_agree,
        "differ": hvr_differ,
        "na": hvr_na,
    }

    # ------------------------------------------------------------------
    # (c) cross_impl — aligned on check_id present in EITHER index
    # ------------------------------------------------------------------
    ci_agree = 0
    ci_differ = 0
    ci_na = 0

    all_ids = set(native_idx) | set(hamlib_idx)
    for check_id in all_ids:
        if check_id not in native_idx or check_id not in hamlib_idx:
            # Present in only one artifact → na
            ci_na += 1
            continue

        native_status = native_idx[check_id][1]
        hamlib_status = hamlib_idx[check_id][1]

        if native_status in _OPAQUE_STATUSES or hamlib_status in _OPAQUE_STATUSES:
            ci_na += 1
            continue

        # Both are in {pass, fail}
        if native_status == hamlib_status:
            ci_agree += 1
        else:
            ci_differ += 1

    cross_impl: dict[str, object] = {
        "agree": ci_agree,
        "differ": ci_differ,
        "na": ci_na,
    }

    return {
        "profile_vs_reality": profile_vs_reality,
        "hamlib_vs_reality": hamlib_vs_reality,
        "cross_impl": cross_impl,
    }


__all__ = ["compute_comparison_dimensions"]
