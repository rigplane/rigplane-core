# `sdr` layer

## Charter

SDR panadapter contracts (MOR-3151): the backend-neutral `IqSource`
protocol for SoapySDR receivers (RTL-SDR first), the `IqBlock` /
`SdrConfig` data carriers, the `IqScopeSink` surface the controller
drives and `IqFftScope` implements, and the deterministic `FakeIqSource`
test double. This package is the contract every other core SDR issue
codes against, so it stays small: no FFT, no SoapySDR import, no server
wiring — those arrive as `IqFftScope` (`sdr/fft_scope.py`, a later
issue) and the SoapySDR adapter (`sdr/soapy_source.py`, a later issue),
which consume these types rather than redefine them.

## Public API

`sdr/__init__.py` exports:

- `IqBlock` — frozen dataclass: `samples` (1-D complex64),
  `center_freq_hz`, `sample_rate_hz`, `timestamp_s` (monotonic),
  `overflow` (samples were dropped before this block).
- `SdrConfig` — frozen dataclass: `device_args` (SoapySDR kwargs
  string), `sample_rate_hz`, `gain_db` (`None` = AGC), `ppm`,
  `freq_offset_hz` (IF-tap offset; 0 for an antenna tap),
  `invert_spectrum`, `span_hz` (`None` = full sample rate),
  `extra_settings`; `from_mapping()` validates and raises `ValueError`
  naming the offending field.
- `IqSource` — runtime_checkable Protocol: `open`/`close`/`is_open`,
  tuning/rate/gain setters, `frequency_range_hz`, single-slot
  `on_block` (callback runs on the source's reader thread).
- `IqScopeSink` — runtime_checkable Protocol: enqueue-only `feed`
  (called from the source's reader thread), `set_view_center`
  (display center inside the current IQ window, no hardware retune),
  `set_span`, `set_tx_active`, single-slot `on_frame` emitting
  `scope.ScopeFrame`.
- `FakeIqSource` — seeded, phase-continuous generator with
  configurable tones `[(offset_hz, dbfs)]`, noise floor (dBFS) and
  block size; `pump(n_blocks)` for synchronous tests, optional
  background reader thread for integration tests.

## Allowed dependencies

`core`, `scope`, numpy (plan §3 matrix row `sdr`). `scope` provides
`ScopeFrame`, which `IqScopeSink.on_frame` emits — the SDR panorama is
a third scope source next to the hardware scope and `audio.fft_scope`.
numpy is lazy-imported through `core._optional_deps._require_numpy`
(see `fake.py`), never at module level.

## Forbidden patterns

- `from rigplane.web` / `from rigplane.runtime` / `from rigplane.audio` —
  `sdr` is a mid-tier sibling of `profiles` and `audio`
  (`independence-mid` in `.importlinter`); the inverse direction is
  forbidden too.
- Direct `SoapySDR` imports outside `sdr/soapy_source.py` — the adapter
  is the single place the driver may appear, so every consumer stays
  mockable through the `IqSource` protocol.
- Module-level `import numpy` — keep `import rigplane.sdr` light; gate
  through `_require_numpy()` at call time as `fake.py` does.
- Redefining the contract types in a later SDR issue instead of
  importing them from here.

## Common operations

- **Add an IQ source backend** → implement `IqSource` (the SoapySDR
  adapter goes in `sdr/soapy_source.py`); conform with
  `isinstance(source, IqSource)` and cover it against `FakeIqSource`'s
  tests under `tests/sdr/`.
- **Build the FFT scope** → implement `IqScopeSink` in
  `sdr/fft_scope.py`, consuming `IqBlock` and emitting `ScopeFrame`
  (reference: `audio/fft_scope.py`).
- **Extend the config** → add the field to `SdrConfig`, extend
  `from_mapping` validation and the cases in `tests/sdr/test_types.py`.

## See also

- `sdr/protocol.py` — the stable `IqSource`/`IqScopeSink` definitions.
- `sdr/fake.py` — `FakeIqSource`, the test double for every consumer.
- `audio/fft_scope.py` — the reference scope-source pattern
  (`AudioFftScope`).
- `scope/LAYER.md` — `ScopeFrame`, the shared frame type.
- `tests/sdr/` — contract tests for types and the fake.
- `.importlinter` `independence-mid` contract — sibling enforcement.
