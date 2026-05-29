"""Pure override-merge layer for generated validation matrices (ADR §4).

Given a generated :class:`~rigplane.validation.schema.MatrixTemplate` (from
Generator A/B) and a sparse, ``check_id``-keyed override patch, produce a
merged ``MatrixTemplate`` deterministically.  This module is a pure transform
plus a dict parser — no CLI wiring, no file discovery, no disk access.

Layer rule: imports only stdlib, ``rigplane.core.capabilities`` (transitively
via siblings), and its own validation siblings ``schema`` and ``registry``.
It MUST NOT import ``backends/``, ``profiles/``, ``cli/`` or ``runtime/``.

Safety invariant (ADR §4.2): an override can never relax a safety gate.  For
any check whose registry ``CheckKind`` is ``TX_ADJACENT_BLOCKED`` (``tx`` /
``tuner``), an attempt to clear ``tx_adjacent`` or to declare the check
``SUPPORTED`` (auto-actuating) is refused.  The safe generated values are kept
for those fields; only the safe fields (``summary``, ``level``) are applied,
and the ``check_id`` is recorded in :attr:`MergeReport.rejected`.
"""

from __future__ import annotations

from dataclasses import dataclass

from rigplane.validation.registry import REGISTRY_BY_ID, CheckKind
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    MatrixTemplate,
    ValidationLevel,
    validate_template_dict,
)

# Reserved override-only declaration sentinel that DROPS an entry (ADR §4.2
# step 3).  This is intentionally NOT a valid ``CapabilityDeclaration``.
EXCLUDED = "excluded"


@dataclass(frozen=True, slots=True)
class OverrideEntry:
    """A sparse, single-check patch.  ``None`` means "leave generated value"."""

    check_id: str
    level: int | None = None
    declaration: str | None = None  # a CapabilityDeclaration value OR "excluded"
    summary: str | None = None
    tx_adjacent: bool | None = None
    capability: str | None = None  # used only when appending a brand-new entry


@dataclass(frozen=True, slots=True)
class OverridePatch:
    """A sparse, check_id-keyed patch parsed from an override file (ADR §4.1)."""

    profile_id: str
    entries: tuple[OverrideEntry, ...]


@dataclass(frozen=True, slots=True)
class MergeReport:
    """Audit trail of how an override patch was applied (ADR §4.2 step 3)."""

    applied: tuple[str, ...]  # check_ids whose generated entry was patched
    appended: tuple[str, ...]  # check_ids added by the override (not in generated)
    excluded: tuple[str, ...]  # check_ids dropped via declaration="excluded"
    rejected: tuple[str, ...]  # check_ids whose unsafe field-change was refused


def parse_override_dict(data: dict[str, object]) -> OverridePatch:
    """Parse an override file dict (the v1 template shape, ADR §4.1).

    Lenient: ``"excluded"`` is accepted as a declaration even though it is not a
    valid :class:`CapabilityDeclaration`.  The ``"override"`` flag is ignored
    here (the loader, a later slice, decides full-vs-patch); this only extracts
    ``radio.profile_id`` and a sparse entry list.  Never raises on a well-formed
    dict; raises :class:`ValueError` only on missing required keys (``radio`` /
    ``entries`` or a per-entry ``check_id``).
    """
    if not isinstance(data, dict):
        raise ValueError("override must be an object")

    radio = data.get("radio")
    if not isinstance(radio, dict):
        raise ValueError("override.radio is required and must be an object")
    profile_id = radio.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("override.radio.profile_id is required")

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("override.entries is required and must be a list")

    entries: list[OverrideEntry] = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict):
            raise ValueError(f"override.entries[{index}] must be an object")
        check_id = raw.get("check_id")
        if not isinstance(check_id, str) or not check_id:
            raise ValueError(f"override.entries[{index}].check_id is required")

        level = raw.get("level")
        level = (
            level if isinstance(level, int) and not isinstance(level, bool) else None
        )

        declaration = raw.get("declaration")
        declaration = declaration if isinstance(declaration, str) else None

        summary = raw.get("summary")
        summary = summary if isinstance(summary, str) else None

        tx_adjacent = raw.get("tx_adjacent")
        tx_adjacent = tx_adjacent if isinstance(tx_adjacent, bool) else None

        capability = raw.get("capability")
        capability = capability if isinstance(capability, str) else None

        entries.append(
            OverrideEntry(
                check_id=check_id,
                level=level,
                declaration=declaration,
                summary=summary,
                tx_adjacent=tx_adjacent,
                capability=capability,
            )
        )

    return OverridePatch(profile_id=profile_id, entries=tuple(entries))


