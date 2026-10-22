# `sdr` layer

## Charter

SDR panadapter contracts (MOR-3151): the backend-neutral `IqSource`
protocol for SoapySDR receivers (RTL-SDR first), the `IqBlock` /
`SdrConfig` carriers, the `IqScopeSink` surface the controller drives
and `IqFftScope` implements, and the `FakeIqSource` test double. SoapySDR
is imported only inside `soapy_source.py`, lazily; the server-side
runtime (`runtime.py`, MOR-3157) is consumed by `web/server.py`.

## Public API

- `IqBlock` — frozen dataclass: `samples` (1-D complex64),
  `center_freq_hz`, `sample_rate_hz`, monotonic `timestamp_s`,
  `overflow`.
- `SdrConfig` — frozen dataclass: `device_args`, `sample_rate_hz`,
  `gain_db` (`None` = AGC), `ppm`, `freq_offset_hz` (`0` = antenna
  tap), `invert_spectrum`, `span_hz` (`None` = full rate),
  `extra_settings` (read-only); `from_mapping()` names the bad field.
- `IqSource` — runtime_checkable Protocol: `open`/`close`/`is_open`,
  tuning/rate/gain setters, `frequency_range_hz`, single-slot
  `on_block` (callback runs on the reader thread).
- `IqScopeSink` — runtime_checkable Protocol: enqueue-only `feed`,
  display-only `set_view_center`, `set_span`, `set_tx_active`,
  single-slot `on_frame` emitting `scope.ScopeFrame`.
- `FakeIqSource` — seeded, phase-continuous generator; tones
  `[(offset_hz, dbfs)]`, noise floor, block size; `pump(n_blocks)` or
  background reader thread.
- `IqFftScope` — IQ blocks → `ScopeFrame` at `fps` (MOR-3154).
- `SdrScopeController` — VFO view-shift/retune + TX freeze (MOR-3156).
- `sdr.runtime` (not re-exported) — `SdrScopeRuntime` pipeline, `resolve_sdr_config`, remote-only GPL guard (MOR-3157).

## Allowed dependencies

`core`, `scope` (`ScopeFrame`), numpy — lazy via
`core._optional_deps._require_numpy` (`fake.py`), never module-level.

## Forbidden patterns

- `web` / `runtime` / `audio` imports (mid-tier sibling,
  `.importlinter` `independence-mid`, either direction).
- Direct `SoapySDR` imports outside `sdr/soapy_source.py`.
- Redefining these contract types in later SDR issues.
