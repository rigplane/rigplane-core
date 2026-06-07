# Radio Capability And Acquisition Policy

MOR-344 adds schema only. It does not implement `StateStore`,
`FreshnessClock`, `AcquisitionScheduler`, Web migration, rigctld migration, or
backend behavior changes.

## Purpose

Radio/provider state behavior is represented as profile metadata:

- `FieldCapability` says whether a canonical field is supported, unsupported,
  or unknown, and whether it can be acquired by unsolicited push, polling,
  stream-like meter updates, or observable command responses.
- `AcquisitionPolicy` says how future scheduler/adapters should poll and
  reconcile fields: cadence, freshness TTL, reconciliation priority, adaptive
  decay, external-CAT pause behavior, and meter coalescing.
- Missing metadata is explicit. `RadioAcquisitionProfile.capability_for(path)`
  returns an `unknown` capability with a diagnostic instead of implying that a
  scheduler should poll forever.

The schema lives under `rigplane.core` and is loaded by `rigplane.profiles`.
Web and rigctld delivery code must not consume this metadata directly; later
scheduler/adapters should consume it.

## Provider Examples

### Icom CI-V

`rigs/ic7610.toml` declares `provider = "icom_civ"`. It uses
`default_reconciliation_priority = "unsolicited"` because representative Icom
CI-V state can arrive as unsolicited updates while still remaining pollable.
Meters are marked `stream_like_meters` and get a short coalescing window.

### Yaesu CAT

`rigs/ftx1.toml` declares `provider = "yaesu_cat"`. It is polling-first:
frequency, mode, and meter fields are read by explicit CAT requests. Power-on
state is explicitly marked unsupported so future schedulers can report it
unavailable instead of retrying indefinitely.

### X6200-Like CI-V

`rigs/x6200.toml` declares `provider = "xiegu_civ"`. Frequency and mode are
polling-capable and command-response observable. The active mode field has a
per-field policy with `reconciliation_priority = "command_response"` to
represent X6200-like tuning behavior as data, not as Web or rigctld delivery
branches. SWR and ALC are marked unknown until hardware evidence proves support.

### External rigctld / Hamlib

External rigctld/Hamlib should use `provider = "external_rigctld"` when a
profile or adapter layer supplies acquisition metadata. Hamlib model output can
map known levels/functions to `supported` fields, omitted capabilities to
`unknown`, and model-proven gaps to `unsupported`. Core owns the schema and
read-only probing/ranking contract; managed setup UX, support evidence, and
private validation matrices stay outside this repository.

## Conservative Defaults

The schema defaults are intentionally cautious:

- cadence: `5.0` seconds
- freshness TTL: `15.0` seconds
- reconciliation priority: `poll`
- adaptive decay: disabled
- external CAT behavior: `pause_polling`

Unsupported and unknown fields cannot be marked pollable, stream-like,
unsolicited, command-response observable, or controllable. Stream-like fields
must be meter-family paths. Per-field meter coalescing is only valid for meter
paths.
