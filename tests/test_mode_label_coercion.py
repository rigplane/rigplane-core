"""MOR-1487 regression guard: rig-TOML mode labels vs. ``Mode`` enum tokens.

The frontend mode buttons send the rig-TOML display label verbatim (e.g.
``"CW-R"``, ``"RTTY-R"``) to ``IcomRadio.set_mode`` / ``._coerce_mode``. The
backend ``Mode`` enum uses underscored tokens (``Mode.CW_R``,
``Mode.RTTY_R``). This module is a table-driven guard, built from the same
``discover_rigs`` profile loader the rest of the test suite and production
code use (see ``tests/test_rig_loader.py``), so that any *future* rig TOML
that adds a hyphenated mode label re-uses this normalization instead of
silently drifting out of sync with the enum again.

Scope note: only rig profiles that are actually reachable through
``IcomRadio``/``CoreRadio`` (the class that owns ``_coerce_mode``) are
covered here — see ``_CORERADIO_ROUTED_MODELS`` below, mirrored from
``backends/factory.py::create_radio``'s model routing. Two pre-existing
label/enum gaps are unrelated to the hyphen-normalization bug this ticket
fixes (the labels have no ``Mode`` enum member at all, hyphenated or not)
and are marked ``xfail(strict=True)`` rather than silently excluded, so a
future full fix is visible as an actionable failure:

- IC-7610 ``PSK`` / ``PSK-R`` — no ``Mode.PSK`` / ``Mode.PSK_R`` member.
- X6200 ``DIGI`` — no ``Mode.DIGI`` member.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import discover_rigs
from rigplane.radio import IcomRadio
from rigplane.types import Mode

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

# Model names for which backends/factory.py::create_radio routes to an
# IcomRadio/CoreRadio-derived class (the class that owns _coerce_mode).
# Keep in sync with the SerialBackendConfig branch there.
#   - Yaesu models (FTX-1, ...) go to YaesuCatRadio, which has its own,
#     unrelated mode mapping (backends/yaesu_cat/radio.py) — not covered.
#   - Any other model (e.g. X6100, TX-500) is rejected by create_radio
#     with "Unsupported serial model" — those TOMLs exist as
#     doc/hamlib-reference profiles only and are not reachable through
#     _coerce_mode today.
_CORERADIO_ROUTED_MODELS = {"IC-705", "IC-7300", "IC-7610", "IC-9700", "X6200"}

# (model, label) pairs with no Mode enum member at all — a separate,
# pre-existing gap, not a hyphen/underscore drift bug.
_KNOWN_UNMAPPED_LABELS = {
    ("IC-7610", "PSK"),
    ("IC-7610", "PSK-R"),
    ("X6200", "DIGI"),
}


def _coerce_mode_cases() -> list[object]:
    rigs = discover_rigs(RIGS_DIR)
    cases = []
    for model in sorted(_CORERADIO_ROUTED_MODELS):
        rig = rigs[model]
        for label in rig.modes:
            case_id = f"{model}:{label}"
            if (model, label) in _KNOWN_UNMAPPED_LABELS:
                cases.append(
                    pytest.param(
                        model,
                        label,
                        id=case_id,
                        marks=pytest.mark.xfail(
                            strict=True,
                            reason=(
                                f"{model} {label!r}: no Mode enum member exists "
                                "for this label (pre-existing gap, unrelated to "
                                "MOR-1487's hyphen-normalization fix)"
                            ),
                        ),
                    )
                )
            else:
                cases.append(pytest.param(model, label, id=case_id))
    return cases


@pytest.mark.parametrize(("model", "label"), _coerce_mode_cases())
def test_rig_toml_mode_labels_coerce(model: str, label: str) -> None:
    """Every mode label shipped for a CoreRadio-routed rig must coerce.

    Guards against future label/token drift between rig TOMLs (hyphenated
    display labels) and ``core.types.Mode`` (underscored enum tokens).
    """
    coerced = IcomRadio._coerce_mode(label)
    assert isinstance(coerced, Mode)
    # The coerced enum member's name must match the label modulo the
    # documented hyphen<->underscore normalization.
    assert coerced.name == label.strip().upper().replace("-", "_")
