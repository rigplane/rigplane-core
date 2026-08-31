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
# rename -- nothing else in the document touched in the same PR -- then
# compares as zero added/zero removed.
#
# `--regenerate` is NOT rename-aware: it only compares the current scan to
# whatever baseline is already on disk, so even a pure rename always looks
# like "removed N old-path pairs, added N new-path pairs" and refuses
# without `--allow-growth`, same as real growth would. Use
# `--regenerate --allow-growth` for any rename, detected or not; whether the
# growth it produces is legitimate is decided by --check-growth in CI, not
# by this flag.
#
# `git mv` does NOT make detection more likely. Git records no rename
# metadata -- `git diff -M` recomputes similarity from tree content alone at
# diff time, so `git mv old new` and `rm old && write new` produce identical
# trees and are indistinguishable to it. Detection is purely a function of
# how much of the document changed in the same diff as the move: rename a
# document AND heavily rewrite it in one PR, and similarity drops below
# git's threshold either way. When that happens it reads as the old
# document's pairs going dead and the new document's pairs being new
# growth, and there is no local command that turns that PR green --
# --check-growth in CI, not --regenerate, decides the outcome, and it still
# sees an undetected move plus unrelated-looking new citations. Splitting
# the rename and the rewrite into two commits in the SAME pull request does
# not help either, because --check-growth compares net content between the
# merge base and the current head, not commit by commit. The route that
# actually works: land the rename alone in its own pull request first
# (untouched content, so similarity is ~100% and git detects it every
# time), then make the content edit in a second pull request against the
# now-renamed path -- an ordinary, non-rename content change with no
# detection question at all.
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
#
# ===========================================================================
# DOC-LINK EXTENSION (MOR-2053)
# ===========================================================================
#
# A second, independent gate lives in this same script and shares its
# mechanism: fails CI if a relative markdown link from one document to
# another (`[text](target.md)`, optionally `[text](target.md#anchor)`)
# resolves to a path that is not a git-tracked file. Same shrink-only
# baseline shape as the citation gate above, selected with --check-links
# (see usage at the bottom of this block) — extending the existing
# mechanism rather than standing up a parallel one.
#
# WHY THIS EXISTS: the citation gate above verifies that docs/** cites CODE
# correctly. Nothing verified that a relative link from one DOCUMENT to
# another actually resolves. Found by hand while writing an unrelated guide:
# frontend/README.md linked to docs/component-architecture.md and
# docs/css-design-tokens.md, and neither file exists. Same shape of rot as
# the citation gate prevents, one surface over, and cheaper to catch — no
# symbol resolution needed, only "does this path exist".
#
# SCOPE — REPO-WIDE *.md, NOT DOCS/**, AND WHY THAT DIFFERS FROM THE
# CITATION GATE ABOVE: the citation gate is scoped to docs/** because its
# rule is specifically about how docs/** prose cites the codebase. A dead
# link carries no such restriction — the frontend/README.md links that
# motivated this gate are themselves outside docs/**, so scoping this check
# to docs/** would never have caught them, or any future recurrence of the
# same shape. Verified empirically (2026-08-31), not assumed: a repo-wide
# scan of every git-tracked `*.md` file (222 at the time of writing) finds
# 155 relative links whose target path ends in `.md`, of which exactly 2 are
# broken — the two frontend/README.md links above. No other breakage turned
# up. `git ls-files '*.md'` is used as both the file set to scan and the
# existence oracle, rather than a filesystem walk plus `[ -f ... ]`: a naive
# `find . -name '*.md'` from the repo root turns up 4036 files, almost all
# vendored (frontend/node_modules, .venv, mkdocs' site/ build output) —
# scanning those would be slow, noisy, and beside the point. Resolving
# against the tracked-file set instead of the raw filesystem also keeps
# behaviour identical between a case-insensitive dev filesystem (macOS'
# default APFS mode) and the case-sensitive Linux CI runner: verified by
# cross-checking the same scan both ways (a filesystem `-f` test vs. exact
# membership in `git ls-files`) and getting the same 2 broken links either
# way, on the corpus as it stands today.
#
# WHAT COUNTS AS A CHECKED LINK: an inline markdown link `[text](target)`,
# not an image embed (`![alt](target)` is skipped). `target` is skipped
# entirely — not a document-to-document link, nothing to resolve — when it
# is a same-document anchor only (`#foo`) or starts with a URI scheme
# (`scheme:`, e.g. `https:`, `mailto:`); every non-relative link in the
# current corpus uses `https:`, verified by scanning for the schemes
# actually present, same evidence standard as the extension allowlist
# above. Of what remains, only targets whose path portion (before any
# `#anchor`) ends in `.md` are checked — a link to an image, a source file,
# or any other non-document asset is out of scope, because the gap being
# closed is specifically "a link from one document to another", not general
# asset existence. No leading-slash (repo-root-absolute) relative link
# exists anywhere in the current corpus (verified by grep); this script
# gives that shape no special handling, so one written today would be
# resolved as relative to the citing file's own directory like any other
# target, not the repo root — a known gap, left alone because there is
# nothing in the corpus to get right or wrong yet.
#
# ANCHORS ARE NOT VERIFIED. This script checks only that the file half of a
# `target.md#anchor` link resolves; it does not confirm the anchor names an
# actual heading in that file (doing so would mean parsing Markdown headings
# and reproducing GitHub's slugging rules, which this does not attempt). The
# `#anchor` fragment, when present, is kept verbatim in the stored baseline
# key (so two links to the same missing file with different anchors are two
# separate pairs — the same pair-level-granularity limitation the citation
# baseline documents above) but is stripped before the existence check.
# Both the "clean" pass message and the new-breakage error message printed
# by this script say "file existence only, #anchor fragments not verified"
# in those words, so a clean run cannot be misread as "links verified" when
# only "link TARGETS verified to exist" is true.
#
# PATH RESOLUTION: a relative target is resolved against the directory of
# the file containing the link — the same rule a browser or GitHub's own
# renderer applies — via pure string normalization of `.`/`..` path
# segments (normalize_relative_path, below); no filesystem access, so it
# cannot be fooled by a symlink, and behaves the same on every OS.
#
# BASELINE: same (docfile, target-as-written) pair format as the citation
# baseline, in the sibling file doc-link-baseline.txt, generated and
# checked through the same --regenerate / --check-growth flags (prefixed
# with --check-links to select this baseline instead of the citation one).
# The FORMAT is reused wholesale. One piece of the citation gate's
# check-mode BEHAVIOUR is deliberately not reused: there, a baseline entry
# that no longer appears in the current scan fails the check ("baseline is
# stale and must shrink") until someone runs --regenerate. The link
# baseline does not fail on that condition — a disappearing entry is
# reported as a courtesy notice, never a failure. Why: at the time this gate
# was written, both grandfathered links were already being fixed by an
# independently authored, already-open pull request with no knowledge that
# this baseline would come to exist. Requiring that PR to also regenerate a
# baseline it cannot know about would either block it or turn `main` red
# the moment it merges — exactly what a shrink-only gate must not do to a
# fix landing in good faith. Growth is still fully blocked, both live (the
# NEW-pairs comparison below) and historically (--check-growth against the
# merge base, unchanged mechanism) — only shrinkage is allowed to happen
# quietly. `--check-links --regenerate` is offered to tidy the baseline
# file when convenient; it is never required for this check to pass.
#
# Usage (run from the repository root; --check-links, if given, must be the
# first argument):
#   check-doc-citations.sh --check-links                   # check mode (CI)
#   check-doc-citations.sh --check-links --regenerate            # shrink-only rewrite
#   check-doc-citations.sh --check-links --regenerate --allow-growth
#   check-doc-citations.sh --check-links --check-growth <git-ref>  # CI-only
#
# ===========================================================================
# DANGLING-CITATION FLOOR (MOR-2065, first step)
# ===========================================================================
#
# A third, independent gate lives in this same script and reuses the same
# MODE-selector / --regenerate / --check-growth mechanism as the doc-link
# gate above, selected with --check-dangling: for every (docfile, citation)
# pair already grandfathered in doc-citation-baseline.txt, verify that the
# cited FILE still exists in the tree and that the cited LINE is still
# within that file's current length.
#
# WHY THIS EXISTS: the citation gate above enforces baseline MEMBERSHIP (is
# this exact citation string grandfathered) but never opens the cited file.
# A citation pointing past end-of-file, or at a file deleted since it was
# grandfathered, stays green forever under that check alone -- it only
# catches a citation being added to or removed from a DOCUMENT, not a
# citation going stale because the CODE it points at changed length or
# disappeared while the document was untouched. This closes that gap for
# path/line existence only. It does NOT verify that a symbol name still
# exists at that position -- resolving a stale citation to "whatever symbol
# sits at that line today" would fabricate a symbol name exactly where the
# citation is already wrong, the same failure mode the citation gate above
# exists to prevent, so symbol verification is a separate, still-open half
# of MOR-2065, not attempted here.
#
# RESOLUTION RULE (see parse_citation, find_candidates, classify_citation
# below): split a citation's path from its cited line number(s) -- the same
# two forms (`path:N[-M]`, `path#LN[-LM]`) the PATTERN regex above already
# accepts. Find candidate files for that path in `git ls-files` (the
# current tree, not the commit that grandfathered the citation) by exact
# match OR by "/"-suffix match -- a bare-filename citation such as
# `radio.py:400` can match more than one tracked file sharing that
# basename, and any one candidate covering the line is enough. Verdict per
# citation: OK if ANY candidate file's current line count is >= the
# citation's max cited line; MISSING if zero candidate files exist;
# PAST_EOF if candidate files exist but none of them covers the line.
# "Current line count" is `awk 'END{print NR}'`, which -- unlike `wc -l` --
# counts a final line that has no trailing newline, so a citation naming
# exactly a file's last line is not misclassified as PAST_EOF depending on
# whether that line happens to end in a newline.
#
# BASELINE: doc-citation-dangling-baseline.txt, same (docfile, citation)
# pair format as doc-citation-baseline.txt, generated with
# `--check-dangling --regenerate` and enforced shrink-only with
# `--check-dangling --check-growth <ref>` -- the exact same --regenerate /
# --check-growth code paths --check-links already uses, parameterized onto
# this baseline via the ACTIVE_* variables below; nothing below this point
# duplicates that logic.
#
# CHECK-MODE, REUSING THE CITATION GATE'S OWN NEW/DEAD LOGIC: plain
# `--check-dangling` computes CURRENT_PAIRS not by scanning docs/** but by
# classifying every pair already in doc-citation-baseline.txt and keeping
# only the ones that do not classify OK (see the MODE="dangling" branch
# below). Comparing that against the committed dangling baseline through
# the same `comm` logic the citation gate uses gives two outcomes for free:
# a NEW pair is a citation that is dangling now but not yet grandfathered
# here -- this is the floor itself, the case that must fail loudly. A DEAD
# pair is a dangling-baseline entry that is no longer dangling, whether
# because its (docfile, citation) pair was dropped from the main citation
# baseline entirely (the citation was fixed) or because the cited file grew
# back past the line -- self-liquidation: unlike the link baseline's DEAD
# handling, a DEAD entry here IS a failure, never a quiet note, because a
# stale exemption must not keep passing unnoticed. Both outcomes share one
# fix: `--check-dangling --regenerate`, run and committed in the SAME PR
# that changed the citation's state, so the citation baseline and this
# baseline move together atomically.
#
# Usage (run from the repository root; --check-dangling, if given, must be
# the first argument):
#   check-doc-citations.sh --check-dangling                        # check mode (CI)
#   check-doc-citations.sh --check-dangling --regenerate                 # shrink-only rewrite
#   check-doc-citations.sh --check-dangling --regenerate --allow-growth
#   check-doc-citations.sh --check-dangling --check-growth <git-ref>     # CI-only

