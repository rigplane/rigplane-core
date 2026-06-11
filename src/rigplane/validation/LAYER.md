# `validation` layer

## Charter

Real-radio validation matrix: the versioned schema and dry-run runner for the
`rigplane validate` vertical. It defines two machine-readable shapes —
capability-declaration **templates** (the planned per-radio matrix) and
validation **artifacts** (recorded evidence) — plus validators that narrow
untrusted JSON into typed dataclasses, and a dry-run runner that maps a
template into per-level `CheckResult` skeletons with operator-safety gating.

Hardware execution is **out of scope** for this layer in the current release.
The runner only produces dry-run plans; the CLI refuses `--hardware` even when
both opt-in gates are open. No transport, radio, or audio I/O happens here.

## Public API

`validation/__init__.py` re-exports the full surface:

- **Enums**: `CheckStatus`, `CapabilityDeclaration`, `ValidationLevel`,
  `FailureDomain`.
- **Dataclasses** (frozen + slots): `CheckResult`, `LevelResult`,
  `OperatorSafetyBlock`, `TransportInfo`, `RadioTarget`,
  `CapabilityDeclarationEntry`, `MatrixTemplate`, `ValidationArtifact`.
- **Validators**: `validate_template_dict`, `validate_artifact_dict`, raising
  `SchemaValidationError`.
- **Runner**: `load_template`, `dry_run_results`, `summarize_results`,
  `build_validation_artifact`, `human_summary`, plus `HARDWARE_OPT_IN_ENV` and
  `HardwareExecutionBlocked`.
- **Constants**: `SCHEMA_VERSION`, `TOOL_NAME`.

## Dependencies

`validation` is a top-layer consumer (it sits alongside `cli/`): it MAY
import `core`, `runtime`, `backends` — including `backends.rigctld_client`,
used by the hamlib-external provider — `profiles`, `audio`, and `commands`.
Today it imports only `rigplane.core.*` (`capabilities`, `exceptions`,
`radio_protocol`, `types`) plus the standard library. It must not import
`web/`, `rigctld/`, or `cli/`; that boundary is enforced by the ruff TID251
banned-import rule. The `cli._validate` module is the sole consumer wiring
`validate` into the CLI.

The leaf-consumer boundary is enforced by import-linter: the
`validation-leaf` forbidden contract in the repo-root `.importlinter` bans
every lower layer (`web`, `rigctld`, `backends`, `runtime`, `profiles`,
`audio`, `commands`, `scope`, `dsp`, `core`) from importing
`rigplane.validation`. Only `cli/` may consume it.

## Contract

The template and artifact shapes are a public, versioned contract — see
`docs/contracts/validation-matrix-v1.md`. Field names, types, and `to_dict`
shapes are frozen for `schema_version = 1`.
