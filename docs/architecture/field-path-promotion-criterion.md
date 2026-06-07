# Backend-neutral FieldPath promotion criterion

Status: Accepted
Context: Radio State Pipeline (MOR-334). Relates to open-core-policy.md.

## Context

Radio state is delivered through a single backend-neutral model. Three layers
must not be conflated:

- Capability protocols (`core.radio_protocol`) — what a backend can do, coarse.
- Acquisition policy (`rigs/<rig>.toml [state_acquisition]`) — which fields THIS
  rig acquires and how (polling_only / stream_like_meters /
  command_response_observable / supported_controls / unsupported).
- The neutral FieldPath registry (`DEFAULT_FIELD_REGISTRY`, `FieldFamily`) — the
  SHARED vocabulary every consumer (Web, rigctld, API) reads, vendor-agnostic.

Question: when does a field earn a neutral FieldPath vs stay vendor-namespaced
(`<vendor>.*` compat) or a documented limitation?

## Decision

Promote a field to a neutral FieldPath when its SEMANTICS are shared:
  (a) two or more backends expose the same concept, OR
  (b) it is a standardized, domain-universal feature (e.g. CTCSS/tone, RIT,
      split, APF) — even if only one backend implements it today.

Keep vendor-namespaced / compat-only when the semantics are vendor-specific
(e.g. Yaesu Contour tone-shaping model, FR/FT function-routing model). Do NOT
mint a neutral name whose meaning would differ per radio — that is the
anti-pattern this ADR exists to prevent.

## Neutral fields are optional per backend (no imposed burden)

A neutral FieldPath imposes ZERO work on a backend that lacks the feature:
- The rig declares no acquisition for it (or lists it `unsupported`).
- `capability_for()` returns an UNKNOWN capability → the scheduler never polls
  it (`acquisition_scheduler`: `can_poll` requires SUPPORTED).
- The Web payload seeds it `missing` (`_default_snapshot_field_status`); it
  never becomes `available`/`stale` without a real observation.
- The fieldStatus-honoring v2 UI hides unobserved fields (MOR-429).

Note: `unsupported` is an acquisition-policy concept; the web `fieldStatus` is
three-valued (`missing | available | stale`) and `unsupported` collapses to
`missing` there.

The only hard requirement is therefore SEMANTIC coherence, not implementation
cost across backends.

## Process

New neutral field = public Core contract change → `radio_protocol.py` first,
then the FieldPath registry / projection / acquisition, per CLAUDE.md. No new
`FieldFamily` unless the field genuinely doesn't fit existing families
(operator_controls / operator_toggles / tx_state / slow_state / meters / …).

## Consequences

- CTCSS/tone and APF qualify on BOTH grounds (≥2 in-repo backends AND domain
  standard) and are gaps in the registry (projection keys already exist) —
  candidates for promotion ahead of the broad survey.
- Yaesu Contour and FR/FT routing stay vendor-namespaced / documented limitation.
- A broad cross-vendor sweep of hamlib capabilities for further gaps is tracked
  in MOR-450.