set -euo pipefail
export LC_ALL=C  # pin collation: sort/comm must be byte-order-stable across
                  # machines and locales, or every regeneration reorders
                  # unrelated lines and buries the one line a reviewer needs
                  # to see in a diff.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASELINE_FILE="${SCRIPT_DIR}/doc-citation-baseline.txt"
LINK_BASELINE_FILE="${SCRIPT_DIR}/doc-link-baseline.txt"
DANGLING_BASELINE_FILE="${SCRIPT_DIR}/doc-citation-dangling-baseline.txt"

EXT='py|ts|svelte|toml|md|c|h|cpp|mjs|yml|ui'
PATTERN="[A-Za-z0-9_/.-]+\\.(${EXT})(:[0-9]+(-[0-9]+)?|#L[0-9]+(-L?[0-9]+)?)"

CITATION_HEADER='# Baseline of grandfathered docs/** (docfile, citation) pairs.
# Format: <doc file path><TAB><citation string>. Generated by:
#   .github/scripts/check-doc-citations.sh --regenerate
# This list may only shrink -- see check-doc-citations.sh for the
# full rationale, including why regeneration alone does not
# enforce that. Do not hand-edit additions.'

LINK_HEADER='# Baseline of grandfathered (docfile, dead-link-target) pairs -- relative
# markdown links whose target does not resolve to a tracked file.
# Format: <doc file path><TAB><link target as written>. Generated by:
#   .github/scripts/check-doc-citations.sh --check-links --regenerate
# Growth is blocked (see check-doc-citations.sh, DOC-LINK EXTENSION); a
# fixed link disappearing from this file is fine and not required before
# CI passes. Do not hand-edit additions.'

