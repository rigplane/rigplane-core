# Radio State Pipeline Regression Matrix

Status: MOR-336 shared test foundation
Date: 2026-06-02

This matrix maps the original state-pipeline symptoms from
`docs/superpowers/specs/2026-06-02-radio-state-pipeline-design.md` to reusable
future tests. CI cases must run without physical radios and should use
`tests/support/state_pipeline.py`. Hardware validation cases are listed
separately and require explicit opt-in.

## Harness Coverage

The shared harness provides:

- `FakeClock` for deterministic monotonic time, freshness deadlines, pending
  overlay expiry, and acquisition scheduling.
- `FieldPath`, `Observation`, `ChangeSet`, and `FreshnessTransition` test
  contracts for model-facing state pipeline assertions.
- `FakeStatePipeline` with `state_revision`, `freshness_revision`, and
  `observation_seq` counters.
- Fake backends for CI-V push, command responses, dropped unsolicited events,
  polling-only backends, Yaesu-like polling, and external rigctld-client
  responses.
- `FakeAcquisitionScheduler` for ensure-fresh scheduling assertions.
- `FakePendingOverlayStore` for scoped read-after-write overlay tests.
- Assertion helpers for revision counters and consumer-visible deltas.

## CI Regression Matrix

| Area | Original symptom or risk | Future CI tests | Harness primitives |
|---|---|---|---|
| Meters | S-meter/meter samples mutate state but wait for unrelated revision bumps before Web sees them. | Meter observations advance `observation_seq`; delivered meter deltas include latest sample and do not require unrelated frequency/mode changes. Add coalescing tests once production store exists. | `FakeCivPushBackend`, `FakeStatePipeline`, `assert_consumer_delta`, `assert_revision_counters` |
| Frequency latency | Slow serial or request/response radios can show delayed panel tuning updates. | CI-V unsolicited frequency, command-response frequency, and polling-only frequency observations all enter the same apply path; pulse polling can be scheduled after local/external tuning. | `FakeCivPushBackend`, `FakeCommandResponseBackend`, `FakePollingOnlyBackend`, `FakeYaesuLikeBackend`, `FakeAcquisitionScheduler`, `FakeClock` |
| Stale fields | A missed or malformed unsolicited frame leaves consumers believing old state is authoritative. | Freshness deadline marks the field stale without changing `stateRevision`; reconciliation poll publishes the corrected value and advances `freshnessRevision` separately. | `FakeDroppedUnsolicitedBackend`, `FakeClock`, `FakeStatePipeline` |
| Snapshots | HTTP snapshots and initial WebSocket state can disagree when they are built from different revision sources. | Snapshot projection uses the same `stateRevision` and `freshnessRevision` as WebSocket initial state; legacy `revision` aliases only `stateRevision`. | `FakeStatePipeline.snapshot()`, revision assertions |
| WebSocket reconnect | Reconnect/reset logic can confuse canonical state revision with transport sequence. | Reconnected WebSocket full state preserves canonical `stateRevision`/`freshnessRevision`; `transportSeq` reset does not look like a state rollback. | `FakeStatePipeline`, consumer delta assertions |
| rigctld GET | rigctld GET can read stale local cache instead of shared confirmed state. | GET frequency/mode reads fresh shared state; stale critical fields request acquisition or report unavailable instead of trusting expired values. | `FakeStatePipeline`, `FakeAcquisitionScheduler`, `FakeExternalRigctldClientBackend` |
| rigctld SET | SET read-after-write behavior currently depends on local pending/cache state. | SET creates a scoped pending overlay, confirms only on matching observation, and expires without mutating confirmed state. | `FakePendingOverlayStore`, `FakeCommandResponseBackend`, `FakeExternalRigctldClientBackend`, `FakeClock` |
| Pending overlays | Optimistic values can leak between Web, HTTP, rigctld sessions, or command IDs. | Pending overlays are scoped by source, session, command id, field path, and expiry; confirmation clears matching overlays only. | `FakePendingOverlayStore` |
| Dropped unsolicited events | Push-capable backends can still lose an event, so revision alone is not proof of physical state. | Dropped push sample does not mutate state or increment `observation_seq`; stale transition plus poll response reconciles the value. | `FakeDroppedUnsolicitedBackend`, `FakeStatePipeline`, `FakeClock` |
| Adaptive acquisition | Background polling can delay user commands or flood slow links. | ensure-fresh requests are deduped by path family, scheduled with fake time, and prioritized separately from background telemetry. | `FakeAcquisitionScheduler`, `FakeClock`, backend capability flags |
| Command responses | Command acknowledgements can be mistaken for confirmed radio state, or confirmed responses can bypass state revisions. | Command lifecycle remains separate; only response observations mutate confirmed state and produce deltas. | `FakeCommandResponseBackend`, `correlation_id`, revision assertions |
| External rigctld client | Hamlib-backed responses need to behave like observations, not a separate cache. | External rigctld-client poll responses publish `hamlib_response` observations into the same pipeline and preserve Hamlib error/read-only tests elsewhere. | `FakeExternalRigctldClientBackend`, `FakeStatePipeline` |

## Hardware Validation Matrix

Hardware validation must remain separate from CI and use explicit opt-in
markers such as `integration`, `hardware`, or `validation_hardware`.

| Hardware case | Purpose | Expected marker |
|---|---|---|
| IC-7610 LAN CI-V unsolicited meter/frequency burst | Compare real CI-V ingress rate with state revision and Web delivery rate. | `hardware` or `validation_hardware` |
| IC-7610 LAN missed-event recovery | Drop or ignore one captured unsolicited frame and verify reconciliation read corrects shared state. | `validation_hardware` |
| X6200 serial panel tuning latency | Measure bounded frequency update latency after external VFO tuning with and without unsolicited frames. | `integration` plus hardware opt-in |
| Yaesu CAT polling profile | Verify request/response polling updates shared state without model-specific state delivery branches. | `integration` plus hardware opt-in |
| External Hamlib rigctld client | Verify real `rigctld` GET/SET responses publish observations and preserve read-after-write behavior. | `integration` or `validation_hardware` |
| WebSocket reconnect against live radio | Verify reconnect full-state envelope preserves canonical state/freshness revisions while transport sequence resets. | `integration` plus hardware opt-in |

## Current MOR-336 Sample Tests

- `test_meter_updates_do_not_wait_for_unrelated_state_revisions` demonstrates a
  meter delta independent from unrelated state revisions.
- `test_stale_field_reconciles_after_missed_unsolicited_event` demonstrates
  stale-field reconciliation after a missed unsolicited event.
- `test_backend_variants_and_pending_overlays_are_scoped` demonstrates backend
  source variants and scoped pending overlays.
