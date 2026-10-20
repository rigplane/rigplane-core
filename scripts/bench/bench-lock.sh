#!/usr/bin/env bash
# Per-radio bench lock: one job per radio at a time (serial ports are exclusive,
# and the LAN radio takes one server).
#
#   bench-lock.sh acquire RADIO TOKEN   0 = acquired, 3 = busy
#   bench-lock.sh release RADIO TOKEN   0 = released, 1 = held by another token
#
# The lock is the directory $BENCH_LOCK_DIR/RADIO.lock (mkdir is atomic). Its
# owner file holds "<epoch-seconds> <token>". A lock older than
# $BENCH_LOCK_STALE_SECONDS is taken over, so a killed job cannot block a radio
# indefinitely. Only processes that use this script see the lock.
set -euo pipefail

LOCK_ROOT="${BENCH_LOCK_DIR:-/tmp/rigplane-bench-locks}"
STALE_SECONDS="${BENCH_LOCK_STALE_SECONDS:-7200}"

usage() { echo "usage: bench-lock.sh acquire|release RADIO TOKEN" >&2; exit 2; }
[ "$#" -eq 3 ] || usage
action="$1" radio="$2" token="$3"
lock="$LOCK_ROOT/$radio.lock"

owner_token() { awk '{print $2}' "$lock/owner" 2>/dev/null || true; }

try_take() {
    mkdir "$lock" 2>/dev/null || return 1
    echo "$(date +%s) $token" > "$lock/owner"
}

case "$action" in
    acquire)
        if [ ! -d "$LOCK_ROOT" ]; then
            mkdir -p "$LOCK_ROOT"
            chmod 1777 "$LOCK_ROOT" 2>/dev/null || true
        fi
        if try_take; then echo "bench lock: $radio acquired"; exit 0; fi
        since="$(awk '{print $1}' "$lock/owner" 2>/dev/null || echo "")"
        now="$(date +%s)"
        if [ -n "$since" ] && [ $((now - since)) -gt "$STALE_SECONDS" ]; then
            echo "bench lock: $radio lock from $(owner_token) is stale; taking over" >&2
            rm -rf "$lock" 2>/dev/null || true
            if try_take; then echo "bench lock: $radio acquired"; exit 0; fi
        fi
        echo "bench lock: $radio busy (held by $(owner_token))"
        exit 3
        ;;
    release)
        [ -d "$lock" ] || { echo "bench lock: $radio not held"; exit 0; }
        if [ "$(owner_token)" != "$token" ]; then
            echo "bench lock: $radio held by $(owner_token), not $token; not released" >&2
            exit 1
        fi
        rm -rf "$lock"
        echo "bench lock: $radio released"
        ;;
    *) usage ;;
esac
