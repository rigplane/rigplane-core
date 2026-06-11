#!/usr/bin/env bash
# validate-release.sh — pre-release validation runner (MOR-648).
#
# Runs the `rigplane validate` matrix for the owned radios (IC-7610, X6200,
# FTX-1). The DEFAULT path is CI-safe: native dry-run only, gated against the
# committed golden artifacts in tests/golden/validation/. No hardware is ever
# touched unless BOTH the --hardware flag AND the
# RIGPLANE_VALIDATION_ALLOW_HARDWARE=1 environment variable are present
# (mirroring the CLI's own triple gate), and the per-radio connection
# environment is configured.
#
# Usage:
#   scripts/validate-release.sh                 # dry-run gates (CI-safe, default)
#   scripts/validate-release.sh --hardware      # + hardware runs (opt-in, see below)
#   scripts/validate-release.sh --help
#
# Hardware opt-in (per radio; unconfigured radios are skipped with a note):
#   RIGPLANE_VALIDATION_ALLOW_HARDWARE=1        required for any hardware run
#   IC-7610:  ICOM_HOST, ICOM_USER, ICOM_PASS_FILE   (LAN, CI-V 0x98)
#   X6200:    X6200_SERIAL_PORT                       (serial 19200, CI-V 0xA4)
#   FTX-1:    FTX1_SERIAL_PORT                        (yaesu-cat 38400)
#
# Exit code: non-zero if any executed step fails (golden-gate regression,
# matrix generation error, or hardware failure). Skips never fail the run.
#
# Idempotent: writes only timestamped artifacts under /tmp on hardware runs;
# never modifies the repository.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GOLDEN_DIR="tests/golden/validation"
HARDWARE=0

for arg in "$@"; do
    case "$arg" in
        --hardware) HARDWARE=1 ;;
        --help|-h)
            sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg (see --help)" >&2
            exit 2
            ;;
    esac
done

# slug:model pairs — slug names the golden file, model is the --model value.
RADIOS=(
    "ic7610:IC-7610"
    "x6200:X6200"
    "ftx1:FTX-1"
)

FAILURES=0
note() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Phase 1 — native dry-run golden gates (always runs; CI-safe, no hardware)
# ---------------------------------------------------------------------------
note "Phase 1: native dry-run golden gates"

for entry in "${RADIOS[@]}"; do
    slug="${entry%%:*}"
    model="${entry#*:}"
    golden="$GOLDEN_DIR/$slug.dry-run.json"

    if [[ -f "$golden" ]]; then
        note "$model: dry-run gate vs $golden"
        if uv run rigplane --model "$model" validate --provider native \
                --dry-run --gate "$golden"; then
            echo "[$model] gate: PASS"
        else
            echo "[$model] gate: FAIL (regression vs golden)" >&2
            FAILURES=$((FAILURES + 1))
        fi
    else
        # No committed golden yet (only ic7610 is committed today). Still run
        # the plain dry-run so a matrix-generation error is caught, but skip
        # the gate. Committing the golden is a separate task — do NOT regen
        # into the repo from this script.
        note "$model: no committed golden ($golden) — running ungated dry-run"
        if uv run rigplane --model "$model" validate --provider native \
                --dry-run > /dev/null; then
            echo "[$model] dry-run: OK (gate SKIPPED — no golden committed)"
            echo "[$model] to create one:" \
                "uv run rigplane --model $model validate --dry-run" \
                "--regen-golden $golden"
        else
            echo "[$model] dry-run: FAIL (matrix generation error)" >&2
            FAILURES=$((FAILURES + 1))
        fi
    fi
done

# ---------------------------------------------------------------------------
# Phase 2 — hardware validation (opt-in only; never runs in CI)
# ---------------------------------------------------------------------------
if [[ "$HARDWARE" -eq 1 ]]; then
    if [[ "${RIGPLANE_VALIDATION_ALLOW_HARDWARE:-}" != "1" ]]; then
        echo "Error: --hardware also requires RIGPLANE_VALIDATION_ALLOW_HARDWARE=1" >&2
        exit 2
    fi
    note "Phase 2: hardware validation (RMVR, no TX/tuner auto-actuation)"
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

    # IC-7610 — LAN (CI-V 0x98). Needs ICOM_HOST/ICOM_USER/ICOM_PASS_FILE.
    if [[ -n "${ICOM_HOST:-}" && -n "${ICOM_USER:-}" && -n "${ICOM_PASS_FILE:-}" ]]; then
        note "IC-7610: hardware run via LAN ($ICOM_HOST)"
        if uv run rigplane --backend lan --pass-file "$ICOM_PASS_FILE" \
                --model IC-7610 --radio-addr 0x98 --timeout 6 \
                validate --hardware --allow-hardware --provider native \
                --json --output "/tmp/ic7610-hw-$STAMP.json"; then
            echo "[IC-7610] hardware: OK (/tmp/ic7610-hw-$STAMP.json)"
        else
            echo "[IC-7610] hardware: FAIL" >&2
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "[IC-7610] hardware: SKIPPED (set ICOM_HOST, ICOM_USER, ICOM_PASS_FILE)"
    fi

    # X6200 — serial (19200 baud, CI-V 0xA4). Port is exclusive: close
    # RigPlane Pro / other clients first.
    if [[ -n "${X6200_SERIAL_PORT:-}" ]]; then
        note "X6200: hardware run via serial ($X6200_SERIAL_PORT)"
        if uv run rigplane --backend serial \
                --serial-port "$X6200_SERIAL_PORT" \
                --serial-baud "${X6200_SERIAL_BAUD:-19200}" \
                --model X6200 --radio-addr 0xA4 --timeout 6 \
                validate --hardware --allow-hardware --provider native \
                --json --output "/tmp/x6200-hw-$STAMP.json"; then
            echo "[X6200] hardware: OK (/tmp/x6200-hw-$STAMP.json)"
        else
            echo "[X6200] hardware: FAIL" >&2
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "[X6200] hardware: SKIPPED (set X6200_SERIAL_PORT)"
    fi

    # FTX-1 — Yaesu CAT serial (38400 baud).
    if [[ -n "${FTX1_SERIAL_PORT:-}" ]]; then
        note "FTX-1: hardware run via yaesu-cat ($FTX1_SERIAL_PORT)"
        if uv run rigplane --backend yaesu-cat \
                --serial-port "$FTX1_SERIAL_PORT" \
                --serial-baud "${FTX1_SERIAL_BAUD:-38400}" \
                --model FTX-1 --timeout 6 \
                validate --hardware --allow-hardware --provider native \
                --json --output "/tmp/ftx1-hw-$STAMP.json"; then
            echo "[FTX-1] hardware: OK (/tmp/ftx1-hw-$STAMP.json)"
        else
            echo "[FTX-1] hardware: FAIL" >&2
            FAILURES=$((FAILURES + 1))
        fi
    else
        echo "[FTX-1] hardware: SKIPPED (set FTX1_SERIAL_PORT)"
    fi
else
    note "Phase 2: hardware validation SKIPPED (opt-in: --hardware +" \
        "RIGPLANE_VALIDATION_ALLOW_HARDWARE=1; see docs/validation/running-validation.md)"
fi

# ---------------------------------------------------------------------------
note "Summary"
if [[ "$FAILURES" -gt 0 ]]; then
    echo "validate-release: $FAILURES step(s) FAILED" >&2
    exit 1
fi
echo "validate-release: all executed steps passed"
