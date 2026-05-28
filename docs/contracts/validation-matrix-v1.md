# Validation Matrix Contract — `rigplane-validation-matrix-v1`

**Schema name:** `rigplane-validation-matrix`
**Schema version:** `1`
**Status:** stable, public, open-source contract.
**Last updated:** 2026-05-28

## Purpose

This contract documents the public, machine-readable shapes used by the
`rigplane validate` vertical: capability-declaration **templates** (the planned
validation matrix for one radio) and validation **artifacts** (the recorded
evidence of a run). It is the spec that `rigplane.validation` builds against and
that any tooling consuming validation output should target.

The current release ships the **dry-run** path only. The artifact shape is
forward-compatible with hardware runs, but hardware execution is intentionally
not implemented and is double-gated (see [Safety policy](#safety-policy)).

## Core boundary

This document is open-source and authoritative for the open-core validation
matrix. The validation runner lives behind the `Radio` protocol and the
`local-extensions/` Pro boundary described in
`docs/architecture/open-core-policy.md`; any Pro-tier validation extensions
(extended check libraries, signed result attestation, fleet aggregation) are
governed by a separate private contract and are deliberately out of scope here.
The schema in this document carries no telemetry and no licence context.

## Template schema

A template plans the matrix for one radio. Top-level object:

| Field            | Type     | Required | Notes                                       |
| ---------------- | -------- | -------- | ------------------------------------------- |
| `schema_version` | integer  | yes      | Must equal `1`.                             |
| `radio`          | object   | yes      | `{ model, profile_id }`, both non-empty.    |
| `entries`        | array    | yes      | Non-empty list of entry objects (below).    |

Each `entries[]` object:

| Field         | Type    | Required | Notes                                                              |
| ------------- | ------- | -------- | ------------------------------------------------------------------ |
| `check_id`    | string  | yes      | Non-empty; unique within the template.                             |
| `capability`  | string  | yes      | `""` or a member of `KNOWN_CAPABILITIES`.                          |
| `level`       | integer | yes      | `0`–`5` (see [Levels](#levels)).                                   |
| `declaration` | string  | yes      | One of the [declaration vocabulary](#capability-declaration-vocabulary). |
| `summary`     | string  | yes      | Human-readable description.                                        |
| `tx_adjacent` | boolean | no       | Default `false`; gates the check behind operator authorization.    |

## Artifact schema

An artifact records the evidence of a run. Top-level object:

| Field            | Type    | Required | Notes                                                    |
| ---------------- | ------- | -------- | -------------------------------------------------------- |
| `schema_version` | integer | yes      | Must equal `1`.                                          |
| `tool`           | string  | yes      | `"rigplane-validation-matrix"`.                          |
| `mode`           | string  | no       | `"dry-run"` by default.                                  |
| `core_version`   | string  | yes\*    | rigplane version that produced the artifact.             |
| `core_commit`    | string  | no       | Git commit; omitted when unavailable.                    |
| `logs_path`      | string  | no       | Path to run logs; omitted when unavailable.              |
| `radio`          | object  | yes      | `{ model, profile_id }`.                                 |
| `transport`      | object  | yes      | `{ backend, host?, port?, baud? }`.                      |
| `safety`         | object  | yes      | See [Safety policy](#safety-policy).                     |
| `levels`         | array   | yes      | List of level objects, each `{ level, checks[] }`.       |
| `metadata`       | object  | no       | Free-form; carries `summary` status counts.              |

\* `core_version` is required by the producer; the validator accepts an empty
string for robustness but the CLI always populates it.

Each `levels[].checks[]` object:

| Field            | Type    | Required | Notes                                                       |
| ---------------- | ------- | -------- | ----------------------------------------------------------- |
| `check_id`       | string  | yes      |                                                             |
| `capability`     | string  | yes      | `""` or a member of `KNOWN_CAPABILITIES`.                   |
| `level`          | integer | yes      | `0`–`5`.                                                    |
| `level_name`     | string  | (emitted)| Lower-cased level name (producer output).                   |
| `status`         | string  | yes      | One of the [status vocabulary](#status-vocabulary).         |
| `declaration`    | string  | yes      | One of the declaration vocabulary.                          |
| `summary`        | string  | yes      |                                                             |
| `failure_domain` | string  | cond.    | **Required** when `status` is `fail` or `blocked`.          |
| `evidence`       | object  | no       | Free-form evidence; omitted when empty.                     |
| `error`          | string  | no       | Error detail; omitted when absent.                          |

## Status vocabulary

| Status            | Meaning                                                      |
| ----------------- | ------------------------------------------------------------ |
| `pass`            | Check executed and succeeded.                                |
| `fail`            | Check executed and failed (carries `failure_domain`).        |
| `skip`            | Not executed this run (e.g. supported, dry-run planned).     |
| `unsupported`     | Capability not declared/available for this radio.            |
| `manual_required` | Requires an operator to perform/verify out of band.          |
| `blocked`         | Not run because authorization was withheld (carries domain). |

## Capability declaration vocabulary

| Declaration                    | Meaning                                                |
| ------------------------------ | ------------------------------------------------------ |
| `supported`                    | Capability is declared in the radio profile.           |
| `unsupported_pending_evidence` | Not declared; absence pending evidence to confirm.     |
| `manual_required`              | Requires manual/operator verification.                 |

## Levels

| Level | Name                     | Scope                                          |
| ----- | ------------------------ | ---------------------------------------------- |
| `0`   | `static_profile`         | Static profile inspection, no I/O.             |
| `1`   | `discovery`              | Discovery / identification.                    |
| `2`   | `basic_control`          | Basic read/write control (freq, mode).         |
| `3`   | `capability_matrix`      | Per-capability functional checks.              |
| `4`   | `compatibility_surfaces` | Audio, scope, rigctld surfaces.                |
| `5`   | `stress_recovery`        | Stress, recovery, TX-adjacent operations.      |

## Failure domains

A `fail` or `blocked` check must name the responsible subsystem:

`discovery`, `transport`, `command_execution`, `readback`,
`state_publishing`, `rigctld`, `audio`, `scope_waterfall`.

## Safety policy

The `safety` block records operator authorization:

| Field                | Type    | Default | Notes                                         |
| -------------------- | ------- | ------- | --------------------------------------------- |
| `tx_allowed`         | boolean | `false` | Authorizes TX-adjacent checks (PTT/TX).       |
| `tuner_allowed`      | boolean | `false` | Authorizes tuner tune-cycle checks.           |
| `operator_id`        | string  | omitted | Operator identifier when supplied.            |
| `authorized_at_unix` | integer | omitted | Authorization timestamp when supplied.        |

Both `tx_allowed` and `tuner_allowed` default to `false`. TX-adjacent checks —
including the antenna tuner tune cycle, which can key the transmitter — are
**opt-in only**. A `tx_adjacent` entry that is not authorized is reported as
`blocked` with the `command_execution` failure domain; it is never silently
skipped.

Hardware execution is additionally double-gated at the CLI: `--hardware`
requires both the `--allow-hardware` flag and the
`RIGPLANE_VALIDATION_ALLOW_HARDWARE=1` environment variable. Even with both
gates open, the current release refuses hardware runs (exit code `3`) because
the hardware path is not implemented.

## Versioning

`schema_version` is `1` for this contract revision. Non-breaking changes
(adding optional fields) keep the version. Breaking changes (removing/renaming
required fields, changing types or status/declaration semantics) mint a new
`schema_version` (e.g. `2`).

## JSON examples

### Template

```json
{
  "schema_version": 1,
  "radio": { "model": "IC-7300", "profile_id": "ic7300" },
  "entries": [
    {
      "check_id": "discovery.identify",
      "capability": "",
      "level": 1,
      "declaration": "supported",
      "summary": "Radio identifies on the configured transport.",
      "tx_adjacent": false
    },
    {
      "check_id": "tx.ptt",
      "capability": "tx",
      "level": 5,
      "declaration": "manual_required",
      "summary": "PTT key/unkey (requires operator authorization).",
      "tx_adjacent": true
    }
  ]
}
```

### Artifact (dry-run)

```json
{
  "schema_version": 1,
  "tool": "rigplane-validation-matrix",
  "mode": "dry-run",
  "core_version": "2.5.1",
  "radio": { "model": "IC-7300", "profile_id": "ic7300" },
  "transport": { "backend": "fixture" },
  "safety": { "tx_allowed": false, "tuner_allowed": false },
  "levels": [
    {
      "level": 5,
      "level_name": "stress_recovery",
      "checks": [
        {
          "check_id": "tx.ptt",
          "capability": "tx",
          "level": 5,
          "level_name": "stress_recovery",
          "status": "blocked",
          "declaration": "manual_required",
          "summary": "PTT key/unkey (requires operator authorization).",
          "failure_domain": "command_execution"
        }
      ]
    }
  ],
  "metadata": {
    "summary": {
      "pass": 0, "fail": 0, "skip": 3,
      "unsupported": 0, "manual_required": 1, "blocked": 2
    }
  }
}
```

## See also

- Design plan: `docs/plans/2026-05-28-real-radio-validation-matrix.md`
- Schema source: `src/rigplane/validation/schema.py`
- Layer charter: `src/rigplane/validation/LAYER.md`
- Open-core policy: `docs/architecture/open-core-policy.md`
