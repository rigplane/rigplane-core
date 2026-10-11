#!/usr/bin/env bash
# Plain-bash tests for scripts/bench/guard.sh and scripts/bench/bench-lock.sh.
# Run: bash scripts/bench/test_bench_shell.sh  (bash 3.2 compatible, as on macOS)
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GUARD="$HERE/guard.sh"
LOCK="$HERE/bench-lock.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PASSED=0
FAILED=0

ok() { PASSED=$((PASSED + 1)); echo "ok   - $1"; }
bad() { FAILED=$((FAILED + 1)); echo "FAIL - $1"; }

# expect <wanted-rc> <description> <command...>
expect() {
    local want="$1" desc="$2"
    shift 2
    "$@" >"$WORK/out" 2>&1
    local got=$?
    if [ "$got" -eq "$want" ]; then ok "$desc"; else bad "$desc (rc=$got, want $want)"; cat "$WORK/out"; fi
}

# Clean environment for the guard: only PATH survives.
clean() { env -i PATH="$PATH" "$@"; }

# --- guard: environment switches -------------------------------------------
expect 0 "guard passes a clean environment" clean bash "$GUARD" check-env
for name in ICOM_ALLOW_PTT ICOM_ALLOW_CW_TX ICOM_ALLOW_AUDIO_TX ICOM_ALLOW_POWER_CONTROL RIGPLANE_HW_ALLOW_TX; do
    expect 1 "guard check-env fails when $name=1" clean "$name=1" bash "$GUARD" check-env
    expect 1 "guard check-env fails when $name is set empty" clean "$name=" bash "$GUARD" check-env
    rm -f "$WORK/ran"
    expect 1 "guard run refuses to start a command when $name=0" \
        clean "$name=0" bash "$GUARD" run -- touch "$WORK/ran"
    if [ -e "$WORK/ran" ]; then bad "command ran despite $name"; else ok "command not started with $name set"; fi
done

# --- guard: argv flags -------------------------------------------------------
for flag in --tx-allowed --tuner-allowed --tx-al --tun --tuner-allowed=1; do
    rm -f "$WORK/ran"
    expect 1 "guard run refuses argv containing $flag" \
        clean bash "$GUARD" run -- touch "$WORK/ran" "$flag"
    if [ -e "$WORK/ran" ]; then bad "command ran despite $flag"; else ok "command not started with $flag"; fi
done
rm -f "$WORK/ran"
expect 0 "guard run executes a benign read-only command" \
    clean bash "$GUARD" run -- touch "$WORK/ran" --timeout 6 --template t --read-only --hardware
if [ -e "$WORK/ran" ]; then ok "benign command ran"; else bad "benign command did not run"; fi
expect 2 "guard rejects an unknown mode" clean bash "$GUARD" bogus

# --- lock ---------------------------------------------------------------------
export BENCH_LOCK_DIR="$WORK/locks"
expect 0 "lock: first acquire succeeds" bash "$LOCK" acquire ic7300 run-1
expect 3 "lock: second acquire by another owner reports busy" bash "$LOCK" acquire ic7300 run-2
expect 0 "lock: other radios are independent" bash "$LOCK" acquire ftx1 run-2
expect 1 "lock: release by a non-owner is refused" bash "$LOCK" release ic7300 run-2
expect 3 "lock: still held after refused release" bash "$LOCK" acquire ic7300 run-3
expect 0 "lock: owner releases" bash "$LOCK" release ic7300 run-1
expect 0 "lock: acquire after release succeeds" bash "$LOCK" acquire ic7300 run-3
# Age the lock past the stale threshold: a dead job must not block forever.
echo "0 run-3" > "$BENCH_LOCK_DIR/ic7300.lock/owner"
expect 0 "lock: a stale lock is taken over" bash "$LOCK" acquire ic7300 run-4
expect 0 "lock: the new owner can release it" bash "$LOCK" release ic7300 run-4
expect 2 "lock: missing arguments are a usage error" bash "$LOCK" acquire

echo "passed=$PASSED failed=$FAILED"
[ "$FAILED" -eq 0 ]
