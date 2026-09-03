#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 5 ]]; then
  echo "usage: $0 CONTROL_ROOT TARGET_ROOT CORE FRONTEND CI" >&2
  exit 2
fi

control_root=$1
target_root=$2
core=$3
frontend=$4
ci=$5

for value in "$core" "$frontend" "$ci"; do
  if [[ "$value" != true && "$value" != false ]]; then
    echo "route inputs must be literal true or false" >&2
    exit 2
  fi
done
if [[ "$core" != true && "$frontend" != true && "$ci" != true ]]; then
  echo "non-documentation worker received no substantive carrier" >&2
  exit 2
fi

"$control_root/.github/scripts/verify-immutable-controls-v1.sh" \
  "$control_root" "$target_root"

if [[ "$ci" == true ]]; then
  node --test "$control_root/.github/scripts/agent-review-gate.test.js"
  GATE_TARGET_ROOT="$target_root" \
    node --test "$control_root/.github/scripts/base-controlled-gates-v1.test.js"
  python3 "$target_root/.github/scripts/run-focused-checks.test.py"
  python3 "$target_root/.github/scripts/quick-path-filters.test.py"
  python3 -m py_compile "$target_root"/.github/scripts/*.py
fi

cd "$target_root"
export UV_PYTHON=3.11

if [[ "$core" == true ]]; then
  uv python install 3.11
  uv sync --all-extras
  uv run ruff check src/ tests/
  uv run ruff format --check src/ tests/
  uv run lint-imports
  uv run rigplane --model IC-7610 validate --provider native --dry-run \
    --gate tests/golden/validation/ic7610.dry-run.json
  uv run rigplane --model X6200 validate --provider native --dry-run \
    --gate tests/golden/validation/x6200.dry-run.json
  uv run rigplane --model FTX-1 validate --provider native --dry-run \
    --gate tests/golden/validation/ftx1.dry-run.json
fi

if [[ "$frontend" == true ]]; then
  (
    cd frontend
    npm ci
    npm run i18n:check
    npm run check
    npx vitest run
    npm run build
  )
  rm -f src/rigplane/web/static
  mkdir -p src/rigplane/web/static
  cp -r frontend/dist/* src/rigplane/web/static/
  (
    cd frontend
    npx playwright install chromium
    npm run test:e2e:i18n
  )
  node frontend/fixtures/capture.mjs
fi

if [[ "$core" == true && "$frontend" == true ]]; then
  uv run mypy --strict src/rigplane/web
fi

if [[ "$core" == true ]]; then
  set +e
  uv run pytest tests/ -n auto --tb=short --timeout=300 --timeout-method=thread \
    | tee pytest-output.txt
  test_rc=${PIPESTATUS[0]}
  set -e
  exit "$test_rc"
fi
