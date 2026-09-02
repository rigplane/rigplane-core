#!/usr/bin/env bash
set -euo pipefail
echo "head_sha=$GITHUB_SHA runner_name=$RUNNER_NAME source_sha=509e9d2c4743e507bddac317ad8a79c779de24f5"
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
  set +e
  timeout 60s uv run pytest tests/test_managed_tx_authority.py tests/test_managed_tx_effect_lane.py tests/test_managed_tx_fence.py -vv --tb=short --timeout=10 --timeout-method=thread 2>&1 | tee "cleanup-proof/bounded-$version.txt"
  test_rc=${PIPESTATUS[0]}
  timeout 5s uv run python .github/diagnostics/cleanup-drain-spin.py 2>&1 | tee "cleanup-proof/unmocked-$version.txt"
  spin_rc=${PIPESTATUS[0]}
  set -e
  echo "python=$version bounded_rc=$test_rc unmocked_rc=$spin_rc" | tee "cleanup-proof/status-$version.txt"
  [[ "$test_rc" == 0 ]]
  grep -F '86 passed' "cleanup-proof/bounded-$version.txt"
  grep -F 'CHECKED cleanup_done=True cleanup_owned=True discard_pending=True' "cleanup-proof/unmocked-$version.txt"
  [[ "$spin_rc" == 0 ]]
  grep -F 'DRAINED' "cleanup-proof/unmocked-$version.txt"
done
uv run ruff check src/ tests/ 2>&1 | tee cleanup-proof/lint.txt
uv run ruff format --check --diff src/ tests/ 2>&1 | tee cleanup-proof/format.txt
uv run lint-imports 2>&1 | tee cleanup-proof/imports.txt
uv run mypy --strict src/rigplane/web 2>&1 | tee cleanup-proof/mypy.txt
