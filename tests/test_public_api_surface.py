"""Public API surface regression test (Form F MVP fence).

This test enforces two invariants for the tier-1 public API as defined in
``docs/api/public-api-surface.md``:

1. Every tier-1 symbol is importable directly via ``from icom_lan import …``
   and resolves to a non-None object.
2. Importing tier-1 symbols does NOT transitively pull tier-3 modules
   (``icom_lan.web``, ``icom_lan.cli``, ``icom_lan.rigctld``) into
   ``sys.modules``.

The second invariant requires a fresh interpreter, because pytest itself
imports lots of modules; we use ``subprocess.run`` with ``sys.executable``
so the child process inherits this venv but starts clean.

If this test fails, the public-API fence has been broken. Either:

* a tier-1 symbol was removed/renamed (update doc + code together), or
* a tier-1 import path now triggers a tier-3 import (un-break the import
  graph; do not weaken this test).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

# Tier-1 symbols, taken verbatim from
# ``docs/api/public-api-surface.md`` § "Tier 1 — Stable".
TIER1_SYMBOLS: tuple[str, ...] = (
    # Backend factory and configs
    "__version__",
    "create_radio",
    "BackendConfig",
    "LanBackendConfig",
    "SerialBackendConfig",
    "YaesuCatBackendConfig",
    # Capability protocols
    "Radio",
    "LevelsCapable",
    "MetersCapable",
    "PowerControlCapable",
    "StateNotifyCapable",
    "AudioCapable",
    "CivCommandCapable",
    "ModeInfoCapable",
    "ScopeCapable",
    "DualReceiverCapable",
    "ReceiverBankCapable",
    "TransceiverBankCapable",
    "VfoSlotCapable",
    "StateCacheCapable",
    "StatePollable",
    "StatePoller",
    "RigctldRoutable",
    "RecoverableConnection",
    "DspControlCapable",
    "AntennaControlCapable",
    "CwControlCapable",
    "VoiceControlCapable",
    "SystemControlCapable",
    "RepeaterControlCapable",
    "AdvancedControlCapable",
    "TransceiverStatusCapable",
    "RitXitCapable",
    "MemoryCapable",
    "SplitCapable",
    # Exceptions
    "IcomLanError",
    "AudioCodecBackendError",
    "AudioError",
    "AudioFormatError",
    "AudioTranscodeError",
    "AuthenticationError",
    "CommandError",
    "ConnectionError",
    "TimeoutError",
    # Public types
    "Mode",
    "AudioCodec",
    "BreakInMode",
    # Public state types
    "RadioState",
    "RadioProfile",
    "VfoSlotState",
    "YaesuStateExtension",
)

# Tier-3 module prefixes that must NOT be loaded transitively by tier-1 imports.
TIER3_PREFIXES: tuple[str, ...] = (
    "icom_lan.web",
    "icom_lan.cli",
    "icom_lan.rigctld",
)

_SUBPROCESS_TIMEOUT = 30.0


@pytest.mark.parametrize("symbol", TIER1_SYMBOLS, ids=list(TIER1_SYMBOLS))
def test_tier1_symbol_importable(symbol: str) -> None:
    """Every tier-1 symbol must import cleanly and resolve to a real object."""
    import icom_lan

    assert hasattr(icom_lan, symbol), (
        f"tier-1 symbol {symbol!r} missing from icom_lan; either restore the "
        f"export in src/icom_lan/__init__.py or remove it from "
        f"docs/api/public-api-surface.md"
    )
    obj = getattr(icom_lan, symbol)
    assert obj is not None, f"tier-1 symbol {symbol!r} resolved to None"


def test_tier1_symbols_listed_in_dunder_all() -> None:
    """Tier-1 symbols (except __version__) should appear in ``icom_lan.__all__``."""
    import icom_lan

    public_all = set(icom_lan.__all__)
    # ``__version__`` is conventionally not in __all__.
    expected = set(TIER1_SYMBOLS) - {"__version__"}
    missing = sorted(expected - public_all)
    assert not missing, (
        f"tier-1 symbols missing from icom_lan.__all__: {missing}. "
        f"Add them to __all__ or remove from docs/api/public-api-surface.md."
    )


def test_tier1_imports_do_not_pull_tier3() -> None:
    """A fresh interpreter importing only tier-1 names must not load tier-3.

    Spawned via ``subprocess`` because pytest's own process has long since
    imported the whole world. The child prints any leaked tier-3 module
    names; the test asserts the output is empty.
    """
    import_lines = ",\n    ".join(TIER1_SYMBOLS)
    forbidden_check = " or ".join(f"m.startswith({p!r})" for p in TIER3_PREFIXES)
    code = (
        "import sys\n"
        "from icom_lan import (\n"
        f"    {import_lines},\n"
        ")\n"
        f"leaked = sorted(m for m in sys.modules if {forbidden_check})\n"
        "print('|'.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, (
        f"child interpreter failed (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    leaked = result.stdout.strip()
    assert leaked == "", (
        f"tier-1 imports leaked tier-3 modules into sys.modules: "
        f"{leaked.split('|')!r}. Tier-3 ({', '.join(TIER3_PREFIXES)}) must "
        f"stay out of the tier-1 import graph; find and break the offending "
        f"transitive import chain."
    )


def test_bare_import_does_not_pull_tier3() -> None:
    """Even ``import icom_lan`` alone must not load tier-3 submodules."""
    code = (
        "import sys\n"
        "import icom_lan  # noqa: F401\n"
        + "leaked = sorted(m for m in sys.modules if "
        + " or ".join(f"m.startswith({p!r})" for p in TIER3_PREFIXES)
        + ")\nprint('|'.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, (
        f"child interpreter failed (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    leaked = result.stdout.strip()
    assert leaked == "", (
        f"`import icom_lan` leaked tier-3 modules: {leaked.split('|')!r}"
    )
