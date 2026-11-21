#!/usr/bin/env bash
# Per-radio bench lock: one job per radio at a time (serial ports are exclusive,
# and the LAN radio takes one server).
#
#   bench-lock.sh acquire RADIO TOKEN   0 = acquired, 3 = busy
#   bench-lock.sh release RADIO TOKEN   0 = released, 1 = held by another token
#
# The lock is the directory $BENCH_LOCK_DIR/RADIO.lock (mkdir is atomic). Its
# owner file holds "<epoch-seconds> <token> [holder-pid]": field 3 is optional
# and only meaningful when the writer knows the long-lived holder's pid (this
# one-shot script does not record one — in CI its parent step shell exits
# before the bench job ends, so a recorded step-shell pid would go dead while
# the radio is still in use). A lock is taken over when any of these holds, so
# a killed job cannot block a radio indefinitely:
#   - the owner epoch is older than $BENCH_LOCK_STALE_SECONDS, or
#   - there is no usable owner file and the lock directory's mtime is older
#     than $BENCH_LOCK_STALE_SECONDS (an ownerless lock cannot prove a live
#     holder), or
#   - the owner file records a holder pid that is not alive.
# Only processes that use this script see the lock.
set -euo pipefail

LOCK_ROOT="${BENCH_LOCK_DIR:-/tmp/rigplane-bench-locks}"
STALE_SECONDS="${BENCH_LOCK_STALE_SECONDS:-7200}"

usage() { echo "usage: bench-lock.sh acquire|release RADIO TOKEN" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
action="$1" radio="$2" token="$3"
lock="$LOCK_ROOT/$radio.lock"

owner_token() { awk '{print $2}' "$lock/owner" 2>/dev/null || true; }
owner_pid() { awk '{print $3}' "$lock/owner" 2>/dev/null || true; }

# is_uint V: 0 = non-empty all-decimal digits within arithmetic range (so
# $((10#...)) is always safe; longer strings could wrap and fake an ancient
# epoch).
is_uint() { case "$1" in ''|*[!0-9]*) return 1 ;; *) [ "${#1}" -le 12 ] ;; esac }

try_take() {
    mkdir "$lock" 2>/dev/null || return 1
    echo "$(date +%s) $token" > "$lock/owner"
    # Confirm our write won the micro-window between mkdir and the owner
    # write: a same-path contender must never leave both callers acquired.
    [ "$(owner_token)" = "$token" ]
}

# pid_state PID: alive | dead | unknown. Only ps's definitive "no such
# process" (exit 1) is proof of death; any other failure to observe must not
# authorize a takeover.
pid_state() {
    command -v ps >/dev/null 2>&1 || { echo unknown; return; }
    if ps -p "$1" >/dev/null 2>&1; then
        echo alive
    elif [ "$?" -eq 1 ]; then
        echo dead
    else
        echo unknown
    fi
}

