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
  -> CommandLifecycle record (pending / acknowledged / failed / timed-out)
  -> RadioState store accepts ordered server state observations
  -> panel-adapters project selected pending targets / ArmedFact
  -> semantic surface presents confirmed state plus an optional marker
```

`frontend/src/lib/stores/commands.svelte.ts` creates the record before sending,
tracks session epoch and delivery result, and captures `ackObservationSeq` at
acknowledgement.  `frontend/src/lib/stores/radio.svelte.ts` is the sole
`RadioState` mutator; it must not be patched optimistically for this feature.
`frontend/src/lib/runtime/adapters/panel-adapters.ts` currently implements the
shared `latestPendingParam` decision table.  For acknowledged commands it
keeps a marker until a post-ack observation sequence can confirm the matching
field, bounded by the grace timeout.  It supplies frequency, filter, preamp,
NB, NR, mode, AGC, filter, RF-front-end, data-mode, and the distinct Manual /
Auto Notch armed projections.

This explains the reported observation: the command lifecycle may exist and
settle correctly while the desktop surface that was used does not render that
projection, or renders only a structural `data-armed` marker that is not
visible in its active composition.  It is not evidence that a transport ACK
should be promoted to radio confirmation.

## Required lifecycle contract

An implementation must retain a per-command immutable identity, original
control-session epoch, semantic command name, target parameters, and an
explicit correlation key for the observed field.  It exposes these phases:

| Phase | Meaning | Exit |
| --- | --- | --- |
| `idle` | No outstanding target for the command key. | Local submission creates a record. |
| `submitted` | The UI has accepted the intent and allocated identity. | It is queued or dispatch begins; local validation failure is `failed`. |
| `queued` | Transport has accepted ownership but has not sent the frame. | Dispatch, cancellation, or failure. |
| `dispatched` | Frame has been handed to the transport. | Delivery acknowledgement/error or timeout. |
| `awaiting-confirmation` | Delivery has succeeded, but no qualifying fresh observation proves the target. | Matching fresh observation, error, supersession, or timeout. |
| `confirmed` | A qualifying fresh radio-originated observation matches this target. | Retain briefly only for feedback, then return to idle. |
| `failed` | Validation, transport, or command-result error. | User-visible bounded feedback, then idle. |
| `timed-out` | The bounded confirmation wait expired. | User-visible bounded feedback, then idle. |

The existing `pending` lifecycle record is an implementation detail that spans
the first delivery phases today; a future change must either refine it without
breaking consumers or add a derived view.  It must not claim that queued and
dispatched are distinguishable when the current transport does not expose that
boundary.  A delivery acknowledgement moves a command to
`awaiting-confirmation`, not `confirmed`.

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
they are selective adapter/surface integrations, use a bounded grace
backstop, and do not supply one shared presentation contract for every
state-backed family.  The Manual Notch off observation is therefore either an
unwired desktop composition/visibility gap or a lifecycle state that cannot be
observed through that composition.  It must be demonstrated with a focused
conformance witness before a production change is selected.

## Decomposition and rollout

Keep each implementation issue within the normal three-file / 400-LOC guard.
The first implementation dependency is MOR-1643; it must start from this
baseline rather than re-auditing the whole command surface.

1. **MOR-1643 — lifecycle projection seam (up to three files).** Define a
   derived command-key view over existing lifecycle records and radio
   observations, with unit tests for same-key supersession, reverse target,
   post-ack matching observation, timeout, and transport error.  No surface
   styling in this slice.
2. **MOR-1644 — Manual Notch presentation witness (up to three files).** Wire
   only the semantic desktop Notch control to that projection and add a
   component/conformance test proving an off command visibly becomes busy,
   remains unconfirmed through delivery acceptance, then settles only after a
   matching observation.  Include `aria-busy` and one live status assertion.
3. **Follow-on family slices.** Add one disjoint family at a time (frequency
   only if the projection cannot reuse its mature path; then mode/filter or
   RIT/XIT).  Each has a matching negative witness for unavailable and
   unsupported state, timeout/error non-mutation, and a public API/markup
   compatibility note.

Before rollout, verify existing MOR-1441/MOR-1488 tests still prove that
pending values never become arithmetic or selection truth.  Do not change
backend polling, command serialization, ACK matching, session cancellation,
safety/TX control, or capability policy as a shortcut.  No bench or radio
operation is required for this plan or for the initial software-only witness.

## Risks and non-goals

- A generic API that combines unrelated controls would obscure receiver/slot
  identity and make false confirmation likely; preserve explicit field maps.
- Poll timing is variable, so an accessibility announcer must be transition
  driven and throttled rather than triggered for every state push.
- The grace timeout is a bounded honesty backstop, not proof that the radio
  adopted a command.
- This plan does not reopen completed predecessor issues, alter MOR-1410's
  hardware release gate, or authorize any physical-radio validation.
