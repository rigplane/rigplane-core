# Radio State Pipeline Contracts

Status: MOR-337 contract baseline
Date: 2026-06-02
Canonical import path: `rigplane.core.state_pipeline_contracts`

This document records the first backend-neutral state pipeline contracts. The
contracts are additive and do not change the current public API, Web payloads,
rigctld wire behavior, backend delivery semantics, or `RadioState` mutation
paths. Later migration lanes can consume these contracts without adding
radio-model-specific branches.

## FieldPath Grammar

`FieldPath` is the canonical state-field address. String paths are stable for
tests, Web projections, rigctld projections, diagnostics, and provider
capability metadata.

Canonical forms:

- `receiver.<receiver>.slot.<A|B>.freq_mode.<field>`
- `receiver.<receiver>.active.freq_mode.<field>`
- `receiver.<receiver>.<family>.<field>`
- `receiver.<receiver>.vfo.active_slot`
- `global.<family>.<field>`
- `scope_controls.receiver.<receiver>.display.<field>`
- `scope_controls.global.display.<field>`

Validation rules:

- Receiver ids, families, and field names use lowercase snake-case tokens.
- VFO slot paths only accept `A`, `B`, or `active`.
- Fixed slot and active slot paths are only valid for `freq_mode` fields.
- Global, connection, and health paths cannot include receiver or slot
  dimensions.
- Scope-control paths can include a receiver dimension, but not a VFO slot.
- Registry construction rejects duplicate serialized paths and ambiguous paths
  that reuse the same target/name with different families.

## Examples

| State | Canonical path | Notes |
|---|---|---|
| Active MAIN frequency | `receiver.main.active.freq_mode.freq_hz` | Active VFO view for commands and consumer projections. |
| MAIN VFO A frequency | `receiver.main.slot.A.freq_mode.freq_hz` | Fixed slot view for unselected-slot freshness and pending confirmation. |
| Active SUB mode | `receiver.sub.active.freq_mode.mode` | Backend-neutral mode string value. |
| MAIN active slot | `receiver.main.vfo.active_slot` | Selected VFO slot state, separate from frequency/mode values. |
| MAIN S-meter | `receiver.main.meters.s_meter` | Receiver-scoped meter sample. |
| ALC meter | `global.meters.alc` | TX meter sample not tied to one receive VFO. |
| Power meter | `global.meters.power` | TX output meter sample. |
| Noise reduction | `receiver.main.operator_toggles.nr` | Operator toggle, writable when backend capabilities allow it. |
| Noise blanker | `receiver.main.operator_toggles.nb` | Operator toggle, writable when backend capabilities allow it. |
| Volume / AF level | `receiver.main.operator_controls.af_level` | Normalized receiver level. |
| RF gain | `receiver.main.operator_controls.rf_gain` | Normalized receiver level. |
| PBT inner | `receiver.main.operator_controls.pbt_inner` | Passband tuning control. |
| PBT outer | `receiver.main.operator_controls.pbt_outer` | Passband tuning control. |
| PTT | `global.tx_state.ptt` | Global TX state. |
| Power on/off | `global.tx_state.power_on` | Global power state. |
| Scope span | `scope_controls.receiver.main.display.span` | Scope display control scoped to a receiver. |

The default registry includes representative MAIN and SUB receiver paths for
frequency, mode, active slot, S-meter, NR/NB, AF level, RF gain, and PBT, plus
global TX and meter paths. It is intentionally not a complete `RadioState`
field map yet; later lanes should extend it as field families migrate.

### FTX-1 `ab_shared` VFO topology note

The FTX-1 profile uses `[vfo] scheme = "ab_shared"`. In this topology there is
no per-receiver A/B slot concept: `FA` and `FB` are the MAIN and SUB receive
frequencies, `VS` selects the active receiver focus, `FR` controls dual/single
RX, and `FT` selects the TX source. The dynamic HF-vs-U/VHF collapse observed
on the radio is not currently modeled in Core. MOR-558 therefore only removes
dead Web `fieldStatus` parent seeds for bare `main/sub.vfoA/vfoB`; it keeps the
existing per-leaf VFO status seeds unchanged.

## Observation

`Observation` represents one decoded state-bearing sample from CI-V, CAT,
external rigctld, polling, command response, or local reconciliation. It is not
a command acknowledgement and does not imply a state change.

Serializable fields:

- `path`: canonical `FieldPath` string.
- `value`: JSON-compatible normalized value.
- `source`: `SourceMetadata`, including source type, provider, transport,
  native protocol id, and optional capability id.
- `timestampMonotonic`: local monotonic timestamp.
- `quality`: flags such as `confirmed`, `partial`, or `synthetic`.
- `correlationId`: optional command or acquisition id.
- `maxAge`: optional freshness policy window.

## ChangeSet

`ChangeSet` represents the result of applying observations to a future
production state model. It carries the canonical state revision and
freshness revision separately. It does not own WebSocket transport sequencing
and does not replace the current delivery behavior in MOR-337.

Serializable fields:

- `revision`: canonical state-value revision after the change.
- `freshnessRevision`: freshness or health revision after the change.
- `observationSeq`: observation sequence after the applied sample or batch.
- `changes`: typed `FieldChange` entries with previous and current values.
- `timestampMonotonic`: local monotonic timestamp.
- `sources`: source metadata represented in the change set.
- `coalesced`: whether multiple observations were batched.

## Command Contracts

`CommandIntent` represents an attempt to query or change radio state from
WebSocket, HTTP, rigctld, public API, diagnostics, or internal policy code.
`CommandLifecycleEvent` records accepted, queued, sent, acknowledged, failed,
timed out, confirmed, and superseded states. These lifecycle events are
separate from confirmed state changes.

Command contracts can name a target `FieldPath`, a pending-overlay policy, and
expected observation paths. They do not implement command execution or mutate
confirmed state.

## MOR-347 Web Delivery Cleanup

MOR-347 removes the Web poller-owned public revision counter. Web HTTP and
WebSocket state payloads use `StateStore.snapshot().state_revision` as the
canonical semantic revision. The legacy Web `revision` key remains present for
existing clients, but it is an alias for `stateRevision`, not a poller or
transport counter.

WebSocket delta/full envelopes may include `transportSeq` as additive ordering
metadata. `transportSeq` is local to the WebSocket representation and must not
be used for stale-state rejection, freshness, or HTTP/WS race resolution.

## Compatibility

MOR-337 only adds a new core module and documentation. Existing import paths,
public `RadioState` serialization, Web state payloads, rigctld text protocol,
Icom/Yaesu/Hamlib adapter behavior, and state delivery semantics remain
unchanged. New code should import contracts from
`rigplane.core.state_pipeline_contracts`; they are not added to the top-level
`rigplane` Tier 1 API in this milestone.

After MOR-347, compatibility-sensitive Web state behavior is:

- legacy `revision` is retained and aliases canonical `stateRevision`;
- `freshnessRevision` remains separate from semantic state revision;
- `healthRevision` remains a public Web compatibility field;
- `transportSeq` is additive WebSocket ordering metadata.
