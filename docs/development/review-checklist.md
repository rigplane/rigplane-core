# Builder review checklist

Goal: a PR needs one or two Codex review rounds, not four to ten. Codex posts only
its top few findings per pass, and every fix is new code that gets new findings,
so the cheap way to save rounds is to find the defects before the first push.
Walk this list against your own diff, then run the local pre-review, then push.

## Measured basis (2026-10-20 to 2026-10-21, merged PRs in Core and Pro)

- PRs of at most 500 changed lines: 0.7 Codex finding-rounds and 0.9 findings on
  average. PRs above 500 lines: 2.1 rounds and 3.6 findings (Linear MOR-3621 has
  the per-PR table).
- The classes in section 2 are clusters of the 188 top-level Codex findings on
  five repositories since 2026-10-07 (infra-control included); most recur on
  several PRs.

## 1. Size: at most 500 changed lines per PR

- Target: additions plus deletions at most 500, and at most 6 files. Above that,
  split before you push; do not ask for review of a 1,500-line PR. The only
  exceptions are listed in this section (generated baselines, fixtures, pure
  moves).
- Split along seams that can merge alone: schema/data model with its tests, then
  the runtime that uses it, then docs and CI wiring. A stacked PR is fine.
- A fix and its test stay in one PR. Generated baselines, fixtures and pure file
  moves may exceed the target; say so in the PR body.
- This is a review-efficiency target below the limits in `CLAUDE.md` (soft
  threshold 6 files and 600 changed lines, hard ceiling 10 files and 1,000
  lines); it does not replace them. The exceptions above change the target
  only, never the hard ceiling.

## 2. Defect classes to walk, function by function

For each changed or added function, ask every question. "Not applicable" is a
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
   remove files), does one failed step skip the rest? Use one `try/finally` per
   step.
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
11. **Tests can fail.** Each fix has a test that fails on the old code; name the
    mutation that turns it red. Test the failure path, not only the happy path.
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
   created by a lane script), do not skip the step silently: write
   `pre-review: not run (no helper)` in the PR body so the coordinator can run
   it. The script runs Codex read-only on the branch diff and writes every P0-P2
   finding, grouped by file, to `tmp/pre-review.md`. Fix all of them, fix the
   siblings of each that lie inside your assigned scope (list the others in the
   PR body), commit, and run it again until it prints `PRE-REVIEW: CLEAN`. If a
   finding is wrong, put the evidence in `tmp/pre-review-reply.md`. If round
   four still reports findings, do not push: the change is usually too big or
   has a design flaw, so report what is left to the coordinator.
3. Push once per round. Never push a fix per finding: each push can start a new
   GitHub review.

## 4. When Codex still reports on GitHub

- Read every thread of the current head before changing anything. Group them by
  root cause, fix the class (grep for siblings), answer each thread, and push
  all fixes in one commit.
- Fix every valid P0-P2 finding with a test, or answer it with evidence that it
  is not a bug, before resolving the thread. A false documentation claim is a
  P2 and gets fixed. A builder never defers a valid P0-P2 to a ticket.
- Only the coordinator may defer: for internal test and bench tooling, P2 polish
  that cannot cause a wrong verdict, dirty state, lost data or a hung run may go
  to a ticket, and the coordinator records the ticket id in the thread before
  resolving it. P3 never blocks.
- Re-run `tmp/pre-review.sh` before the re-push.
