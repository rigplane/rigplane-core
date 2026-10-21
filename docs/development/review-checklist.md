# Builder review checklist

Goal: a PR needs one or two Codex review rounds, not four to ten. Before the
Code Review Rules in `AGENTS.md` existed, Codex's GitHub reviews posted a median
of one finding per pass (mean 1.6, at most 6), and every fix is new code that gets new findings, so
the cheap way to save rounds is to find the defects before the first push.
Walk this list against your own diff, then run the local pre-review, then push.

## Measured basis (2026-10-20 to 2026-10-21, merged PRs in Core and Pro)

- PRs of at most 500 changed lines: 0.7 Codex finding-rounds and 0.9 findings on
  average. PRs above 500 lines: 2.1 rounds and 3.6 findings (Linear MOR-3621 has
  the per-PR table).
- The classes in section 2 are clusters of the 188 top-level Codex findings on
  five repositories since 2026-10-07 (infra-control included); most recur on
  several PRs.
- Method: Codex findings come from `gh api --paginate
  repos/<owner>/<repo>/pulls/<n>/comments`, keeping comments by
  `chatgpt-codex-connector[bot]` with no `in_reply_to_id`; a finding round is one
  head (`commit_id`) with at least one such comment. The PR set is the merged
  PRs listed by `gh pr list --state merged --search "merged:>=2026-10-20"`
  (changed lines = additions plus deletions from `--json additions,deletions`),
  filtered to merges after the Codex app was installed on the organization
  (2026-10-20 16:36 UTC). The findings corpus used `--search "updated:>=2026-10-07"`
  on each repository. The per-PR table is in Linear MOR-3621.

## 1. Size: at most 500 changed lines per PR

- Target: additions plus deletions at most 500, and at most 6 files. Above that,
  split before you push; do not ask for review of a 1,500-line PR. The only
  exceptions are listed in this section: generated baselines, fixtures, pure
  file moves, and an indivisible change (one cohesive fix and its test with no
  independently mergeable seam). Say which exception applies in the PR body.
- Split along seams that can merge alone: schema/data model with its tests, then
  the runtime that uses it, then docs and CI wiring. Each piece must be
  mergeable and verifiable on its own.
- A fix and its test stay in one PR.
- This is a review-efficiency target below the limits in `CLAUDE.md` (soft
  threshold 6 files and 600 changed lines, hard ceiling 10 files and 1,000
  lines); it does not replace them. The exceptions above change the target
  only, never the hard ceiling.

## 2. Defect classes to walk, unit by unit

Code is written by Kimi via `route run`; Claude orchestrates and checks. Which findings
must be fixed is decided by the severity policy in section 4.

For each changed or added unit (a function, a workflow step, a configuration or
schema declaration, a shell command, a documentation claim), ask every question. "Not applicable" is a
valid answer; skipping the question is not.

1. **Untrusted or dynamic input.** Does the value come from a file, socket,
   device, CLI argument, profile or another process? Check type (a string where
   a bool is expected, an unhashable name), range (NaN, infinity, negative,
   zero), shape (path with `..` or absolute path, IPv6 address that needs
   brackets, truncated, stale or mislabeled frame, reply that only shares a
   prefix with the request). Reject with the documented error; do not coerce.
2. **Failure paths.** For every call that can raise, time out or return an
   error: is it caught where the caller expects an outcome (a failed receipt, an
   abort), not a traceback? When many steps must all run (restore settings,
   remove files), does one failed step skip the rest? Every step must be attempted even
   when an earlier one fails: nest the `finally` blocks, or run each step and
   collect its exception, then re-raise after all of them ran. A failed key-up
   must not skip restoring power or settings.
3. **State restored or cleaned up on interrupt.** Can SIGTERM, SIGINT, kill or
   a crash arrive between any two statements? Are signal handlers installed
   before the first side effect? Does `BaseException` (cancellation, a custom
   interrupt) bypass your `except Exception`? Are temp files, sockets, PTYs,
   process groups, scheduled wakes and locks undone, and only the ones this run
   created? If you journal progress, decide whether the entry means "about to"
   or "done" and make recovery match.
4. **Concurrency, cancellation, ordering.** Can two instances run at once (lock
   acquired before IDs and files are created, stale-lock check that survives PID
   or process-group reuse)? Can a cancel arrive before the thing it cancels
   exists? Is a fire-and-forget call followed by a step that depends on it? Does
   a multi-await sequence on shared hardware hold a lock? Can stream packets
   arrive between a request and its reply? Is cancellation awaited before the
   resource is reused?
5. **Time and clocks.** A fixed `sleep` used as synchronization is a bug: wait
   for the condition with a deadline. Does a measurement window start when the
   thing under test is ready, not when the command started? Do budgets add up to
   more than the time left? Is the timeout shorter than the callee's own
   bounds? Wall clock where monotonic is needed? Is the very first sample
   already "success"?
