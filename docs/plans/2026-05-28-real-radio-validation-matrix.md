# Real-radio validation matrix — Core vertical

**Date:** 2026-05-28
**Issues:** #1645, #1646, #1647, #1651
**Status:** dry-run vertical landed; hardware execution stubbed.

## Summary

The validation matrix gives RigPlane Core a versioned, machine-readable way to
declare *what should work* on a given radio (a **template**) and to record
*what was observed* (an **artifact**). This vertical lands the schema, the
validators, a dry-run runner, the `rigplane validate` CLI, four seed radio
templates, and the public contract. Hardware execution is intentionally
deferred and stubbed behind explicit opt-in gates.

## Scope

In scope for this vertical:

- Frozen schema dataclasses + enums (`schema_version = 1`).
- `validate_template_dict` / `validate_artifact_dict` validators that narrow
  untrusted JSON into typed dataclasses without `type: ignore`.
- Dry-run runner: map a template into per-level `CheckResult` skeletons,
  gating TX-adjacent and tuner checks behind operator authorization.
- `rigplane validate` CLI (dry-run; JSON or human summary).
- Seed templates for IC-7300, FTX-1, TX-500, X6200.
- Public contract `docs/contracts/validation-matrix-v1.md`.
- `validation_hardware` pytest marker registration.

Out of scope (stubbed or deferred):

- **Hardware I/O** — no transport, radio, or audio access. `--hardware` is
  double-gated (`--allow-hardware` + `RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`)
  and still refuses with exit `3` because no hardware path exists yet.
- Capability bug fixes, Pro code, resume/persistence engines, new
  abstractions, YAML or new third-party dependencies, `.importlinter` changes.

## File layout

```
src/rigplane/validation/
  __init__.py        public re-exports
  schema.py          enums, dataclasses, validators
  runner.py          dry-run runner + artifact assembly
  LAYER.md           layer charter
src/rigplane/cli/_validate.py   CLI subcommand (mirrors _diagnose.py)
docs/contracts/validation-matrix-v1.md
docs/validation/templates/{ic7300,ftx1,tx500,x6200}.json
tests/test_validation_{schema,runner,templates}.py
tests/fixtures/validation_template_ic7300.json
tests/fixtures/validation_artifact_mixed.json
tests/fixtures/validation_artifact_reverse_sync_fail.json
```

The `validation` layer imports only `rigplane.core.capabilities` and the
standard library; `cli._validate` is its sole CLI consumer.

## Safety model

`tx_allowed` and `tuner_allowed` default to `false`. TX-adjacent checks
(PTT/TX and the tuner tune cycle, which can key the transmitter) are opt-in
only; an unauthorized `tx_adjacent` entry is reported `blocked` with the
`command_execution` failure domain, never silently skipped.

## What is stubbed

The hardware execution path. The artifact shape is forward-compatible with
real runs (`mode`, `transport`, `failure_domain`, `evidence`, `error`,
`logs_path` are all part of the v1 contract), but this release produces only
dry-run plans.

## TDD order

1. `schema.py` → `tests/test_validation_schema.py`
2. `runner.py` → `tests/test_validation_runner.py`
3. `cli/_validate.py` + CLI wiring
4. seed templates → `tests/test_validation_templates.py`
5. contract doc + this plan
6. `validation_hardware` pytest marker

## Links

- #1645 — schema + contract
- #1646 — dry-run runner + CLI (this vertical)
- #1647 — seed templates
- #1651 — hardware execution (future, stubbed here)
