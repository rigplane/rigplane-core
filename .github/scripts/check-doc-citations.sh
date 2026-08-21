#!/usr/bin/env bash
# Doc-citation gate: fail CI if docs/** grows a new file:line citation that
# is not already grandfathered in doc-citation-baseline.txt (sibling file).
#
# Why this exists: documentation under docs/ used to cite code as
# `path/to/file.py:1234`. Line numbers cannot be kept correct by hand — in one
# day, four citations in a single design document were found pointing at the
# wrong lines, and one document had been printing a call shape that raises at
# runtime since it was written. A reader who trusts a citation and does not
# open it inherits the error. The owner has ruled: documentation cites file
# plus SYMBOL NAME (e.g. `radio.py: IcomRadio.set_frequency`), never a line
# number.
#
# At the time this gate was introduced (the commit that first regenerated
# doc-citation-baseline.txt in the current (docfile, citation) format),
# docs/** carried 861 existing pairs across 27 files. That number is a
# historical snapshot of one commit, not a live count, and this comment is
# not kept in sync as the baseline shrinks -- the number a clean
# `check-doc-citations.sh` run prints is always the authoritative current
# count; trust that output over this sentence if they ever disagree. (This
# note exists because an earlier draft of this paragraph carried the
# previous design round's *citation-string* count forward as if it were the
# new *pair* count, unremeasured -- inside the one script whose job is
# catching exactly that kind of stale, unremeasured figure.)
#
# Resolving each stale citation to "whatever symbol sits at that line today"
# would fabricate a symbol name wherever the citation is already wrong --
# exactly the defect this gate exists to catch. So this is NOT a bulk
# rewrite: existing citations are grandfathered in doc-citation-baseline.txt,
# and that baseline may only shrink over time as citations are individually
# converted to symbol names.
#
# WHAT ENFORCES "MAY ONLY SHRINK", AND WHERE: this script alone cannot make
# that true. `--regenerate` (below) refuses to add a pair unless you pass
# --allow-growth, which stops an *honest* contributor from accidentally
# growing the baseline with a local command — but a contributor could still
# hand-edit doc-citation-baseline.txt directly, add the matching citation to
# a doc, and this script's own check mode would see the two agree and pass.
# The actual, unavoidable enforcement is the SEPARATE `--check-growth <ref>`
# mode below, run by CI (see doc-citation-gate.yml) against the baseline
# blob as it exists at the merge base / previous commit, fetched from git
# history that the CI workflow controls, not the contributor's working tree.
# That comparison cannot be dodged by any local operation, because CI always
# re-derives it from a git ref, independent of what got committed.
#
# RENAMING A CITED DOCUMENT: moving or renaming a docs/** file re-keys every
# one of its pairs (the docfile half of the key changes), which would
# otherwise look identical to bulk growth to --check-growth. Before
# comparing, --check-growth runs `git diff --name-status -M <base-ref> --
# docs/` and, for every path git itself calls a rename, rewrites that path's
# entries in the base-ref baseline to the new path before diffing. A pure
# rename (citations unchanged) then compares as zero added/zero removed; a
# rename that also edits citations still shows exactly those edits as
# growth or shrinkage, because only the unchanged pairs get re-keyed. If a
# rename's content diff falls below git's similarity threshold and it is
# not detected as a rename, it will read as the old document's pairs going
# dead and the new document's pairs being new growth -- in that case use
# `git mv` (which git's detector favours) or, failing that,
# `--regenerate --allow-growth` plus an explanation in the PR, since
# --check-growth in CI is what actually decides the outcome regardless of
# what --regenerate did locally.
#
# BASELINE KEY: each grandfathered entry is a (docfile, citation) PAIR, not
# just a bare citation string. A flat set of citation strings (no docfile)
# has two holes: (a) a brand-new document citing a citation string that
# already happens to be grandfathered elsewhere would be invisible, and
# (b) retargeting a citation to a different stale line in the same document
# could survive undetected if that same string also happens to occur,
# unchanged, in some other document. Keying on the pair closes both: a new
# document citing an old string is a new pair, and retargeting a citation in
# one document changes that document's pair SET regardless of what any
# other document does with the same string.
#
# LIMIT OF THAT DESIGN, STATED PLAINLY: a (docfile, citation) pair is
# deduplicated within a document, so it records WHICH citations a document
# makes, not how many times or in what order. If a document cites the same
# target more than once and one specific occurrence is swapped for a
# citation that is ALREADY grandfathered elsewhere in that same document,
# the pair set for that document does not change, and the swap is invisible
# to this gate -- occurrence count, not just identity, would be needed to
# catch it. This is not a hypothetical: 149 of the 1010 raw citation
# occurrences in the current corpus are within-document duplicates. This is
# a deliberate boundary of pair-level granularity, not an oversight left to
# fix later: tracking occurrence counts instead would flag ordinary,
# harmless deduplication (a document dropping a redundant repeat citation)
# as baseline shrinkage in one run and growth in the next just as often as
# it would catch a genuine swap, which is a worse trade than the hole it
# would close.
#
# EXTENSIONS: py, ts, svelte, toml, md, c, h, cpp, mjs, yml, ui. Verified
# present in real docs/** citations today (2026-08-21) by scanning docs/**
# with a maximally permissive `\.[A-Za-z0-9]{1,8}:` / `#L` probe and manually
# classifying every distinct match found. Excluded, and why: that permissive
# probe also turns up numeric pseudo-extensions from IP:port literals
# (`127.0.0.1:8080` reads as extension "1"), hostnames (`dxc.nc7j.com:7373`
# reads as "com"), and method-chain properties (`RadioSession.open:260`
# reads as "open") — none of these are source-file extensions, so they are
# excluded by not appearing in the allowlist below, not by a separate
# carve-out rule. Extend this list only after repeating that scan against
# the current corpus; do not add extensions by guessing.
#
# CITATION FORMS matched: `path.ext:line`, `path.ext:line-line`, and the
# GitHub line-anchor idiom `path.ext#Lline` / `path.ext#Lline-Lline`. The
# anchor form has zero occurrences in docs/** today, but it is the obvious
# next workaround once `path:line` starts failing this gate, so it is caught
# from day one rather than added reactively later.
#
# WHAT COUNTS AS A CITATION: any match anywhere in a docs/** file, including
# inside fenced code blocks, indented blocks, and table cells. This gate does
# NOT special-case those contexts: a stale line number misleads a reader the
# same regardless of whether it sits in prose, a code fence, or a table cell,
# so excluding any of them would just be a way to smuggle a citation past the
# gate. Nothing else is excluded by context — only by the extension
# allowlist above, which was arrived at by evidence, not assumption.
#
# Usage (run from the repository root):
#   check-doc-citations.sh                          # check mode (CI, always)
#   check-doc-citations.sh --regenerate              # shrink-only rewrite
#   check-doc-citations.sh --regenerate --allow-growth   # explicit override
#   check-doc-citations.sh --check-growth <git-ref>  # CI-only growth check
#
# Regenerate whenever you intentionally remove or convert a grandfathered
# citation. `--regenerate` refuses to write if doing so would add a pair not
# already present in the baseline on disk; that refusal is a local courtesy,
# not the enforcement mechanism (see above).

