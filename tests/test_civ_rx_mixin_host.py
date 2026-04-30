"""Focused tests for CivRuntime host coupling (ex-_CivRxMixin).

These tests exercise small pieces of the CI-V runtime against a minimal
fake host, so that accidental removal of required attributes is more likely
to surface as a runtime failure rather than only at type-check time.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from icom_lan.runtime._civ_rx import CivRuntime
from icom_lan.exceptions import ConnectionError


@dataclass
class _DummyTracker:
    """Minimal stub for CivRequestTracker used by advance_generation."""

    generation: int = 0
    last_error: Exception | None = None

    def advance_generation(self, error: Exception) -> int:
        self.last_error = error
        self.generation += 1
        return self.generation


class _FakeCivHost:
    """Minimal host implementing just enough for targeted CivRuntime tests."""

    def __init__(self) -> None:
        self._civ_request_tracker = _DummyTracker()
        self._civ_epoch = self._civ_request_tracker.generation
        self._civ_transport = object()


class TestCivRuntimeHost:
    """Tests for small pieces of CivRuntime against a fake host."""

    def test_advance_generation_updates_epoch_and_uses_tracker(self) -> None:
        host = _FakeCivHost()
        runtime = CivRuntime(host)
        assert host._civ_epoch == 0
        tracker = host._civ_request_tracker

        runtime.advance_generation("unit-test")

        assert host._civ_epoch == 1
        assert tracker.last_error is not None
        assert "unit-test" in str(tracker.last_error)

    def test_ensure_civ_runtime_raises_when_no_transport(self) -> None:
        host = _FakeCivHost()
        host._civ_transport = None
        runtime = CivRuntime(host)

        with pytest.raises(ConnectionError, match="Not connected to radio"):
            runtime._ensure_civ_runtime()