DANGLING_HEADER='# Baseline of grandfathered (docfile, citation) pairs from
# doc-citation-baseline.txt whose citation is currently dangling: the cited
# file does not exist, or the cited line is past that file'"'"'s current
# length. Format: <doc file path><TAB><citation string>, identical to
# doc-citation-baseline.txt. Generated by:
#   .github/scripts/check-doc-citations.sh --check-dangling --regenerate
# Shrink-only, enforced the same way as the citation baseline (see
# check-doc-citations.sh, DANGLING-CITATION FLOOR); an entry that is no
# longer dangling, or no longer a pair in doc-citation-baseline.txt, is a
# self-liquidation FAILURE, not a quiet removal -- regenerate this file in
# the same PR that changed the citation. Do not hand-edit additions.'

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
    # $1 = target baseline file path
    # $2 = header comment block (newline-joined, each line already "# ...")
    # $3 = sorted-unique "docfile<TAB>citation" pairs (newline-joined)
    {
        printf '%s\n' "$2"
        printf '%s\n' "$3"
    } > "$1"
}

# Resolves $2 (a relative path, possibly containing "." and ".." segments)
# against base directory $1, by pure string normalization -- no filesystem
# access, so this cannot be fooled by a symlink or a case-insensitive
# filesystem, and it behaves identically on every OS. A ".." that would
# escape above the topmost given segment is kept literally as "..", which
# never matches a tracked file and therefore correctly reads as "missing".
normalize_relative_path() {
    local base_dir="$1" rel_path="$2"
    local combined="${base_dir:+$base_dir/}$rel_path"
    local IFS='/'
    local -a segs=() out=()
    read -ra segs <<< "$combined"
    for seg in "${segs[@]}"; do
        case "$seg" in
            '' | '.') continue ;;
            '..')
                if [ "${#out[@]}" -gt 0 ] && [ "${out[-1]}" != '..' ]; then
                    unset 'out[-1]'
                else
                    out+=('..')
                fi
                ;;
            *) out+=("$seg") ;;
        esac
    done
    local IFS='/'
    echo "${out[*]}"
}

