"""Pins the artifact cleanup script and the wiring of its workflow.

The workflow is read as text, as ``tests/test_soak_workflow.py`` reads its
workflow.
"""

from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "artifact_cleanup.py"
WORKFLOW = (ROOT / ".github" / "workflows" / "artifact-cleanup.yml").read_text(
    encoding="utf-8"
)
_SPEC = importlib.util.spec_from_file_location("artifact_cleanup", SCRIPT)
assert _SPEC and _SPEC.loader
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

NOW = datetime(2026, 10, 12, 4, 40, tzinfo=timezone.utc)
MAX_AGE = timedelta(days=3)


def artifact(
    artifact_id: int,
    age: timedelta,
    size: int,
    *,
    expired: bool = False,
    now: datetime = NOW,
) -> dict[str, object]:
    created = (now - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": artifact_id,
        "size_in_bytes": size,
        "created_at": created,
        "expired": expired,
    }


def sample(now: datetime = NOW) -> list[dict[str, object]]:
    return [
        artifact(1, timedelta(days=3, seconds=1), 100, now=now),  # just too old
        artifact(2, MAX_AGE, 40, now=now),  # exactly at the limit
        artifact(3, timedelta(days=1), 10, now=now),
        artifact(4, timedelta(days=30), 5000, expired=True, now=now),
        artifact(5, timedelta(days=9), 200, now=now),
    ]


def ids(artifacts: list[dict[str, object]]) -> list[object]:
    return [a["id"] for a in artifacts]


def test_selection_deletes_only_strictly_older_artifacts_and_ignores_expired() -> None:
    to_delete, to_keep = script.select_expired(sample(), NOW, MAX_AGE)
    assert (ids(to_delete), ids(to_keep)) == ([1, 5], [2, 3])


def test_selection_of_an_empty_list_is_empty() -> None:
    assert script.select_expired([], NOW, MAX_AGE) == ([], [])


def test_summary_text_after_a_real_run() -> None:
    deleted: list[object] = []
    summary, failures = script.cleanup(sample(), NOW, MAX_AGE, deleted.append, False)
    assert deleted == [1, 5]
    assert failures == 0
    assert summary == (
        "### Artifact cleanup: older than 3 days\n"
        "\n"
        "| Outcome | Artifacts | Bytes |\n"
        "| --- | ---: | ---: |\n"
        "| Deleted | 2 | 300 |\n"
        "| Failed to delete | 0 | 0 |\n"
        "| Kept | 2 | 50 |"
    )


def test_dry_run_never_calls_the_deleter_and_reports_what_it_would_delete() -> None:
    def delete(artifact_id: object) -> None:
        raise AssertionError(f"dry run deleted {artifact_id}")

    summary, failures = script.cleanup(sample(), NOW, MAX_AGE, delete, True)
    assert failures == 0
    assert (
        summary.splitlines()[0] == "### Artifact cleanup: older than 3 days (dry run)"
    )
    assert "| Would delete | 2 | 300 |" in summary
    assert "| Kept | 2 | 50 |" in summary


def test_failed_delete_is_counted_reported_and_does_not_stop_the_rest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    attempts: list[object] = []

    def delete(artifact_id: object) -> None:
        attempts.append(artifact_id)
        if artifact_id == 1:
            raise OSError("HTTP Error 403: Forbidden")

    summary, failures = script.cleanup(sample(), NOW, MAX_AGE, delete, False)
    assert attempts == [1, 5]
    assert failures == 1
    assert "| Deleted | 1 | 200 |" in summary
    assert "| Failed to delete | 1 | 100 |" in summary
    assert "delete failed for artifact 1: HTTP Error 403: Forbidden" in (
        capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("args", "delete_fails", "exit_code", "attempts"),
    [
        ([], False, 0, [1, 5]),
        ([], True, 1, [1, 5]),
        (["--dry-run"], True, 0, []),
    ],
)
def test_main_exit_status_and_step_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    delete_fails: bool,
    exit_code: int,
    attempts: list[int],
) -> None:
    summary_file = tmp_path / "summary.md"
    attempted: list[object] = []

    def delete(api: str, repo: str, token: str, artifact_id: object) -> None:
        attempted.append(artifact_id)
        if delete_fails:
            raise OSError("boom")

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    now = datetime.now(timezone.utc)  # main() reads the real clock
    listed = [
        artifact(1, timedelta(days=4), 100, now=now),
        artifact(3, timedelta(days=1), 10, now=now),
        artifact(5, timedelta(days=9), 200, now=now),
    ]
    monkeypatch.setattr(script, "list_artifacts", lambda api, repo, token: listed)
    monkeypatch.setattr(script, "delete_artifact", delete)
    assert script.main(["--max-age-days", "3", *args]) == exit_code
    assert attempted == attempts
    assert summary_file.read_text(encoding="utf-8").startswith("### Artifact cleanup")


