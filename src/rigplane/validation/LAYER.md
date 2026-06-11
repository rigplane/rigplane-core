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

Imports only `rigplane.core.capabilities` (`KNOWN_CAPABILITIES`) and the
standard library. It must not import from upper layers (`web/`, `rigctld/`,
`backends/`, `runtime/`, `cli/`). The `cli._validate` module is the sole
consumer wiring `validate` into the CLI.

The upward-import boundary (no imports from `web/`, `rigctld/`, `cli/`) is
governed by this charter and enforced by the ruff TID251 banned-import rule.
`rigplane.validation` is not currently listed in `.importlinter` — it sits
outside the layer DAG, mirroring `rigplane.diagnostics`. Adding it to the
import-linter contract is deferred; do not claim import-linter enforcement
that does not exist.

## Automated audio probes (`audio_checks.py`)

The `AUDIO_PROBE` check kind (GH #1650; MOR-639/640/641) is the CI-automated
counterpart of the MANUAL `audio.rx` / `scope.capture` operator checks:

- `run_rx_rms_check` — injects a reference tone through
  `FakeAudioBackend`/`FakeRxStream` and verifies the delivered PCM RMS
  (`failure_domain=audio`).
- `run_tx_byte_perfect_check` — pushes captured frames through the REAL
  `AudioStream` LAN packetization and requires a byte-perfect reassembled
  payload (`failure_domain=audio`).
- `run_scope_presence_check` — feeds PCM to the REAL `AudioFftScope` and
  requires in-band bins above the out-of-band baseline
  (`failure_domain=scope_waterfall`; MOR-512/528 regression guard).

Integration decision: the pre-existing MANUAL `audio.rx`/`scope.capture`
entries are KEPT for real-hardware operator confirmation; the probes are
additive. Generated hardware templates carry `AUDIO_PROBE` checks as
`MANUAL_REQUIRED` (they are never auto-run on a live radio; `hardware.py`
SKIPs them defensively), while the CI harness executes them via
`run_audio_probe_checks()` + `audio_probe_level_results()` and folds the
results into `build_validation_artifact` → `gate_artifacts`, so audio
regressions gate exactly like command regressions. `audio.tx.byte_perfect`
is `tx_adjacent=True` per GH #1650 (TX audio stays behind explicit operator
safety enablement on real hardware).

This module imports `rigplane.audio` (the deterministic fakes, the LAN audio
packetizer, the FFT scope) and `rigplane.scope` (`ScopeFrame`) — permitted:
`validation` is a top-level consumer outside the layer DAG, and only the CI
harness imports `audio_checks` (the `rigplane.validation` facade does not
re-export it).

## Contract

The template and artifact shapes are a public, versioned contract — see
`docs/contracts/validation-matrix-v1.md`. Field names, types, and `to_dict`
shapes are frozen for `schema_version = 1`.