# Code context is stripped before link-matching (used by
# scan_broken_link_occurrences below), unlike the citation gate above
# (which deliberately checks fenced/indented code and table cells too,
# because a stale citation still misleads a reader there regardless of
# where it sits). A link is different: CommonMark parses code spans and
# fenced code blocks BEFORE link syntax, so `[text](url)` written inside
# backticks or a fenced block is never rendered as a clickable link by any
# Markdown renderer -- it is inert example text by construction, not a
# reference that can rot. Found by dogfooding: this script's own first
# attempt at documenting this feature in CLAUDE.md used exactly that shape
# as a syntax example and immediately false-positived against itself.
#
# Blanks fenced code blocks and strips inline code spans from a file,
# preserving line COUNT and ORDER (blanked lines become empty, never
# removed) so line numbers taken from the result still match the original
# file. A fence marker (``` or ~~~) may be indented a few spaces (seen in
# this corpus, e.g. AGENTS.md), so leading whitespace is trimmed before
# checking for one; nested fences are not handled, because CommonMark
# fences do not nest and none do in this corpus (verified 2026-08-31).
# Inline code spans are stripped per line, so a code span is not
# recognised if it spans multiple lines, and an escaped backtick is not
# recognised as escaped -- neither shape occurs in this corpus today, same
# evidence bar as the rest of this gate's scope decisions.
#
# One `awk` process per file, not one process per line: an earlier version
# of this function forked `sed` per LINE (inside a bash while-read loop)
# and was slow enough to time out scanning this corpus.
strip_code_context() {
    awk '
        {
            line = $0
            trimmed = line
            sub(/^[ \t]+/, "", trimmed)
            if (trimmed ~ /^(```|~~~)/) {
                infence = !infence
                print ""
                next
            }
            if (infence) { print ""; next }
            gsub(/`[^`]*`/, "", line)
            print line
        }
    ' "$1"
}

# Scans every git-tracked *.md file for relative links to another *.md file
# and prints one "docfile<TAB>lineno<TAB>target" line per OCCURRENCE that
# does NOT resolve to a tracked file (not deduplicated -- the caller dedups
# while building FIRST_LOCATION, exactly like the citation scan below does
# for ALL_OCCURRENCES). See the DOC-LINK EXTENSION comment block above for
# exactly what counts as a checked link and what is deliberately out of
# scope (anchors, non-.md targets, absolute URLs).
#
# Deliberately prints rather than populating FIRST_LOCATION directly: this
# function's result is always captured via "$(...)" command substitution,
# which runs it in a subshell -- any associative-array writes made in here
# would be invisible to the caller once the subshell exits. Passing the raw
# occurrences back as text and doing the stateful dedup in the foreground
# (in the caller) is what makes FIRST_LOCATION visible afterward.
scan_broken_link_occurrences() {
    local link_re='!?\[[^]]*\]\([^)]+\)'

    declare -A tracked_md=()
    local f
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        tracked_md["$f"]=1
    done < <(git ls-files '*.md')

    local docfile base_dir lineno match target path_part resolved
    while IFS= read -r docfile; do
        [ -z "$docfile" ] && continue
        base_dir="$(dirname "$docfile")"
        [ "$base_dir" = "." ] && base_dir=""
        while IFS=: read -r lineno match; do
            [ -z "${match:-}" ] && continue
            case "$match" in
                '!'*) continue ;;                 # image embed, not a doc link
            esac
            target="${match#*](}"
            target="${target%)}"
            case "$target" in
                '#'*) continue ;;                 # same-document anchor only
                [A-Za-z]*:*) continue ;;           # URI scheme (https:, mailto:, ...)
            esac
            path_part="${target%%#*}"
            [ -z "$path_part" ] && continue
            case "$path_part" in
                *.md) ;;
                *) continue ;;                    # not a link to a document
            esac
            resolved="$(normalize_relative_path "$base_dir" "$path_part")"
            if [ -z "${tracked_md[$resolved]+x}" ]; then
                printf '%s\t%s\t%s\n' "$docfile" "$lineno" "$target"
            fi
        done < <(strip_code_context "$docfile" | grep -noE "$link_re" || true)
    done < <(git ls-files '*.md')
}