class _Response:
    def __init__(self, body: bytes = b"") -> None:
        self._body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, *size: int) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def test_listing_reads_pages_until_a_short_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {"1": [{"id": n} for n in range(100)], "2": [{"id": 100}, {"id": 101}]}
    requested: list[str] = []

    def urlopen(request: object, timeout: float) -> _Response:
        url = request.full_url  # type: ignore[attr-defined]
        requested.append(url)
        page = parse_qs(urlparse(url).query)["page"][0]
        return _Response(json.dumps({"artifacts": pages[page]}).encode())

    monkeypatch.setattr(script.urllib.request, "urlopen", urlopen)
    listed = script.list_artifacts("https://api.example", "owner/repo", "token")
    assert len(listed) == 102
    assert [parse_qs(urlparse(u).query)["page"] for u in requested] == [["1"], ["2"]]
    assert urlparse(requested[0]).path == "/repos/owner/repo/actions/artifacts"
    assert parse_qs(urlparse(requested[0]).query)["per_page"] == ["100"]


def test_delete_is_an_authenticated_delete_of_the_artifact_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[object] = []

    def urlopen(request: object, timeout: float) -> _Response:
        sent.append(request)
        return _Response()

    monkeypatch.setattr(script.urllib.request, "urlopen", urlopen)
    script.delete_artifact("https://api.example", "owner/repo", "token", 42)
    (request,) = sent
    assert request.get_method() == "DELETE"  # type: ignore[attr-defined]
    assert request.full_url == (  # type: ignore[attr-defined]
        "https://api.example/repos/owner/repo/actions/artifacts/42"
    )
    assert request.get_header("Authorization") == "Bearer token"  # type: ignore[attr-defined]


def _block(key: str) -> str:
    match = re.search(rf"(?m)^{key}:\n((?:  .*\n|\n)+)", WORKFLOW)
    assert match, f"no top-level {key}: block"
    return match.group(1)


def test_workflow_runs_weekly_and_on_dispatch_with_the_default_of_three_days() -> None:
    events = _block("on")
    assert '    - cron: "40 4 * * 0"\n' in events
    assert "  workflow_dispatch:" in events
    max_age = re.search(r"(?m)^      max_age_days:\n((?:        .*\n)+)", events)
    assert max_age and 'default: "3"' in max_age.group(1)
    dry_run = re.search(r"(?m)^      dry_run:\n((?:        .*\n)+)", events)
    assert dry_run and {"type: boolean", "default: false"} <= {
        line.strip() for line in dry_run.group(1).splitlines()
    }
    assert "inputs.max_age_days || '3'" in WORKFLOW


def test_workflow_token_scope_and_concurrency() -> None:
    permissions = [line.strip() for line in _block("permissions").splitlines()]
    assert [p for p in permissions if p.endswith("write")] == ["actions: write"]
    concurrency = _block("concurrency")
    assert "group: ${{ github.workflow }}" in concurrency
    assert "cancel-in-progress: false" in concurrency
    assert "timeout-minutes:" in WORKFLOW


def test_workflow_passes_inputs_by_env_to_a_script_that_exists() -> None:
    run = WORKFLOW.split("        run: ", 1)[1]
    assert "${{" not in run
    assert ".github/scripts/artifact_cleanup.py" in run
    assert SCRIPT.is_file()
