"""Shared probe-test setup (MOR-4502)."""

from __future__ import annotations

from pathlib import Path

import platformdirs
import pytest


@pytest.fixture(autouse=True)
def probe_state_dir(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """Every probe test reads and writes the checks journal under a temporary
    folder, never the real per-user state folder."""
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(platformdirs, "user_state_path", lambda app: state / app)
    return state / "rigplane"
