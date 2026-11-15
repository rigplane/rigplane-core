"""Scope-mode family contract: one radio-owned-window family in two languages.

The CI-V wave-data modes where the radio owns the displayed window —
FIX (1), SCROLL-C (2), SCROLL-F (3) — are a single named family on the
server (``RADIO_OWNED_WINDOW_SCOPE_MODES``) and a single predicate on the
client (``isRadioOwnedWindowScope``). ``contracts/scope-mode-families.json``
is the shared fact; this test pins the server side, the vitest
``spectrum-panel-logic.test.ts`` family case pins the client side. Change
the family in all three places at once.
"""

import json
from pathlib import Path

from rigplane.runtime.radio_scope_source import RADIO_OWNED_WINDOW_SCOPE_MODES

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "contracts" / "scope-mode-families.json"


def test_server_family_matches_shared_fixture() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert sorted(RADIO_OWNED_WINDOW_SCOPE_MODES) == list(
        fixture["radio_owned_window_scope_modes"]
    )
