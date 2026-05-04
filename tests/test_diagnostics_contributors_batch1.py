"""Tests for built-in diagnostic contributors batch 1 (#1390).

Covers: ``SystemContributor``, ``InvocationContributor``,
``DependenciesContributor``, ``ConfigContributor``.

Tests instantiate contributors directly (not via ``discover()``) so they
do not need ``_BUILT_IN_CONTRIBUTORS`` isolation — they only need
``_RUNTIME_REGISTERED`` cleanup to match the project pattern.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from icom_lan.diagnostics import _discovery
from icom_lan.diagnostics.contributor import BundleContext
from icom_lan.diagnostics.contributors import (
    ConfigContributor,
    DependenciesContributor,
    InvocationContributor,
    SystemContributor,
)
from icom_lan.diagnostics.contributors import system as system_mod


@pytest.fixture(autouse=True)
def _clear_runtime_registered() -> Any:
    """Ensure runtime-registered contributors don't leak between tests."""
    _discovery._RUNTIME_REGISTERED.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()


def _make_ctx(**overrides: Any) -> BundleContext:
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": Path("/tmp/cfg-does-not-exist-1390"),
        "log_dir": Path("/tmp/log-does-not-exist-1390"),
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "sub-batch1",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


# ------------------------------------------------------------------- wiring


def test_built_in_contributors_wired() -> None:
    """``_BUILT_IN_CONTRIBUTORS`` includes batch-1 classes with expected names."""
    names = {cls().name for cls in _discovery._BUILT_IN_CONTRIBUTORS}
    assert {"system", "invocation", "dependencies", "config"}.issubset(names)


# --------------------------------------------------------------------- system


def test_system_writes_required_keys(tmp_path: Path) -> None:
    SystemContributor().contribute(_make_ctx(), tmp_path)
    out = tmp_path / "system.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    for key in (
        "os",
        "arch",
        "python_version",
        "python_implementation",
        "icom_lan_version",
        "install_method",
    ):
        assert key in payload, f"missing key: {key}"
    assert payload["os"] in {"darwin", "linux", "windows"} or isinstance(
        payload["os"], str
    )


def test_system_install_method_is_one_of_known(tmp_path: Path) -> None:
    SystemContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "system.json").read_text())
    assert payload["install_method"] in {"editable", "wheel", "unknown"}


def test_system_paths_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Paths in serialised output get scrubbed via ``redact_paths``."""
    monkeypatch.setattr(
        system_mod, "_get_version", lambda: "1.0.0+/Users/secret-user/work"
    )
    SystemContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "system.json").read_text()
    # JSON must round-trip — guards against a regex consuming structural chars.
    json.loads(text)
    assert "/Users/secret-user" not in text
    assert "<USER>" in text


def test_system_get_version_returns_unknown_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_name: str) -> str:
        raise RuntimeError("nope")

    monkeypatch.setattr(system_mod.importlib.metadata, "version", boom)
    assert system_mod._get_version() == "unknown"


# ----------------------------------------------------------------- invocation


def test_invocation_writes_argv_and_env(tmp_path: Path) -> None:
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    out = tmp_path / "invocation.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert isinstance(payload["argv"], list)
    assert isinstance(payload["env"], dict)


def test_invocation_env_allowlist_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ICOM_LAN_REPORT_ENDPOINT", "https://example.com/x")
    monkeypatch.setenv("UNRELATED_SECRET_VAR_1390", "should-not-leak")
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "invocation.json").read_text())
    assert payload["env"].get("ICOM_LAN_REPORT_ENDPOINT") == "https://example.com/x"
    assert "UNRELATED_SECRET_VAR_1390" not in payload["env"]
    assert "should-not-leak" not in (tmp_path / "invocation.json").read_text()


def test_invocation_argv_credentials_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["icom-lan", "--password=secret123abc"])
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "invocation.json").read_text()
    # JSON must round-trip — would fail if a regex consumed the closing quote
    # or any other structural character of the surrounding JSON document.
    json.loads(text)
    assert "secret123abc" not in text
    assert "REDACTED" in text


def test_invocation_env_credentials_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env values containing credential patterns are redacted at the value level."""
    monkeypatch.setenv(
        "ICOM_LAN_REPORT_ENDPOINT", "https://example.com/x?password=secret123env"
    )
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "invocation.json").read_text()
    # JSON must round-trip — guards against a regex consuming structural chars.
    payload = json.loads(text)
    assert "secret123env" not in text
    assert "REDACTED" in payload["env"]["ICOM_LAN_REPORT_ENDPOINT"]


def test_invocation_argv_drops_executable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/icom-lan", "discover"])
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "invocation.json").read_text())
    assert payload["argv"] == ["icom-lan", "discover"]


