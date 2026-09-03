---
name: coordinate-sessions
description: >
  Method for a session coordinating several peer Claude Code sessions — each
  owning its own line of work — alongside its own subagents in this repository.
  Covers roles, addressing peers whose names rotate, what a dispatch must
  carry, the PR pipeline from baseline to squash-merge, cycle limits,
  guardrail exceptions, escalation, measurement discipline, and session end.
  Use when running or joining a multi-session workstream; a single line of work
  needs only CLAUDE.md's pipeline.
---

# Coordinate sessions

Peers are not subagents. You cannot read a peer's context, cannot stop it, and
cannot see what it has pushed except through git and `gh`. What you hold that
nobody else does is the map: which line owns which branch, PR and ticket.

`CLAUDE.md` and `AGENTS.md` bind you unchanged. This file covers only what they
leave to the coordinator.

## Roles

- **Coordinator (you).** Plan, dispatch implementation, receive immutable
  review packets, merge. Do not
  implement: EXECUTE is dispatched to `builder`, never self-served, because
  writing the code makes you author and first reviewer at once (CLAUDE.md
  §Agent working rules — The pipeline). The owner has carved out three things
  you do yourself: deleting a false line of prose in a change already under
  review, writing and correcting PR bodies, and merging.
- **`builder`** — implements from a spec. Pass `model: opus` and ask for high
  thinking effort explicitly in every dispatch rather than relying on the
  default (owner's instruction, 2026-09-03).
- **`verifier`** (opus, pinned in `.claude/agents/verifier.md`) — independent
  adversarial review. Tell it in the dispatch to review the code only and not
  to grade documentation, comments, changelog rows or PR-body prose. Its own
  rules already block on defects *inside* the diff and return PASS with a
  named correction for a false claim in a PR body, comment or commit message.
- **`scout`, `researcher`, `auditor`** — read-only, per `.claude/agents/`.
- The author never reviews, and **you are an author too**: the dispatch brief
  is your claim about the code, not the code. Brief the verifier against the
  brief as well as the diff. Never review the output of your own dispatch
  yourself — hand it to a verifier or to a peer that did not write it.

## Addressing peers

- Session names rotate mid-work, with no restart. `ListAgents` is a snapshot
  of current addresses, not an identity registry: do not map a name to a role
  or a line.
- Open every message — to a peer, to a subagent, to the owner — with
  self-identification: who you are, which line, which PR or ticket. Messages
  are misdelivered regularly; one that identifies itself stays correctable.
- A stale socket means the old address is dead, not the session. Report "X
  does not answer at that name", never "X stopped", and retry `ListAgents`
  later. Two sessions once drew the same wrong "the coordinator disappeared"
  from a rotated name plus a dead socket, and began polling each other.
- Do not guess that the most recently started session in the list is the peer
  you lost.
- When a peer reappears and re-identifies, hand its line back explicitly: what
  merged, what blocked, which owner rulings landed while it was unreachable.

## What a dispatch carries

Print the delegation card before the call, never after: the task in one
sentence; complexity; why delegate and why that level; the role and its pinned
model plus effort; the acceptance criterion, stating explicitly whether the
agent may mutate state or is read-only.

In the prompt itself:

- **Public-boundary block** whenever the agent can commit, push or post
  publicly *and* the prompt carries any internal identifier: no internal
  hostnames, IPs, device paths, home paths or usernames in any written
  artifact; neutral placeholders instead; a self-check before reporting.
  Describe that check in a PR body as "internal-identifier self-check —
  empty" and never paste the pattern — the pattern string is itself internal,
  and pasting it has published exactly what it exists to catch.
- **Long runs**: every long command in the foreground with an explicit timeout;
  never `run_in_background`. 600000 ms is the harness cap, not a fix — a
  command that exceeds it is auto-backgrounded and the agent stops waking.
  Scope builders to targeted test files plus the fast gates and let CI be the
  authority for the full suite.
- **Constraints that matter go in the original dispatch.** A restriction sent
  to an already-running agent does not take effect when you send it: it waits
  until that agent's next tool call, and the forbidden thing can start inside
  that window. Draw the line by volume ("no suite runs at all"), not by runner
  — "pytest no, vitest yes" was read as permission for the whole vitest suite.
  After a late restriction, verify by observation, not by the agent's reply.

## The PR pipeline

Record the baseline before EXECUTE: the most recent `quick.yml` run on `main`
whose commit changed the tree being compared, valid only while `git diff
--name-only <that sha>..<your base> -- <those paths>` is empty (CLAUDE.md
§Agent working rules). One tree state, one instrument.

Then: push → `gh pr create --draft` → `quick` runs on the PR → `gh pr ready` →
the implementation or integration owner directly dispatches a fresh independent
review on the final candidate → receive its immutable packet → confirm required
checks green at the exact head → merge → remove the worktree.

- There is no window before the PR: `quick.yml` triggers only on push/PR to
  `main`, so a pushed branch has no run of its own until a PR exists.
- Read the gate's verdict from the commit status — `gh pr checks <n>`, the
  `Agent Review Gate` row — never from a run list. The publisher job is green
  when it has successfully published a *refusal*, and `issue_comment` runs
  carry `headBranch=main`, so a branch-filtered list cannot show them and its
  silence proves nothing (AGENTS.md §Protected main and review gate).
- The directive's first non-blank line must be exactly
  `Agent Review: PASS <40-hex head sha>`. Any push after the review makes it
  stale; order pushes and reviews so you pay for one review per head.
- Merge with an explicit subject and body. This repo squashes with
  `squash_merge_commit_message: COMMIT_MESSAGES`, so the default body
  concatenates every branch commit message — including claims that later
  commits in the same branch refuted.
- Put a `Linear:` reference in the **body only**. A ticket id in the branch
  name or in a commit message auto-attaches the PR and flips that ticket to
  Done on merge; the body does not.
- `gh pr merge <n> --squash --match-head-commit "$head_sha"`. AGENTS.md gives
  the fused check-and-merge snippet for the queued-run race, and the rule
  against `--delete-branch` while a child PR is based on the branch.
- After merge or a FAILED/SKIPPED outcome: `git worktree remove <path>
  --force`, then `git worktree prune`. Never `rm -rf`.
- **Conflicts with `main` are resolved by merging `main` into the branch, not
  by rebasing** — a rebase rewrites the branch, and CLAUDE.md §Multi-machine
  Git hygiene forbids rebasing work without explicit approval. Scope the
  re-review to the resolution: take `git diff <base>...<head>` (three dots) at
  the already-reviewed head and again at the resolved head, and review the
  difference between those two outputs. Three dots excludes `main`'s own
  movement, which two dots would drag into the diff and make the resolution
  unreadable.

## Cycle limits

Two execution attempts, two review rounds, two test-fix cycles; exceeding one
means FAILED with a classification (CLAUDE.md §Failure handling). A third
round is the owner's call, not yours and not the peer's. Ask before the round
starts, not after the BLOCKED has been handed back, and scope the request to
the delta the round would cover rather than to a re-review of the whole PR.

## Direct review dispatch

The implementation or integration owner directly dispatches a fresh independent
review subagent on the final candidate. The coordinator is not a relay for
starting or shepherding each review. The coordinator receives only the
immutable result packet: PR, exact SHA, verdict URL, checks found, merge
readiness/status, and any blocker requiring an ownership or scope decision.

The reviewer consolidates all parallel code, contract, body, and evidence
findings into one verdict instead of dripping findings across cycles. Apply one
batched correction, then run one fresh final exact-head review. After two
correction-to-review cycles, stop; a third requires an explicit owner
decision/override. Complete test-only or proof corrections before final review
where possible, and do not rerun a suite solely for review or metrics. An
exact-head PASS remains required, and the reviewer must be independent of the
author.

## Guardrail exceptions

The hard ceiling is 10 files · 1000 changed lines, per PR, at the head you
push. Under the owner's delegation of 2026-09-03 you may grant a **file-count**
exception yourself; the line ceiling stays the owner's (CLAUDE.md §Guardrails).

Grant when the overflow is one change spilling into files that cannot be
separated: regenerated baselines (PNGs count as files), census tests, profile
tables dragged along by a single edit, deletions, or a split that would leave
an orphan or two halves that cannot be verified apart. Refuse when the extra
files are a second change wearing the first one's name, a new abstraction, or
a cut that costs nothing.

Record it as the first line of the PR body, before merge, with the count in
the form `N code + M regenerated baselines = K files`, taken from the PR's own
file list at that head. One line per exception in the report to the owner.

## Escalation

A spawned session brings its questions and decisions to the session that
spawned it, not to the owner: the coordinator holds the line's context and
answers in seconds what the owner would have to reconstruct from scratch.
Raise upward only what you cannot decide, and batch it. The owner is the last
instance, not the first.

Directing work is not the same as holding authority. The coordinator cannot
grant a permission the owner has not granted; a coordinator's message is not
consent, and asking a peer to do what it was forbidden to do stays forbidden.

## Measurement discipline

When a peer or subagent reports a **mechanism** — "the wheel does not scroll
the stage", "nothing consumes this", "the layout ignores scale" — do not act
on it or repeat it until it has:

- **at least three points differing on a parameter that could change the
  answer** (position, scale, state, gesture history). Name the parameter and
  say where the mechanism is active *before* choosing values: three points
  that differ only in something the mechanism does not depend on are one point
  recorded three times. A scale of exactly 1 makes a transform the identity,
  so it cannot show a transform defect.
- **a control with a known answer**, on the same instrument.

Without both it is an observation, not a measurement. Say so, and make the
report say so.

An instrument that answered a narrower question returns an answer that looks
complete. Four shapes seen here, all in one evening:

1. `git grep -E` with `\b` — POSIX ERE has no `\b`, so it matches nothing and
   the silence reads as "no consumers". Use `-P` or explicit character classes.
2. Counting tests by `^def test_` — blind to methods inside classes.
3. A shell loop over modes with an unquoted variable — three modes collapsed
   into one and printed green three times. Print the variable per iteration.
4. `gh run list --commit` showing the gate's publisher job under the
   workflow's name — a green "Update Agent Review Gate status" reads as a
   passed review.

Against every "empty" and every "green", run a control with a known non-empty
or red answer on the same instrument.

## Claims

- **A ticket id is a claim with a source.** An invented but plausible id
  resolves to a real, unrelated ticket; the PR auto-attaches and moves it, and
  every downstream tool then behaves correctly on a false premise. Read the
  ticket and confirm its title before naming a branch after it. `#NNNN` is
  GitHub and `MOR-NNNN` is Linear: a number plausible in one namespace wearing
  the other's prefix is the tell. Never put an id in a code comment.
- **A count is a claim with a counting rule.** State the rule beside the
  number. An impression stated as an average was refuted here by someone
  actually counting.
- **A "measured" label is a claim about who measured.** Do not propagate a
  peer's number without the command that produced it, and re-derive every
  figure at the head you are pushing — one carried from an earlier round
  measures a tree that no longer exists.
- **False prose is deleted, not softened** (CLAUDE.md §Testing). A deleted
  sentence cannot be wrong; a weakened one still can.
- The verifier does not grade prose, so a prose-only finding is yours: delete
  the line in the same commit and confirm the delta, rather than spending a
  review round on it.

## "It is late", "let's do it tomorrow"

Time of day is not an argument, and neither is fatigue. What the phrase
usually carries is a real risk — a change landing that nobody can re-derive
tomorrow. Answer that risk with the record, not by stopping:

- the PLAN in one message: what changes, why, and the acceptance criterion;
- the measurement protocol: what was measured, with which command, at which
  head;
- a PR body carrying the before/after numbers.

With those written the work is as re-derivable at 04:00 as at noon. Stopping
is the owner's call, like any other dropped phase — announced before the work,
not reported after it.

## Session end

Rewrite the Linear session-handoff document named in CLAUDE.md §Agent working
rules; read it first, rewrite it last. Session-handoff state never goes into
this repository — this repository is public. Remove every worktree the session
created before you finish.