# Splits one docs/** citation string ("path.ext:N", "path.ext:N-M",
# "path.ext#LN", "path.ext#LN-LM") into PARSE_PATH and PARSE_MAXLINE
# (globals -- bash functions cannot return a struct). Every string passed
# here already matched the PATTERN regex above when it was scanned, so
# these two forms are exhaustive; this function does not re-validate that.
parse_citation() {
    local citation="$1" path rest a b
    if [[ "$citation" == *'#L'* ]]; then
        path="${citation%%#L*}"
        rest="${citation##*#L}"
    else
        path="${citation%%:*}"
        rest="${citation#*:}"
    fi
    if [[ "$rest" == *-* ]]; then
        a="${rest%%-*}"
        b="${rest##*-}"
        b="${b#L}"   # a "#Lx-Ly" range's second bound may repeat the "L"
    else
        a="$rest"
        b=""
    fi
    PARSE_PATH="$path"
    if [ -n "$b" ] && [ "$b" -gt "$a" ]; then
        PARSE_MAXLINE="$b"
    else
        PARSE_MAXLINE="$a"
    fi
}

# Prints every candidate file for citation path $1, one per line, matching
# it against ALL_TRACKED_FILES (must already be populated by the caller --
# `git ls-files` output for the whole tree, not just docs/**) by exact
# match or by "/"-suffix match -- a bare filename like `radio.py` can match
# more than one tracked path. The exact-match half is a single `grep -F -x`
# lookup; the suffix half narrows with a fixed-string `grep -F` pass first
# (one process over the whole file list, not one per tracked file) and
# confirms the path-boundary per surviving candidate in bash, because a
# plain substring match on "/$1" would also match a longer trailing
# segment that merely contains it (e.g. "/radio.py" inside
# "/old_radio.py") -- that grep pass alone is a superset, not the answer.
find_candidates() {
    local p="$1" exact prefiltered f
    exact="$(printf '%s\n' "$ALL_TRACKED_FILES" | grep -F -x -- "$p" || true)"
    prefiltered="$(printf '%s\n' "$ALL_TRACKED_FILES" | grep -F -- "/$p" || true)"
    {
        [ -n "$exact" ] && printf '%s\n' "$exact"
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            [[ "$f" == */"$p" ]] && printf '%s\n' "$f"
        done <<< "$prefiltered"
    } | sort -u
}

# Classifies citation string $1 against the current tree, setting
# CLASSIFY_VERDICT to one of OK / MISSING / PAST_EOF (globals, same
# constraint as parse_citation) and, for the two dangling verdicts,
# CLASSIFY_DETAIL to a human-readable reason naming the closest-covering
# candidate (the one with the most lines, so the message shows the file
# that came nearest to covering the citation, not an arbitrary one).
# ALL_TRACKED_FILES must already be populated (see find_candidates).
# "Current line count" is `awk 'END{print NR}'` -- see the
# DANGLING-CITATION FLOOR header comment for why that, not `wc -l`, is the
# right measure of a file's length here.
classify_citation() {
    local citation="$1" candidates f linecount best_path="" best_count=-1
    CLASSIFY_DETAIL=""  # cleared unconditionally so a stale value from a
                        # prior OK-verdict call is never mistaken for this
                        # citation's reason
    parse_citation "$citation"
    candidates="$(find_candidates "$PARSE_PATH")"
    if [ -z "$(printf '%s' "$candidates" | tr -d '[:space:]')" ]; then
        CLASSIFY_VERDICT="MISSING"
        CLASSIFY_DETAIL="file missing: no tracked file matches '${PARSE_PATH}' (exact or path-suffix)"
        return
    fi
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        linecount=$(awk 'END{print NR+0}' "$f" 2>/dev/null || echo 0)
        if [ "$linecount" -gt "$best_count" ]; then
            best_path="$f"
            best_count="$linecount"
        fi
        if [ "$linecount" -ge "$PARSE_MAXLINE" ]; then
            CLASSIFY_VERDICT="OK"
            return
        fi
    done <<< "$candidates"
    CLASSIFY_VERDICT="PAST_EOF"
    CLASSIFY_DETAIL="line ${PARSE_MAXLINE} past EOF of '${best_path}' with ${best_count} lines"
}

MODE="citation"
if [ "${1:-}" = "--check-links" ]; then
    MODE="link"
    shift
elif [ "${1:-}" = "--check-dangling" ]; then
    MODE="dangling"
    shift
fi

if [ "$MODE" = "link" ]; then
    ACTIVE_BASELINE_FILE="$LINK_BASELINE_FILE"
    ACTIVE_BASELINE_REL_PATH=".github/scripts/doc-link-baseline.txt"
    ACTIVE_HEADER="$LINK_HEADER"
    ACTIVE_RENAME_PATHSPEC='*.md'
    ACTIVE_LABEL="Doc-link gate"
elif [ "$MODE" = "dangling" ]; then
    ACTIVE_BASELINE_FILE="$DANGLING_BASELINE_FILE"
    ACTIVE_BASELINE_REL_PATH=".github/scripts/doc-citation-dangling-baseline.txt"
    ACTIVE_HEADER="$DANGLING_HEADER"
    ACTIVE_RENAME_PATHSPEC='docs/'  # dangling-baseline docfiles are citation
                                    # docfiles, same rename scope as citation mode
    ACTIVE_LABEL="Doc-citation dangling-floor gate"
