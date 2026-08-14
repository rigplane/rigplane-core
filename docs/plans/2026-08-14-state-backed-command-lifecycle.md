# State-backed command lifecycle

**Status:** research plan for Core #2561 / MOR-1641.  This document specifies
the next implementation work; it changes no command, store, transport, test,
or radio behaviour.

## Decision

RigPlane must describe a state-backed command in two independent dimensions:

1. **delivery:** accepted for local submission, queued, dispatched, or rejected
   by the command transport; and
2. **radio truth:** a fresh `RadioState` observation that matches the requested
   field value, or a bounded failure/timeout.

Transport acceptance is never radio confirmation.  The confirmed `RadioState`
remains the sole source for selected, pressed, enabled-by-observation, and
arithmetic-base values.  A command target can be displayed only as explicitly
unconfirmed feedback.

`armed` remains a useful presentation word, but it is not the lifecycle name:
it means “this control has a current unconfirmed target”.  The reusable
machine is named **command lifecycle**, and a surface may project its
`awaiting-confirmation` phase as `armed`.

## Current-main inventory and trace

The following is an inventory of semantic command families, not an assertion
that every member should receive the same UI treatment in one change.

| Family | Current command/observation shape | Current feedback | Scope decision |
| --- | --- | --- | --- |
| Frequency | `set_freq`; per-receiver confirmed frequency | `pendingDisplayHz`, with an explicit pending marker | Reuse as the first high-rate / supersession reference. |
| Mode and filter | `set_mode`, `set_filter`; confirmed receiver mode/filter | generic `ArmedFact` for mode/filter plus earlier filter marker | Candidate for shared lifecycle projection. |
| VFO | selection, swap/equalize and tuning operations; receiver/slot observations | focused/relative-selection feedback is separate from a value confirmation | Do not infer receiver/slot truth from transport success. |
| DSP / notch / NB | independent `set_nb`, `set_nr`, `set_manual_notch`, `set_auto_notch` commands and receiver fields | NB/NR pending accessors; Manual/Auto Notch armed accessors | Manual Notch off is the motivating missing-or-not-observable presentation case.  Manual and auto notch must remain separate facts. |
| RIT / XIT | state-backed toggles/offsets where declared and observed | no universal lifecycle projection identified | Include only after target-to-observed-field mapping is explicit. |
| Levels | normalized/absolute level commands and receiver observations | family-specific controls; no common confirmation UI identified | Avoid optimistic knob values becoming confirmed truth. |
| Scan / scope | commands and asynchronous state/resource updates | resource health and state are not a generic control acknowledgement | Treat resource/session lifecycle separately. |
| Safety controls | PTT/TX, tuner and related safety state | dedicated TX controller with its own authoritative phases | Do not merge safety semantics into this generic lifecycle; safety remains fail-closed. |

Unsupported means capability absent: no command is offered.  Unavailable means a
capability exists but the required observed/fresh field or context is absent:
the control remains unavailable rather than fabricating a target or
confirmation.  Neither state should be styled as an in-flight command.

The current path is:

```
semantic surface handler
  -> dispatchRadioIntent() in frontend runtime
  -> beginCommand() + WebSocket command delivery events
  -> CommandLifecycle record (pending / acknowledged / failed / cancelled / timed-out)
  -> RadioState store accepts ordered server state observations
  -> panel-adapters project selected pending targets / ArmedFact
  -> semantic surface presents confirmed state plus an optional marker
```

`frontend/src/lib/stores/commands.svelte.ts` creates the record before sending,
tracks the original session epoch, and captures `ackObservationSeq` at
acknowledgement. Its current statuses intentionally do not distinguish every
proposed delivery phase: `transport-sent` is emitted by `ws-client.ts` but is
ignored by `radio-intents.ts`, while both `ack` and `response-ok` become
`acknowledged`, including a server `ok:true` reply with `result.superseded`.
An `ok:true` response can mean server acceptance or enqueue, not an executed
backend write or a radio observation. Offline idempotent commands queue by
command name and keep only the latest; non-idempotent offline commands are
rejected. A server-coalesced command can also acknowledge successfully without
reaching the radio. The store's pending timeout is cleared on acknowledgement;
the adapter's post-ack grace is a non-reactive `Date.now()` backstop and only
clears on a later derived recomputation. A later uncorrelated execution failure
currently arrives as a session notification rather than a command-id-correlated
lifecycle transition, and session replacement or a delivery cancellation can
cancel pending records.
The backend `CommandService` has richer execution/overlay outcomes, including
`confirmed`, `reconciled`, and `superseded`, but those states are not currently
projected as a correlated frontend command lifecycle. The contract must
preserve those distinctions rather than infer radio truth from delivery.
`frontend/src/lib/stores/radio.svelte.ts` is the sole
`RadioState` mutator; it must not be patched optimistically for this feature.
`frontend/src/lib/runtime/adapters/panel-adapters.ts` currently implements the
shared `latestPendingParam` decision table.  For acknowledged commands it
keeps a marker until a post-ack observation sequence can confirm the matching
field, bounded by the grace timeout.  It supplies frequency, filter, preamp,
NB, NR, mode, AGC, filter, RF-front-end, data-mode, and the distinct Manual /
Auto Notch armed projections.

