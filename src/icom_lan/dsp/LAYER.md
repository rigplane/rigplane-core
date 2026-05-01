# `dsp` layer

## Charter

Pure real-time DSP pipeline: nodes, resampler, tap registry, exception
hierarchy. Independent of every other layer — `dsp` may be reused
verbatim in any audio context that wants a chainable PCM processor.
Audio-specific stages (NoiseGate / Limiter / RmsNormalizer) live in
`audio/dsp.py` on top of this primitive layer.

## Public API

`dsp/__init__.py` exports:

- `DSPNode`, `DSPPipeline` — the chain abstraction (`dsp/pipeline.py`).
- `TapHandle`, `TapRegistry` — multi-consumer PCM analysis bus
  (`dsp/tap_registry.py`).
- `DSPBackendUnavailable`, `DSPConfigError` — error hierarchy
  (`dsp/exceptions.py`).

`dsp/nodes/` ships the built-in node implementations (`PassthroughNode`,
`GainNode`, `NRScipyNode`, …); `dsp/resample.py` exposes the
inter-node resampler used to bridge nodes that disagree on sample rate.

The `icom-lan-pro` consumer reaches `dsp.pipeline`, `dsp.nodes.base`,
and `dsp.exceptions` directly via canonical paths — these are part of
the Tier 2 stable contract (plan §9).

## Allowed dependencies

None internally. `dsp` consults `core` only via `core._optional_deps`
helpers (numpy/scipy), and even those are import-time-conditional.
The `independence-low` contract in `.importlinter` enforces that
`dsp` ⊥ `commands` ⊥ `scope`.

## Forbidden patterns

- `from icom_lan.audio` / `from icom_lan.runtime` — `audio` depends on
  `dsp`, not the reverse. Audio-policy nodes live in `audio/dsp.py`.
- Module-level numpy/scipy imports. Gate via `_require_numpy()` /
  `_require_scipy()` at construction time; `DSPBackendUnavailable` is
  the canonical failure mode.
- Stateful global registries. `TapRegistry` instances are explicitly
  passed; nodes are explicitly constructed.

## Common operations

- **Add a node** → subclass `DSPNode` under `dsp/nodes/`, implement
  `process(frame)` and the rate/format invariants; export through
  `dsp/nodes/__init__.py` if it is part of the public node catalog.
- **Add a sample-rate path** → extend `dsp/resample.py`; verify
  zero-copy fast paths and the `numpy`-availability gate.
- **Tap a pipeline for telemetry** → `pipeline.add_tap(TapHandle(...))`;
  consumers receive PCM frames via the registry, not by patching.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2 ("`dsp` is
  independent of the rest"), §3 (matrix), §9 (icom-lan-pro contract).
- `audio/dsp.py` — audio-policy stages built on top.
- `tests/test_dsp_*.py` — node + pipeline + tap coverage.
- `.importlinter` `independence-low` contract.