def test_invocation_path_truncated_to_five_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    entries = [f"/tmp/p{i}" for i in range(10)]
    monkeypatch.setenv("PATH", os.pathsep.join(entries))
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "invocation.json").read_text())
    captured = payload["env"]["PATH"].split(os.pathsep)
    assert len(captured) == 5


def test_invocation_path_redacts_each_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each PATH segment is redacted individually, not as one joined string.

    Regression for Codex review on PR #1409: previously the join-then-redact
    sequence let ``redact_paths``'s ``(?<![/:\\w])`` lookbehind skip every
    segment after the first ``:``, leaking ``/Users/<name>`` from positions
    2..N.
    """
    import os

    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(
            [
                "/Users/alice/bin",
                "/Users/bob/bin",
                "/usr/local/bin",
            ]
        ),
    )
    InvocationContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "invocation.json").read_text()
    payload = json.loads(text)
    path_value = payload["env"]["PATH"]
    # Neither real username may leak.
    assert "/Users/alice" not in path_value
    assert "/Users/bob" not in path_value
    # Both home prefixes must show the redacted form.
    assert path_value.count("/Users/<USER>/bin") == 2
    # The non-home segment is preserved.
    assert "/usr/local/bin" in path_value


# --------------------------------------------------------------- dependencies


def test_dependencies_writes_pip_freeze(tmp_path: Path) -> None:
    DependenciesContributor().contribute(_make_ctx(), tmp_path)
    out = tmp_path / "pip-freeze.txt"
    assert out.exists()
    lines = [line for line in out.read_text().splitlines() if line]
    assert lines, "pip-freeze.txt is empty"
    for line in lines:
        assert "==" in line
    # sorted case-insensitive
    assert lines == sorted(lines, key=str.lower)


def test_dependencies_includes_icom_lan(tmp_path: Path) -> None:
    DependenciesContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "pip-freeze.txt").read_text()
    assert any(line.lower().startswith("icom-lan==") for line in text.splitlines()), (
        "icom-lan distribution not present in pip-freeze.txt"
    )


# -------------------------------------------------------------------- config


def test_config_drops_secret_keys(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "rig.toml").write_text(
        """
[server]
host = "192.168.1.10"
password = "abc"
pwd = "def"

[server.auth]
token = "xyz"
secret = "qqq"
user = "bob"
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    payload = json.loads((out_dir / "config-summary.json").read_text())

    assert payload["files"][0]["name"] == "rig.toml"
    content = payload["files"][0]["content"]

    def _walk_keys(value: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(value, dict):
            keys.update(value.keys())
            for v in value.values():
                keys.update(_walk_keys(v))
        elif isinstance(value, list):
            for item in value:
                keys.update(_walk_keys(item))
        return keys

    keys_lower = {k.lower() for k in _walk_keys(content)}
    assert "password" not in keys_lower
    assert "pwd" not in keys_lower
    assert "token" not in keys_lower
    assert "secret" not in keys_lower
    assert "host" in keys_lower
    assert "user" in keys_lower


def test_config_drops_secret_keys_in_array_of_tables(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "radios.toml").write_text(
        """
[[radios]]
name = "ic7610"
password = "leak1"

[[radios]]
name = "ic9700"
secret = "leak2"
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    text = (out_dir / "config-summary.json").read_text()
    assert "leak1" not in text
    assert "leak2" not in text
    assert "ic7610" in text
    assert "ic9700" in text


def test_config_handles_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=missing), out_dir)
    payload = json.loads((out_dir / "config-summary.json").read_text())
    assert payload == {"files": []}


def test_config_credentials_redacted(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "rig.toml").write_text(
        'description = "see password=mySecret123 in the logs"\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    text = (out_dir / "config-summary.json").read_text()
    assert "mySecret123" not in text
    assert "REDACTED" in text


def test_config_redacts_value_with_credential_pattern(tmp_path: Path) -> None:
    """Per-value redaction preserves valid JSON (regex can't span structural chars)."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "rig.toml").write_text(
        'description = "credentials: password=secretdata"\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    text = (out_dir / "config-summary.json").read_text()
    # JSON must round-trip — would fail if `\S+` regex consumed the closing
    # quote of the JSON string value when applied post-dump.
    payload = json.loads(text)
    assert "secretdata" not in text
    assert payload["files"][0]["name"] == "rig.toml"
    description = payload["files"][0]["content"]["description"]
    assert "REDACTED" in description


def test_config_empty_dir_returns_empty_files_list(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    payload = json.loads((out_dir / "config-summary.json").read_text())
    assert payload == {"files": []}


def test_config_records_parse_error_per_file(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "bad.toml").write_text("this is = = not valid toml [[", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ConfigContributor().contribute(_make_ctx(config_dir=cfg_dir), out_dir)
    payload = json.loads((out_dir / "config-summary.json").read_text())
    assert len(payload["files"]) == 1
    assert payload["files"][0]["name"] == "bad.toml"
    assert "error" in payload["files"][0]
