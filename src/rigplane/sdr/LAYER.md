# `sdr` layer

## Charter

SDR panadapter contracts (MOR-3151): the backend-neutral `IqSource`
protocol for SoapySDR receivers (RTL-SDR first), the `IqBlock` /
`SdrConfig` carriers, the `IqScopeSink` surface the controller drives
and `IqFftScope` implements, and the `FakeIqSource` test double. SoapySDR
is imported only inside `soapy_source.py`, lazily. `scope_source.py`
composes acquisition and TX policy for the shared scope hub;
`runtime.py` retains source construction and CLI configuration helpers.

## Public API

- `IqBlock` — frozen dataclass: `samples` (1-D complex64),
  `center_freq_hz`, `sample_rate_hz`, monotonic `timestamp_s`,
  `overflow`.
- `SdrConfig` — frozen dataclass: `device_args`, `sample_rate_hz`,
  `gain_db` (`None` = AGC), `ppm`, `freq_offset_hz` (`0` = antenna
  tap), `invert_spectrum`, `span_hz` (`None` = full rate),
  `extra_settings` (read-only); `from_mapping()` names the bad field.
- `IqSourceFacts` — frozen dataclass declaring `independent` or `slaved`
  tuning, the slaved window's `center_offset_hz`, and optional positive
  `usable_bandwidth_hz` (`None` = full sample rate).
- `SoapySourceSpec` / `UdpIqSourceSpec` / `IqSourceSpec` — frozen, `kind`-tagged source selection; UDP carries radio-LAN wire facts and the alias is the union extension point.
- `xiegu_udp_source.XieguUdpIqSource` — slaved UDP IQ in 1920-sample blocks; sends only a settled, in-range centre minus offset after silence; no stop exists.
- `IqDisplaySettings` — Protocol for the controller's read-only
  `span_hz` and `freq_offset_hz` settings; `SdrConfig` satisfies it.
- `IqSource` — runtime_checkable Protocol: `open`/`close`/`is_open`,
  `facts`, tuning/rate/gain setters, `frequency_range_hz`, single-slot
  `on_block` (callback runs on the reader thread). A slaved source records
  the controller-computed physical centre; centre `0` means unknown, so it
  drops samples and sends no radio tune command until a real centre arrives.
- `IqScopeSink` — runtime_checkable Protocol: enqueue-only `feed`,
  display-only `set_view_center`, `set_span`, `set_tx_active`,
  single-slot `on_frame` emitting `scope.ScopeFrame`.
- `FakeIqSource` — seeded, phase-continuous generator; tones
  `[(offset_hz, dbfs)]`, noise floor, block size; `pump(n_blocks)` or
  background reader thread.
- `IqFftScope` — IQ blocks → `ScopeFrame` at `fps` (MOR-3154).
- `SdrScopeController` — source-facts-driven VFO tracking + TX freeze:
  independent sources keep debounced view-shift/retune decisions, while
  slaved sources immediately follow `radio RF + center_offset_hz`.
- `IqFftSource` — demand-owned provider/controller/FFT composition. The
  controller's acquisition and TX decisions are retained, while each hub
  subscription projects its own view from the full native capture.
- `sdr.runtime` (not re-exported) — `default_source_factory`,
  `register_source_kind` / `build_source` for the source-kind registry,
  `resolve_sdr_config` and the remote-only GPL guard (MOR-3157).

## Allowed dependencies

`core`, `scope` (`ScopeFrame`), numpy — lazy via
`core._optional_deps._require_numpy` (`fake.py`), never module-level.

## Forbidden patterns

- `web` / `runtime` / `audio` imports (mid-tier sibling,
  `.importlinter` `independence-mid`, either direction).
- Direct `SoapySDR` imports outside `sdr/soapy_source.py`.
- Redefining these contract types in later SDR issues.
