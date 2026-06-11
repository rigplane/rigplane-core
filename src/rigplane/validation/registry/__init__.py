"""Capability→check-spec registry for the universal validation matrix.

Public facade for the ``rigplane.validation.registry`` package.  Defines the
closed set of check kinds and value-mutation rules, the ``CheckSpec``
dataclass (pure data, no hardware dependencies), and ``REGISTRY`` — the
canonical check tuple that drives every validation run regardless of radio
model or backend.

The check rows live in per-domain submodules so coverage-expansion work can
edit disjoint files (MOR-637):

- ``_structural`` — discovery + freq/mode structural checks (1-4)
- ``_levels`` — filter width, gains, preamp, attenuator (5-9)
- ``_dsp`` — notch, NB, NR, AGC (10-13)
- ``_tuning`` — RIT, XIT, squelch (14-16)
- ``_surfaces`` — audio, scope, meters (17-19)
- ``_tx`` — tuner, PTT (20-21)
- ``_tone`` — CTCSS repeater tone, TSQL, tone frequencies (MOR-642)

``_assembly`` concatenates them into ``REGISTRY`` (order-preserving) and runs
the import-time invariant guard; ``_builders`` holds the template generators.
This facade re-exports the exact public API of the former monolithic
``registry.py`` — every ``from rigplane.validation.registry import X`` keeps
working unchanged.

Layer rule: imports only stdlib, ``rigplane.core.capabilities``, and
``rigplane.validation.schema``.  No backends, profiles, transports, or
hardware protocols.
"""

from __future__ import annotations

from rigplane.validation.registry._assembly import (
    REGISTRY,
    REGISTRY_BY_ID,
    get_spec,
)
from rigplane.validation.registry._assembly import (
    _validate_registry as _validate_registry,
)
from rigplane.validation.registry._builders import (
    build_hamlib_template_from_capabilities,
    build_template_from_capabilities,
)
from rigplane.validation.registry._types import (
    VALUE_RULES,
    CheckKind,
    CheckSpec,
    ValueRule,
)

__all__ = [
    "CheckKind",
    "ValueRule",
    "VALUE_RULES",
    "CheckSpec",
    "REGISTRY",
    "REGISTRY_BY_ID",
    "get_spec",
    "build_template_from_capabilities",
    "build_hamlib_template_from_capabilities",
]
