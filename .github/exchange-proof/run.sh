#!/usr/bin/env bash
set -euo pipefail
source_sha=2b38a97976d6c058772638c7f4a33fb372c7b897
export PYTHONDONTWRITEBYTECODE=1
mkdir -p exchange-proof
git rev-parse HEAD | tee exchange-proof/head.txt
nodes=(
  tests/test_rigctld_client_backend.py::test_transport_connect_query_and_close
  tests/test_rigctld_client_backend.py::test_transport_command_accepts_only_rprt_zero_and_preserves_failure_code
  tests/test_rigctld_client_backend.py::test_cancelled_exchange_quarantines_before_close_barrier
  tests/test_rigctld_client_backend.py::test_cancelled_lock_waiter_keeps_active_exchange
  tests/test_rigctld_client_backend.py::test_cancelled_lifecycle_preserves_real_stream_close_future
  tests/test_rigctld_client_backend.py::test_old_exchange_interruption_does_not_retire_replacement
  tests/test_rigctld_client_backend.py::test_delayed_cancelled_rprt_cannot_complete_next_command
)
active_patch=
restore() {
  if [ -n "$active_patch" ]; then
    git apply --reverse "$active_patch"
    active_patch=
  fi
}
trap restore EXIT
run_cases() {
  local label=$1
  shift
  local bytecode_dir
  bytecode_dir=$(mktemp -d)
  export PYTHONPYCACHEPREFIX="$bytecode_dir"
  echo "$label PYTHONPYCACHEPREFIX=$PYTHONPYCACHEPREFIX PYTHONDONTWRITEBYTECODE=$PYTHONDONTWRITEBYTECODE" | tee -a exchange-proof/status.txt
  set +e
  timeout 90s uv run pytest "$@" -vv --tb=short --timeout=15 --timeout-method=thread --junitxml="exchange-proof/$label.xml" 2>&1 | tee "exchange-proof/$label.log"
  test_rc=${PIPESTATUS[0]}
  set -e
  echo "$label pytest_rc=$test_rc" | tee -a exchange-proof/status.txt
}
git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
for version in 3.11 3.12 3.13; do
  export UV_PYTHON=$version
  uv sync --all-extras
  uv run python --version | tee "exchange-proof/python-$version.txt"
  run_cases "focused-$version" tests/test_rigctld_client_backend.py
  test "$test_rc" -eq 0
  uv run python .github/exchange-proof/check.py focused "exchange-proof/focused-$version.xml"
done
uv run ruff check src/ tests/ 2>&1 | tee exchange-proof/ruff.log
uv run ruff format --check --diff src/ tests/ 2>&1 | tee exchange-proof/format.log
uv run lint-imports 2>&1 | tee exchange-proof/imports.log
uv run mypy --strict src/rigplane/web 2>&1 | tee exchange-proof/mypy.log
run_cases initial "${nodes[@]}"
test "$test_rc" -eq 0
uv run python .github/exchange-proof/check.py control exchange-proof/initial.xml
for mutation in m1 m2 m3 m4 m5 positive; do
  patch_path=".github/exchange-proof/$mutation.patch"
  git apply "$patch_path"
  active_patch=$patch_path
  git diff -- src/rigplane/backends/rigctld_client/transport.py | tee "exchange-proof/$mutation.diff"
  run_cases "$mutation" "${nodes[@]}"
  restore
  git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
  echo "$mutation restored exact source" | tee -a exchange-proof/status.txt
  expected_rc=1
  if [ "$mutation" = positive ]; then expected_rc=0; fi
  test "$test_rc" -eq "$expected_rc"
  uv run python .github/exchange-proof/check.py "$mutation" "exchange-proof/$mutation.xml"
  run_cases "$mutation-restored" "${nodes[@]}"
  test "$test_rc" -eq 0
  uv run python .github/exchange-proof/check.py control "exchange-proof/$mutation-restored.xml"
done
git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