6. **Verdicts fail closed.** `|| true`, an empty result, "could not measure" and
   partial coverage must not become PASS. `any()` over repeated cycles should
   usually be `all()`. A later PASS must not overwrite an earlier FAIL. A
   baseline must not decrease silently. Success needs positive evidence that the
   effect happened (observed outage, observed recovery), not only a 2xx.
7. **Contract fidelity.** Read the pinned contract, schema, profile or fixture
   instead of remembering it: status-only endpoints, which field holds the model,
   byte values, whether the radio or transport supports the feature at all.
   When you tighten one matcher or validator, grep for every other path that
   does the same job and fix the ones inside your assigned scope in the same
   change; report the ones outside it in the PR body.
8. **Siblings and companion files.** Grep for every place that names or branches
   on the thing you changed: docs and runbooks, allow-lists, label lists,
   topology whitelists, Makefile `.PHONY`, tests that pin literals, other
   workflows, instruction files, release inventories, runner prerequisites.
9. **Prose is true.** Every comment, docstring, README line, commit message and
   PR-body claim (a number, "read-only", "never", "all", "disabled by default",
   a reproduction command) is checked against the code as of your last commit.
   Reproduction commands must run from this repository. Delete a claim you
   cannot verify.
10. **Restart, upgrade, rerun (services and deploy code).** What happens after a
    restart with persisted state, after a secret rotates, when upgrading from
    the previous deployment, when a limit is lowered, when the task runs twice?
    Is the rerun idempotent, and does a retry keep what it must?
11. **Tests can fail.** Each behavior fix has a test that fails on the old code;
    name the mutation that turns it red. (A documentation-only correction needs no
    test.) Test the failure path, not only the happy path.
12. **Process.** Change class and Linear id in the PR body (freeze rules), no
    personal data (user names, host names, addresses, keys) in code, fixtures or
    text, nothing private in a public repository, no secret exposed to fork PRs.

## 3. Before every push

1. Walk section 2 against the whole diff, including the lines your latest fix
   added: new code gets new findings.
2. Commit, then run the local pre-review: `tmp/pre-review.sh` from the worktree
   root. The coordinator's lane scripts copy that file into every builder
   worktree when they start the lane, and it needs the Codex CLI logged in with
   the ChatGPT subscription. If the file is missing (the worktree was not
   created by a lane script), stop and ask the coordinator to provision it (a
   copy of the lane scripts' `pre-review.sh`), and do not push until it prints
   `PRE-REVIEW: CLEAN`. The script runs Codex read-only on the branch diff and
   prints the findings that must be fixed (section 4), grouped by file, as its
   output, followed by a short list of deferrable items. Fix all the
   must-fix ones, put the deferrable ones in the PR body under "Deferred", fix
   the siblings of each that lie inside your assigned scope (list the others
   in the PR body), commit, and run it again until it prints
   `PRE-REVIEW: CLEAN`. If a finding is wrong, pass the evidence with
   `tmp/pre-review.sh -R '<evidence>'`; the next round reads it. The script
   keeps its working files (prompt, diff, round reports, rebuttals) outside the
   checkout, in a private directory of the coordinator's lane scripts, and
   deletes them when it prints CLEAN; nothing is written into the repository.
   Keep going while each round brings new evidence and your fixes stay inside
   your scope; new defects of an already known class are yours to fix. Stop and
   report what is left to the coordinator only when a round repeats a finding
   you believed fixed (the root cause is unresolved), the change would cross the
   size or scope limits, or a fix needs a decision outside your scope. Never
   push before CLEAN.
3. Push only when the local pre-review has printed `PRE-REVIEW: CLEAN`, and push
   once. Never push a fix per finding or an intermediate state: each push can
   start a new GitHub review. After a GitHub review round, push all fixes of
   that round together (section 4).

## 4. Severity policy: what to fix, what to defer

It applies to the local pre-review and to Codex threads on GitHub alike.

- Always fix before merge: P0, P1, and a P2 that changes behaviour (a wrong
  result or verdict, data loss, state left dirty, a hang, a security or
  radio-transmit safety defect, a false claim about safety or support in
  user-facing documentation). A behaviour fix gets a regression test; or answer
  the finding with evidence that it is not a bug.
- Do not spend a round on documentation wording, comments, PR-body phrasing,
  naming, style, test-only polish, or robustness nits in internal tooling that
  cannot change a verdict or leave state dirty. On a GitHub thread reply
  `deferred to MOR-xxxx` and resolve it; the gate accepts a resolved thread.
  One follow-up ticket per PR collects the deferred items (builders list them in
  the PR body, the coordinator files the ticket). Prose fixes ride along with the
  next code change; a prose-only PR gets one backup review pass, no Codex loop.
- P3: ticket or ignore.
- At most two Codex rounds per PR. If a third would be needed, escalate to the
  coordinator.
- When Codex reports on GitHub: read every thread of the current head, group
  them by root cause, fix the class that must be fixed (grep for siblings),
  answer every thread, re-run `tmp/pre-review.sh`, and push all fixes of the
  round in one commit.
