# Autonomous Issue Resolution

Resolve `$ARGUMENTS` under `AGENTS.md` and
`docs/internals/coordinator-policy.md`. The coordinator owns planning and
integration; assigned workers retain their roles. Resolve the Linear planning
owner and current acceptance criteria where the repository delegates planning
there. A GitHub execution issue must link that owner, not duplicate its scope.

## Pre-flight and plan

1. Read applicable instructions from the exact active worktree. Fetch refs and
   inspect status under the Git hygiene rules; preserve uncertain work.
2. Resolve scope, dependencies, reproduction/evidence, and acceptance. Use an
   ordinary tool for narrow lookup; use a researcher only for a bounded problem
   that benefits from independent exploration. Filter responses before output.
3. Create or use the assigned isolated `codex/<topic>` worktree; never edit
   `main` or an uncertain shared checkout. Do not disturb active services.
4. Plan the minimal change from those findings. Respect `CLAUDE.md` guardrails;
   missing authority, a real safety collision, or a blocking ambiguity stops
   dependent work. No invented numeric confidence threshold decides scope.
5. For behavioral regression claims, record relevant existing baseline evidence
   in private working notes outside all worktrees. Check its path freshness;
   skipped gates provide no test counts. Documentation-only work needs no suite
   baseline. Do not add a full run solely to populate a workflow phase.

## Implement and freeze

Dispatch one builder for material implementation with the plan, exact file
lease, explicit supported model/effort, checks, exclusions, allowed actions,
and private report destination. A small deterministic edit may use ordinary
tools; required independent review still uses a different author.

Prefer tests before behavior changes. Use focused changed-scope development
checks, preserve meaningful failure evidence, and finish related corrections
before freezing the candidate. Follow `AGENTS.md` for batching; workers do not
launch separate natural suites, PRs, or final reviews for one delivery batch.

Commit with an English conventional message linked to the owning issue. Push
the final candidate and open or mark one PR Ready against `main`. Link every
covered Linear child explicitly; use `Closes #N` only for an actual GitHub
execution issue whose scope is completed. No draft PR may merge.

## Independent review and CI in parallel

Dispatch a fresh verifier immediately against the frozen exact head. Review
scope, safety, correctness, layering, and claims; the builder cannot review its
own work. Relay the verifier's SHA-bound PASS/BLOCKED verdict as soon as it is
ready, with CI state as found. CI completion does not gate the code verdict.

Assign one CI observer to the candidate's required run. Reuse its evidence for
regression comparison and merge readiness; do not re-read the same unchanged
run at each phase or duplicate full suites across roles. Diagnose failures from
existing artifacts and a focused reproduction. Test counts alone do not prove
behavioral equivalence. Documentation-only changes follow `AGENTS.md`: no
citation, link, Markdown, product, visual, or full automation.

A corrected head needs its required new-head run and fresh delta review. Expand
review only for affected dependencies or concrete interaction risk. Main
movement alone does not invalidate an unchanged candidate.

## Merge readiness and completion

- Recheck the current diff and guardrail counts at the candidate head; justify
  one unit of work when it crosses the soft threshold.
- Apply all REQUIRED BEFORE MERGE and MANDATORY SQUASH-BODY CORRECTION findings.
- Immediately before merge, verify the current head, exact-head Agent Review
  Gate, and all required statuses; guard the merge with `--match-head-commit`.
- Follow `AGENTS.md` for final aggregate main evidence and separate installation,
  visual, and hardware acceptance where required. Do not equate merge with those
  acceptance gates.
- Deliver status, commit/tree, files, evidence, risks, and next action to the
  coordinator. Update the authoritative private checkpoint, not repository
  operational state files.

After two unsuccessful attempts without new evidence, change the hypothesis,
escalate the bounded diagnosis, or report the external blocker. Classify
failure under `CLAUDE.md`; continue other useful authorized work. Scope and
safety limits remain binding. Hardware work requiring owner presence waits
for that acceptance gate rather than claiming mocked evidence is sufficient.

Preserve the worktree after PR creation, failure, or skip until ownership is
released, required work/evidence retained, its state verified safe, and cleanup
is authorized. No automatic forced removal, startup pruning, or session reset.
