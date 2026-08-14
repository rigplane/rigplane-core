# Trailing target-aware post-write reconciliation

**Status:** research decision record for Core #2564 / MOR-1644. This document
changes no production behavior, transport, provider profile, test, or hardware
expectation.

## Decision

A successful executed backend write may be followed by a **bounded,
target-aware reconciliation request** for the canonical field bundle affected
by that write. It is an observation request, not an optimistic state update
and not a second write. The StateStore remains authoritative only when it
accepts a suitably fresh provider observation.

The initial 100 ms delay is a hypothesis to validate, not a universal radio
constant. The coordinator must use the existing acquisition scheduler at USER
priority. It must never access a transport directly, add a fast global poll,
or turn a delivery acknowledgement into radio truth.

This is distinct from MOR-1641/#2561's command-lifecycle presentation: that
work describes pending versus confirmed UI feedback. Reconciliation supplies a
bounded opportunity for a fresh observation; it does not make an ACK, timer,
or requested target confirmed state.

## Current-main evidence and boundaries

| Concern | Current evidence | Design consequence |
| --- | --- | --- |
| Command ingress | Coalescers and accumulators can discard or replace an earlier intent before it executes. | Schedule only after the concrete backend write succeeds, never after UI intent, queued acceptance, a held command, coalescing, or failure. |
| Icom writes | An actual write success is the useful causal boundary for post-write observation. | Icom is the first evidence source, not proof that all providers share one settle time. |
| Acquisition | The scheduler already owns `ensure_fresh(priority=USER)`, single-flight, fairness, and serial budgeting. | Reconciliation enters that scheduler as normal USER work; no bypass or parallel transport read. |
| Read storms | Bursty tuning/slider traffic plus per-write reads can amplify serial traffic. | Use a trailing-edge, latest-target timer rather than one timer/read per write. |
| Canonical state | StateStore has a provider/generation fence for ordered observations. | Carry the generation in the reconciliation key; a stale or replaced provider cannot satisfy a pending target. |

Provider boundaries remain explicit:

- **Icom:** validate initial timing and affected-field mapping with fake-provider
  evidence before any provider-specific policy is adopted.
- **Yaesu:** do not inherit Icom timing or side-effect assumptions. A profile
  override requires independent evidence and a bounded fake-provider test.
- **rigctld:** no rollout is authorized by this spike. Its external-process
  provider boundary, polling behavior, and command/read semantics need their
  own compatibility and failure analysis.

The contract is public-core generic. It contains no device-specific support
workflow, local-host detail, bench claim, or assertion about FTX-1.

## Coordinator contract

After an executed write succeeds, the backend submits an affected-field bundle
to a coordinator with this correlation identity:

```
(provider generation, receiver scope, canonical store paths, latest target,
 write generation)
```

`receiver scope` is required where a field is receiver-specific. A bundle is
atomic for deduplication only: it can request several mapped canonical fields,
but it does not fabricate a combined Store value.

1. Only an actual successful write registers the bundle.
2. A newer write for the same provider-generation/receiver/path key replaces
   the old target and cancels or supersedes its timer.
3. At the trailing deadline, the latest live entry asks the acquisition
   scheduler for a USER-priority fresh observation of the bundle.
4. The resulting observation goes through normal StateStore generation/order
   validation. The coordinator does not patch canonical state.
5. One bounded target-aware grace/retry may be considered after a fresh but
   stale mismatch. It must be finite, visible in metrics, and never become an
   unbounded poll loop.
6. A read error or timeout clears coordinator busy state and records a
   diagnostic outcome; it does not alter confirmed StateStore data.

A reverse/restore write is simply a newer target and supersedes the prior
target. Out-of-band physical changes remain canonical observations; a mismatch
is not proof of either write success or failure. Reconnect/provider-generation
change invalidates old entries. Shutdown cancels timers without starting new
reads. TX-interlock holds and unsuccessful execution schedule nothing.

