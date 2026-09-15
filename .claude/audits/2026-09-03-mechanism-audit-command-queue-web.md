# Command queue identity and Web execution lifetime

**Date:** 2026-09-03

**Audited revision:** `fdbd74035a47085b4d80256387617d3515889528`

**Method:** `.claude/skills/mechanism-audit/SKILL.md`

**Evidence class:** independent static source review; not runtime certification.

## Scope and limits

This is a bounded review of changed queue-claim, pending-cancellation and
operation-lifetime capabilities, together with their Web integration.
It is not a systematic module-wide attribute, dynamic-access, public-API or
downstream-consumer census. No additional deletion recommendation follows.
Runtime results belong in the associated PR evidence, not this archive.
No claim is made about RF state, managed OFF completion, managed ON readiness,
or safe stop/provider replacement.

## Prior rulings

The 2026-09-01 accepted architecture in
`docs/plans/2026-09-01-runtime-transmit-authority.md` separates runtime intent,
provider acceptance and observed RF, and states that raw/direct paths are
outside that authority. Queue completion must not become another authority.

The earlier
`.claude/audits/2026-08-30-mechanism-audit-command-path.md`, finding F4, identified
divergent queue drivers. Its broader consolidation recommendation is not proof
that every backend policy belongs in this narrower execution kernel.

## Definition sites and observed consumers

- `src/rigplane/runtime/_poller_types.py: CommandQueueEntry` is the frozen entry
  definition; `CommandQueue` owns the existing segment storage.
- `CommandQueue.put_ordered` serves ordered producers, including
  `src/rigplane/web/server.py: WebServer._execute_scope_command` and HTTP batch
  execution. `CommandQueue.remove_pending` is called by its cancellation
  callback; `pending_count` and `take_entry` serve the normal Web drain.
- `src/rigplane/core/command_dispatch.py: DispatchQueue` is a structural
  protocol, not another queue; its ordered return is opaque `object`.
- `src/rigplane/runtime/_poller_types.py: execute_command_queue_entry` is the
  sole new lifetime/completion kernel. Its production consumer is
  `src/rigplane/web/radio_poller.py: RadioPoller._run`.
- `RadioPoller._execute_queued_entry_action` owns one entry-to-action metadata
  mapping, used by the normal drain's local leaf and the existing
  `_execute_queued_entry` compatibility wrapper.
- That wrapper has six direct interlock-test call sites and a canonical
  receiver-level fixture consumer. Those callers exercise successful Future
  completion and separate direct execution from lifecycle failure reporting.
- `CommandQueue.drain_entries` remains live in both provider drains and
  `RadioPoller.drain_tx_safety_commands`. `src/rigplane/_poller_types.py` remains
  a module-alias compatibility shim, not another implementation.

## Steelman

Backend execution, readback and Web logging/backoff have legitimate local
owners. Moving them wholesale would change direct-call semantics and obscure
protocol-specific evidence. Likewise, shutdown unkey execution intentionally
survives cancellation of its reply and cannot use the ordinary cancellation
skip unchanged. Keeping these boundaries is stronger than superficial uniformity.
What can be shared is pending identity management and ownership of one actual
operation through terminal cleanup.

## Deletions

No additional deletions recommended. The wrapper, bulk drain and import shim
have observed consumers. The scoped replacement of normal Web completion logic
does not establish that adjacent APIs or deferred-lane machinery are dead.

## Consolidations

### 1. Pending identity and cleanup — already shared

Observation: `CommandQueue.put_ordered` returns the exact stored frozen entry.
`take_entry` claims one entry in existing segment/PTT order; `remove_pending`
compares identity only and never completes the reply. `pending_count` is a
property; queue truthiness is not redefined. Notification clears when pending
storage becomes empty.

Observation: the automatic callback removes cancelled pending entries except
the canonical `PttOff` class. Non-cancelled terminal replies remain queued.
Explicit identity removal remains generic. The exception matches the existing
final-drain classifier rather than creating a second release registry.

### 2. Operation lifetime — consolidation achieved for Web

Observation: `execute_command_queue_entry` checks cancelled replies before
scheduling and inside the child before invocation. Once invoked, the child is
shielded from caller-only reply cancellation. Drainer cancellation sends one
child cancellation, joins terminal cleanup despite repeated cancellation,
consumes a terminal child exception, then cancels the pending reply and
re-raises the original drainer cancellation. Independent child cancellation
does not cancel the drainer. Ordinary exceptions retain identity; already
terminal replies are not overwritten.

Observation: `RadioPoller._run` passes the exact claimed entry directly to the
kernel. Its local leaf reports failures before terminalization; outer catches
retain logging/backoff. The compatibility wrapper invokes the same kernel with
the raw action leaf, without nesting kernels or duplicating completion logic.

### 3. Scheduling and release — legitimately local

Observation: `RadioPoller._run.entries_for_turn` fixes its claim quota once.
Skipped, deferred and failed claims consume that quota; arrivals cannot refill
it. `_stage_tx_interlocked_entries` advances held work once after the current
finite turn. Closing the generator does not drain its unvisited tail.

Observation: `RadioPoller._execute_pending_unkeys` remains outside the generic
kernel, and the acquisition scheduler's TX observation boundary is unchanged.
The pending `PttOff` exemption preserves final-drain eligibility, not a universal
OFF guarantee: an ordinary drain can still claim and skip a cancelled reply.

### 4. Provider migration — incomplete, not a missing second kernel

Observation: `YaesuCatPoller._drain_commands` and
`RigctldClientObservationPoller._drain_commands` still use bulk draining.
The shared target now exists; later integration must reuse it while preserving
provider hooks and deferred-release policy. No additional mechanism is proposed
or authorized by this report.

## Weakest link

The cancellation-lifetime conclusion is static. First verify actual child
cleanup under repeated drainer cancellation, including cancellation before the
child's first step and cleanup that returns or raises. Also verify producers
whose cancellation now removes formerly retained pending queue entries.
This review is not mutation evidence or complete runtime acceptance.

## Cleared

Canonical queue ownership; opaque core protocol; single Web completion kernel;
one metadata/action mapping; finite live-pending quota; direct-helper lifecycle
separation; distinct legacy final drain; retained bulk API and compatibility shim.