set -euo pipefail
export LC_ALL=C  # pin collation: sort/comm must be byte-order-stable across
                  # machines and locales, or every regeneration reorders
                  # unrelated lines and buries the one line a reviewer needs
                  # to see in a diff.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_FILE="${SCRIPT_DIR}/doc-citation-baseline.txt"

EXT='py|ts|svelte|toml|md|c|h|cpp|mjs|yml|ui'
PATTERN="[A-Za-z0-9_/.-]+\\.(${EXT})(:[0-9]+(-[0-9]+)?|#L[0-9]+(-L?[0-9]+)?)"

strip_comments() {
    grep -vE '^[[:space:]]*#' | grep -vE '^[[:space:]]*$' || true
}

# Reads the on-disk baseline file (or stdin, for --check-growth) and prints
# sorted-unique "docfile<TAB>citation" pairs.
load_baseline_pairs() {
    if [ -n "${1:-}" ]; then
        strip_comments < "$1" | sort -u
    else
        strip_comments | sort -u
    fi
}

write_baseline() {
    # $1 = sorted-unique "docfile<TAB>citation" pairs (already newline-joined)
    {
        echo "# Baseline of grandfathered docs/** (docfile, citation) pairs."
        echo "# Format: <doc file path><TAB><citation string>. Generated by:"
        echo "#   .github/scripts/check-doc-citations.sh --regenerate"
        echo "# This list may only shrink -- see check-doc-citations.sh for the"
        echo "# full rationale, including why regeneration alone does not"
        echo "# enforce that. Do not hand-edit additions."
        printf '%s\n' "$1"
    } > "$BASELINE_FILE"
}