Unknown write side effects require an explicit matcher decision: a child may
use only field bundles with proven target-to-observation mapping. It must not
guess a broad read set from a command name.

## Safety, fairness, and observability invariants

- At most one pending trailing timer exists for a reconciliation key.
- A superseded timer cannot submit a read for its prior write generation.
- All reads remain scheduler USER work and preserve its single-flight,
  fairness, and serial-budget policy relative to other users.
- The coordinator is bounded by a documented attempt count and deadline;
  reconnect and shutdown leave no runnable stale timer.
- Metrics/logging distinguish registered, superseded, fired, scheduler-denied,
  fresh-match, fresh-mismatch, retry, read-error, timeout, generation-drop,
  and shutdown-cancel outcomes. They must not log private payloads.
- Presentation consumers may use the outcome to end pending feedback, but only
  an accepted matching Store observation can report radio confirmation.

## Required fake-clock simulations

The first implementation must prove these with deterministic fake time and
fake providers/scheduler seams, not physical equipment:

| Simulation | Acceptance witness |
| --- | --- |
| Slider/VFO burst | Many successful writes for one key yield one trailing latest-target read, never one read per write. |
| Same-key supersession | Earlier timer cannot fire or confirm after a newer target/write generation. |
| Cross-key fairness | Bundles share normal USER scheduling and do not starve unrelated acquisition work. |
| Fresh mismatch | At most one explicitly configured grace/retry occurs; final timeout leaves confirmed Store state unchanged. |
| Read error | Busy/outcome bookkeeping terminates without a Store patch or retry loop. |
| Reconnect/generation change | Old-generation timers and completions are ignored/cancelled. |
| Shutdown | Timers cancel and no post-shutdown scheduler request occurs. |
| Provider policy | Icom default, Yaesu override candidate, and unsupported rigctld behavior are independently represented rather than silently sharing timing. |

## Bounded implementation decomposition

No child may exceed three files or 400 LOC. Exact paths are selected only
after its issue confirms current ownership; the following slices are intended
to prevent a cross-layer refactor masquerading as one change.

| Child | Owned seam (maximum three files) | Acceptance boundary |
| --- | --- | --- |
| A: coordinator core | coordinator module, its fake-clock test, narrow integration seam | Keying, trailing supersession, bounded retry, reconnect/shutdown cancellation. |
| B: scheduler bridge | scheduler integration, focused scheduler test, metrics seam | USER priority only; no bypass; fairness and serial-budget witnesses. |
| C: Icom mapping | Icom mapping seam, fake-provider test, profile/data declaration | Proven affected-field bundle and default timing; no universal provider claim. |
| D: Store/lifecycle projection | one derived outcome seam, its test, one consumer adapter | No optimistic Store write; consumers retain MOR-1641 pending/confirmed distinction. |
| E: provider expansion | one provider declaration, fake test, documentation/update seam | Yaesu or rigctld only after its own evidence and compatibility decision. |

The exact ownership of stale PR #1717 must be reconciled before any child that
touches its paths. Do not overwrite, duplicate, or silently absorb that
branch's work; establish whether it is integrated, superseded, or needs a
normal reanchor and independent review first.

## Owner decisions still required

1. Confirm or revise the 100 ms generic starting delay and define whether it
   is globally fixed, profile/data-driven, or only a provider default.
2. Approve the provider-override mechanism and evidence standard, especially
   for Yaesu.
3. Define the safe policy for commands with an unknown side-effect matcher:
   no reconciliation by default, or an explicitly bounded generic read.
4. Decide whether/when rigctld receives a separate rollout after its external
   process and polling contract are evaluated.
5. Reconcile stale #1717 ownership before assigning overlapping paths.

## Non-goals and relationship to active work

This spike neither blocks nor reopens the narrow MOR-1642/#2562 Filter Width
correctness work unless a future finding establishes a concrete dependency. It
does not fix MOR-1639 accidental wheel tuning, perform a hardware acceptance
run, or authorize any FTX-1 work. It proposes no public API, CLI, config, or
rigctld wire change.
