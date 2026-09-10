# Coordinator operating policy

Adopted operating rules for coordinators, workers, and verifiers. A new coordinator must read this policy through the startup requirement in `AGENTS.md`. Assigned workers and verifiers apply its operating rules while retaining their dispatched roles; reading it does not appoint another coordinator or transfer ownership. The initial coordinator target is Astra/Low, requested through a supported runtime setting. Policy adoption does not install or activate an automatic lifecycle handler.

## Objective and authority

Maximize accepted engineering outcomes per scarce usage allowance, accounting for coordinator and worker inference, retries, verification, and human rework. Preserve correctness and required independent review. Do not equate token count, credits, context occupancy, and remaining subscription allowance.

Use instructions from the exact active worktree, not another checkout. Where repository instructions delegate planning to Linear, Linear owns scope, acceptance, dependencies, and progress; GitHub owns code, review, and CI evidence. Existing repository rules govern batching, checks, merge, and hardware acceptance. This policy does not replace those gates.

Respect platform/system and developer requirements first. Within those limits, current explicit user instructions override repository policy. Applicable repository instructions govern execution and may explicitly delegate planning authority to Linear; preserve that delegation rather than applying a blanket repository-over-Linear rule. A verified versioned checkpoint supersedes historical status summaries, but cannot override current instructions, live authoritative evidence, or earlier user decisions and permissions that remain in force. Resolve material conflicts before dependent action. For orchestration, routing, output, CI observation, and session lifecycle, this policy supersedes conflicting legacy role or command wording. That limited precedence does not waive scope, safety, required checks, independent review, guarded merge, or hardware acceptance.

## Model and delegation defaults

Keep Astra as the root coordinator initially; treat its advantage as a working choice to evaluate, not a universal requirement. Request Low for routine orchestration through an actual supported setting. A sentence in a prompt does not confirm the active reasoning setting changed. Do not claim an unverified setting change or manipulate the current running turn through an unsupported mechanism.

For an authorized new Codex coordinator task, the task-creation request must
explicitly select `model: "gpt-6-astra"` and `thinking: "low"` when the current
host advertises that combination. A CLI launch can use a one-run override:

```sh
codex -C /path/to/assigned/worktree --model gpt-6-astra \
  --config 'model_reasoning_effort="low"'
```

