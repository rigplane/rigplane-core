"""Tests for built-in diagnostic contributors batch 3 (#1392).

Covers: ``LogsContributor``, ``StateContributor``, ``ErrorsContributor`` plus
the ``ExceptionRing`` / hook-installation infrastructure in ``_error_ring``.

Tests instantiate contributors directly (not via ``discover()``).
The autouse ``_reset_error_ring`` fixture isolates global ring state and
``sys.excepthook`` mutations between tests.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from icom_lan.diagnostics import _discovery, _error_ring
from icom_lan.diagnostics.contributor import BundleContext
from icom_lan.diagnostics.contributors import (
    ErrorsContributor,
    LogsContributor,
    StateContributor,
)


@pytest.fixture(autouse=True)
def _clear_runtime_registered() -> Any:
    _discovery._RUNTIME_REGISTERED.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()


@pytest.fixture(autouse=True)
def _reset_error_ring() -> Any:
    """Isolate global ring + excepthook between tests."""
    _error_ring.get_ring().clear()
    _error_ring.uninstall_hooks()
    saved_hook = sys.excepthook
    yield
    _error_ring.uninstall_hooks()
    _error_ring.get_ring().clear()
    sys.excepthook = saved_hook


def _make_ctx(**overrides: Any) -> BundleContext:
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": Path("/tmp/cfg-does-not-exist-1392"),
        "log_dir": Path("/tmp/log-does-not-exist-1392"),
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "sub-batch3",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


# ------------------------------------------------------------------- wiring


def test_built_in_contributors_wired() -> None:
    """``_BUILT_IN_CONTRIBUTORS`` includes batch-3 classes with expected names."""
    names = {cls().name for cls in _discovery._BUILT_IN_CONTRIBUTORS}
    assert {"logs", "state", "errors"}.issubset(names)


# ----------------------------------------------------------------- ExceptionRing


def test_ring_records_and_snapshots() -> None:
    ring = _error_ring.ExceptionRing()
    ring.record(ValueError, ValueError("x"), None)
    snap = ring.snapshot()
    assert len(snap) == 1
    assert snap[0].type_name == "ValueError"
    assert snap[0].message == "x"
    assert isinstance(snap[0].timestamp_unix, int)


def test_ring_capacity_caps() -> None:
    ring = _error_ring.ExceptionRing(capacity=3)
    for i in range(5):
        ring.record(ValueError, ValueError(f"e{i}"), None)
    snap = ring.snapshot()
    assert len(snap) == 3
    # The most recent 3 are e2, e3, e4.
    assert [item.message for item in snap] == ["e2", "e3", "e4"]


def test_install_hooks_records_uncaught() -> None:
    _error_ring.install_hooks()
    sys.excepthook(ValueError, ValueError("hooked"), None)
    snap = _error_ring.get_ring().snapshot()
    assert len(snap) == 1
    assert snap[0].type_name == "ValueError"
    assert snap[0].message == "hooked"


def test_install_hooks_idempotent() -> None:
    _error_ring.install_hooks()
    hook_after_first = sys.excepthook
    _error_ring.install_hooks()
    hook_after_second = sys.excepthook
    # Second install must not re-wrap the hook.
    assert hook_after_first is hook_after_second
    sys.excepthook(RuntimeError, RuntimeError("once"), None)
    snap = _error_ring.get_ring().snapshot()
    assert len(snap) == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_install_hooks_captures_threading_excepthook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker-thread uncaught exceptions go through ``threading.excepthook``.

    Regression for Codex review on PR #1411: previously only ``sys.excepthook``
    was installed, so exceptions raised in ``threading.Thread`` workers were
    silently dropped by the error ring.
    """
    import threading

    # Suppress the chained default print-to-stderr — we only care that the
    # ring captured the exception, not that the default reporter ran.
    monkeypatch.setattr(_error_ring, "_PREVIOUS_THREADING_EXCEPTHOOK", None)
    _error_ring.install_hooks()
    # Re-suppress the previous-hook reference recorded inside install_hooks
    # so the wrapped hook does not chain to the default printer.
    monkeypatch.setattr(
        _error_ring, "_PREVIOUS_THREADING_EXCEPTHOOK", lambda args: None
    )

    def _worker() -> None:
        raise ValueError("from worker thread")

    t = threading.Thread(target=_worker)
    t.start()
    t.join()

    snap = _error_ring.get_ring().snapshot()
    assert len(snap) == 1
    assert snap[0].type_name == "ValueError"
    assert snap[0].message == "from worker thread"


