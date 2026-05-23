"""Tests for Hamlib model list parsing and subprocess loading."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import Mock

from rigplane.backends.hamlib_models import (
    HamlibModelCatalog,
    HamlibModelMetadata,
    load_hamlib_model_catalog,
    parse_hamlib_model_list,
)


_FIXTURE = Path(__file__).parent / "fixtures" / "hamlib_rigctl_list.txt"


def test_parse_hamlib_model_list_fixture() -> None:
    models = parse_hamlib_model_list(_FIXTURE.read_text(encoding="utf-8"))

    assert models[3073] == HamlibModelMetadata(
        model_id=3073,
        name="Icom IC-7610",
        version="20240312",
        status="Stable",
    )
    assert models[2].name == "Hamlib NET rigctl"
    assert models[1040].status == "Untested"


def test_parse_hamlib_model_list_skips_unparseable_lines() -> None:
    models = parse_hamlib_model_list(
        """
        heading without model id
        42 Example Radio With Spaces 1.0 Stable
        not-a-number Example Broken 1.0 Stable
        43 missing-status
        """
    )

    assert list(models) == [42]
    assert models[42].name == "Example Radio With Spaces"


def test_load_catalog_prefers_rigctld(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(
            args, 0, stdout=_FIXTURE.read_text(), stderr=""
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    catalog = load_hamlib_model_catalog(tools=("rigctld-success",), timeout=0.1)

    assert isinstance(catalog, HamlibModelCatalog)
    assert catalog.degraded_reason is None
    assert catalog.source_tool == "rigctld-success"
    assert 3073 in catalog.models
    assert calls == [["rigctld-success", "-l"]]


def test_load_catalog_falls_back_when_first_tool_missing(monkeypatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "missing-rigctld":
            raise FileNotFoundError(args[0])
        return subprocess.CompletedProcess(
            args, 0, stdout=_FIXTURE.read_text(), stderr=""
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    catalog = load_hamlib_model_catalog(
        tools=("missing-rigctld", "rigctl-fallback"),
        timeout=0.1,
    )

    assert catalog.degraded_reason is None
    assert catalog.source_tool == "rigctl-fallback"
    assert catalog.models[3073].name == "Icom IC-7610"


def test_load_catalog_degrades_when_all_tools_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "rigplane.backends.hamlib_models.subprocess.run",
        Mock(side_effect=FileNotFoundError("rigctld")),
    )

    catalog = load_hamlib_model_catalog(tools=("all-missing-a", "all-missing-b"))

    assert catalog.models == {}
    assert catalog.degraded_reason == "hamlib model list unavailable: tool not found"
    assert catalog.source_tool is None


def test_load_catalog_degrades_on_timeout_without_exposing_output(monkeypatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(
            args,
            timeout=0.1,
            output="PRIVATE_KEY=abc123",
            stderr="token=secret",
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    catalog = load_hamlib_model_catalog(tools=("timeout-tool",), timeout=0.1)

    assert catalog.models == {}
    assert catalog.degraded_reason == "hamlib model list unavailable: command timed out"
    assert "PRIVATE_KEY" not in str(catalog)
    assert "secret" not in str(catalog)


def test_load_catalog_degrades_on_nonzero_without_exposing_stderr(
    monkeypatch,
) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            2,
            stdout="",
            stderr="hostname=private.local token=secret",
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    catalog = load_hamlib_model_catalog(tools=("nonzero-tool",), timeout=0.1)

    assert catalog.models == {}
    assert catalog.degraded_reason == "hamlib model list unavailable: command failed"
    assert "private.local" not in str(catalog)
    assert "secret" not in str(catalog)


def test_load_catalog_degrades_on_empty_parse(monkeypatch) -> None:
    def fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args, 0, stdout="Rig #  Mfg Model", stderr=""
        )

    monkeypatch.setattr("rigplane.backends.hamlib_models.subprocess.run", fake_run)

    catalog = load_hamlib_model_catalog(tools=("empty-parse-tool",), timeout=0.1)

    assert catalog.models == {}
    assert (
        catalog.degraded_reason == "hamlib model list unavailable: no parseable models"
    )
