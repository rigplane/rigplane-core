"""`scripts/local-ci.sh --pre-push`: host routing, verdicts and step wiring (MOR-4372).

Infrastructure fakes only: a fake `mini-test`, `uv`, `npm`, `npx` and `node` on PATH record what the
script runs; the real script, the real `scripts/pre_push_scope.py` and the real CI classifier run
in a scratch repo. The exit codes are the contract with `pre-review.sh`: 0 passed, 1 a real
failure, 2 the checks could not run (CI stays the gate).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# records host and argv of every --run; the reply comes from FAKE_REPLY
FAKE_MINI_TEST = """#!/usr/bin/env bash
{ echo "host=$MINI_HOST"; echo "argv=$*"; } >> "$FAKE_LOG/mini-test"
printf '%s' "$5" > "$FAKE_LOG/remote-script"
[ -z "${FAKE_REPLY:-}" ] || echo "$FAKE_REPLY"
exit "${FAKE_MINI_RC:-0}"
"""
# `uv run ruff --version` and `uv run python scripts/pre_push_scope.py ...` answer for real, the
# rest is logged; FAKE_FAIL names one command line (after `uv run`) that exits 1
FAKE_UV = """#!/usr/bin/env bash
echo "uv $*" >> "$FAKE_LOG/steps"
shift  # run
[ "$*" != "${FAKE_FAIL:-}" ] || exit 1
case "$1 $2" in
  "ruff --version") echo "ruff ${FAKE_RUFF:-0.15.2}"; exit 0 ;;
  "python scripts/pre_push_scope.py") shift; exec python3 "$@" ;;
esac
[ "$1" != pytest ] || exit "${FAKE_PYTEST_RC:-0}"
exit 0
"""
FAKE_NODE_TOOL = """#!/usr/bin/env bash
echo "$(basename "$0") $* [NODE_OPTIONS=${NODE_OPTIONS:-}]" >> "$FAKE_LOG/steps"
[ "$(basename "$0") $*" != "${FAKE_FAIL:-}" ] || exit 1
exit 0
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "repo"
    for rel in (
        "scripts/local-ci.sh",
        "scripts/pre_push_scope.py",
        ".github/scripts/classify-quick-paths.py",
    ):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, repo / rel)
    _write(repo, "uv.lock", '[[package]]\nname = "ruff"\nversion = "0.15.2"\n')
    _write(repo, "src/rigplane/audio/bridge.py", "X = 1\n")
    _write(repo, "src/rigplane/web/server.py", "Y = 1\n")
    _write(repo, "frontend/src/lib/meter.ts", "export const m = 1;\n")
    _write(repo, "frontend/package-lock.json", "{}\n")
    _write(repo, "tests/test_bridge.py", "from rigplane.audio.bridge import X\n")
    _write(repo, "docs/guide/other.md", "prose\n")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    bin_dir, log = tmp_path / "bin", tmp_path / "log"
    bin_dir.mkdir()
    log.mkdir()
    for name, body in (
        ("mini-test", FAKE_MINI_TEST),
        ("uv", FAKE_UV),
        ("npm", FAKE_NODE_TOOL),
        ("npx", FAKE_NODE_TOOL),
    ):
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {
        **{
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("FAKE_", "LOCAL_CI"))
        },
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "FAKE_REPLY": "PRE-PUSH-HOST: PASS",
    }
    return repo, env


def _run(
    repo: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/local-ci.sh", *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _host_run(
    repo: Path, env: dict[str, str], **extra: str
) -> subprocess.CompletedProcess[str]:
    """The steps as the test host runs them (the orchestrator execs this with the merge base)."""
    return _run(
        repo,
        {**env, "LOCAL_CI_ON_TEST_HOST": "1", **extra},
        "--pre-push",
        "--base",
        "origin/main",
    )


def _steps(env: dict[str, str]) -> list[str]:
    path = Path(env["FAKE_LOG"]) / "steps"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _uv_steps(env: dict[str, str]) -> list[str]:
    return [
        s for s in _steps(env) if s.startswith("uv run ") and "pre_push_scope" not in s
    ]


def _change(repo: Path, rel: str) -> None:
    _write(repo, rel, "changed\n")


