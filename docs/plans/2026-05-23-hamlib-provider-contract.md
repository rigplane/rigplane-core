# Hamlib Provider Contract and DiscoveryCandidate Schema

**Date:** 2026-05-23
**Status:** Accepted initial architecture
**Issue:** `#1577`
**Linear source:** `MOR-29`

## Purpose

Define the public open-core contract for adding a Hamlib-backed provider without
letting Hamlib become RigPlane's state model or consumer-facing API.

This note is a design contract only. It does not add production code, CLI
options, config keys, or rigctld wire behavior.

## Decision

Hamlib support is a replaceable backend adapter under the existing RigPlane
`Radio` protocol and capability protocols. Consumers continue to depend on
RigPlane capabilities, not Hamlib internals.

The initial Hamlib integration boundary is:

```text
RigPlane -> TCP rigctld text protocol -> external rigctld -> Hamlib -> radio
```

Direct `libhamlib` integration is deferred. It is not the default follow-up
phase. A future direct-library integration requires a separate spike with
evidence, acceptance criteria, licensing review, and crash-containment analysis.

Upper layers must not branch on Hamlib model IDs, Hamlib command names, or
rigctld command letters. `web/`, `rigctld/`, CLI command execution, and other
consumer paths must continue to operate through `Radio` and optional capability
protocols.

## Provider Contract

The first Hamlib provider is a thin client adapter for an already-running
external `rigctld` process. Its job is to translate between RigPlane's
capability model and the minimal rigctld commands needed by that model.

Required properties:

- It is selected as a backend/provider implementation, not as a replacement
  for `Radio`.
- It exposes RigPlane capability tags and protocol methods; it does not expose
  Hamlib model numbers or rigctld command names to consumers.
- It treats rigctld as an unreliable external service: connection failure,
  timeout, malformed response, or unsupported command is a backend failure or
  missing capability, not a reason for upper layers to use Hamlib-specific
  fallback logic.
- It maps Hamlib-specific mode names and VFO identifiers into RigPlane's
  normalized mode and receiver/VFO model before state reaches consumers.
- It keeps feature growth capability-driven. New Hamlib operations become
  available only when they can be represented by existing or deliberately
  extended RigPlane capability protocols.

### Minimal first-provider capability surface

The first provider should start with the smallest useful cross-vendor control
surface:

| Capability | Required behavior |
|------------|-------------------|
| Frequency | Read and set the active VFO frequency in Hz. |
| Mode | Read and set the active mode using RigPlane's normalized mode names. |
| PTT | Set transmit state and, when supported, read transmit state. |
| VFO | Select or report VFO only when the radio/rigctld target supports it reliably. |

The first provider does not need to expose audio, waterfall/scope, memories,
advanced filters, tuner controls, or vendor-specific panels unless later issues
define capability contracts for them.

## DiscoveryCandidate Schema

Assisted discovery returns ranked candidates. A candidate is evidence for a
possible setup path, not proof that a radio is safe to control.

Every `DiscoveryCandidate` must include:

| Field | Meaning |
|-------|---------|
| `transport` | The observed connection type, such as `serial`, `tcp`, `udp`, or `rigctld`. |
| `address` | The connection address. Examples: serial device path, `host:port`, or discovered endpoint. |
| `observed_identity` | Facts observed without unsafe writes, such as USB VID/PID, serial manufacturer/product, CI-V address response, rigctld model name, or banner data. |
| `suggested_backend` | The backend/provider RigPlane should try next, such as `serial`, `yaesu-cat`, or `hamlib`. |
| `suggested_model` | The best model hint available for the suggested backend. For Hamlib this may be a Hamlib model ID or name, but it remains candidate metadata and must not leak into consumer branching. |
| `confidence` | A bounded confidence value or label that lets UI/CLI rank candidates and distinguish exact, probable, ambiguous, and manual cases. |
| `evidence` | A structured list of observations and probe results that explain the ranking. Evidence should be specific enough for diagnostics and user review. |
| `safe_next_action` | The next action that does not perform unsafe writes or transmit, such as "ask user to confirm model", "try read-only rigctld status", or "manual configuration required". |

Discovery rules:

- Prefer read-only observations and bounded probes.
- Do not key upper-layer behavior directly from `suggested_model`.
- Do not transmit, toggle PTT, change frequency, write memories, or alter radio
  state during discovery.
- If candidates are ambiguous, return multiple ranked candidates with evidence
  instead of guessing silently.
- If no safe read probe exists, return a manual-configuration candidate with
  clear evidence and next action.

## Open-Core and Pro Boundary

The open-core repository owns the generic public contract:

- the Hamlib backend/provider shape;
- the external rigctld client behavior;
- the `DiscoveryCandidate` schema;
- safe, generic read-only discovery and ranking;
- tests and docs that are useful without hosted services or proprietary
  integrations.

Managed rigctld launch, process supervision, bundled Hamlib binaries, installer
packaging, automatic upgrades, support-bundle workflows, and desktop setup
wizards belong outside this repository. Those product and packaging workflows
are Pro concerns, even when they use the public Hamlib provider contract.

This boundary keeps the open-core implementation useful for users who already
run Hamlib themselves while leaving managed desktop convenience to the private
product layer.

## Compatibility Impact

- Public API: no change in this note.
- CLI: no behavior change in this note.
- Config: no behavior change in this note.
- rigctld wire behavior: no behavior change in this note.
- Docs: adds a public design contract for future implementation issues.

## Follow-up Implementation Issues

Implementation issues should treat this document as the source of truth for the
Hamlib boundary unless a later ADR supersedes it. In particular:

- fake rigctld tests should prove the provider maps rigctld responses into
  RigPlane capabilities without leaking Hamlib branches upward;
- the first provider should target the minimal capability surface above;
- discovery work should return `DiscoveryCandidate` values with confidence,
  evidence, and safe next actions rather than opaque device lists.
