# State delivery: liveness-only changes go out at most once per second

MOR-4244. Design note for the WebSocket `state_update` stream.

## Problem

With a radio whose state does not change, the server still sent about 13
`state_update` messages per second (IC-705 simulator, measured on the Mac mini
bench, evidence in MOR-4244). The delivery key
(`server.py: WebServer._public_state_delivery_key`) contained
`StateSnapshot.observation_seq` and the radio's `last_valid_frame_monotonic`,
so every radio answer re-delivered the state, including answers that only
confirmed an unchanged value (the PTT poll alone).

## What counts as liveness-only

Derived from the producer's types, not from payload keys:

| Producer field | Typed source | Class |
| --- | --- | --- |
| `observation_seq` | `state_store.py: StateSnapshot` | liveness |
| per-field `last_observed_monotonic`, `max_age` | `state_store.py: FieldSnapshot` | liveness (moves with `observation_seq`) |
| per-field `SourceMetadata` other than `source`/`provider` (`native_id`, `transport`, ...) | `state_pipeline_contracts.py: SourceMetadata` | liveness (moves with `observation_seq`) |
| frame stamp `last_valid_frame_monotonic` | `radio_protocol.py: Radio` | liveness |
| `state_revision` (a value changed) | `StateSnapshot` | content |
| `freshness_revision` (fresh/stale transition) | `StateSnapshot` | content |
| per-field `source.source` (observation kind), `source.provider`, `quality`, `clock_domain` | `FieldSnapshot` | content |
| `provider_generation`, health revision, identity/session/audio records, client counts, SDR status, monitor mute, liveness `staleAfterMs`/`source` | as today | content |

`max_age` and `native_id` are read-provenance. On the IC-705 simulator the
`maxAge` of the frequency/mode fields (and on some reads their
`source.nativeId`) changes with no value change about once per second
(relative-VFO retention re-stamps `max_age` in
`state_store.py: StateStore.apply_relative_vfo_observations`). No client reads
either (Core frontend: type declaration only; Pro: none). The observation kind
(`command_response` versus `poll_response`) and `quality` are read by the
frontend (`scope-passband-display.ts`, `panel-commands.ts`), so they stay
content.

The payload fields that move when only liveness moves are the projections of
the rows above plus the build-time clocks: `observationSeq`,
`fieldStatus.*.lastObservedMonotonic`, `fieldStatus.*.maxAge`,
`fieldStatus.*.source.nativeId` (and the other provenance leaves),
`connection.liveness.lastFrameAgeMs`, `radioHealth.sinceMs`, `updatedAt`,
`publicStateSeq`.

The key becomes a typed record, `server.py: PublicStateDeliveryKey`
(`content`, `field_metadata`, `liveness`).

## Rule

- Any change of `content` is delivered at once (existing 50 ms coalescing
  unchanged).
- A change of `liveness` alone is delivered at most once per
  `LIVENESS_DELIVERY_INTERVAL_S` (1 s) after the previous delivery; a deferred
  one is flushed by a timer at the end of the interval, so the last evidence
  always reaches clients.
- For `COMMAND_FOLLOWUP_WINDOW_S` (3 s) after any command lifecycle event,
  liveness-only changes are not held back. The frontend confirms a command on
  the post-write read-back of the commanded field
  (`commands.svelte.ts: reconcileStateBackedCommands`,
  `pending-intent-table.ts: latestPendingParam`); when the read-back carries
  the same value (a no-op or refused write), only `lastObservedMonotonic`
  moves, and holding it back would keep the pending marker up to 1 s longer.
- Connect baselines, explicit subscribes and `force` deliveries are not
  affected.

## Consumers checked

| Consumer | Reads | Need | Effect |
| --- | --- | --- | --- |
| Core frontend `connection.svelte.ts` | time of last accepted `state_update` | stale after 5 s silence | 1 Hz heartbeat keeps it fresh |
| Core frontend command confirmation | `fieldStatus.*.lastObservedMonotonic`, `observationSeq` | per-field read-back after a command | covered by the command follow-up window |
| Core frontend display/feedback adapters | `freshness`, `availability`, `lastObservedMonotonic` as evidence | fresh/stale state | freshness transitions are content, delivered at once |
| Core frontend `connection.liveness.lastFrameAgeMs`, `radioHealth.sinceMs` | type declarations only, no reader | none | none |
| Pro companion event feed (`event_feed.py`) | state facts, `control_stream_live` | stream alive | unchanged |
| Pro RC-28 dispatcher | lamp facts; stale after 5 s with no `state_update` | stream alive | 1 Hz heartbeat keeps it fresh |
| Pro dogfood `tx_smoke` | `lastObservedMonotonic` via HTTP state | snapshot read | unaffected (HTTP) |
| rigctld server | radio and store directly | n/a | unaffected |
| Station, Tower | no `state_update` reader | n/a | unaffected |
| External WebSocket clients | same envelopes and fields | | only the cadence of liveness-only deltas changes |

No consumer reads `lastFrameAgeMs` from the stream today, so no client-side
age computation is added. A future reader that needs a smooth age computes
`lastFrameAgeMs + (now - receivedAt)` on its own clock; `GET /api/v1/info`
keeps serving the age at response time.

## Compatibility

No field is added, removed or renamed; envelopes, `stateContractVersion` and
delta semantics are unchanged (a delta is always against the last delivered
body). Only the cadence of liveness-only deltas changes, so no version bump.

## Tests

- Static simulator over a deterministic clock: count of `state_update` per
  second with only liveness moving (red before: one per poll).
- A value change right after a delivery is still delivered at once.
- A same-value change of observation kind (`command_response`) is delivered at
  once; a same-value re-read with another `max_age` is held back.
- Within the command follow-up window liveness-only changes go out unthrottled.
- A deferred liveness change is flushed by the timer.
