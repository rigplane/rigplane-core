from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_e2e_does_not_default_to_private_live_backend() -> None:
    impl = ROOT / "frontend" / "tests" / "e2e" / "v2-ui-interactive.impl.ts"
    config = ROOT / "frontend" / "playwright.config.ts"

    text = impl.read_text(encoding="utf-8") + config.read_text(encoding="utf-8")

    assert "192.168.55.152" not in text
    assert "RIGPLANE_V2_URL" in text
