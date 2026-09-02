#!/usr/bin/env bash
set -euo pipefail
echo "head_sha=$GITHUB_SHA runner_name=$RUNNER_NAME source_sha=1ff4926f7aafe5c711519415c375b5f9d633cc93"
case "$RUNNER_NAME" in
  mm-build-core-1|mm-build-core-2) ;;
  *) echo 'Rejected non-Mini runner'; exit 2 ;;
esac
mkdir -p cleanup-proof
for version in 3.11 3.12 3.13; do
  export UV_PYTHON="$version"
  uv python install "$version"
  uv sync --all-extras
  uv run python --version | tee "cleanup-proof/version-$version.txt"
  nodes=(tests/test_managed_tx_authority.py::test_cleanup_drain_reaps_done_task_before_queued_discard)
  expected=1
  if [[ "$version" == 3.11 ]]; then
    nodes+=(tests/test_managed_tx_authority.py::test_startup_wait_failure_leaves_no_pending_tasks)
    expected=4
  fi
  set +e
  timeout 30s uv run pytest "${nodes[@]}" -vv --tb=short --timeout=10 --timeout-method=thread 2>&1 | tee "cleanup-proof/bounded-$version.txt"
  test_rc=${PIPESTATUS[0]}
  timeout 5s uv run python .github/diagnostics/cleanup-drain-spin.py 2>&1 | tee "cleanup-proof/unmocked-$version.txt"
  spin_rc=${PIPESTATUS[0]}
  set -e
  echo "python=$version bounded_rc=$test_rc unmocked_rc=$spin_rc" | tee "cleanup-proof/status-$version.txt"
  [[ "$test_rc" == 1 ]]
  grep -F "$expected failed" "cleanup-proof/bounded-$version.txt"
  grep -F 'drain awaited completed owned cleanup before queued discard' "cleanup-proof/bounded-$version.txt"
  grep -F 'CHECKED cleanup_done=True cleanup_owned=True discard_pending=True' "cleanup-proof/unmocked-$version.txt"
  if [[ "$version" == 3.11 ]]; then
    [[ "$spin_rc" == 0 ]]
    grep -F 'DRAINED' "cleanup-proof/unmocked-$version.txt"
  else
    [[ "$spin_rc" == 124 ]]
    if grep -Fq 'DRAINED' "cleanup-proof/unmocked-$version.txt"; then exit 1; fi
  fi
done