These are launch settings, not a role-selective global or project default.
Verify the selected model/effort in the created task or UI before claiming it
is active. Do not change user/project defaults or worker defaults to enforce
this coordinator target. Automatic enforcement still needs the authorized
creator to supply and verify the launch arguments; this policy does not supply
that creator or activate succession. CLI override precedence and explicit
subagent selection are documented in [Codex configuration
precedence](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence)
and [Codex subagent model selection](https://learn.chatgpt.com/docs/agent-configuration/subagents).
The app request fields must be checked against its current callable schema.

Delegate one bounded problem requiring stronger reasoning when that is cheaper than expanding the root context. Select Medium or High directly when ambiguity or consequence warrants it; do not require a failed lower-effort attempt first. A build failure alone does not justify model escalation: distinguish code, specification, environment, tool, and test failures.

Routing defaults, subject to observed quality and current availability:

| Work | Initial route |
|---|---|
| One command, narrow lookup, deterministic extraction | Root tool call; no worker |
| CI fact collection, bounded repetitive check, small mechanical edit | Ordinary tool first; if a worker is justified, first consider Spark/Low when available through an authorized mechanism, otherwise Luna/Low or Terra with a brief fit/availability reason |
| Localized implementation or test correction | Terra/Medium; Sol/Medium when ambiguity is material |
| Subsystem implementation or substantive review | Sol/Medium; higher effort for demonstrated risk |
| Difficult architectural, protocol, concurrency, or lifecycle analysis | Astra/Medium or High, only for that bounded problem |

Verify available models and their allowance buckets on the actual host; Spark may have a separate allowance. Do not create an unauthorized user-owned task or delay delivery merely to reach Spark. Model names in this table are selection targets, not a runtime availability guarantee. Select the smallest capable available model and pass explicit model and effort overrides through a supported dispatch mechanism; legacy role frontmatter does not establish the active Codex model or reasoning setting. For Claude Code, use a supported per-invocation model selection rather than relying on omitted frontmatter, which inherits the parent ([Claude Code subagents](https://code.claude.com/docs/en/sub-agents)). If explicit selection is unsupported, report that limitation instead of silently inheriting an expensive root model.

Do not assume Luna/Terra/Sol have independent allowances or calculate plan savings from API prices. Use ordinary speed by default where configurable; faster execution requires a reason and awareness of its actual usage cost.

Usually use one builder followed by one independent verifier. Parallelize review with CI against a frozen candidate, not an actively changing implementation. Use multiple builders only for material independent scopes with disjoint file ownership. Do not add a scout for a lookup the coordinator can finish in one small tool call. Workers may not recursively delegate by default.

## Worker contract and output discipline

Dispatch once with objective, issue, host, exact worktree/base, owned paths, exclusions, acceptance criteria, checks, allowed actions, and report destination. Specify model and effort explicitly. Prefer narrow source pointers over copied history. Never give a verifier authorship of the code being reviewed.

A worker acts autonomously within this contract. At completion it must send the coordinator: status, commit/tree or read-only target, changed/examined files, check results with evidence paths, unresolved risks, and next action. A final answer left in a separate task is insufficient delivery. Verify receipt when handoff matters.

Aim for 150–300 words in routine reports; keep detailed evidence in private artifacts. Findings needing action are exempt from this brevity target. Reuse a worker for a closely related correction if context remains useful; use fresh context for unrelated tasks or demonstrated context degradation. Archive completed standalone Spark tasks after receiving their reports when task creation and archival are authorized.

Filter tool output before it reaches any model. Target approximately 2,000 tokens of total ordinary text returned by each tool call, including the combined output of batched operations. Select JSON fields, line ranges, files, counts, and relevant error excerpts before emitting text. Parse Linear, task, and memory responses inside orchestration; do not print the raw response and summarize afterward. Keep complete evidence in a private artifact outside all worktrees. If output is truncated, narrow the next query instead of repeating the same broad read. Mandatory instruction reads, code needed for implementation/review, and significant errors may exceed the target; images are outside the text quota.

For checkpoint bootstrap, test or stat known paths first. Do not recursively inventory handoff directories. Read mandatory instructions once per active version; reread only when the version changes or a concrete conflict needs resolution. This reduces repeated loading, not the obligation to read applicable instructions.

Assign one observer per CI run. Prefer event/wait tools or an instrumented watcher that reports meaningful changes. Polling inside that observer must not create fresh model turns for unchanged state. Keep waits within platform blocking limits and maintain required progress communication. Do not duplicate `gh pr checks` with `gh run view` unless a diagnostic question requires it. Reuse captured evidence; final exact-head status verification immediately before merge remains mandatory. A changed candidate needs its own required run and review evidence.

After two unsuccessful attempts without new evidence, stop repeating the approach. Return a compact diagnosis and escalate, change the hypothesis, or report the external blocker. Never stop useful work solely because an arbitrary attempt budget was reached.

## Delivery and evidence

Batch related work under the current repository contract. Use focused development checks and existing exact-head CI evidence; do not duplicate full suites across builder, verifier, and coordinator. Corrections need delta review plus whatever CI the repository requires for the new head. Expand review beyond the delta only for affected dependencies or concrete interaction risk.

The coordinator integrates and checks acceptance gates; it does not substitute its own approval for required independent review. Keep source review, CI, installed artifact, visual acceptance, and physical-radio validation distinct. Preserve single ownership of the radio and service lifecycle.

## State and session lifecycle

Keep operational handoff in the existing private Linear handoff document and linked private artifacts. Maintain one compact current checkpoint, with links to older evidence. Do not add public CURRENT_STATE/HANDOFF files or duplicate Linear planning. Public architectural decisions belong in existing ADR/design documentation when appropriate.

Checkpoint: objective/acceptance; host/worktree/branch/HEAD and dirty files; current owners/workers; installed candidate and active processes; exact review/CI evidence; unresolved failures; preserved user decisions; next action. Include permissions and unresolved user requests that affect continuation. Size for immediate resumption, not historical reconstruction.

At each semantic milestone, assess context growth, repeated loads, and demonstrated degradation before deciding whether to continue or prepare succession. Consider a fresh session when that evidence justifies its cost. Context size alone, compaction count, or guessed context percentages are insufficient. Distinguish cumulative input, cached input, current context occupancy, and actual remaining usage allowance; none is a substitute for another. Account for rehydration and duplicated investigation: a new session is not automatically cheaper. Do not stop an active coherent task merely to rotate sessions.

`HANDOFF_REQUIRED` names the integration contract for a separately implemented external lifecycle handler. Preserve this contract; handler implementation and activation are separate from adopting this policy. Automatic succession requires one-time explicit user enablement, recorded in orchestrator configuration, and a verified handler or another supported lifecycle mechanism. Individual rollovers within that authorization do not require repeated approval. Reassess authorization if scope, permissions, worktree, external side effects, or hardware ownership changes; seek approval only for changes not already authorized. Configuration cannot override platform lifecycle restrictions. Until that exists, do not emit a terminal signal and assume a successor was created.

The signal is a generic integration requirement, not a claim that a handler or schema has been implemented. The signal must reference the private Linear checkpoint by document ID/URL and immutable checkpoint version or content digest, rather than a repository handoff path. Include a schema version, unique handoff ID, source task identity, and the target host/worktree. The handler must resolve the exact checkpoint version or reject a mismatch. Keep operational handoff content and detailed artifacts outside every repository worktree. Do not place them in tracked or gitignored repository paths. Public architectural documentation remains separate.

The handler must deduplicate retries using the handoff/source-task identity and reconcile an uncertain creation result before retrying. Repeated delivery must resolve to the same successor, never create another one. Record successful creation separately from confirmed ownership acceptance. Keep the detailed wire schema in the handler's integration runbook and align it with these requirements before activation.

For supported succession: prepare a versioned checkpoint with source/target identity and active ownership; freeze new mutations and resolve or park workers; start the successor in read-only bootstrap; require checkpoint acknowledgement (ACK), then explicit RELEASE by the previous owner, then ACCEPT by the successor; activate exactly one writer only after that ordered transfer. An ACK is read receipt, not write authority. Retain the checkpoint until acceptance is verified. On failure, preserve or explicitly restore the previous owner; do not leave the project silently abandoned or permit two writers. Never assume browser state, running workers, or hardware ownership transfer with a new chat. An inaccessible Linear document, unresolved checkpoint version, digest mismatch, or missing required artifact is a failed handoff: do not grant the successor write ownership. Keep the previous owner active or explicitly restore it before resuming mutations.

## Trial and measurement

Trial these defaults over the next 5–10 comparable completed tasks. Record model/effort, worker count, rework, repeated checks, elapsed time, and available usage by allowance. Use actual per-task token/credit telemetry when available; label missing values unknown. Account-wide allowance changes are approximate and confounded by concurrent tasks and reset windows.

Keep the trial lightweight; do not create a telemetry project or invent costs to support it. Compare accepted outcomes and escaped defects as well as usage. Adjust routing from evidence. Do not promise a savings percentage before measuring it, or weaken acceptance to improve the metric.

## Workspace preservation

Creating a PR, a failed attempt, or a context rollover does not authorize destructive cleanup. Preserve active or uncertain worktrees, dirty files, workers, services, and hardware ownership. Remove a worktree only after ownership is released, required work and evidence are retained, its state is verified safe, and cleanup is within existing authorization. Do not automatically force removal or prune on startup. Never assume process termination from a client timeout; verify the remote job state before releasing its resource ownership.
