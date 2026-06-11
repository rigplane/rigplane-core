"""Template generators driven by the assembled REGISTRY.

Generator A (``build_template_from_capabilities``) maps a declared capability
set into a ``MatrixTemplate``; Generator B
(``build_hamlib_template_from_capabilities``) additionally gates each check on
available Hamlib tokens.
"""

from __future__ import annotations

from collections.abc import Callable

from rigplane.validation.registry._assembly import REGISTRY
from rigplane.validation.registry._types import CheckKind
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    MatrixTemplate,
    RadioTarget,
    ValidationLevel,
)

# Maps each CheckKind to the CapabilityDeclaration used when the capability
# is present in the profile's declared capability set.
_KIND_TO_DECLARATION: dict[CheckKind, CapabilityDeclaration] = {
    CheckKind.READ_ONLY: CapabilityDeclaration.SUPPORTED,
    CheckKind.RMVR_SAFE_WRITE: CapabilityDeclaration.SUPPORTED,
    CheckKind.WRITE_ONLY_OBSERVE: CapabilityDeclaration.SUPPORTED,
    CheckKind.MANUAL: CapabilityDeclaration.MANUAL_REQUIRED,
    CheckKind.TX_ADJACENT_BLOCKED: CapabilityDeclaration.MANUAL_REQUIRED,
    # AUDIO_PROBE checks are CI-automated (rigplane.validation.audio_checks);
    # on a real radio they keep the MANUAL operator-confirmation posture.
    CheckKind.AUDIO_PROBE: CapabilityDeclaration.MANUAL_REQUIRED,
}


def _presence_entries(
    capabilities: frozenset[str],
    functional_caps: set[str],
) -> list[CapabilityDeclarationEntry]:
    """Return synthetic ``<cap>.presence`` entries for undiscovered capabilities.

    For every capability in *capabilities* that is not covered by any registry
    check (i.e. not in *functional_caps*), emit a ``CapabilityDeclarationEntry``
    at ``ValidationLevel.STATIC_PROFILE`` with ``UNSUPPORTED_PENDING_EVIDENCE``.
    The list is sorted alphabetically by capability name so output is stable.
    """
    result: list[CapabilityDeclarationEntry] = []
    for cap in sorted(capabilities):
        if cap not in functional_caps:
            result.append(
                CapabilityDeclarationEntry(
                    check_id=f"{cap}.presence",
                    capability=cap,
                    level=ValidationLevel.STATIC_PROFILE,
                    declaration=CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE,
                    summary=(
                        f"Capability {cap!r} is declared by the profile but has "
                        "no functional check yet."
                    ),
                    tx_adjacent=False,
                )
            )
    return result


def build_template_from_capabilities(
    capabilities: frozenset[str],
    *,
    model: str,
    profile_id: str,
    probe: Callable[[str], bool] | None = None,
) -> MatrixTemplate:
    """Generate a ``MatrixTemplate`` from a set of declared capability strings.

    Algorithm (ADR §3.1):

    1. ``probe`` is accepted but **unused** in v1 — it is reserved for a future
       hardware-probe integration pass that can upgrade UNSUPPORTED_PENDING_EVIDENCE
       entries to SUPPORTED without a full hardware run.  The parameter is kept in
       the signature for forward-compatibility; do not branch on it.
    2. Every registry entry with ``capability == ""`` (structural) is always emitted
       with ``CapabilityDeclaration.SUPPORTED``.
    3. Functional entries are emitted with a declaration derived from
       ``_KIND_TO_DECLARATION`` when the capability is declared, or
       ``UNSUPPORTED_PENDING_EVIDENCE`` otherwise.
    4. Capabilities that appear in *capabilities* but have no registry check receive
       a synthetic ``<cap>.presence`` entry at ``ValidationLevel.STATIC_PROFILE`` with
       ``UNSUPPORTED_PENDING_EVIDENCE`` (they are declared but not yet exercised).
    5. The final list is stable-sorted by level (ascending) so that presence entries
       (level 0) appear first.

    Layer rule: this function takes a plain ``frozenset[str]`` — it does **not** import
    ``rigplane.profiles``.  The caller is responsible for resolving the profile and
    extracting its capabilities.
    """
    entries: list[CapabilityDeclarationEntry] = []
    functional_caps: set[str] = set()

    for spec in REGISTRY:
        if spec.capability == "":
            # Structural check — always supported.
            declaration = CapabilityDeclaration.SUPPORTED
        else:
            functional_caps.add(spec.capability)
            if spec.capability in capabilities:
                declaration = _KIND_TO_DECLARATION[spec.kind]
            else:
                declaration = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE

        entries.append(
            CapabilityDeclarationEntry(
                check_id=spec.check_id,
                capability=spec.capability,
                level=spec.level,
                declaration=declaration,
                summary=spec.summary,
                tx_adjacent=spec.tx_adjacent,
            )
        )

    # Presence entries: declared capabilities not covered by any registry check.
    entries.extend(_presence_entries(capabilities, functional_caps))

    # Stable sort by level — preserves registry order within each level;
    # presence entries are level 0 so they sort before DISCOVERY (level 1).
    entries.sort(key=lambda e: int(e.level))

    return MatrixTemplate(
        radio=RadioTarget(model=model, profile_id=profile_id),
        entries=entries,
    )