if [ "${1:-}" = "--check-growth" ]; then
    BASE_REF="${2:?--check-growth requires a git ref argument}"

    # F6: resolve the ref FIRST, as its own check. "I could not resolve
    # this ref at all" (typo, deleted branch, bad workflow input) and "this
    # ref resolves fine but the gate did not exist yet at that commit" are
    # different situations and must not share an exit code or a message --
    # collapsing them made an unresolvable ref look like a clean bootstrap.
    if ! RESOLVED_BASE_REF="$(git rev-parse --verify "${BASE_REF}^{commit}" 2>/dev/null)"; then
        echo "::error::--check-growth could not resolve '${BASE_REF}' as a commit -- this is a ref/workflow problem, not evidence that the baseline is clean. Fix the ref (or the workflow input computing it) rather than treating this as a pass." >&2
        exit 2
    fi

    if ! BASE_BLOB="$(git show "${RESOLVED_BASE_REF}:.github/scripts/doc-citation-baseline.txt" 2>/dev/null)"; then
        echo "Doc-citation gate: ${RESOLVED_BASE_REF} resolves, but no baseline exists there (gate not introduced yet at that commit) -- skipping growth check."
        exit 0
    fi
    BASE_PAIRS_RAW="$(printf '%s\n' "$BASE_BLOB" | load_baseline_pairs)"

    # F3: rename-aware. Translate the base baseline's docfile through any
    # docs/** rename git itself detects between RESOLVED_BASE_REF and the
    # current tree, so moving a cited document does not read as bulk
    # growth (old path's pairs "disappearing") plus bulk shrinkage (new
    # path's pairs "appearing"). See the header for the full rationale and
    # the fallback when a rename is edited too heavily for git to detect.
    declare -A RENAME_TO=()
    while IFS=$'\t' read -r status oldpath newpath; do
        [ -z "$oldpath" ] && continue
        case "$status" in
            R*) RENAME_TO["$oldpath"]="$newpath" ;;
        esac
    done < <(git diff --name-status -M "${RESOLVED_BASE_REF}" -- docs/ 2>/dev/null || true)

    BASE_PAIRS="$(
        printf '%s\n' "$BASE_PAIRS_RAW" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            target="${RENAME_TO[$docfile]:-$docfile}"
            printf '%s\t%s\n' "$target" "$citation"
        done | sort -u
    )"

    CURRENT_PAIRS="$(load_baseline_pairs "$BASELINE_FILE")"
    ADDED="$(comm -13 <(printf '%s\n' "$BASE_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    if [ -n "$(printf '%s' "$ADDED" | tr -d '[:space:]')" ]; then
        echo "::error::the committed baseline grew relative to ${RESOLVED_BASE_REF} (docs/** renames already accounted for) -- the baseline may only shrink, and this check is not bypassable by any local command:" >&2
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}" >&2
        done
        echo "If this includes a legitimate document rename that git's detector missed (heavily edited in the same change), see the RENAMING section in this script's header." >&2
        exit 1
    fi
    echo "Doc-citation gate: baseline did not grow relative to ${RESOLVED_BASE_REF}."
    exit 0
fi

if [ ! -d docs ]; then
    echo "::error::run this script from the repository root (docs/ not found)" >&2
    exit 2
fi

# One pass over docs/**: every matched occurrence as "docfile:docline:citation".
ALL_OCCURRENCES="$(grep -rnoE "$PATTERN" docs/ || true)"

declare -A FIRST_LOCATION=()
CURRENT_PAIR_LIST=()
while IFS= read -r line; do
    [ -z "$line" ] && continue
    docfile="${line%%:*}"
    rest="${line#*:}"
    docline="${rest%%:*}"
    citation="${rest#*:}"
    key="${docfile}"$'\t'"${citation}"
    if [ -z "${FIRST_LOCATION[$key]+x}" ]; then
        FIRST_LOCATION[$key]="$docline"
        CURRENT_PAIR_LIST+=("$key")
    fi