# ---- orchestration on the laptop: where it runs and what the exit code means -------------------


def test_the_orchestrator_runs_the_host_steps_on_the_mac_mini_with_the_merge_base(
    sandbox,
) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _run(repo, env, "--pre-push", "--base", "origin/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OVERALL: PASS" in result.stdout
    log = (Path(env["FAKE_LOG"]) / "mini-test").read_text(encoding="utf-8")
    assert "host=moroz@192.168.55.152" in log
    assert "argv=--run -- bash -c" in log
    mb = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert log.rstrip().endswith(f"_ {mb}")  # the merge base is the last argument
    remote = (Path(env["FAKE_LOG"]) / "remote-script").read_text(encoding="utf-8")
    assert "uv sync" in remote and "LOCAL_CI_ON_TEST_HOST=1" in remote
    assert "UV_PYTHON=3.11" in remote  # the interpreter CI pins (MOR-590)


def test_the_test_host_can_be_overridden(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    _run(repo, {**env, "PRE_PUSH_HOST": "moroz@nsu-ci"}, "--pre-push")
    assert "host=moroz@nsu-ci" in (Path(env["FAKE_LOG"]) / "mini-test").read_text()


@pytest.mark.parametrize(
    ("reply", "rc", "word"),
    [
        ("PRE-PUSH-HOST: PASS", 0, "PASS"),
        (
            "PRE-PUSH-HOST: FAIL",
            1,
            "FAIL",
        ),  # a real failure: the builder fixes and reruns
        (
            "",
            2,
            "NOT RUN",
        ),  # the host never reached its verdict line: CI stays the gate
    ],
)
def test_the_verdict_line_decides_the_exit_code(
    sandbox, reply: str, rc: int, word: str
) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _run(repo, {**env, "FAKE_REPLY": reply}, "--pre-push")
    assert result.returncode == rc, result.stdout + result.stderr
    assert word in result.stdout


def test_an_unreachable_host_is_not_a_failure(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _run(repo, {**env, "FAKE_REPLY": "", "FAKE_MINI_RC": "255"}, "--pre-push")
    assert result.returncode == 2


def test_without_mini_test_it_refuses_instead_of_running_here(sandbox) -> None:
    repo, env = sandbox
    (Path(env["PATH"].split(":")[0]) / "mini-test").unlink()
    result = _run(
        repo,
        {**env, "PATH": f"{env['PATH'].split(':')[0]}:/usr/bin:/bin"},
        "--pre-push",
    )
    assert result.returncode == 2
    assert "mini-test" in result.stderr
    assert _steps(env) == []  # nothing ran on this machine


def test_a_missing_merge_base_is_not_a_failure(sandbox) -> None:
    repo, env = sandbox
    result = _run(repo, env, "--pre-push", "--base", "origin/nope")
    assert result.returncode == 2


def test_other_modes_are_not_offered(sandbox) -> None:
    repo, env = sandbox
    assert _run(repo, env).returncode == 2
    assert _run(repo, env, "--quick").returncode == 2
    assert _run(repo, env, "--pre-push", "--base").returncode != 0


def test_the_output_never_carries_the_environment(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _run(repo, {**env, "SECRET_TOKEN": "s3cr3t-value"}, "--pre-push")
    assert "s3cr3t-value" not in result.stdout + result.stderr


# ---- the steps on the test host ----------------------------------------------------------------


def test_a_python_change_runs_ruff_imports_goldens_and_the_tests_that_mention_it(
    sandbox,
) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _host_run(repo, env)
    assert "PRE-PUSH-HOST: PASS" in result.stdout, result.stdout + result.stderr
    uv = _uv_steps(env)
    assert uv[0] == "uv run ruff --version"
    assert "uv run ruff check src/ tests/" in uv
    assert "uv run ruff format --check src/ tests/" in uv
    assert "uv run lint-imports" in uv
    for model in ("IC-7610", "X6200", "FTX-1"):
        assert any(
            f"--model {model} validate --provider native --dry-run --gate" in s
            for s in uv
        )
    pytest_calls = [s for s in uv if s.startswith("uv run pytest")]
    assert len(pytest_calls) == 1 and pytest_calls[0].endswith("tests/test_bridge.py")
    assert (
        "--timeout=300" in pytest_calls[0]
        and "--timeout-method=thread" in pytest_calls[0]
    )
    assert not any(
        "mypy" in s for s in uv
    )  # CI runs the strict web check only with the frontend
    assert not any(s.startswith(("npm", "npx")) for s in _steps(env))


def test_a_frontend_change_runs_install_check_tests_and_build_with_ci_heap_size(
    sandbox,
) -> None:
    repo, env = sandbox
    _change(repo, "frontend/src/lib/meter.ts")
    result = _host_run(repo, env)
    assert "PRE-PUSH-HOST: PASS" in result.stdout, result.stdout + result.stderr
    heap = "[NODE_OPTIONS=--max-old-space-size=3072]"
    steps = _steps(env)
    assert [s for s in steps if s.startswith(("npm", "npx"))] == [
        f"npm ci --no-audit --no-fund {heap}",
        f"npm run lint {heap}",
        f"npm run i18n:check {heap}",
        f"npm run check {heap}",
        f"npx vitest run --maxWorkers=4 {heap}",
        f"npm run build {heap}",
    ]
    assert not any(
        "ruff" in s for s in steps
    )  # a frontend-only diff skips the Python checks


def test_npm_ci_is_skipped_when_node_modules_matches_the_lockfile(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "frontend/src/lib/meter.ts")
    digest = hashlib.sha1(
        (repo / "frontend/package-lock.json").read_bytes()
    ).hexdigest()[:12]
    _write(repo, "frontend/node_modules/.mini-test-lock", digest + "\n")
    _host_run(repo, env)
    steps = _steps(env)
    assert not any(s.startswith("npm ci") for s in steps)
    assert any(s.startswith("npm run check") for s in steps)


def test_a_web_change_adds_the_strict_type_check_ci_runs(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/web/server.py")
    _host_run(repo, env)
    steps = _steps(env)
    assert "uv run mypy --strict src/rigplane/web" in steps
    assert any(s.startswith("npm run build") for s in steps)
    assert "uv run ruff check src/ tests/" in steps


def test_prose_only_changes_run_nothing(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "docs/guide/other.md")
    result = _host_run(repo, env)
    assert "PRE-PUSH-HOST: PASS" in result.stdout
    assert _uv_steps(env) == []
    assert not any(s.startswith(("npm", "npx")) for s in _steps(env))


def test_every_failing_step_is_reported_not_just_the_first(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/web/server.py")
    result = _host_run(repo, env, FAKE_FAIL="ruff check src/ tests/")
    assert "PRE-PUSH-HOST: FAIL" in result.stdout
    # the later legs still ran: one pass shows the builder everything that is red
    assert any(s.startswith("npm run check") for s in _steps(env))


def test_a_frontend_failure_fails_the_host_run(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "frontend/src/lib/meter.ts")
    result = _host_run(repo, env, FAKE_FAIL="npm run check")
    assert "PRE-PUSH-HOST: FAIL" in result.stdout


def test_a_stale_ruff_on_the_host_fails_with_the_fix(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _host_run(repo, env, FAKE_RUFF="0.14.0")
    assert "PRE-PUSH-HOST: FAIL" in result.stdout
    assert "0.14.0" in result.stdout and "0.15.2" in result.stdout


def test_a_selection_that_collects_nothing_is_not_a_failure(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _host_run(repo, env, FAKE_PYTEST_RC="5")
    assert "PRE-PUSH-HOST: PASS" in result.stdout


def test_a_failing_test_run_fails_the_host_run(sandbox) -> None:
    repo, env = sandbox
    _change(repo, "src/rigplane/audio/bridge.py")
    result = _host_run(repo, env, FAKE_PYTEST_RC="1")
    assert "PRE-PUSH-HOST: FAIL" in result.stdout


def test_the_caller_contract_with_pre_review_is_present() -> None:
    # pre-review.sh greps the script for this flag before it runs the gate (MOR-4101)
    assert "--pre-push" in (ROOT / "scripts/local-ci.sh").read_text(encoding="utf-8")