else
    ACTIVE_BASELINE_FILE="$BASELINE_FILE"
    ACTIVE_BASELINE_REL_PATH=".github/scripts/doc-citation-baseline.txt"
    ACTIVE_HEADER="$CITATION_HEADER"
    ACTIVE_RENAME_PATHSPEC='docs/'
    ACTIVE_LABEL="Doc-citation gate"
fi

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

    if ! BASE_BLOB="$(git show "${RESOLVED_BASE_REF}:${ACTIVE_BASELINE_REL_PATH}" 2>/dev/null)"; then
        echo "${ACTIVE_LABEL}: ${RESOLVED_BASE_REF} resolves, but no baseline exists there (gate not introduced yet at that commit) -- skipping growth check."
        exit 0
    fi
    BASE_PAIRS_RAW="$(printf '%s\n' "$BASE_BLOB" | load_baseline_pairs)"

    # F3: rename-aware. Translate the base baseline's docfile through any
    # rename git itself detects between RESOLVED_BASE_REF and the current
    # tree (scoped to docs/ for the citation baseline, to every tracked
    # *.md file for the link baseline), so moving a cited/linking document
    # does not read as bulk growth (old path's pairs "disappearing") plus
    # bulk shrinkage (new path's pairs "appearing"). See the header for the
    # full rationale and the fallback when a rename is edited too heavily
    # for git to detect.
    declare -A RENAME_TO=()
    while IFS=$'\t' read -r status oldpath newpath; do
        [ -z "$oldpath" ] && continue
        case "$status" in
            R*) RENAME_TO["$oldpath"]="$newpath" ;;
        esac
    done < <(git diff --name-status -M "${RESOLVED_BASE_REF}" -- "${ACTIVE_RENAME_PATHSPEC}" 2>/dev/null || true)

    BASE_PAIRS="$(
        printf '%s\n' "$BASE_PAIRS_RAW" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            target="${RENAME_TO[$docfile]:-$docfile}"
            printf '%s\t%s\n' "$target" "$citation"
        done | sort -u
    )"

    CURRENT_PAIRS="$(load_baseline_pairs "$ACTIVE_BASELINE_FILE")"
    ADDED="$(comm -13 <(printf '%s\n' "$BASE_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    if [ -n "$(printf '%s' "$ADDED" | tr -d '[:space:]')" ]; then
        echo "::error::the committed baseline grew relative to ${RESOLVED_BASE_REF} (renames already accounted for) -- the baseline may only shrink, and this check is not bypassable by any local command:" >&2
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}" >&2
        done
        if [ "$MODE" = "citation" ]; then
            echo "If this includes a legitimate document rename that git's detector missed (heavily edited in the same change), see the RENAMING section in this script's header." >&2
        fi
        exit 1
    fi
    echo "${ACTIVE_LABEL}: baseline did not grow relative to ${RESOLVED_BASE_REF}."
    exit 0
fi

if [ ! -d docs ]; then
    echo "::error::run this script from the repository root (docs/ not found)" >&2
    exit 2
fi

declare -A FIRST_LOCATION=()

if [ "$MODE" = "link" ]; then
    # One pass over every tracked *.md file: every broken-link occurrence as
    # "docfile<TAB>lineno<TAB>target" (see scan_broken_link_occurrences).
    ALL_BROKEN_LINKS="$(scan_broken_link_occurrences)"

    CURRENT_PAIR_LIST=()
    while IFS=$'\t' read -r docfile lineno target; do
        [ -z "$docfile" ] && continue
        key="${docfile}"$'\t'"${target}"
        if [ -z "${FIRST_LOCATION[$key]+x}" ]; then
            FIRST_LOCATION[$key]="$lineno"
            CURRENT_PAIR_LIST+=("$key")
        fi
    done <<EOF
$ALL_BROKEN_LINKS
EOF

    CURRENT_PAIRS="$(printf '%s\n' "${CURRENT_PAIR_LIST[@]:-}" | grep -v '^$' | sort -u || true)"
elif [ "$MODE" = "dangling" ]; then
    # CURRENT_PAIRS is not scanned from docs/** here -- it is DERIVED by
    # classifying every (docfile, citation) pair already committed in
    # doc-citation-baseline.txt (the CITATION baseline, not this one)
    # against the current tree, keeping only the pairs whose citation does
    # not classify OK. See classify_citation above and the
    # DANGLING-CITATION FLOOR header comment for the resolution rule.
    if [ ! -f "$BASELINE_FILE" ]; then
        echo "::error::baseline file not found at ${BASELINE_FILE}" >&2
        exit 2
    fi
    CITATION_BASELINE_PAIRS="$(load_baseline_pairs "$BASELINE_FILE")"
    ALL_TRACKED_FILES="$(git ls-files)"

    declare -A CITATION_VERDICT=()  # citation string -> OK | MISSING | PAST_EOF
    declare -A CITATION_DETAIL=()   # citation string -> reason (dangling verdicts only)

    CURRENT_PAIR_LIST=()
    while IFS=$'\t' read -r docfile citation; do
        [ -z "$docfile" ] && continue
        if [ -z "${CITATION_VERDICT[$citation]+x}" ]; then
            classify_citation "$citation"
            CITATION_VERDICT["$citation"]="$CLASSIFY_VERDICT"
            CITATION_DETAIL["$citation"]="$CLASSIFY_DETAIL"
        fi
        if [ "${CITATION_VERDICT[$citation]}" != "OK" ]; then
            CURRENT_PAIR_LIST+=("${docfile}"$'\t'"${citation}")
        fi
    done <<EOF
