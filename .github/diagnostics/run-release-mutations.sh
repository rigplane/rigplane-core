#!/usr/bin/env bash
set -euo pipefail

case "$RUNNER_NAME" in
  mm-build-core-1|mm-build-core-2) ;;
  *) echo 'Rejected non-Mini runner'; exit 2 ;;
esac
mkdir -p mutation-proof
echo "proof_sha=$GITHUB_SHA source_sha=509e9d2c4743e507bddac317ad8a79c779de24f5 runner=$RUNNER_NAME"
uv run python --version
authority=tests/test_managed_tx_authority.py
fence=tests/test_managed_tx_fence.py
controls=(
  "$fence::test_force_off_invalidates_at_call_and_keeps_each_batch_epoch"
  "$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[force_off]"
  "$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[offline_shutdown]"
  "$authority::test_late_physical_on_retains_debt_until_a_fresh_off"
  "$authority::test_cancelled_close_can_be_rejoined_until_cleanup_finishes"
  "$authority::test_ptt_up_does_not_cancel_unrelated_registered_work"
  "$authority::test_cleanup_drain_reaps_done_task_before_queued_discard"
  "$authority::test_startup_wait_failure_leaves_no_pending_tasks"
  "tests/test_managed_tx_effect_lane.py::test_force_receive_awaits_isolation_and_maps_failure[already_isolated-True]"
)
timeout 45s uv run pytest "${controls[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee mutation-proof/control-before.txt
grep -F '12 passed' mutation-proof/control-before.txt

for mutation in cleanup-wait deferred-invalidation post-off-isolation lock-held-cleanup callback-only-drain; do
  case "$mutation" in
    cleanup-wait|lock-held-cleanup)
      nodes=("$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[force_off]")
      expected=1 ;;
    deferred-invalidation)
      nodes=("$fence::test_force_off_invalidates_at_call_and_keeps_each_batch_epoch"
             "$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[offline_shutdown]")
      expected=2 ;;
    post-off-isolation)
      nodes=("$authority::test_late_physical_on_retains_debt_until_a_fresh_off")
      expected=2 ;;
    callback-only-drain)
      nodes=("$authority::test_cleanup_drain_reaps_done_task_before_queued_discard")
      expected=1 ;;
  esac
  patch_file=".github/diagnostics/$mutation.patch"
  git apply --check "$patch_file"
  git apply "$patch_file"
  echo "mutation=$mutation expected_failures=$expected nodes=${nodes[*]}"
  set +e
  timeout 45s uv run pytest "${nodes[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee "mutation-proof/$mutation.txt"
  result=${PIPESTATUS[0]}
  set -e
  git apply --reverse "$patch_file"
  git diff --exit-code -- src/rigplane/runtime/managed_tx_authority.py src/rigplane/runtime/managed_tx_fence.py src/rigplane/runtime/managed_tx_effect_lane.py
  echo "mutation=$mutation pytest_rc=$result restored=true"
  test "$result" -eq 1
  grep -Eq "[[:space:]]$expected failed in " "mutation-proof/$mutation.txt"
  if [[ "$mutation" == callback-only-drain ]]; then
    grep -F 'drain awaited completed owned cleanup before queued discard' "mutation-proof/$mutation.txt"
  fi
done

timeout 45s uv run pytest "${controls[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee mutation-proof/control-after.txt
grep -F '12 passed' mutation-proof/control-after.txt
git diff --exit-code -- src/ tests/