# lock_is_stale NOW: 0 = the existing lock may be taken over, 1 = held live.
lock_is_stale() {
    local now="$1" since mtime pid state
    since="$(awk '{print $1}' "$lock/owner" 2>/dev/null || echo "")"
    if is_uint "$since"; then
        since=$((10#$since))  # 10#: a leading zero must not parse as octal
        [ $((now - since)) -gt "$STALE_SECONDS" ] && return 0
    else
        # No usable owner file: age by the directory mtime, which no live
        # holder would leave untouched past the stale bound.
        mtime="$(stat -c %Y "$lock" 2>/dev/null || stat -f %m "$lock" 2>/dev/null || echo "")"
        if is_uint "$mtime"; then
            mtime=$((10#$mtime))
            [ $((now - mtime)) -gt "$STALE_SECONDS" ] && return 0
        fi
    fi
    pid="$(owner_pid)"
    if is_uint "$pid" && [ "$((10#$pid))" -gt 1 ]; then
        # A pid can be reused; that only delays takeover until the epoch is
        # stale, never takes a live radio early.
        state="$(pid_state "$pid")"
        [ "$state" = dead ] && return 0
    fi
    return 1
}

# Stale takeover is check-then-remove, so clearers must serialize or two of
# them can both pass the re-check and delete each other's fresh lock. There is
# no flock(1) on macOS: a mkdir guard is the atomic primitive. A leftover
# guard is reclaimed once it is older than $GUARD_RECLAIM_SECONDS (a holder
# keeps it for milliseconds; the short bound caps how long a clearer killed
# mid-critical-section can block other clearers, and the lock itself still
# self-heals through the normal stale takeover).
# Residual race, accepted for bench tooling: two reclaimers can both observe
# an aged guard and both enter the critical section; the 60 s reclaim bound
# keeps that window negligible, and takeover_if_unchanged's content re-check
# turns it into a refused takeover, not a double acquire. The guard is
# per-radio: one radio's stuck guard must never block another radio's
# release or takeover.
CLEAR_GUARD="$LOCK_ROOT/.stale-clear.$radio.guard"
GUARD_RECLAIM_SECONDS=60

takeover_if_unchanged() {
    # Runs with the clear guard held: $snapshot is the owner content that was
    # classified stale; remove and re-take only if it is still exactly that.
    [ "$(cat "$lock/owner" 2>/dev/null || true)" = "$snapshot" ] || return 1
    echo "bench lock: $radio lock from $(owner_token) is stale; taking over" >&2
    rm -rf "$lock" 2>/dev/null || true
    if try_take; then echo "bench lock: $radio acquired"; return 0; fi
    return 1
}

with_clear_guard() {
    local attempt now mtime
    for attempt in 1 2 3 4 5; do
        if mkdir "$CLEAR_GUARD" 2>/dev/null; then
            "$@"
            local rc=$?
            rm -rf "$CLEAR_GUARD"
            return $rc
        fi
        now="$(date +%s)"
        mtime="$(stat -c %Y "$CLEAR_GUARD" 2>/dev/null || stat -f %m "$CLEAR_GUARD" 2>/dev/null || echo "")"
        # A guard that vanished before the stat is somebody's fresh guard:
        # not reclaimable. Only a readable, aged mtime authorizes reclaim.
        if is_uint "$mtime"; then
            mtime=$((10#$mtime))
            [ $((now - mtime)) -gt "$GUARD_RECLAIM_SECONDS" ] && { rm -rf "$CLEAR_GUARD" 2>/dev/null || true; continue; }
        fi
        sleep 0.1
    done
    return 1
}

case "$action" in
    acquire)
        if [ ! -d "$LOCK_ROOT" ]; then
            mkdir -p "$LOCK_ROOT"
            chmod 1777 "$LOCK_ROOT" 2>/dev/null || true
        fi
        if try_take; then echo "bench lock: $radio acquired"; exit 0; fi
        now="$(date +%s)"
        snapshot="$(cat "$lock/owner" 2>/dev/null || true)"
        if lock_is_stale "$now" && with_clear_guard takeover_if_unchanged; then
            exit 0
        fi
        echo "bench lock: $radio busy (held by $(owner_token))"
        exit 3
        ;;
    release)
        [ -d "$lock" ] || { echo "bench lock: $radio not held"; exit 0; }
        release_if_unchanged() {
            # Runs with the clear guard held: a stale clearer mid-takeover
            # must not have the lock snatched between the token check and
            # the removal.
            if [ "$(owner_token)" != "$token" ]; then
                echo "bench lock: $radio held by $(owner_token), not $token; not released" >&2
                return 1
            fi
            if rm -rf "$lock" 2>/dev/null; then
                echo "bench lock: $radio released"
                return 0
            fi
            echo "bench lock: $radio could not be removed" >&2
            return 1
        }
        with_clear_guard release_if_unchanged && exit 0
        exit 1  # message printed by release_if_unchanged (another token)
        ;;
    *) usage ;;
esac