def build_hamlib_template_from_capabilities(
    capabilities: frozenset[str],
    available_hamlib_tokens: frozenset[str],
    *,
    model: str,
    profile_id: str,
) -> MatrixTemplate:
    """Generate a ``MatrixTemplate`` gated on available Hamlib tokens.

    This is Generator B (ADR §6): it mirrors ``build_template_from_capabilities``
    (Generator A) but gates each check on whether its ``CheckSpec.hamlib_token``
    is present in *available_hamlib_tokens*.

    Parameters
    ----------
    capabilities:
        The set of capability strings declared by the radio profile.
    available_hamlib_tokens:
        A plain ``frozenset[str]`` of Hamlib token names reported by the
        Hamlib backend (e.g. ``{"f", "m", "RF", "AF", "PREAMP", "ATT", "t"}``).
        This function takes a plain frozenset — it does **not** import
        ``rigplane.backends``.  The caller (CLI, MOR-211) is responsible for
        flattening a ``HamlibCaps`` object into tokens before calling here.
    model:
        Radio model string for ``RadioTarget``.
    profile_id:
        Profile identifier for ``RadioTarget``.

    Algorithm (ADR §6):

    1. Walk REGISTRY in order.  For each ``CheckSpec``:
       - If ``spec.hamlib_token is None`` → ``UNSUPPORTED_PENDING_EVIDENCE``.
       - elif ``spec.hamlib_token not in available_hamlib_tokens`` →
         ``UNSUPPORTED_PENDING_EVIDENCE``.
       - else (token present):
         - If ``spec.capability == ""`` (structural) → ``SUPPORTED``.
         - elif ``spec.capability in capabilities`` →
           ``_KIND_TO_DECLARATION[spec.kind]``.
         - else (token present but cap not declared) →
           ``UNSUPPORTED_PENDING_EVIDENCE``.
    2. Track functional caps (non-empty capability) for presence-entry exclusion.
    3. Append ``_presence_entries(capabilities, functional_caps)``.
    4. Stable-sort by level; return ``MatrixTemplate``.
    """
    entries: list[CapabilityDeclarationEntry] = []
    functional_caps: set[str] = set()

    for spec in REGISTRY:
        # Track all functional capabilities regardless of token/cap status.
        if spec.capability:
            functional_caps.add(spec.capability)

        if spec.hamlib_token is None:
            declaration = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
        elif spec.hamlib_token not in available_hamlib_tokens:
            declaration = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE
        else:
            # Token is present — now check structural vs functional.
            if spec.capability == "":
                declaration = CapabilityDeclaration.SUPPORTED
            elif spec.capability in capabilities:
                declaration = _KIND_TO_DECLARATION[spec.kind]
            else:
                declaration = CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE

        entries.append(
            CapabilityDeclarationEntry(
                check_id=spec.check_id,
                capability=spec.capability,
                level=spec.level,
                declaration=declaration,
                summary=spec.summary,
                tx_adjacent=spec.tx_adjacent,
            )
        )

    # Presence entries: declared capabilities not covered by any registry check.
    entries.extend(_presence_entries(capabilities, functional_caps))

    # Stable sort by level — preserves registry order within each level;
    # presence entries are level 0 so they sort before DISCOVERY (level 1).
    entries.sort(key=lambda e: int(e.level))

    return MatrixTemplate(
        radio=RadioTarget(model=model, profile_id=profile_id),
        entries=entries,
    )