$CITATION_BASELINE_PAIRS
EOF

    CURRENT_PAIRS="$(printf '%s\n' "${CURRENT_PAIR_LIST[@]:-}" | grep -v '^$' | sort -u || true)"
else
    # One pass over docs/**: every matched occurrence as "docfile:docline:citation".
    ALL_OCCURRENCES="$(grep -rnoE "$PATTERN" docs/ || true)"

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
fi

if [ "${1:-}" = "--regenerate" ]; then
    ALLOW_GROWTH=0
    [ "${2:-}" = "--allow-growth" ] && ALLOW_GROWTH=1

    if [ -f "$ACTIVE_BASELINE_FILE" ]; then
        OLD_PAIRS="$(load_baseline_pairs "$ACTIVE_BASELINE_FILE")"
    else
        OLD_PAIRS=""
    fi

    ADDED="$(comm -13 <(printf '%s\n' "$OLD_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    REMOVED="$(comm -23 <(printf '%s\n' "$OLD_PAIRS") <(printf '%s\n' "$CURRENT_PAIRS") || true)"
    ADDED_COUNT=$(printf '%s\n' "$ADDED" | grep -c . || true)
    REMOVED_COUNT=$(printf '%s\n' "$REMOVED" | grep -c . || true)

    if [ "$ADDED_COUNT" -gt 0 ] && [ "$ALLOW_GROWTH" -ne 1 ]; then
        echo "::error::--regenerate would ADD ${ADDED_COUNT} pair(s) not already in the baseline; refused by default because the baseline may only shrink." >&2
        if [ "$MODE" = "citation" ]; then
            echo "Added pairs (one of these is probably a new citation you meant to write as file+symbol instead):" >&2
        elif [ "$MODE" = "dangling" ]; then
            echo "Added pairs (one of these citations just became dangling -- file missing or line past EOF; fix the citation or the doc instead of grandfathering it, if possible):" >&2
        else
            echo "Added pairs (one of these is probably a new dead link you meant to fix instead of grandfathering):" >&2
        fi
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}" >&2
        done
        echo "If this addition is genuinely intentional, re-run with: --regenerate --allow-growth" >&2
        echo "Note: CI's --check-growth check is authoritative regardless of this flag -- see this script's header." >&2
        exit 1
    fi

    write_baseline "$ACTIVE_BASELINE_FILE" "$ACTIVE_HEADER" "$CURRENT_PAIRS"
    echo "Regenerated ${ACTIVE_BASELINE_FILE}: removed ${REMOVED_COUNT}, added ${ADDED_COUNT}."
    if [ "$ADDED_COUNT" -gt 0 ]; then
        echo "Added (via --allow-growth):"
        printf '%s\n' "$ADDED" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            echo "  ${docfile}: ${citation}"
        done
    fi
    exit 0
fi

if [ ! -f "$ACTIVE_BASELINE_FILE" ]; then
    echo "::error::baseline file not found at ${ACTIVE_BASELINE_FILE}" >&2
    exit 2
fi

BASELINE_PAIRS="$(load_baseline_pairs "$ACTIVE_BASELINE_FILE")"

NEW="$(comm -23 <(printf '%s\n' "$CURRENT_PAIRS") <(printf '%s\n' "$BASELINE_PAIRS") || true)"
DEAD="$(comm -13 <(printf '%s\n' "$CURRENT_PAIRS") <(printf '%s\n' "$BASELINE_PAIRS") || true)"

FAIL=0

