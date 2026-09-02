#!/usr/bin/env bash
set -euo pipefail
source_sha=1e22244e0d66a07e7eee08880ae59791dc0ff760
mkdir -p exchange-proof
git rev-parse HEAD | tee exchange-proof/head.txt
uv sync --all-extras
uv run python --version | tee exchange-proof/python.txt
nodes=(
  tests/test_rigctld_client_backend.py::test_transport_connect_query_and_close
  tests/test_rigctld_client_backend.py::test_transport_command_accepts_only_rprt_zero_and_preserves_failure_code
  tests/test_rigctld_client_backend.py::test_cancelled_exchange_quarantines_before_close_barrier
  tests/test_rigctld_client_backend.py::test_cancelled_lock_waiter_keeps_active_exchange
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
  set +e
  timeout 90s uv run pytest "${nodes[@]}" -vv --tb=short --timeout=15 --timeout-method=thread --junitxml="exchange-proof/$label.xml" 2>&1 | tee "exchange-proof/$label.log"
  test_rc=${PIPESTATUS[0]}
  set -e
  echo "$label pytest_rc=$test_rc" | tee -a exchange-proof/status.txt
}
git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
run_cases initial
test "$test_rc" -eq 0
uv run python .github/exchange-proof/check.py control exchange-proof/initial.xml
for mutation in m1 m2 m3 m4; do
  patch_path=".github/exchange-proof/$mutation.patch"
  git apply "$patch_path"
  active_patch=$patch_path
  git diff -- src/rigplane/backends/rigctld_client/transport.py | tee "exchange-proof/$mutation.diff"
  run_cases "$mutation"
  restore
  git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
  echo "$mutation restored exact source" | tee -a exchange-proof/status.txt
  test "$test_rc" -eq 1
  uv run python .github/exchange-proof/check.py "$mutation" "exchange-proof/$mutation.xml"
  run_cases "$mutation-restored"
  test "$test_rc" -eq 0
  uv run python .github/exchange-proof/check.py control "exchange-proof/$mutation-restored.xml"
done
git diff --exit-code "$source_sha" -- src/ tests/ .claude/audits/