The reported Manual Notch observation alone does not establish a current-main
Notch rendering defect. The adapters expose distinct Manual and Auto Notch
`ArmedFact` projections, but this spike has not produced an exact
surface/selector/conformance witness for the composition that was observed.
Treat the gap as open validation: reproduce it against the named semantic
surface and record whether the marker is absent, visually ineffective, or
correctly rendered. It is not evidence that a transport ACK should be promoted
to radio confirmation.

## Required lifecycle contract

An implementation must retain a per-command immutable identity, original
control-session epoch, semantic command name, target parameters, and an
explicit correlation key for the observed field.  It exposes these phases:

| Phase | Meaning | Exit |
| --- | --- | --- |
| `idle` | No outstanding target for the command key. | Local submission creates a record. |
| `submitted` | The UI has accepted the intent and allocated identity. | It is queued or dispatch begins; local validation failure is `failed`. |
| `queued` | The client has retained an offline idempotent command; this is unavailable for commands rejected while offline. | Send, supersession, cancellation, capacity rejection, or failure. |
| `dispatched` | Frame has been handed to the transport. | Delivery acknowledgement/error or timeout. |
| `awaiting-confirmation` | A delivery acknowledgement or `response-ok` was received, but no qualifying fresh observation proves the target. It does not imply backend execution. | Matching fresh observation, a correlated error, supersession, cancellation, or timeout. |
| `confirmed` | A qualifying fresh radio-originated observation matches this target. | Retain briefly only for feedback, then return to idle. |
| `failed` | Validation, transport, or command-result error. | User-visible bounded feedback, then idle. |
| `timed-out` | The bounded confirmation wait expired. | User-visible bounded feedback, then idle. |

The existing `pending` lifecycle record is an implementation detail that spans
the first delivery phases today; a future change must either refine it without
breaking consumers or add a derived view. The proposed `queued` and
`dispatched` phases are only available where their actual transport events
support them: a queued command has no `transport-sent` event until reconnect,
and current runtime consumers deliberately ignore that event. A delivery
acknowledgement moves a command to `awaiting-confirmation`, not `confirmed`.
Because a late backend failure may be uncorrelated, timeout remains an honesty
backstop rather than a claim that the command succeeded or failed at the radio.

Before a surface claims a reliable timeout outcome, the implementation must
choose one reactive deadline owner: either the lifecycle store owns a
post-ack confirmation timer, or a runtime-level lifecycle coordinator does.
It must document the bounded duration, whether acknowledgement restarts the
submission deadline, how a newer same-key target cancels/restarts it, and how
long confirmed/failed/timed-out outcomes remain available for one bounded
announcement. It must not rely on `latestPendingParam`'s non-reactive
`Date.now()` grace as the lifecycle clock, and a Manual Notch accessor test
alone cannot prove this contract.

Confirmation requires: same control-session epoch, a radio-originated applied
observation after the relevant acknowledgement boundary, and a value matching
the command's explicit target for its mapped field.  An unrelated state push,
a stale matching snapshot, or a local store patch does not confirm it.

## Concurrency, reversal, and external change rules

The lifecycle key is `(semantic control key, receiver/slot scope where
applicable)`, not merely command name.  Commands for different keys may be
shown concurrently.  For the same key:

- A repeated click with the same target while a latest target is outstanding
  must be throttled or ignored; it must not emit a duplicate command merely to
  obtain feedback.
- A newer target supersedes the older same-key target for presentation.  The
  older transport record stays auditable but no longer drives the control.
- A reverse or restore action is simply a newer explicit target.  Its target
  must be computed from confirmed radio state, never from a pending target.
- A matching observation confirms only the latest applicable target.  A late
  acknowledgement or observation for a superseded record cannot overwrite the
  newer presentation.
- An out-of-band physical change always updates confirmed `RadioState`.  If it
  does not match the latest target, the surface continues to say awaiting
  confirmation until bounded failure/timeout; it must not label that physical
  observation a successful command result.
- Timeout or error clears the busy/armed presentation but leaves the latest
  confirmed radio state untouched.  It neither rolls back nor invents a
  failure value.

Manual Notch is deliberately two keys: `set_manual_notch` maps to
`manualNotch` and `set_auto_notch` maps to `autoNotch`.  “Notch off” must not
be re-derived as a combined synthetic state or collapse either command's
correlation.

## Presentation and accessibility contract

