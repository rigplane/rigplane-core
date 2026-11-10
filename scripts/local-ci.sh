#!/usr/bin/env bash
# Pre-push quick gate for rigplane-core (MOR-4372): the cheap checks `quick.yml` runs, on the
# changed files only, before a push costs a ~13 minute CI round. Same mechanism and same exit
# codes as rigplane-pro's `scripts/local-ci.sh --pre-push` (MOR-4101); `pre-review.sh` calls it
# in the worktree before it spends a Codex round.
#
# Usage:
#   scripts/local-ci.sh --pre-push [--base REF]     # REF defaults to origin/main
#
# Exit code: 0 all checks passed, 1 a check failed (a real failure: fix, commit, rerun),
# 2 the checks could not run (no `mini-test`, unreachable test host, bad usage): CI stays the gate.
#
# It never runs checks on the machine it is called from. The orchestrator hands the worktree to a
# test host through `mini-test --run` (slot admission, cancellation, busy-directory protection);
# PRE_PUSH_HOST overrides the host (default the Mac mini). The host prints a final
# `PRE-PUSH-HOST: PASS|FAIL` line; no such line means it never reached a verdict.
#
# What runs on the host is decided by scripts/pre_push_scope.py from the diff against the merge
# base, through CI's own path classifier:
#   python    ruff == uv.lock version, ruff check/format, lint-imports, validation dry-run goldens
#   mypy-web  mypy --strict src/rigplane/web (CI: only when core and frontend are both selected)
#   always    the tests that mention a changed file (a changed test file counts as mentioning)
#   frontend  npm ci (cached by package.json + lockfile hash), lint, i18n:check, svelte-check, vitest,
#             build, with NODE_OPTIONS=--max-old-space-size=3072 as in quick.yml
# Not covered (they need a browser and run in CI only): the Playwright i18n visual, fixture
# capture, placeholder-guard, chrome-inset, FTX-1 RIT and operator-journey specs.

set -euo pipefail

# ---- Colors -----------------------------------------------------------------

if [[ -t 1 ]]; then
    GREEN=$'\033[0;32m'
    RED=$'\033[0;31m'
    BOLD=$'\033[1m'
    DIM=$'\033[2m'
    RESET=$'\033[0m'
else
    GREEN=""; RED=""; BOLD=""; DIM=""; RESET=""
fi

# ---- Args -------------------------------------------------------------------

usage_error() {
    printf "%s%s%s\n" "$RED" "$1" "$RESET" >&2
    sed -n '2,12p' "$0" >&2
    exit 2
}

