#!/usr/bin/env bash
# Wave 2 final grep gate: fail CI if any new icom-lan / icom_lan / IcomLanError
# reference appears outside the allow-list of preserved-by-design files.
#
# Why this exists: the v2.0.0 rebrand from icom-lan -> rigplane (epic
# docs/roadmap/2026-05-04-rigplane-rebrand-epic.md) intentionally PRESERVES a
# small set of brand references — backwards-compat shims, wire contracts (v1
# diagnostic-bundle, LAN discovery magic, local-extensions deprecation
# alias), platformdirs / localStorage migration logic, and a vendor-protocol
# diagram label parallel to IcomSerial / YaesuCAT. Everything else must be
# rebranded. This gate codifies that distinction.
#
# To add a new exception: append the path to ALLOWLIST below with a comment
# explaining WHY the reference is intentional. Anything NOT on the list that
# matches the pattern fails CI.
#
# Usage: run from the repository root with no arguments.

set -euo pipefail

ALLOWLIST=(
    # Shim package (PR-2B) — re-exports rigplane with DeprecationWarning.
    'src/icom_lan/'
    # PR-2C: persistent-state migration logic.
    'src/rigplane/_platformdirs_migration.py'
    'frontend/src/lib/migrate-legacy-storage.ts'
    'frontend/src/lib/__tests__/migrate-legacy-storage.test.ts'
    # Wire contracts (handled per spec — schema v2 in PR-2G; LAN discovery
    # magic stays for backwards-compat with v1 clients until Wave 4).
    'src/rigplane/diagnostics/_manifest.py'
    'src/rigplane/diagnostics/bundle.py'
    'src/rigplane/web/discovery.py'
    'docs/contracts/diagnostic-bundle-v1.md'
    'docs/contracts/diagnostic-bundle-v2.md'
    # Local-extensions deprecation alias (cross-repo Pro contract).
    'frontend/src/lib/local-extensions/host-api.ts'
    'frontend/src/lib/local-extensions/__tests__/host-api.test.ts'
    'frontend/src/lib/api/diagnostics.ts'
    'frontend/src/lib/api/__tests__/diagnostics.test.ts'
    # Vendor-protocol diagram label (parallel to IcomSerial / YaesuCAT —
    # NOT a product brand reference; describes the transport family).
    'src/rigplane/core/radio_protocol.py'
    'docs/radio-protocol.md'
    # Intentional historical / migration references.
    'README.md'
    'docs/CHANGELOG.md'
    # User-facing migration guide for v1.x icom-lan -> v2.x rigplane;
    # the legacy names are the literal subject of the page.
    'docs/migrate.md'
    # mkdocs nav label for the migration guide reads
    # "Migration from icom-lan" -- legacy name is intentional.
    'mkdocs.yml'
    'tests/golden/wsjtx_dual_rx_session.txt'
    # pyproject.toml — preserved console-script alias `icom-lan` (line 43),
    # hatch packages list keeping `src/icom_lan` shim, ruff per-file-ignores
    # for the shim, and mypy override for the shim module.
    'pyproject.toml'
    # Pre-rebrand and historical plans.
    'docs/plans/discovery-artifacts/'
    'docs/plans/archive/'
    'docs/plans/2026-03-'
    'docs/plans/2026-04-'
    'docs/plans/2026-05-01-'
    'docs/plans/2026-05-02-'
    'docs/plans/2026-05-03-'
    # Tests of preserved / migration code.
    'tests/test_icom_lan_shim.py'
    'tests/test_platformdirs_migration.py'
    'tests/test_diagnostics_bundle.py'                # asserts SCHEMA_VERSION_V1 backwards-compat
    'tests/test_diagnostics_contributors_batch1.py'   # asserts v1 system.json key absence
    'tests/test_discovery_responder.py'               # tests wire magic
    'tests/test_backend_factory.py'                   # cosmetic test fn name (PR-2A)
    'tests/test_diagnostics_logging.py'               # cosmetic test fn name
    # This script itself: contains literal pattern in its grep regex and
    # docstring.
    '.github/scripts/check-rebrand-allowlist.sh'
    # Workflow definition for the gate: references the pattern in its
    # job description.
    '.github/workflows/rebrand-gate.yml'
    # Release skill documents src/icom_lan/__init__.py shim as DO-NOT-TOUCH
    # (deprecation shim — must not be modified during version bumps).
    '.claude/skills/release/SKILL.md'
)

# Collect all files matching brand pattern.
MATCHES=$(git grep -lEi 'icom[-_]?lan|IcomLanError' || true)

LEAKS=""
while IFS= read -r file; do
    [ -z "$file" ] && continue
    SKIP=false
    for allowed in "${ALLOWLIST[@]}"; do
        case "$file" in
            "$allowed"*)
                SKIP=true
                break
                ;;
        esac
    done
    if [ "$SKIP" = false ]; then
        LEAKS="${LEAKS}${file}"$'\n'
    fi
done <<EOF
$MATCHES
EOF

if [ -n "$(printf '%s' "$LEAKS" | tr -d '[:space:]')" ]; then
    {
        echo "::error::Wave 2 rebrand leak — files contain icom-lan / icom_lan / IcomLanError but are not in the allow-list:"
        printf '%s' "$LEAKS"
        echo ""
        echo "Either:"
        echo "  1. Rename the brand reference (preferred)."
        echo "  2. If the reference is intentional (wire contract / migration / historical), add the"
        echo "     path to the ALLOWLIST in .github/scripts/check-rebrand-allowlist.sh with a comment"
        echo "     explaining why."
    } >&2
    exit 1
fi

echo "Wave 2 rebrand grep gate: clean."
