# Radio StateStore Semantics

MOR-338 adds the runtime-level `StateStore` in `rigplane.core.state_store`.
It is the future single source of truth for decoded radio state, while legacy
mutable `RadioState` remains a compatibility surface until later migrations.

## Ownership

`StateStore` owns the mutable backing dictionaries for semantic field values
and freshness metadata. New consumers should read through snapshot APIs only:

- `StateStore.snapshot()` returns a full `StateSnapshot`.
- `StateStore.delta_since(snapshot)` returns semantic and freshness deltas
  since a prior snapshot.
- `StateStore.apply(observation)` is the only semantic write path.
- `StateStore.mark_stale_due()` is the only freshness-expiration write path.

Values are copied on ingress and egress so mutating an input observation payload
or exported snapshot dictionary cannot mutate store-owned state.

## Revisions

The store maintains three independent counters:

- `stateRevision`: increments only when a field value changes semantically.
- `freshnessRevision`: increments when a field moves between `unknown`,
  `fresh`, and `stale`, even when the semantic value is unchanged.
- `observationSeq`: increments for every applied observation, including no-op
  observations that only confirm an existing value.

Transport sequence is not owned by `StateStore`. Backend transports may keep
their own packet/frame ordering metadata, but that sequence is not interpreted
as a state revision and is not exposed by the store API.

## Freshness

Each observation may carry `max_age`. When `mark_stale_due()` observes that a
fresh field has exceeded that age, the field becomes stale without changing its
semantic value. The returned delta includes a `ReconciliationRequest` hint for
future acquisition scheduling. MOR-339 owns the actual scheduler and
`ensure_fresh` policy.

An observation for a stale field marks it fresh again. If the value is
unchanged, only `freshnessRevision` advances; `stateRevision` remains stable.

## Compatibility Boundary

This module does not import Web, rigctld, runtime, transport, or legacy
`RadioState` types. One-way compatibility adapters may be added later, but
direct mutation of the store's backing state is not a supported public API.