def _is_safety_gated(check_id: str) -> bool:
    """True if the registry classifies *check_id* as a TX-adjacent safety gate."""
    spec = REGISTRY_BY_ID.get(check_id)
    return spec is not None and spec.kind is CheckKind.TX_ADJACENT_BLOCKED


def _apply_to_existing(
    base: CapabilityDeclarationEntry, patch: OverrideEntry
) -> tuple[CapabilityDeclarationEntry, bool]:
    """Replace the provided mutable fields on *base* from *patch*.

    Returns the (possibly safety-clamped) entry and a flag indicating whether
    an unsafe field-change was refused (so the caller can record a rejection).
    """
    gated = _is_safety_gated(base.check_id)
    rejected = False

    # level — always safe to move.
    level = base.level
    if patch.level is not None:
        level = ValidationLevel(patch.level)

    # summary — always safe.
    summary = patch.summary if patch.summary is not None else base.summary

    # declaration — refused if it would auto-actuate a safety gate.
    declaration = base.declaration
    if patch.declaration is not None and patch.declaration != EXCLUDED:
        candidate = CapabilityDeclaration(patch.declaration)
        if gated and candidate is CapabilityDeclaration.SUPPORTED:
            rejected = True  # keep the safe generated declaration
        else:
            declaration = candidate

    # tx_adjacent — refused if it would clear the flag on a safety gate.
    tx_adjacent = base.tx_adjacent
    if patch.tx_adjacent is not None:
        if gated and patch.tx_adjacent is False:
            rejected = True  # keep the safe generated tx_adjacent (True)
        else:
            tx_adjacent = patch.tx_adjacent

    merged = CapabilityDeclarationEntry(
        check_id=base.check_id,
        capability=base.capability,
        level=level,
        declaration=declaration,
        summary=summary,
        tx_adjacent=tx_adjacent,
    )
    return merged, rejected


def _build_appended(patch: OverrideEntry) -> CapabilityDeclarationEntry:
    """Construct a brand-new entry from *patch* with sensible defaults."""
    declaration = (
        CapabilityDeclaration(patch.declaration)
        if patch.declaration is not None
        else CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
    )
    level = (
        ValidationLevel(patch.level)
        if patch.level is not None
        else ValidationLevel.STATIC_PROFILE
    )
    return CapabilityDeclarationEntry(
        check_id=patch.check_id,
        capability=patch.capability if patch.capability is not None else "",
        level=level,
        declaration=declaration,
        summary=patch.summary if patch.summary is not None else "",
        tx_adjacent=patch.tx_adjacent if patch.tx_adjacent is not None else False,
    )


def merge_overrides(
    generated: MatrixTemplate, patch: OverridePatch
) -> tuple[MatrixTemplate, MergeReport]:
    """Merge *patch* onto *generated* deterministically (ADR §4.2).

    See the module docstring for the safety invariant.  The merged template is
    stable-sorted by level (mirroring the generators) and round-tripped through
    :func:`validate_template_dict` to guarantee a valid v1 template.
    """
    # 1. Index generated entries by check_id, preserving order.
    indexed: dict[str, CapabilityDeclarationEntry] = {}
    order: list[str] = []
    for entry in generated.entries:
        indexed[entry.check_id] = entry
        order.append(entry.check_id)

    applied: list[str] = []
    appended: list[str] = []
    excluded: list[str] = []
    rejected: list[str] = []

    for ov in patch.entries:
        check_id = ov.check_id

        # 2a. Exclusion sentinel drops the matching generated entry.
        if ov.declaration == EXCLUDED:
            if check_id in indexed:
                del indexed[check_id]
                order.remove(check_id)
                excluded.append(check_id)
            continue

        # 2b. Replace existing entry's mutable fields.
        if check_id in indexed:
            merged_entry, was_rejected = _apply_to_existing(indexed[check_id], ov)
            indexed[check_id] = merged_entry
            applied.append(check_id)
            if was_rejected:
                rejected.append(check_id)
            continue

        # 2c. Append a brand-new entry.
        indexed[check_id] = _build_appended(ov)
        order.append(check_id)
        appended.append(check_id)

    # 3. Rebuild the entry list, then stable-sort by level (mirror generators).
    entries = [indexed[check_id] for check_id in order]
    entries.sort(key=lambda e: int(e.level))

    merged = MatrixTemplate(radio=generated.radio, entries=entries)

    # 4. Round-trip through the schema validator to guarantee validity.
    validated = validate_template_dict(merged.to_dict())

    report = MergeReport(
        applied=tuple(applied),
        appended=tuple(appended),
        excluded=tuple(excluded),
        rejected=tuple(rejected),
    )
    return validated, report


__all__ = [
    "EXCLUDED",
    "OverrideEntry",
    "OverridePatch",
    "MergeReport",
    "parse_override_dict",
    "merge_overrides",
]
