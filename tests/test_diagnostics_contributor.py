"""Tests for ``icom_lan.diagnostics`` contributor protocol and discovery."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from icom_lan.diagnostics import (
    BundleContext,
    DiagnosticContributor,
    discover,
    register,
)
from icom_lan.diagnostics import _discovery


@pytest.fixture(autouse=True)
def _clear_runtime_registered() -> Any:
    """Ensure runtime-registered contributors don't leak between tests."""
    _discovery._RUNTIME_REGISTERED.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()


def _make_ctx(**overrides: Any) -> BundleContext:
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": Path("/tmp/cfg"),
        "log_dir": Path("/tmp/log"),
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "sub-123",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


class _CompliantContributor:
    name = "compliant"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        return None


class _MissingNameContributor:
    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        return None


def test_protocol_runtime_checkable_accepts_compliant_class() -> None:
    obj = _CompliantContributor()
    assert isinstance(obj, DiagnosticContributor)


def test_protocol_rejects_missing_attributes() -> None:
    obj = _MissingNameContributor()
    assert not isinstance(obj, DiagnosticContributor)


def test_bundle_context_is_frozen() -> None:
    ctx = _make_ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.submission_id = "other"  # type: ignore[misc]


def test_bundle_context_is_hashable() -> None:
    ctx = _make_ctx()
    bucket = {ctx: "ok"}
    assert bucket[ctx] == "ok"


def test_register_appends_runtime_contributor() -> None:
    register(_CompliantContributor)
    found = discover()
    assert any(isinstance(c, _CompliantContributor) for c in found)


def test_discover_dedupes_by_name() -> None:
    class A:
        name = "dup"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class B:
        name = "dup"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    register(A)
    register(B)
    found = discover()
    dups = [c for c in found if c.name == "dup"]
    assert len(dups) == 1
    assert isinstance(dups[0], B)


def test_discover_includes_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    class FromEp:
        name = "from-ep"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class _FakeEP:
        name = "ep-fake"

        def load(self) -> type:
            return FromEp

    def fake_entry_points(*, group: str) -> tuple[Any, ...]:
        assert group == _discovery._ENTRY_POINT_GROUP
        return (_FakeEP(),)

    monkeypatch.setattr(
        _discovery.importlib.metadata, "entry_points", fake_entry_points
    )

    found = discover()
    assert any(isinstance(c, FromEp) for c in found)


def test_discover_swallows_entry_point_load_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GoodEpClass:
        name = "good"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class _BadEP:
        name = "bad-ep"

        def load(self) -> type:
            raise RuntimeError("boom")

    class _GoodEP:
        name = "good-ep"

        def load(self) -> type:
            return GoodEpClass

    def fake_entry_points(*, group: str) -> tuple[Any, ...]:
        return (_BadEP(), _GoodEP())

    monkeypatch.setattr(
        _discovery.importlib.metadata, "entry_points", fake_entry_points
    )

    found = discover()
    assert any(isinstance(c, GoodEpClass) for c in found)


def test_discover_swallows_instantiation_error() -> None:
    class Boom:
        name = "boom"

        def __init__(self) -> None:
            raise RuntimeError("nope")

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class Ok:
        name = "ok"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    register(Boom)
    register(Ok)
    found = discover()
    names = {c.name for c in found}
    assert "ok" in names
    assert "boom" not in names


def test_discover_precedence_runtime_wins_over_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FromEp:
        name = "shared"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class FromRuntime:
        name = "shared"

        def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
            return None

    class _FakeEP:
        name = "ep"

        def load(self) -> type:
            return FromEp

    def fake_entry_points(*, group: str) -> tuple[Any, ...]:
        return (_FakeEP(),)

    monkeypatch.setattr(
        _discovery.importlib.metadata, "entry_points", fake_entry_points
    )
    register(FromRuntime)

    found = discover()
    shared = [c for c in found if c.name == "shared"]
    assert len(shared) == 1
    assert isinstance(shared[0], FromRuntime)
