from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_e2e_does_not_default_to_private_live_backend() -> None:
    impl = ROOT / "frontend" / "tests" / "e2e" / "v2-ui-interactive.impl.ts"
    config = ROOT / "frontend" / "playwright.config.ts"

    text = impl.read_text(encoding="utf-8") + config.read_text(encoding="utf-8")

    assert "192.168.55.152" not in text
    assert "RIGPLANE_V2_URL" in text


def test_frontend_e2e_config_bounds_actions() -> None:
    """A missing/disabled control must fail fast, never stall the whole test.

    Without a bounded ``actionTimeout`` an un-timed Playwright action (e.g.
    ``scrollIntoViewIfNeeded`` on a control that never renders) would hang on
    the 180s per-test budget instead of producing a localized failure.
    """
    config = (ROOT / "frontend" / "playwright.config.ts").read_text(encoding="utf-8")

    assert "actionTimeout" in config