PRE_PUSH=0
BASE=origin/main
while [[ $# -gt 0 ]]; do
    arg="$1"; shift
    case "$arg" in
        --pre-push) PRE_PUSH=1 ;;
        --base)
            [[ $# -gt 0 ]] || usage_error "--base needs a ref"
            BASE="$1"; shift ;;
        --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
        *) usage_error "Unknown flag: $arg" ;;
    esac
done
[[ $PRE_PUSH -eq 1 ]] || usage_error "only --pre-push is supported"

# Ensure we're at the repo root (scripts/ sits one level deep).
cd "$(dirname "$0")/.."

# ---- Step runner ------------------------------------------------------------

declare -a STEPS_NAME STEPS_STATUS STEPS_TIME
FAILED=0
INFRA_FAIL=0
TOTAL_START=$(date +%s)

run_step() {
    local name="$1"; shift
    printf "%s▶ %s%s\n" "$BOLD" "$name" "$RESET"
    local step_start step_end dur rc=0
    step_start=$(date +%s)
    set +e
    "$@"
    rc=$?
    set -e
    step_end=$(date +%s)
    dur=$((step_end - step_start))
    STEPS_NAME+=("$name")
    STEPS_TIME+=("${dur}s")
    if [[ $rc -eq 0 ]]; then
        STEPS_STATUS+=("PASS")
        printf "  %s✓ PASS%s %s(%ss)%s\n\n" "$GREEN" "$RESET" "$DIM" "$dur" "$RESET"
    else
        STEPS_STATUS+=("FAIL")
        FAILED=1
        printf "  %s✗ FAIL%s (rc=%d, %ss)\n\n" "$RED" "$RESET" "$rc" "$dur"
    fi
}

print_summary() {
    local total_end total
    total_end=$(date +%s)
    total=$((total_end - TOTAL_START))
    printf "\n%s════════ Summary ════════%s\n" "$BOLD" "$RESET"
    local i color
    for i in "${!STEPS_NAME[@]}"; do
        color="$GREEN"
        [[ "${STEPS_STATUS[$i]}" == "FAIL" ]] && color="$RED"
        printf "  %s%-46s %s%s  %s(%s)%s\n" \
            "$color" "${STEPS_NAME[$i]}" "${STEPS_STATUS[$i]}" "$RESET" \
            "$DIM" "${STEPS_TIME[$i]}" "$RESET"
    done
    printf "\n%sTotal: %ss%s\n" "$DIM" "$total" "$RESET"
    if [[ $FAILED -eq 1 ]]; then
        printf "%s%sOVERALL: FAIL%s\n" "$BOLD" "$RED" "$RESET"
        exit 1
    fi
    if [[ $INFRA_FAIL -eq 1 ]]; then
        printf "%s%sOVERALL: NOT RUN%s (the checks could not run; CI stays the gate)\n" "$BOLD" "$RED" "$RESET"
        exit 2
    fi
    printf "%s%sOVERALL: PASS%s\n" "$BOLD" "$GREEN" "$RESET"
}
trap print_summary EXIT

# ---- Steps on the test host ---------------------------------------------------

PY_SCOPE=(uv run python scripts/pre_push_scope.py)
LEGS=""

pre_push_select() {
    LEGS="$("${PY_SCOPE[@]}" legs --base "$BASE")" || { echo "check selection failed" >&2; return 1; }
    echo "selected: ${LEGS:-nothing}" | tr '\n' ' '
    echo
}

has_leg() {
    [[ $'\n'"$LEGS"$'\n' == *$'\n'"$1"$'\n'* ]]
}

pre_push_ruff_pin() {
    local out
    out="$(uv run ruff --version)" || return 1
    "${PY_SCOPE[@]}" ruff-pin "$out"
}

# Dry-run golden gates of quick.yml (sub-second, no hardware): one per model.
pre_push_goldens() {
    local spec model file rc=0
    for spec in IC-7610:ic7610 X6200:x6200 FTX-1:ftx1; do
        model="${spec%%:*}"; file="tests/golden/validation/${spec##*:}.dry-run.json"
        echo "validation golden: $model"
        uv run rigplane --model "$model" validate --provider native --dry-run --gate "$file" || rc=1
    done
    return $rc
}

# pytest exit 5 = nothing collected (a selected file that has no test on this platform): not a failure.
pre_push_run_tests() {
    local rc=0
    uv run pytest -p no:cacheprovider -q -n auto --maxprocesses 4 --tb=short \
        --timeout=300 --timeout-method=thread "$@" || rc=$?
    if [[ $rc -eq 5 ]]; then
        echo "no test collected on this platform for the selection"
        return 0
    fi
    return $rc
}

pre_push_related_tests() {
    local files
    files="$("${PY_SCOPE[@]}" related --base "$BASE")" || { echo "test selection failed" >&2; return 1; }
    if [[ -z "$files" ]]; then
        echo "no test mentions a changed file"
        return 0
    fi
    echo "$files"
    # shellcheck disable=SC2086  # one path per line, no spaces in test paths
    pre_push_run_tests $files
}

# node_modules is kept between runs and marked with the hash of the two files `npm ci` reads
# (package.json AND package-lock.json: CI's `npm ci` rejects a manifest the lockfile does not
# cover, so a dependency added to package.json alone must force a fresh install here too).
pre_push_frontend_install() {
    local manifest_hash
    manifest_hash="$(cat frontend/package.json frontend/package-lock.json | shasum | cut -c1-12)"
    (
        cd frontend
        if [[ -d node_modules && "$(cat node_modules/.pre-push-install 2>/dev/null)" == "$manifest_hash" ]]; then
            echo "node_modules matches package.json + package-lock.json (cached)"
        else
            rm -f node_modules/.pre-push-install
            npm ci --no-audit --no-fund && mkdir -p node_modules && echo "$manifest_hash" > node_modules/.pre-push-install
        fi
    )
}

# Each frontend step runs in a subshell so its `cd frontend` does not leak into the next one.
frontend_in() { ( cd frontend && "$@" ); }

pre_push_on_host() {
    run_step "Select checks from the diff" pre_push_select
    if has_leg python; then
        run_step "ruff == uv.lock version"  pre_push_ruff_pin
        run_step "Ruff check"               uv run ruff check src/ tests/
        run_step "Ruff format --check"      uv run ruff format --check src/ tests/
        run_step "Import boundaries"        uv run lint-imports
        run_step "Validation dry-run goldens" pre_push_goldens
    fi
    if has_leg mypy-web; then
        run_step "Mypy --strict (web boundary)" uv run mypy --strict src/rigplane/web
    fi
    run_step "Tests of changed files"       pre_push_related_tests
    if has_leg frontend; then
        # quick.yml: svelte-check needs 1.5-2 GiB of Node heap; 3 GiB leaves headroom.
        export NODE_OPTIONS=--max-old-space-size=3072
        run_step "Frontend install (npm ci)" pre_push_frontend_install
        run_step "Frontend lint"             frontend_in npm run lint
        run_step "Frontend i18n check"       frontend_in npm run i18n:check
        run_step "Frontend svelte-check"     frontend_in npm run check
        run_step "Frontend unit tests"       frontend_in npx vitest run --maxWorkers=4
        run_step "Frontend build"            frontend_in npm run build
    fi
    if [[ $FAILED -eq 1 ]]; then echo "PRE-PUSH-HOST: FAIL"; else echo "PRE-PUSH-HOST: PASS"; fi
}

# ---- Orchestration (the laptop or any non-test host) -----------------------------

# Remote shell script of the host job; $1 = the merge base. UV_PYTHON as in quick.yml (MOR-590),
# `uv sync --all-extras` as in quick.yml (MOR-1978: the extras gate tests that must not skip).
PRE_PUSH_REMOTE='
export UV_PYTHON=3.11
{ uv sync -q --all-extras >/dev/null 2>&1 || uv sync --all-extras; } || { echo "uv sync failed"; exit 1; }
LOCAL_CI_ON_TEST_HOST=1 exec bash scripts/local-ci.sh --pre-push --base "$1"
'

not_run() {
    printf "%s%s%s\n" "$RED" "$1" "$RESET" >&2
    INFRA_FAIL=1
    exit 2
}

pre_push_orchestrate() {
    command -v mini-test >/dev/null 2>&1 || not_run "mini-test not found: --pre-push runs on the test host, never here"
    MERGE_BASE="$(git merge-base "$BASE" HEAD)" || not_run "no merge base with $BASE (git fetch origin main?)"
    local log mini_rc=0 verdict
    log="$(mktemp)"
    MINI_HOST="${PRE_PUSH_HOST:-moroz@192.168.55.152}" \
        mini-test --run -- bash -c "$PRE_PUSH_REMOTE" _ "$MERGE_BASE" > "$log" 2>&1 < /dev/null || mini_rc=$?
    printf "%s▶ test host report%s\n" "$BOLD" "$RESET"
    sed '/Ignoring unknown extended header/d' "$log"
    # The host prints its verdict as the last PRE-PUSH-HOST line, after every step; an earlier
    # such line is test output. The verdict counts only when the executor's status agrees with it
    # (host exits 0 on PASS, 1 on FAIL): a dropped connection or a cancelled run is "not run".
    verdict="$(grep -E '^PRE-PUSH-HOST: (PASS|FAIL)$' "$log" | tail -n 1 || true)"
    STEPS_NAME+=("test host"); STEPS_TIME+=("-")
    if [[ "$verdict" == "PRE-PUSH-HOST: PASS" && $mini_rc -eq 0 ]]; then
        STEPS_STATUS+=("PASS")
    elif [[ "$verdict" == "PRE-PUSH-HOST: FAIL" && $mini_rc -eq 1 ]]; then
        STEPS_STATUS+=("FAIL"); FAILED=1
    else
        STEPS_STATUS+=("FAIL"); INFRA_FAIL=1
    fi
    rm -f "$log"
    exit 0
}

if [[ -n "${LOCAL_CI_ON_TEST_HOST:-}" ]]; then pre_push_on_host; exit 0; fi
pre_push_orchestrate