def test_uninstall_hooks_restores_threading_excepthook() -> None:
    """``uninstall_hooks`` must restore the original ``threading.excepthook``."""
    import threading

    saved = threading.excepthook
    _error_ring.install_hooks()
    assert threading.excepthook is not saved
    _error_ring.uninstall_hooks()
    assert threading.excepthook is saved


# --------------------------------------------------------------------- logs


def test_logs_copies_files_with_redaction(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "icom-lan.log").write_text(
        "2026-05-03 boot: cwd=/Users/foo/secret/work\n"
        "2026-05-03 connect: host=8.8.8.8 password=topsecret\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    LogsContributor().contribute(_make_ctx(log_dir=log_dir), out_dir)

    text = (out_dir / "icom-lan.log").read_text()
    assert "/Users/foo/secret" not in text
    assert "/Users/<USER>/" in text
    assert "topsecret" not in text
    assert "REDACTED" in text
    assert "8.8.8.8" not in text  # public IPv4 redacted to <IP>


def test_logs_oserror_does_not_leak_unredacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If reading raises OSError mid-iteration, dst MUST be absent (no fallback copy).

    Regression: previously a ``shutil.copy2`` fallback copied the original
    UNREDACTED log into the bundle on read failure — a privacy leak.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    src = log_dir / "icom-lan.log"
    src.write_text(
        "2026-05-03 boot: cwd=/Users/foo/secret/work password=topsecret\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    real_open = Path.open

    def flaky_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == src:
            raise OSError("simulated read failure")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    LogsContributor().contribute(_make_ctx(log_dir=log_dir), out_dir)

    dst = out_dir / "icom-lan.log"
    # Destination must be absent — no fallback copy that would leak content.
    assert not dst.exists()
    # No leftover tmp file either.
    assert list(out_dir.iterdir()) == []


def test_logs_handles_missing_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    missing = tmp_path / "nope"
    LogsContributor().contribute(_make_ctx(log_dir=missing), out_dir)
    assert list(out_dir.iterdir()) == []


def test_logs_skips_missing_rotation_files(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "icom-lan.log").write_text("only-the-base\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    LogsContributor().contribute(_make_ctx(log_dir=log_dir), out_dir)

    files = sorted(p.name for p in out_dir.iterdir())
    assert files == ["icom-lan.log"]


# --------------------------------------------------------------------- state


def test_state_unavailable_when_radio_is_none(tmp_path: Path) -> None:
    StateContributor().contribute(_make_ctx(radio=None), tmp_path)
    payload = json.loads((tmp_path / "state.json").read_text())
    assert payload["available"] is False
    assert "note" in payload


def test_state_uses_state_snapshot_method_if_present(tmp_path: Path) -> None:
    fake_radio = SimpleNamespace(
        state_snapshot=lambda: {"freq_hz": 14250000, "mode": "USB"}
    )
    StateContributor().contribute(_make_ctx(radio=fake_radio), tmp_path)
    payload = json.loads((tmp_path / "state.json").read_text())
    assert payload["available"] is True
    assert payload["state"]["freq_hz"] == 14250000
    assert payload["state"]["mode"] == "USB"


def test_state_snapshot_path_redacts_strings(tmp_path: Path) -> None:
    """``state_snapshot()`` return values must pass through string redaction."""
    fake_radio = SimpleNamespace(
        state_snapshot=lambda: {
            "path_field": "/Users/foo/baz",
            "freq_hz": 14250000,
        }
    )
    StateContributor().contribute(_make_ctx(radio=fake_radio), tmp_path)
    text = (tmp_path / "state.json").read_text()
    payload = json.loads(text)
    assert payload["available"] is True
    assert "/Users/foo" not in text
    assert payload["state"]["path_field"] == "/Users/<USER>/baz"
    assert payload["state"]["freq_hz"] == 14250000


def test_state_falls_back_to_individual_attrs(tmp_path: Path) -> None:
    fake_radio = SimpleNamespace(
        freq_hz=14250000,
        mode="USB",
        active_vfo="A",
        meters={"smeter": 5},
    )
    StateContributor().contribute(_make_ctx(radio=fake_radio), tmp_path)
    payload = json.loads((tmp_path / "state.json").read_text())
    assert payload["available"] is True
    assert payload["state"]["freq_hz"] == 14250000
    assert payload["state"]["mode"] == "USB"
    assert payload["state"]["vfo"] == "A"
    assert payload["state"]["meters"] == {"smeter": 5}


# --------------------------------------------------------------------- errors


def test_errors_writes_empty_when_no_exceptions(tmp_path: Path) -> None:
    ErrorsContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "recent-tracebacks.json").read_text())
    assert payload == {"count": 0, "items": []}


def test_errors_writes_recorded_exceptions(tmp_path: Path) -> None:
    ring = _error_ring.get_ring()
    ring.record(ValueError, ValueError("first"), None)
    ring.record(RuntimeError, RuntimeError("second"), None)

    ErrorsContributor().contribute(_make_ctx(), tmp_path)
    payload = json.loads((tmp_path / "recent-tracebacks.json").read_text())
    assert payload["count"] == 2
    assert len(payload["items"]) == 2
    types = {item["type_name"] for item in payload["items"]}
    assert types == {"ValueError", "RuntimeError"}
    for item in payload["items"]:
        assert "message" in item
        assert "traceback_lines" in item
        assert isinstance(item["traceback_lines"], list)


def test_errors_redacts_message_field(tmp_path: Path) -> None:
    """Exception ``message`` (str(exc)) must be redacted — paths + credentials."""
    ring = _error_ring.get_ring()
    ring.record(
        ValueError,
        ValueError("password=secret123 at /Users/foo"),
        None,
    )

    ErrorsContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "recent-tracebacks.json").read_text()
    payload = json.loads(text)
    assert "secret123" not in text
    assert "/Users/foo" not in text
    msg = payload["items"][0]["message"]
    assert "secret123" not in msg
    assert "/Users/<USER>" in msg


