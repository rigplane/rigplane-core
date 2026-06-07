# Radio AcquisitionScheduler Semantics

MOR-339 adds the minimal backend-neutral acquisition scheduler in
`rigplane.core.acquisition_scheduler`. It is a freshness and reconciliation
request queue only; it does not call radio backends, mutate confirmed state, or
deliver Web/rigctld updates.

## Model Service API

`RadioStateModelService.ensure_fresh(paths, max_age, priority, reason)` first
checks the shared `StateStore` snapshot. If every requested field is fresh and
newer than `max_age`, the service returns the current `FieldSnapshot` values.
Otherwise it delegates to `AcquisitionScheduler.ensure_fresh(...)`.

This keeps consumers behind the model service. Web, rigctld, CLI, and public
API callers should request freshness through this service instead of calling
backend getters directly.

## Scheduler Output

The scheduler emits `AcquisitionRequest` objects. A request includes:

- typed `FieldPath` targets;
- priority and reason metadata;
- requested time, freshness deadline, timeout, and max age;
- provider and capability ids from `RadioAcquisitionProfile`;
- selected acquisition method (`poll`, `command_response`, or
  `wait_for_unsolicited`);
- the relevant `AcquisitionPolicy`, including meter coalescing and external
  CAT behavior.

Backend executors may consume these requests and later apply real
`Observation` values through `StateStore.apply(...)`. The scheduler must not
invent synthetic confirmed state.

## Dedupe And Priority

Requests dedupe by compatible field family, receiver/slot scope, acquisition
method, and acquisition policy. Repeated compatible requests keep one
acquisition id, preserve accumulated reasons, merge target paths, and upgrade to
the highest priority and most urgent deadline/max age. Pending requests are
returned in execution order so user-facing freshness and command confirmation
can preempt background telemetry.

## External CAT Ownership

`pause_external_cat(...)` defers requests whose policy requires polling pause.
`resume_external_cat()` queues the deferred requests in priority order. Fields
with `external_cat_pause = "continue"` can still queue while ownership is
active, and the emitted request records that external CAT was paused.

Unsupported or unknown capabilities return `UNAVAILABLE` instead of retrying
forever or bypassing the backend-neutral queue.
