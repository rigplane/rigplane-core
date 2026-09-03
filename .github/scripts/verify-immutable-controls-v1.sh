#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CONTROL_ROOT TARGET_ROOT" >&2
  exit 2
fi

control_root=$1
target_root=$2

declare -A expected_modes=(
  [.github/scripts/agent-review-gate.js]=100644
  [.github/scripts/base-controlled-gates-v1.test.js]=100644
  [.github/scripts/base-gate-policy-v1.js]=100644
  [.github/scripts/quick-v2-worker-v1.sh]=100755
  [.github/scripts/verify-immutable-controls-v1.sh]=100755
  [.github/workflows/agent-review-gate-v2.yml]=100644
  [.github/workflows/quick-v2.yml]=100644
)

for path in "${!expected_modes[@]}"; do
  control_entry=$(git -C "$control_root" ls-tree HEAD -- "$path")
  target_entry=$(git -C "$target_root" ls-tree HEAD -- "$path")
  expected_prefix="${expected_modes[$path]} blob "
  if [[ -z "$control_entry" || "$control_entry" != "$expected_prefix"* ]]; then
    echo "trusted immutable control has unexpected Git type or mode: $path" >&2
    exit 1
  fi
  if [[ "$target_entry" != "$control_entry" ]]; then
    echo "immutable control content, type, or mode changed: $path" >&2
    echo "trusted: $control_entry" >&2
    echo "target:  ${target_entry:-missing}" >&2
    exit 1
  fi
done
