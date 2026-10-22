#!/usr/bin/env bash
# Bench nightly safety guard: no TX and no tuner, ever.
#
#   guard.sh check-env           fail if any TX switch is present in the environment
#   guard.sh run -- CMD [ARG...]  check the environment and CMD's argv, then exec CMD
#
# A switch counts as present when the variable is set at all, whatever its value.
# An argv token is refused when it is a prefix (of three characters or more) of a
# forbidden flag, because argparse accepts unambiguous abbreviations.
# Exit codes: 0 ok (or CMD's own code), 1 refused, 2 usage error.
set -euo pipefail

FORBIDDEN_ENV="ICOM_ALLOW_PTT ICOM_ALLOW_CW_TX ICOM_ALLOW_AUDIO_TX ICOM_ALLOW_POWER_CONTROL RIGPLANE_HW_ALLOW_TX"
FORBIDDEN_FLAGS="--tx-allowed --tuner-allowed"

check_env() {
    local name refused=0
    for name in $FORBIDDEN_ENV; do
        if printenv "$name" >/dev/null; then
            echo "bench guard: $name is set; TX switches are forbidden on the bench" >&2
            refused=1
        fi
    done
    return "$refused"
}

check_argv() {
    local arg name flag refused=0
    for arg in "$@"; do
        name="${arg%%=*}"
        case "$name" in --?*) ;; *) continue ;; esac
        for flag in $FORBIDDEN_FLAGS; do
            case "$flag" in
                "$name"*)
                    echo "bench guard: argument '$arg' selects $flag; forbidden on the bench" >&2
                    refused=1
                    ;;
            esac
        done
    done
    return "$refused"
}

case "${1:-}" in
    check-env)
        check_env || exit 1
        echo "bench guard: environment clean"
        ;;
    run)
        shift
        [ "${1:-}" = "--" ] && shift
        [ "$#" -gt 0 ] || { echo "usage: guard.sh run -- CMD [ARG...]" >&2; exit 2; }
        env_ok=0
        argv_ok=0
        check_env || env_ok=1
        check_argv "$@" || argv_ok=1
        [ "$env_ok" -eq 0 ] && [ "$argv_ok" -eq 0 ] || exit 1
        exec "$@"
        ;;
    *)
        echo "usage: guard.sh check-env | guard.sh run -- CMD [ARG...]" >&2
        exit 2
        ;;
esac
