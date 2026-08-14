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
| Command ingress | `runtime/_poller_types.py:CommandQueue` preserves ordered entries but replaces same-type commands in a coalesced tail; `web/radio_poller.py` drains the surviving entry. | Schedule only after that concrete surviving entry's backend write succeeds, never after UI intent, queued acceptance, a held command, a replaced coalesced entry, or a failure. |
| Existing immediate reconciliation | `web/radio_poller.py` owns `_POST_WRITE_READBACK_FIELDS` and `_request_post_write_readback()`. The table maps `SetFreq`, `SetMode`, `SetRfGain`, `SetSquelch`, `SetAttenuator`, `SetPreamp`, `SetFilter`, `SetDataMode`, and `SetFilterWidth` to canonical `FieldPath`s; the helper queues `ensure_fresh(..., max_age=1e-9, priority=USER, reason="post_write_readback")` after `_execute`. | The proposed delayed contract must extend or replace this current Icom-oriented jump-queue deliberately. It must not add a second, competing immediate read for a mapped write. |
| Icom readback | The current Icom `RadioPoller` performs the immediate post-write scheduler request above; CI-V observations subsequently enter StateStore/CommandService rather than being a write response. | Icom supplies a real software-only default-path witness, but not a universal settle-time proof. The child must compare current immediate behavior with a trailing request under fake time. |
| Yaesu same-cycle readback | `backends/yaesu_cat/poller.py:_medium_loop()` drains the successful command batch and then immediately calls `_poll_medium()` under the same serialized cycle. The observation path emits MAIN/SUB frequency and mode (plus PTT/target and supported filter width) as `yaesu_poll_response`. | Yaesu already has a provider-owned immediate readback opportunity. The generic trailing coordinator is **disabled for Yaesu** initially; it must not coexist with and duplicate this medium poll. A future policy may replace the relevant medium reads only after the fake-Yaesu witness below proves the replacement is equivalent and bounded. |
| Acquisition | `core/acquisition_scheduler.py` groups paths by request key, coalesces existing requests, keeps the higher priority, and merges paths/reasons. Its cadence and priming paths also have bounded/rotating work rules. | Reconciliation enters that scheduler as normal USER work; no bypass or parallel transport read. |
| rigctld correlation | `backends/rigctld_client/radio.py` retains pending command readback correlations, annotates the next matching external readback, then discards unmatched expectations; `core/command_service.py` normalizes that correlation before StateStore reconciliation. | A trailing coordinator must neither duplicate nor steal that immediate correlated-readback ownership. Its initial policy is explicitly **disabled for rigctld** until a dedicated fake-rigctld compatibility slice proves coexistence. |
| Frontend tuning accumulation | `frontend/src/lib/runtime/commands/tuning-accumulator.ts` keeps one latest target per receiver, emits at 60 ms pacing, expires after 4 s quiet, and invalidates on control-session epoch or capabilities-generation changes. | The server coordinator keys only successful executed writes; it never imports the frontend accumulator's timer or treats each gesture as a write. Each surviving backend write replaces the same receiver/path target. Its provider generation independently fences reconnects, while frontend session/generation invalidation prevents stale gestures from creating later writes. |
| Read storms | Bursty tuning/slider traffic plus per-write reads can amplify serial traffic. | Use a trailing-edge, latest-target timer rather than one timer/read per write. |
| Canonical state | StateStore has a provider/generation fence for ordered observations. | Carry the generation in the reconciliation key; a stale or replaced provider cannot satisfy a pending target. |

Provider assessment is concrete rather than deferred:

- **Icom:** the existing table and immediate `ensure_fresh` path are sufficient
  software evidence to make Icom the first default candidate. A fake-clock
  test must prove that replacing its immediate request with one trailing
  request preserves the affected `FieldPath` mapping and never creates both
  requests for one executed write.
- **Yaesu:** the provider already drains a successful command batch and emits
  freq/mode observations from the immediately following medium poll in the
  same cycle. Its effective generic-coordinator policy is therefore
  `disabled`, not "coexist". A fake Yaesu poller test must enqueue a successful
  frequency/mode write, run one medium cycle, and prove exactly one provider
  poll emits the latest target's canonical observation, with zero generic
  coordinator submission. Replacing that provider poll remains a later owner
  choice and must prove the same witness plus no duplicate CAT read.
- **rigctld:** external rigctld already owns command/readback correlation.
  Generic coordinator support must start `disabled`; a separate fake-rigctld
  test may enable it only if it proves that a trailing read does not consume,
  relabel, or race the existing correlation queue. No physical rigctld or
  bench evidence is required for that decision.

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