done <<EOF
$ALL_OCCURRENCES
EOF

CURRENT_PAIRS="$(printf '%s\n' "${CURRENT_PAIR_LIST[@]:-}" | grep -v '^$' | sort -u || true)"

if [ "${1:-}" = "--regenerate" ]; then
    ALLOW_GROWTH=0
    [ "${2:-}" = "--allow-growth" ] && ALLOW_GROWTH=1

    if [ -f "$BASELINE_FILE" ]; then
        OLD_PAIRS="$(load_baseline_pairs "$BASELINE_FILE")"
    else
        OLD_PAIRS=""
    fi

    ADDED="$(comm -13 <(printf '%s\n' "$OLD_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    REMOVED="$(comm -23 <(printf '%s\n' "$OLD_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    ADDED_COUNT=$(printf '%s\n' "$ADDED" | grep -c . || true)
    REMOVED_COUNT=$(printf '%s\n' "$REMOVED" | grep -c . || true)

    if [ "$ADDED_COUNT" -gt 0 ] && [ "$ALLOW_GROWTH" -ne 1 ]; then
        echo "::error::--regenerate would ADD ${ADDED_COUNT} pair(s) not already in the baseline; refused by default because the baseline may only shrink." >&2
        echo "Added pairs (one of these is probably a new citation you meant to write as file+symbol instead):" >&2
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}" >&2
        done
        echo "If this addition is genuinely intentional, re-run with: --regenerate --allow-growth" >&2
        echo "Note: CI's --check-growth check is authoritative regardless of this flag -- see this script's header." >&2
        exit 1
    fi

    write_baseline "$CURRENT_PAIRS"
    echo "Regenerated ${BASELINE_FILE}: removed ${REMOVED_COUNT}, added ${ADDED_COUNT}."
    if [ "$ADDED_COUNT" -gt 0 ]; then
        echo "Added (via --allow-growth):"
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}"
        done
    fi
    exit 0
fi

if [ ! -f "$BASELINE_FILE" ]; then
    echo "::error::baseline file not found at ${BASELINE_FILE}" >&2
    exit 2
fi

BASELINE_PAIRS="$(load_baseline_pairs "$BASELINE_FILE")"

NEW="$(comm -23 <(printf '%s\n' "$CURRENT_PAIRS") <(printf '%s\n' "$BASELINE_PAIRS") || true)"
DEAD="$(comm -13 <(printf '%s\n' "$CURRENT_PAIRS") <(printf '%s\n' "$BASELINE_PAIRS") || true)"

FAIL=0

if [ -n "$(printf '%s' "$NEW" | tr -d '[:space:]')" ]; then
    FAIL=1
    echo "::error::new docs/** citations found that are not in the baseline:" >&2
    printf '%s\n' "$NEW" | while IFS=$'\t' read -r docfile citation; do
        [ -z "$docfile" ] && continue
        key="${docfile}"$'\t'"${citation}"
        docline="${FIRST_LOCATION[$key]:-?}"
        echo "  ${docfile}:${docline}: new citation '${citation}' -- cite file plus symbol name instead (e.g. \`radio.py: IcomRadio.set_frequency\`), never a line number; line numbers rot." >&2
    done
fi

if [ -n "$(printf '%s' "$DEAD" | tr -d '[:space:]')" ]; then
    FAIL=1
    echo "::error::baseline entries no longer found in the doc file they name -- the baseline is stale and must shrink:" >&2
    printf '%s\n' "$DEAD" | while IFS=$'\t' read -r docfile citation; do
        [ -z "$docfile" ] && continue
        echo "  ${docfile}: ${citation}" >&2
    done
    echo "Regenerate it: .github/scripts/check-doc-citations.sh --regenerate (then commit the updated baseline file)." >&2
fi

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi

TOTAL=$(printf '%s\n' "$BASELINE_PAIRS" | grep -c . || true)
echo "Doc-citation gate: clean (${TOTAL} grandfathered citations)."