if [ -n "$(printf '%s' "$NEW" | tr -d '[:space:]')" ]; then
    FAIL=1
    if [ "$MODE" = "link" ]; then
        echo "::error::new dead relative links found that are not in the baseline (file existence only -- #anchor fragments, if any, are not verified):" >&2
        printf '%s\n' "$NEW" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            key="${docfile}"$'\t'"${citation}"
            docline="${FIRST_LOCATION[$key]:-?}"
            echo "  ${docfile}:${docline}: link to '${citation}' does not resolve to a tracked file -- fix it, or if intentionally tracked separately, grandfather it via '.github/scripts/check-doc-citations.sh --check-links --regenerate'." >&2
        done
    elif [ "$MODE" = "dangling" ]; then
        echo "::error::doc-citation-baseline.txt entries are dangling (cited file/line does not resolve, see DANGLING-CITATION FLOOR above) and not yet grandfathered in the dangling-floor baseline:" >&2
        printf '%s\n' "$NEW" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            detail="${CITATION_DETAIL[$citation]:-(reason unavailable)}"
            echo "  ${docfile}: '${citation}' -- ${detail}. Two legal responses: (1) delete the citation from ${docfile} per the 2026-08-31 ruling (cite file plus symbol name instead) and regenerate both baselines ('.github/scripts/check-doc-citations.sh --regenerate' then '--check-dangling --regenerate'); or (2) if the cited file was legitimately edited and the citation should point elsewhere, fix the citation in the doc and regenerate both baselines the same way." >&2
        done
    else
        echo "::error::new docs/** citations found that are not in the baseline:" >&2
        printf '%s\n' "$NEW" | while IFS=$'\t' read -r docfile citation; do
            [ -z "$docfile" ] && continue
            key="${docfile}"$'\t'"${citation}"
            docline="${FIRST_LOCATION[$key]:-?}"
            echo "  ${docfile}:${docline}: new citation '${citation}' -- cite file plus symbol name instead (e.g. \`radio.py: IcomRadio.set_frequency\`), never a line number; line numbers rot." >&2
        done
    fi
fi

if [ "$MODE" = "citation" ] && [ -n "$(printf '%s' "$DEAD" | tr -d '[:space:]')" ]; then
    FAIL=1
    echo "::error::baseline entries no longer found in the doc file they name -- the baseline is stale and must shrink:" >&2
    printf '%s\n' "$DEAD" | while IFS=$'\t' read -r docfile citation; do
        [ -z "$docfile" ] && continue
        echo "  ${docfile}: ${citation}" >&2
    done
    echo "Regenerate it: .github/scripts/check-doc-citations.sh --regenerate (then commit the updated baseline file)." >&2
elif [ "$MODE" = "dangling" ] && [ -n "$(printf '%s' "$DEAD" | tr -d '[:space:]')" ]; then
    # Self-liquidation is a FAILURE here, never a quiet note (unlike the
    # link baseline's DEAD handling below): a stale exemption in this
    # baseline must not keep passing silently. STRICT orphan semantics: a
    # dangling-baseline entry whose (docfile, citation) pair no longer
    # appears in doc-citation-baseline.txt at all is just as much a
    # failure as one that is still a baseline pair but no longer dangling.
    FAIL=1
    echo "::error::doc-citation-dangling-baseline.txt entries are stale -- each must be regenerated in the SAME PR that changed the underlying citation, so the citation baseline and this baseline move together atomically:" >&2
    declare -A CITATION_BASELINE_PAIR_SET=()
    while IFS=$'\t' read -r df ci; do
        [ -z "$df" ] && continue
        CITATION_BASELINE_PAIR_SET["${df}"$'\t'"${ci}"]=1
    done <<< "$CITATION_BASELINE_PAIRS"
    printf '%s\n' "$DEAD" | while IFS=$'\t' read -r docfile citation; do
        [ -z "$docfile" ] && continue
        key="${docfile}"$'\t'"${citation}"
        if [ -z "${CITATION_BASELINE_PAIR_SET[$key]+x}" ]; then
            reason="orphan: this pair is no longer present in doc-citation-baseline.txt"
        else
            reason="resolved: this citation now classifies as OK against the current tree"
        fi
        echo "  ${docfile}: ${citation} -- stale dangling entry (${reason}). Fix: run '.github/scripts/check-doc-citations.sh --check-dangling --regenerate' in the same PR that changed the citation, and commit both updated baselines together." >&2
    done
elif [ "$MODE" = "link" ] && [ -n "$(printf '%s' "$DEAD" | tr -d '[:space:]')" ]; then
    # Deliberately not a failure -- see the DOC-LINK EXTENSION / BASELINE
    # note above for why a link baseline is allowed to shrink quietly.
    DEAD_COUNT=$(printf '%s\n' "$DEAD" | grep -c . || true)
    echo "${ACTIVE_LABEL}: ${DEAD_COUNT} grandfathered link(s) no longer appear broken -- run '.github/scripts/check-doc-citations.sh --check-links --regenerate' to shrink the baseline (not required for this check to pass)."
fi

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi

TOTAL=$(printf '%s\n' "$BASELINE_PAIRS" | grep -c . || true)
if [ "$MODE" = "link" ]; then
    echo "${ACTIVE_LABEL}: clean (${TOTAL} grandfathered dead link(s), repo-wide *.md scope). Checked: the linked file exists among tracked *.md files. NOT checked: #anchor fragments, links to non-.md targets, and links with a URI scheme (https:, mailto:, ...)."
elif [ "$MODE" = "dangling" ]; then
    echo "${ACTIVE_LABEL}: clean (${TOTAL} grandfathered dangling citation(s)). Checked: the cited file exists and the cited line is within its current length. NOT checked: whether a symbol name still exists at that position (see DANGLING-CITATION FLOOR in this script)."
else
    echo "${ACTIVE_LABEL}: clean (${TOTAL} grandfathered citations)."
fi
