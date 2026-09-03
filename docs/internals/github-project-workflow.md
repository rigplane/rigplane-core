---
robots: noindex, follow
---

# GitHub Execution Workflow

The `RigPlane Core UI Composition Architecture v3` project uses Linear for
planning and GitHub for bounded implementation evidence. Its live,
project-specific Linear control-plane contract takes precedence over the legacy
GitHub-Project planning and control-plane language in `CLAUDE.md`. This document
defines the GitHub execution plane; it does not create a second backlog or
roadmap.

That precedence changes only control-plane ownership. All other `CLAUDE.md`
commands, architecture, hygiene, protected-main, exact-head `Agent Review
Gate`, and guarded merge rules remain binding. The batching and CI cadence in
this document and `AGENTS.md` supersede older draft-first CI and per-merge
`main`-wait instructions; safety and acceptance gates remain binding.

## Control-plane boundary

Linear is authoritative for:

- backlog and scope;
- parent/child relations and dependencies;
- priority and milestones;
- acceptance criteria and status.

GitHub is authoritative for:

- implementation branches and commits;
- pull requests and diffs;
- checks, independent review, and merge evidence.

Resolve the Linear owner before any non-trivial implementation, and work from
its current acceptance criteria. Do not infer scope, priority, dependencies, or
completion from GitHub labels, Projects, issue state, or a merged PR.

## Agent intake checklist

Before creating a branch, PR, or optional GitHub issue, an agent must:

1. Identify the existing Linear issue and read its current acceptance criteria.
2. Confirm that the Linear issue is ready and that its dependencies permit work.
3. Check for an existing GitHub PR or branch for the same Linear issue.
4. Define the smallest concrete PR-bound scope and owned paths.
5. Create a GitHub issue only when that atomic execution scope benefits from a
   GitHub-native discussion; link the existing Linear issue in it.

Do not create a GitHub planning issue before resolving the Linear owner. A
GitHub issue is optional, never a substitute for the Linear item, and must not
carry a separate plan, dependency graph, priority, milestone, acceptance
criteria, or status.

## Planning-only GitHub issues

Planning-only GitHub issues are legacy execution artifacts and must be retired,
not maintained. First identify and link the existing Linear issue that owns the
scope. Then add a concise retirement note naming that Linear issue and close the
GitHub issue as superseded. If no Linear owner exists, create or obtain the
Linear item first; do not continue GitHub planning while it is unresolved.

## PR workflow

Every non-trivial PR must link its Linear issue, directly or through an allowed
atomic GitHub execution issue. Keep PR text focused on the implementation,
checks, review, and merge evidence.

The coordinator's initial dispatch records the Linear owner, current acceptance
criteria and dependencies, exact file lease, and verification matrix. The
builder then owns routine decisions within that contract and escalates only a
real contract, scope, ownership, dependency, or safety collision.

Combine related child tickets in one branch and PR when they share one semantic
contract, file lease, and verification matrix. Link every covered Linear child
and reconcile each one after merge. Keep unrelated contracts, unleased paths,
compatibility breaks, and TX/PTT or hardware safety work separate.

Before opening the final PR:

1. Fetch and inspect the repository state; use a fresh issue branch/worktree,
   never shared `main`.
2. Confirm the Linear acceptance criteria still match the bounded change.
3. Run focused changed-scope verification on the Mac mini and check the diff
   for unintended changes. Intentional RED and development iterations do not
   run the full suite.
4. Ensure public/open-core boundaries are preserved and redact private bench or
   environment details from public artifacts.
5. Push the final candidate and open it Ready, or mark it Ready once. Draft
   pushes do not run the expensive self-hosted `quick` or `visual` jobs;
   `ready_for_review` starts the candidate's one natural required run.

The immutable final head gets one natural `quick`; `visual` runs only for its
affected paths, and `full` is reserved for a release or an explicitly recorded
cross-cutting risk. The independent reviewer consumes those artifacts and does
not rerun suites. A substantive correction creates a new final head, which gets
its own natural required run and exact-head review. Movement on `main` alone is
not a reason to refresh a branch or repeat its checks/review; do that only for
an actual conflict, proven base-sensitive dependency, or concrete interaction.

Before merging a non-trivial PR:

1. Use a fresh independent reviewer (the verifier) who did not author the
   change.
2. Verifier: review the exact current 40-hex head SHA and post a normal
   comment beginning `Agent Review: PASS <full-40-hex-head-SHA>` only after
   a PASS result. Report CI state as found; do not wait for it to finish
   (AGENTS.md § Protected main and review gate).
3. Coordinator or merge operator: confirm the PR is non-draft, all required
   checks are green, and the exact-head `Agent Review Gate` is green.
4. Merge with `--match-head-commit` against the expected head SHA. A merge
   operator may train-merge file-disjoint, independently green PRs without
   waiting for or rerunning intermediate `main` quick runs.
5. Re-read Linear acceptance criteria and reconcile Linear status deliberately;
   a merged PR or closed GitHub issue alone is not acceptance.

After the final train merge, require one aggregate `main` quick on that final
batch head. A real conflict, proven interaction between train members, or final
aggregate failure stops the train for diagnosis. Recent draft/head churn
produced multiple cancelled and duplicate full quick runs on the single shared
runner; Ready-only PR CI and one final aggregate run remove that queue churn
without weakening exact-head review or required checks.

`main` remains protected. A `BLOCKED` review identifies the problem, required
fixes, and verification; refresh review and checks after changing the head.
Cancelled required checks must be rerun and reach a terminal green result before
merge.

## Optional GitHub execution issues

When an atomic GitHub issue is justified, keep it limited to the concrete
PR-bound execution slice. Link the Linear issue, reference the intended PR, and
avoid duplicating Linear planning data. Close it only as implementation evidence
after the relevant PR merges; Linear remains the source for acceptance and
status.