Presentation adapters may derive a small, semantic view such as
`{ phase, target, busy, outcome }`; transport and `RadioState` stores must not
contain CSS or control-specific prose.  A control in `submitted`, `queued`,
`dispatched`, or `awaiting-confirmation`:

- shows a non-colour-only visible pending/armed marker;
- sets `aria-busy="true"` on the appropriate control or semantic group;
- associates concise, throttled live status (for example, “Mode change
  pending”) with the control; and
- preserves confirmed selection/pressed state separately from the target.

Duplicate same-target controls may be disabled while busy.  A different
explicit target may remain allowed when the family supports supersession;
otherwise the reason for temporary disablement must be exposed.  Confirmation,
failure, and timeout emit one bounded live announcement, not a poll-rate
stream.  Clearing busy on timeout/error must not clear a confirmed value or
announce an unobserved result as successful.

## Predecessor reuse and gap

MOR-1441 introduced pending frequency and discrete-control affordances;
MOR-1488 corrected their settlement so acknowledgement alone cannot clear a
target before a fresh matching observation.  MOR-1519 introduced the generic
`armed` projection for polled mode controls.  MOR-1536 adopted it across
additional polled selector surfaces.  MOR-1541 hardened the armed affordance
and kept Manual and Auto Notch as separate real-command projections.

Those precedents supply the correct direction but not a complete lifecycle:
they are selective adapter/surface integrations, use a bounded grace backstop,
and do not supply one shared presentation contract for every state-backed
family. The Manual Notch observation remains an open validation question, not
a selected defect or implementation target; a focused conformance witness must
first establish the actual current surface behaviour.

## Decomposition and rollout

Keep each implementation issue within the normal three-file / 400-LOC guard.
MOR-1643 is the first consumer of this research, but its live acceptance is
specifically **Filter Width**: visible and accessible pending target versus
confirmed value, 200 ms debounce/quantization, same-control supersession,
matching StateStore confirmation, timeout/error restoration to canonical state,
and `aria-busy` or a live announcement. The pending target must never be
labeled radio-confirmed.

That acceptance honestly spans at least four distinct files today:
`panel-adapters.ts` plus its adapter lifecycle test, and `FilterPanel.svelte`
plus its isolated component test. The existing panel-command intent suite can
continue to pin debounce/quantization but does not shrink that ownership.
Therefore #2563 must be treated as a tracking parent before implementation,
with two issue-backed children rather than a falsely narrow one-seam change:

1. **MOR-1643 child A — Filter Width lifecycle projection (up to three
   files).** Own the derived Filter Width pending/confirmed view and adapter
   lifecycle tests for supersession, fresh matching observation, timeout, and
   error; preserve canonical `RadioState` as truth.
2. **MOR-1643 child B — Filter Width surface and accessibility (up to three
   files).** Own the FilterPanel rendering and isolated test proving the
   pending target is visibly and accessibly distinct, remains unconfirmed until
   the matching observation, then clears on confirmation, timeout, or error.
   It must include the mandated `aria-busy` and/or throttled live-status
   witness. The parent issue's 200 ms input/debounce/quantization coverage
   remains explicitly required, assigned to the child whose existing test owns
   that behaviour after a tracker split.
3. **MOR-1644 — trailing target-aware post-write reconciliation research.**
   This is a separate architecture spike, not a Manual Notch presentation
   slice. Its scope is the existing `_POST_WRITE_READBACK_FIELDS` and
   `_request_post_write_readback` path, `ensure_fresh(priority=USER)`,
   scheduler/coalescer/single-flight and serial-budget invariants, plus
   target-aware delayed/retry/reconnect behaviour after an executed write. It
   may inform how quickly a valid observation arrives, but it must not replace
   the #2561/#2563 pending-to-confirmed presentation lifecycle or treat a
   transport acknowledgement as radio truth.
4. **Follow-on family slices.** Add one disjoint family at a time (frequency
   only if the projection cannot reuse its mature path; then mode/filter or
   RIT/XIT). Each has a matching negative witness for unavailable and
   unsupported state, timeout/error non-mutation, and a public API/markup
   compatibility note.

Before rollout, verify existing MOR-1441/MOR-1488 tests still prove that
pending values never become arithmetic or selection truth. Do not change
backend polling, command serialization, ACK matching, session cancellation,
safety/TX control, or capability policy as a shortcut. MOR-1644 may research
the existing backend reconciliation path but is not authorized to change it.
No bench or radio operation is required for this plan or for the initial
software-only witness.

## Risks and non-goals

- A generic API that combines unrelated controls would obscure receiver/slot
  identity and make false confirmation likely; preserve explicit field maps.
- Poll timing is variable, so an accessibility announcer must be transition
  driven and throttled rather than triggered for every state push.
- The grace timeout is a bounded honesty backstop, not proof that the radio
  adopted a command.
- This plan does not reopen completed predecessor issues, alter MOR-1410's
  hardware release gate, or authorize any physical-radio validation.
