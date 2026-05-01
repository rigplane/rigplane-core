# icom-lan 1.0.0

**Release date:** 2026-05-01
**Theme:** Public API stability + multi-radio capability architecture

This is the 1.0 cut. The library has been at production quality on IC-7610
for some time; what changes today is the contract — the Tier 1 surface in
`docs/api/public-api-surface.md` (the `Radio` protocol, capability protocols,
`create_radio`, the `local-extensions/` host API) is now under SemVer, and
multi-radio support is no longer a special case threaded through the runtime
but a first-class capability-driven architecture.

## Highlights for users

- **rigctld dual-RX is fully working again.** WSJT-X, fldigi, and JS8Call
  golden-replay tests pass against IC-7610 with `chk_vfo='1'` re-enabled
  and full per-VFO routing for `f`/`m`/`t`/`s`/`S`/`j`/`l`/`L`/`u`/`U`
  (#1319 → #1342, #1343, #1344, #1345, #1346). The rollback in #1319 was
  the immediate fix; Variant A landed the parser support and per-VFO
  command routing that the original v0.17.0 advertising required.
- **Yaesu CAT (FTX-1) backend is stable.** A second radio family now
  produces the same Web UI and Python API as IC-7610 — same skins, same
  meters dock, same `rigctld` bridge. `YaesuCatRadio.set_rf_power` filled
  the last `PowerControlCapable` gap (#1331).
- **Web UI v1 is gone.** `RadioLayoutV2` + `components-v2/` is the only
  shell. The `?ui=v1` URL fallback and the `ui-version` store are removed
  (#1216, #1217, #1218, #1220, #1227). Default-since-v0.15.1 means almost
  no one will notice; the maintenance benefit is large.
- **Web UI runs on a tighter event loop.** Two blocking file-I/O paths
  (band-plan config writes, EiBi cache loads) now go through
  `asyncio.to_thread` (#1332). The `runtime_capabilities` USB-audio
  fallback also recognises the `UsbAudioCapable` protocol cleanly (#1356).
- **Sync API regression fixed.** `get_alc_meter` is now exposed on
  `sync.IcomRadio` (#1228); the v0.19 `get_alc` removal had left the sync
  wrapper inconsistent with the async API.

## For integrators / Pro builders

- **Capability Protocols are the stable contract.** Implement
  `AudioCapable`, `ScopeCapable`, `MetersCapable`, `LevelsCapable`,
  `StatePollable`, `RigctldRoutable`, `UsbAudioCapable`, … — and your
  custom backend works with the runtime, Web UI, and rigctld layers
  without any of those layers knowing about your radio. Epic #1322
  finished migrating the last backend-id discriminators (web startup,
  power-control unit, rigctld routing, USB audio detection) to
  `isinstance`-based capability dispatch.
- **`Radio` protocol = the open-core / Pro boundary.** The Pro layer in
  development consumes icom-lan through the protocol surface plus
  `frontend/src/lib/local-extensions/`. Both are documented in
  `docs/api/public-api-surface.md` as Tier 1 with full breakage policy
  (#1273, #1277, #1275). The principles behind that boundary —
  open-core erosion, headless invariants, telemetry rules — are codified
  in `docs/architecture/open-core-policy.md` (#1276).
- **Layered architecture with import-linter enforcement.** `src/icom_lan/`
  is now 11 layered packages (`core/` → `commands/`/`scope/`/`dsp/` →
  `profiles/`/`audio/` → `runtime/` → `backends/` → `web/`+`rigctld/` →
  `cli/`) with one layered contract and three sibling-independence
  contracts gated in CI. Per-layer charters live in
  `src/icom_lan/<layer>/LAYER.md`. See `ARCHITECTURE.md`.

## Under the hood

- **5,600+ unit tests** (5,609 collected on `main`) with import-linter,
  mypy, and ruff all clean.
- **Public API surface regression test** (#1273). Every Tier 1 symbol
  documented in `docs/api/public-api-surface.md` is now locked by an
  automated import test that also verifies Tier 1 imports do not pull
  Tier 3 modules into `sys.modules`.
- **Decomposed god-objects.** `radio.py` (#1258, #1259, #1260),
  `_civ_rx.py` table-driven dispatch verified by 72 golden fixtures
  (#1256, #1257, #1266), `WebServer` start/stop and routing
  (#1261, #1262), `transport._handle_packet` (#1239), and the read-only
  control dispatch (#1263).
- **Frontend panel boundary locked at lint time.** The 18-panel migration
  off direct `$lib/stores/*` imports is complete (#1244–#1248) and ESLint
  enforces the boundary going forward (#1241).
- **CI bug fixed: subdirectory tests now actually run** (#1352). The
  `tests/test_*.py` collection glob silently skipped suites in nested
  directories. The fix exposed (and trivially passed) several test
  suites that had been quietly inert.

## Known issues

- **#942 — Audio transport broken-pipe storm after radio power-cycle.**
  Pre-existing (predates v0.19.0). Workaround: restart `icom-lan`. A
  watchdog with auto-reconnect for the audio path is tracked for 1.1.

## Migration

**No public API breaking changes.** Every `from icom_lan.<old_path> import …`
keeps working via `sys.modules`-aliased re-export shims introduced during
the modularization (#1283 series). New code SHOULD use canonical layer
paths — `icom_lan.runtime.radio`, `icom_lan.backends.discovery`,
`icom_lan.commands.commander`, etc. — see `ARCHITECTURE.md`.

The deprecation closures already announced in v0.19 land in 1.0 on
schedule:

- `IcomRadio.set_split_mode` → use `set_split` (#1205).
- `IcomRadio.get_alc` → use `get_alc_meter` (#1207).
- `commands.levels.{get_power,set_power,get_sql,set_sql}` aliases
  → use `get_rf_power` / `set_rf_power` / `get_squelch` / `set_squelch`
  (#1208).
- `IcomRadio.set_vfo("A"/"B"/"MAIN"/"SUB")` legacy overload + `select_vfo`
  → use `ReceiverBankCapable.select_receiver` and
  `VfoSlotCapable.set_vfo_slot` (#1206).
- `meter_cal._TABLES` / `meter_cal.calibrate()` → unreachable since v0.19,
  removed (#1209). `MeterType` and `interpolate_swr` remain exported.

## Install

```bash
pip install icom-lan==1.0.0
# or upgrade:
pip install --upgrade icom-lan
```

## Acknowledgements

- The [wfview](https://wfview.org/) project for their reverse engineering
  of the Icom LAN protocol — the foundation any independent implementation
  builds on.
- The amateur radio community for testing, profile contributions, and
  field reports.
- Leon Toorenburg (WW0R) for the high-latency / WireGuard tuning report
  that hardened the audio defaults.

## Full commit log

See [`v0.19.0...v1.0.0`](https://github.com/morozsm/icom-lan/compare/v0.19.0...v1.0.0)
on GitHub. Detailed changelog: [`CHANGELOG.md`](CHANGELOG.md#100--2026-05-01).
