"""MOR-2515 — default keyboard chords must dodge OS/browser-reserved combos.

macOS swallows Ctrl+ArrowUp/ArrowDown (Mission Control / App Exposé) before
the page sees the keypress, which is why the AF/RF defaults live on
Alt/Option+Arrow. This guard fails when any single-step default binding
collides with a chord the OS or browser consumes first, so the next default
that wanders onto a reserved combo is caught here rather than on a bench.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
DEFAULT_KEYBOARD_TOML = RIGS_DIR / "_keyboard-default.toml"

ARROW_KEYS = ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight")

# Chords the OS or browser consumes before the page, on at least one
# supported platform. Defaults ship one set for all platforms, so a chord
# reserved anywhere is off limits. Keep this list short and named.
RESERVED_DEFAULT_CHORDS: frozenset[tuple[frozenset[str], str]] = frozenset(
    # macOS Mission Control / App Exposé / Spaces (MOR-2515).
    [(frozenset({"CTRL"}), arrow) for arrow in ARROW_KEYS]
    # macOS Cmd+Arrow: Safari Back/Forward (Left/Right), Home/End-style
    # line jumps in text fields (Up/Down).
    + [(frozenset({"META"}), arrow) for arrow in ARROW_KEYS]
    # Browser Back / Forward on Windows/Linux.
    + [(frozenset({"ALT"}), arrow) for arrow in ("ArrowLeft", "ArrowRight")]
    # Tab switching: Ctrl+Tab in browsers, Cmd+Tab app switcher on macOS,
    # Alt+Tab window switcher on Windows/Linux.
    + [(frozenset({modifier}), "Tab") for modifier in ("CTRL", "META", "ALT")]
)


def _default_chords() -> set[tuple[frozenset[str], str]]:
    data = tomllib.loads(DEFAULT_KEYBOARD_TOML.read_text())
    chords: set[tuple[frozenset[str], str]] = set()
    for binding in data["keyboard"]["bindings"]:
        sequence = binding.get("sequence") or [binding["key"]]
        if len(sequence) != 1:
            continue
        modifiers = frozenset(str(m).upper() for m in binding.get("modifiers", []))
        chords.add((modifiers, sequence[0]))
    return chords


def test_default_bindings_avoid_os_reserved_chords() -> None:
    collisions = _default_chords() & RESERVED_DEFAULT_CHORDS
    assert not collisions, (
        f"rigs/_keyboard-default.toml binds OS/browser-reserved chords: "
        f"{sorted((sorted(modifiers), key) for modifiers, key in collisions)} "
        f"— see RESERVED_DEFAULT_CHORDS"
    )


def test_af_and_rf_defaults_live_on_alt_arrows() -> None:
    """MOR-2515 ruling: AF level on Alt+ArrowUp/Down, RF gain on
    Alt+Shift+ArrowUp/Down — Ctrl+Arrow is macOS-reserved."""
    data = tomllib.loads(DEFAULT_KEYBOARD_TOML.read_text())
    by_id = {b["id"]: b for b in data["keyboard"]["bindings"]}
    assert by_id["af-level-up"]["modifiers"] == ["ALT"]
    assert by_id["af-level-down"]["modifiers"] == ["ALT"]
    assert by_id["rf-level-up"]["modifiers"] == ["ALT", "SHIFT"]
    assert by_id["rf-level-down"]["modifiers"] == ["ALT", "SHIFT"]