def test_errors_redacts_ips_in_traceback(tmp_path: Path) -> None:
    """Public IPs in traceback lines must be redacted to ``<IP>``;
    RFC 1918 LAN IPs are kept (radio host context)."""
    ring = _error_ring.get_ring()
    captured = _error_ring.CapturedException(
        timestamp_unix=1700000000,
        type_name="ConnectionError",
        message="connect failed",
        traceback_lines=[
            "  upstream=8.8.8.8 lan=192.168.55.40 failed\n",
        ],
    )
    ring._items.append(captured)

    ErrorsContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "recent-tracebacks.json").read_text()
    payload = json.loads(text)
    assert "8.8.8.8" not in text  # public IP redacted
    assert "192.168.55.40" in text  # RFC 1918 kept
    tb_line = payload["items"][0]["traceback_lines"][0]
    assert "<IP>" in tb_line


def test_errors_redacts_paths_in_tracebacks(tmp_path: Path) -> None:
    """Real exception caught from a file path-mentioning frame; redaction applied."""
    ring = _error_ring.get_ring()

    # Manually craft a CapturedException with a synthetic traceback line that
    # contains a home-directory path. Avoids fragile reliance on actual frame
    # paths under the test runner.
    captured = _error_ring.CapturedException(
        timestamp_unix=1700000000,
        type_name="ValueError",
        message="boom",
        traceback_lines=[
            'File "/Users/foo/secret/code/mod.py", line 1, in <module>\n',
            "    raise ValueError('boom')\n",
        ],
    )
    ring._items.append(captured)  # direct append OK — single-threaded test

    ErrorsContributor().contribute(_make_ctx(), tmp_path)
    text = (tmp_path / "recent-tracebacks.json").read_text()
    payload = json.loads(text)
    assert "/Users/foo/secret" not in text
    # Each traceback_line should contain <USER> token after redaction.
    tb_lines = payload["items"][0]["traceback_lines"]
    assert any("<USER>" in line for line in tb_lines)
