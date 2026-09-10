# Refactor

Manual `/refactor <target>` workflow for a module, area, or concrete code smell.
Read `AGENTS.md` and `docs/internals/coordinator-policy.md` from the assigned
worktree. The coordinator retains integration ownership; workers keep their
assigned roles. No automatic refactoring or unrelated feature work.

## Invariant and plan

Refactoring must preserve behavior and public API. Ground the plan in the
actual target code, relevant tests, and current acceptance criteria. A code
smell alone does not authorize a new layer, abstraction, or speculative cleanup.
Resolve the planning owner under the repository's Linear delegation first.

Record the plan and evidence in private artifacts outside every repository
worktree. Include goal, non-goals, exact files/symbols, small reversible steps,
risks, and how behavioral equivalence will be checked. Respect `CLAUDE.md`
guardrails; no changes outside the authorized plan.

Use existing relevant baseline evidence and verify compared-path freshness.
A skipped CI path provides no counts; absent evidence is an explicit gap, not
an instruction to rerun a full suite. If the affected behavior lacks coverage,
add focused regression coverage before implementation within the approved
scope. A failed relevant baseline must be understood before relying on it.

## Implement and freeze

Dispatch a builder for material implementation with explicit supported
model/effort and the bounded contract. Use ordinary tools for a small
mechanical edit when that is sufficient; independent review remains separate.

Use focused changed-scope checks after meaningful steps or corrections. Keep
related fixes in one delivery batch and freeze one candidate after focused
checks pass. Do not start a full suite per step. Record changes and evidence
privately. On failure, preserve the evidence and diagnose before correcting;
revert only your known changes when safe. Never blanket-checkout files or
rollback uncertain work. A proven behavior change violates this contract and
must be corrected before the refactor can pass.

After two unsuccessful attempts without new evidence, change the hypothesis,
escalate a bounded diagnosis, or report the external blocker; arbitrary retry
counts do not end useful work. Scope expansion or missing safety authority
stops dependent action until resolved.

## Review and CI in parallel

Commit an English `refactor:` message linked to the owning issue, push the final
candidate, and open or mark one PR Ready against `main`. Immediately dispatch a
fresh independent verifier on that exact head while required CI runs. Have it
review the intended improvement, behavior and API preservation, tests, and
unintended changes. Equal pass counts alone do not prove equal behavior.

One observer owns the candidate's CI run and provides evidence to all roles.
Use the `AGENTS.md` path contract and existing artifacts; do not duplicate full
suites. Review returns its code verdict with CI state as found, without waiting
for CI completion. A correction requires new-head CI and fresh delta review;
broaden only for affected dependencies or concrete interaction risk.

Documentation-only changes use their explicit `AGENTS.md` exception: readback,
diff proof, and independent review without check automation.

## Acceptance and preservation

Re-derive guardrail counts at the pushed head and justify the batch size when
needed. The PR states the actual change and evidence. Apply required review
corrections, verify exact-head Agent Review Gate and all required statuses
immediately before a guarded merge, and follow `AGENTS.md` for final main
acceptance. Installed, visual, and physical-radio acceptance remain separate.

Return status, commit/tree, files, evidence, risks, and next action to the
coordinator. Keep operational progress and handoff outside all worktrees;
publish only reusable architecture decisions when appropriate.

Preserve active or uncertain worktrees and services. Cleanup requires released
ownership, retained work/evidence, verified safe state, and existing authority;
never force-remove automatically. Context size or corrections alone do not
require resetting the session. Follow the policy's supported ownership transfer
before any succession; a new chat does not inherit radio or process ownership.
