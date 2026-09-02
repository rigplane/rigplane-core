#!/usr/bin/env bash
set -euo pipefail

case "$RUNNER_NAME" in
  mm-build-core-1|mm-build-core-2) ;;
  *) echo 'Rejected non-Mini runner'; exit 2 ;;
esac
mkdir -p mutation-proof
echo "proof_sha=$GITHUB_SHA source_sha=03ce10b17a703def4d617760b98a0df988fecc70 runner=$RUNNER_NAME"
authority=tests/test_managed_tx_authority.py
fence=tests/test_managed_tx_fence.py
controls=(
  "$fence::test_force_off_invalidates_at_call_and_keeps_each_batch_epoch"
  "$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[force_off]"
  "$authority::test_real_fence_releases_before_cleanup_and_shutdown_waits_for_cleanup[offline_shutdown]"
  "$authority::test_late_physical_on_retains_debt_until_a_fresh_off"
  "$authority::test_cancelled_close_can_be_rejoined_until_cleanup_finishes"
  "$authority::test_ptt_up_does_not_cancel_unrelated_registered_work"
)
uv run pytest "${controls[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee mutation-proof/control-before.txt

for mutation in cleanup-wait deferred-invalidation post-off-isolation lock-held-cleanup; do
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
  esac
  patch_file=".github/diagnostics/$mutation.patch"
  git apply --check "$patch_file"
  git apply "$patch_file"
  echo "mutation=$mutation expected_failures=$expected nodes=${nodes[*]}"
  set +e
  uv run pytest "${nodes[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee "mutation-proof/$mutation.txt"
  result=${PIPESTATUS[0]}
  set -e
  git apply --reverse "$patch_file"
  git diff --exit-code -- src/rigplane/runtime/managed_tx_authority.py src/rigplane/runtime/managed_tx_fence.py src/rigplane/runtime/managed_tx_effect_lane.py
  echo "mutation=$mutation pytest_rc=$result restored=true"
  test "$result" -eq 1
  grep -Eq "[[:space:]]$expected failed in " "mutation-proof/$mutation.txt"
done

uv run pytest "${controls[@]}" -vv --tb=short --timeout=15 --timeout-method=thread 2>&1 | tee mutation-proof/control-after.txt
git diff --exit-code -- src/ tests/