The latest target is the target of the last **executed backend write**, not the
frontend's latest gesture. The tuning accumulator can emit several paced
targets from one receiver's 4 s burst; command-queue coalescing can then replace
some of those before execution. Only each surviving successful write advances
the coordinator write generation and resets the trailing deadline. The
frontend's 60 ms pace and 4 s quiet window are presentation/ingress controls,
not coordinator constants.

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
- All reads remain scheduler USER work. This is not a claim that USER work is
  magically fair: the coordinator has its own bounded fairness contract — at
  most one queued trailing request per reconciliation key, at most one grace
  retry, and a per-drain serial budget that round-robins distinct live keys.
  When that budget is exhausted, the next key is retained for the next drain;
  a continuously rewritten key cannot prevent an older live key from being
  selected. Scheduler coalescing still applies after selection.
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
| Cross-key fairness | With a finite serial budget, a continuously updated key cannot starve an older distinct live key; each is selected in bounded round-robin order before scheduler coalescing. |
| Fresh mismatch | At most one explicitly configured grace/retry occurs; final timeout leaves confirmed Store state unchanged. |
| Read error | Busy/outcome bookkeeping terminates without a Store patch or retry loop. |
| Reconnect/generation change | Old-generation timers and completions are ignored/cancelled. |
| Shutdown | Timers cancel and no post-shutdown scheduler request occurs. |
| Provider policy | Icom trailing candidate, Yaesu generic-coordinator disabled/provider-poll retained, and rigctld disabled are independently represented rather than silently sharing timing. |
| Yaesu no-duplicate path | One fake medium cycle drains a successful freq/mode batch and emits the latest canonical observations once; the generic coordinator receives zero submissions. A replacement experiment must suppress the same-cycle provider read before enabling a trailing one. |
| Accumulator handoff | Multiple 60 ms frontend emissions followed by command-queue replacement produce reconciliation registrations only for successful surviving writes; receiver, provider-generation, and write-generation keys cannot inherit an expired 4 s burst. |

## Bounded implementation decomposition

No child may exceed three files or 400 LOC. Exact paths are selected only
after its issue confirms current ownership; the following slices are intended
to prevent a cross-layer refactor masquerading as one change.

| Child | Owned seam (maximum three files) | Acceptance boundary |
| --- | --- | --- |
| A: coordinator core | `src/rigplane/web/post_write_reconciliation.py` (new), `tests/test_post_write_reconciliation.py` (new) | Keying, trailing supersession, bounded retry, reconnect/shutdown cancellation, and the round-robin budget as a pure fake-clock unit. |
| B: Icom poller integration | `src/rigplane/web/radio_poller.py`, `tests/test_radio_poller_coverage.py` | Route only successful mapped Icom writes through A; assert exactly one of immediate or trailing request, never both. This overlaps stale #1717's `radio_poller.py` and test ownership, so requires its ownership reconciliation first. |
| C: scheduler bridge proof | `src/rigplane/core/acquisition_scheduler.py`, `tests/test_acquisition_scheduler.py` | Expose only the minimal USER request/budget seam needed by A and prove scheduler coalescing still happens. Both paths overlap stale #1717. |
| D: lifecycle projection | `frontend/src/lib/stores/commands.svelte.ts`, `frontend/src/lib/stores/__tests__/commands.test.ts` | Derive a non-authoritative pending outcome; no Store patch. This exact pair is outside stale #1717's file list. |
| E: provider policy declarations | `src/rigplane/profiles/__init__.py`, `src/rigplane/profiles/rig_loader.py`, `tests/test_rig_loader.py` | Parse a disabled-by-default provider/profile override and prove invalid policy is rejected. All three paths overlap stale #1717. |
| F: Yaesu no-duplicate evidence | `src/rigplane/backends/yaesu_cat/poller.py`, `tests/test_yaesu_cat_poller.py` | Fake one successful command-batch drain followed by the same-cycle medium poll; assert latest freq/mode observations appear once and generic coordinator submission stays disabled. These exact paths do not overlap stale #1717. |
| G: rigctld compatibility decision | `src/rigplane/backends/rigctld_client/radio.py`, `src/rigplane/backends/rigctld_client/observations.py`, `tests/test_rigctld_client_observation_adapter.py` | **Not implementation-ready until owner choice 4.** If authorized, prove a trailing read cannot consume, relabel, or race existing correlation. The latter two paths overlap stale #1717; `radio.py` does not. Resolve overlap ownership before dispatch. |

Stale PR #1717 actually overlaps `src/rigplane/web/radio_poller.py`,
`src/rigplane/core/acquisition_scheduler.py`, `src/rigplane/profiles/__init__.py`,
`src/rigplane/profiles/rig_loader.py`, `tests/test_radio_poller_coverage.py`,
`tests/test_acquisition_scheduler.py`, and `tests/test_rig_loader.py`. For the
provider slices, its exact overlaps are
`src/rigplane/backends/yaesu_cat/observations.py` with
`tests/test_yaesu_cat_observation_adapter.py`, and
`src/rigplane/backends/rigctld_client/observations.py` with
`tests/test_rigctld_client_observation_adapter.py`. It does **not** own
`src/rigplane/backends/rigctld_client/radio.py`,
`src/rigplane/backends/yaesu_cat/poller.py`, or
`tests/test_yaesu_cat_poller.py`. Do not overwrite, duplicate, or silently
absorb that branch's work.
Reconcile whether it is integrated, superseded, or needs a normal reanchor and
independent review before B, C, E, or a rigctld provider child begins. A and D
remain independently ownable only after their issue checks current paths.

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
