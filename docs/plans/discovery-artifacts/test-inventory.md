# Test Inventory — `tests/` (this worktree)

Phase 1 / Discovery artifact (Agent B). Maps in-tree tests to the source modules
they cover, identifies orphan tests (multi-target / no clear single subject),
flags untested source modules, and catalogues conftest cross-layer fixtures.

Method: ripgrep / static text scan of `from icom_lan(.X)? import …` and
`import icom_lan(.X)?` in `tests/`. A test is considered to "cover" a source
module when it imports that module's exact dotted path. Coverage of a module
through a re-export at a parent package is not counted (we want signal about
where the migration touch points are, not test-runtime coverage).

Totals:
- Test files importing `icom_lan.*`: **177**
- Source `.py` files under `src/icom_lan/` (excluding `__init__.py`,
  `__main__.py`, `py.typed`): **135**
- Source modules with at least one direct test importer: **91**
- Source modules with **zero** direct test importers: **44**

---

## 1. Source-module → tests map

Per source module, test files that directly import it (exact dotted path).
Modules under packages (e.g. `icom_lan.audio.backend`) are listed separately
from their parent packages; importing the parent package does not count toward
testing a child module.

### Top-level modules / loose files

| source module | tests |
| --- | --- |
| `icom_lan.audio_analyzer` | `test_audio_analyzer.py` |
| `icom_lan.audio_bridge` | `test_audio_bridge.py`, `test_cli_coverage.py`, `test_serial_backend_smoke.py` |
| `icom_lan.audio_bus` | `integration/test_audio_routing.py`, `test_audio_bridge.py`, `test_audio_bus.py`, `test_ftx1_audio.py`, `test_handlers_coverage.py`, `test_web_server.py` |
| `icom_lan.audio_fft_scope` | `test_audio_fft_scope.py` |
| `icom_lan.auth` | `integration/full_handshake.py`, `integration/probe_login.py`, `test_auth.py` |
| `icom_lan.capabilities` | `test_rig_schema.py`, `test_web_ptt_readonly.py` |
| `icom_lan.civ` | `test_civ.py`, `test_civ_rx_coverage.py` |
| `icom_lan.cli` | `test_cli.py`, `test_cli_async.py`, `test_cli_coverage.py`, `test_cli_e2e.py`, `test_cli_model.py`, `test_dx_cluster.py`, `test_yaesu_cli_factory.py` |
| `icom_lan.command_map` | `test_command_map_integration.py`, `test_commands.py`, `test_rig_loader.py` |
| `icom_lan.command_spec` | `test_command_spec.py`, `test_ftx1_radio.py` |
| `icom_lan.commander` | `test_civ_rx_coverage.py`, `test_commander.py`, `test_radio_coverage.py`, `test_radio_extended.py` |
| `icom_lan.cw_auto_tuner` | `test_cw_auto_tuner.py` |
| `icom_lan.discovery` | `test_discovery.py` |
| `icom_lan.env_config` | `test_env_config.py` |
| `icom_lan.exceptions` | 35 test files (most-shared error-path module; see `external-usage.md` for full list) |
| `icom_lan.ic705` | `test_ic705.py`, `test_profiles_runtime.py` |
| `icom_lan.meter_cal` | `test_swr_calibration.py` |
| `icom_lan.profiles` | 17 test files (key shared fixture; e.g. `test_dsp_filter_family.py`, `test_handlers_*.py`, `test_profiles_routing.py`, `test_radio_poller_coverage.py`, `test_rig_loader.py`, …) |
| `icom_lan.profiles_runtime` | `test_profiles_runtime.py` |
| `icom_lan.protocol` | `test_auth.py`, `test_protocol.py` |
| `icom_lan.proxy` | `test_proxy.py`, `test_proxy_coverage.py` |
| `icom_lan.radio` | 38 test files (the core surface; covers conftest + most integration + most unit tests in the radio family) |
| `icom_lan.radio_protocol` | 20 test files (Tier-1 contract surface; see e.g. `test_radio_protocol.py`, `test_icom_receiver_tier.py`, `test_web_server.py`) |
| `icom_lan.radio_state` | 16 test files (state-cache consumers across `test_civ_rx_*`, `test_radio_state.py`, `test_rigctld_handler.py`, `test_web_server.py`, …) |
| `icom_lan.radios` | `test_radios.py` |
| `icom_lan.rig_loader` | 11 test files |
| `icom_lan.scope` | `test_audio_fft_scope.py`, `test_civ_rx_coverage.py`, `test_cli_coverage.py`, `test_handlers_coverage.py`, `test_radio_coverage.py`, `test_scope.py`, `test_scope_render.py`, `test_scope_stress.py`, `test_web_server.py` |
| `icom_lan.scope_render` | `test_scope_render.py`, `test_scope_render_coverage.py` |
| `icom_lan.sync` | `test_sync.py`, `test_sync_coverage.py` |
| `icom_lan.transport` | `test_radio_connect.py`, `test_radio_coverage.py`, `test_transport.py` |
| `icom_lan.types` | 59 test files (the most-shared type module; touched by every command/protocol-level test) |
| `icom_lan.usb_audio_resolve` | `test_usb_audio_resolve.py` |

### Private (`_…`) loose files

| source module | tests |
| --- | --- |
| `icom_lan._audio_codecs` | `test_audio_codecs.py`, `test_web_audio_streaming_profile.py` |
| `icom_lan._audio_transcoder` | `test_audio_transcoder.py`, `test_audio_transcoder_coverage.py` |
| `icom_lan._bounded_queue` | `test_bounded_queue.py` |
| `icom_lan._bridge_metrics` | `test_audio_bridge.py` |
| `icom_lan._bridge_state` | `test_audio_bridge.py` |
| `icom_lan._civ_rx` | `test_bsr_band_switching.py`, `test_civ_rx_coverage.py`, `test_civ_rx_mixin_host.py`, `test_tx_band_edge.py` |
| `icom_lan._connection_state` | `test_docs_runtime_sync.py`, `test_lifecycle_diagnostics.py`, `test_radio_coverage.py`, `test_reconnect.py`, `test_sync_coverage.py` |
| `icom_lan._poller_types` | `test_yaesu_cat_poller.py` |
| `icom_lan._shared_state_runtime` | `test_shared_state_runtime.py` |
| `icom_lan._state_queries` | `test_state_queries.py` |

### `icom_lan.audio.*`

| source module | tests |
| --- | --- |
| `icom_lan.audio.backend` | `test_audio_backend.py`, `test_audio_bridge.py`, `test_audio_resample.py`, `test_usb_audio_stub.py` |
| `icom_lan.audio.config` | `test_audio_config.py` |
| `icom_lan.audio.dsp` | `test_audio_dsp.py` |
| `icom_lan.audio.lan_stream` | `test_audio_bridge.py` |
| `icom_lan.audio.resample` | `test_audio_resample.py` |
| `icom_lan.audio.usb_driver` | `test_ftx1_audio.py`, `test_macos_audio_uid.py` |

### `icom_lan.backends.*`

| source module | tests |
| --- | --- |
| `icom_lan.backends.config` | `test_backend_factory.py`, `test_cli.py`, `test_cli_coverage.py`, `test_ic705_backend.py`, `test_ic7300_backend.py`, `test_ic9700_backend.py`, `test_yaesu_cli_factory.py` |
| `icom_lan.backends.factory` | `test_ic705_backend.py`, `test_ic7300_backend.py`, `test_ic9700_backend.py`, `test_yaesu_cli_factory.py` |
| `icom_lan.backends.ic705.serial` | `test_ic705_backend.py`, `test_supports_command.py` |
| `icom_lan.backends.ic7300.serial` | `test_ic7300_backend.py`, `test_ic9700_backend.py`, `test_supports_command.py` |
| `icom_lan.backends.ic9700.serial` | `test_ic9700_backend.py`, `test_supports_command.py` |
| `icom_lan.backends.icom7610.drivers.contracts` | `test_backend_factory.py` |
| `icom_lan.backends.icom7610.drivers.serial_civ_link` | `test_serial_civ_link.py` |
| `icom_lan.backends.icom7610.drivers.serial_stub` | `integration/test_rigctld_wsjtx.py`, `test_backend_contract_matrix.py`, `test_ic705_backend.py`, `test_lifecycle_diagnostics.py`, `test_rigctld_server.py`, `test_serial_backend_smoke.py`, `test_serial_stub_framing.py`, `test_web_server.py` |
| `icom_lan.backends.icom7610.drivers.usb_audio` | `test_usb_audio_stub.py` |
| `icom_lan.backends.icom7610.serial` | `test_ic705_backend.py`, `test_supports_command.py` |
| `icom_lan.backends.yaesu_cat.parser` | `test_ftx1_radio.py`, `test_yaesu_cat_parser.py` |
| `icom_lan.backends.yaesu_cat.poller` | `test_yaesu_cat_poller.py` |
| `icom_lan.backends.yaesu_cat.radio` | `test_backend_factory.py`, `test_dsp_filter_family.py`, `test_ftx1_audio.py`, `test_ftx1_radio.py`, `test_radio_protocol.py`, `test_rigctld_handler.py`, `test_supports_command.py`, `test_yaesu_audio.py`, `test_yaesu_cli_factory.py` |
| `icom_lan.backends.yaesu_cat.transport` | `test_discovery.py`, `test_ftx1_radio.py` |

### `icom_lan.commands.*`

| source module | tests |
| --- | --- |
| `icom_lan.commands._codec` | `test_dsp_filter_family.py`, `test_rig_ic7300.py` |
| `icom_lan.commands._frame` | `test_tx_band_edge.py` |
| `icom_lan.commands.tx_band` | `test_tx_band_edge.py` |

(All other `icom_lan.commands.<name>` submodules — `antenna`, `config`, `cw`,
`dsp`, `freq`, `levels`, `memory`, `meters`, `mode`, `power`, `ptt`, `scope`,
`speech`, `system`, `tone`, `vfo`, plus `_builders` — have no direct test
importer; they are exercised through `icom_lan.commands` package imports. See §3
"Untested modules".)

### `icom_lan.dsp.*`

| source module | tests |
| --- | --- |
| `icom_lan.dsp.exceptions` | `test_dsp_nr_scipy.py` |
| `icom_lan.dsp.nodes.nr_scipy` | `test_dsp_nr_scipy.py` |
| `icom_lan.dsp.pipeline` | `test_dsp_nodes.py` |
| `icom_lan.dsp.resample` | `test_dsp_nr_scipy.py` |
| `icom_lan.dsp.tap_registry` | `test_cw_auto_tune_wiring.py`, `test_tap_registry.py` |

### `icom_lan.rigctld.*`

| source module | tests |
| --- | --- |
| `icom_lan.rigctld.audit` | `test_audit.py` |
| `icom_lan.rigctld.circuit_breaker` | `test_circuit_breaker.py`, `test_poller.py`, `test_poller_coverage.py`, `test_rigctld_server_coverage.py` |
| `icom_lan.rigctld.contract` | 14 test files (used as the public rigctld DTO surface) |
| `icom_lan.rigctld.handler` | `test_data_mode.py`, `test_dsp_filter_family.py`, `test_golden_protocol.py`, `test_rigctld_handler.py`, `test_server_wire.py` |
| `icom_lan.rigctld.poller` | `test_data_mode.py`, `test_poller.py`, `test_poller_coverage.py` |
| `icom_lan.rigctld.protocol` | `test_golden_protocol.py`, `test_rigctld_protocol.py` |
| `icom_lan.rigctld.server` | `integration/test_rigctld_wsjtx.py`, `test_lifecycle_diagnostics.py`, `test_rate_limiter.py`, `test_rigctld_server.py`, `test_rigctld_server_coverage.py`, `test_serial_backend_smoke.py`, `test_server_wire.py` |
| `icom_lan.rigctld.state_cache` | 17 test files (the cache is a high-traffic shared state across web/rigctld/runtime tests) |
| `icom_lan.rigctld.utils` | `test_rigctld_utils.py` |

### `icom_lan.web.*`

| source module | tests |
| --- | --- |
| `icom_lan.web._delta_encoder` | `test_delta_encoder.py` |
| `icom_lan.web.discovery` | `test_discovery_responder.py` |
| `icom_lan.web.dx_cluster` | `test_dx_cluster.py` |
| `icom_lan.web.handlers.audio` | `test_tap_registry.py` |
| `icom_lan.web.handlers.control` | `test_cw_auto_tune_wiring.py`, `test_web_ptt_readonly.py` |
| `icom_lan.web.protocol` | `test_audio_fft_scope.py`, `test_docs_runtime_sync.py`, `test_handlers_coverage.py`, `test_serial_backend_smoke.py`, `test_web_audio_streaming_profile.py`, `test_web_server.py` |
| `icom_lan.web.radio_poller` | 14 test files |
| `icom_lan.web.rtc` | `test_webrtc_signaling.py` |
| `icom_lan.web.runtime_helpers` | `test_web_runtime_helpers.py`, `test_web_server_coverage.py` |
| `icom_lan.web.server` | 13 test files |
| `icom_lan.web.tls` | `test_tls.py` |
| `icom_lan.web.websocket` | `integration/test_audio_routing.py`, `test_dx_cluster.py`, `test_env_config.py`, `test_handlers_coverage.py`, `test_web_runtime_helpers.py`, `test_web_server.py`, `test_websocket_coverage.py` |

---

## 2. Orphan tests (mixed / no clear single target)

Definition: test files whose `icom_lan.*` imports span **4 or more distinct
top-level subpackages** (counting `icom_lan.<seg1>.…` by `<seg1>`; bare
`from icom_lan import …` is ignored for counting). These tests will stress
re-export shims more than module-focused tests; they are also more likely to
need attention if multiple layers move in the same step.

Count: **36 orphan files** (out of 177).

| test file | distinct top-level subpkgs | imported paths |
| --- | --- | --- |
| `tests/integration/test_audio_routing.py` | audio_bus, radio_protocol, types, web | `audio_bus`, `radio_protocol`, `types`, `web.handlers`, `web.websocket` |
| `tests/integration/test_operator_toggles_integration.py` | commands, exceptions, radio, types | `commands`, `exceptions`, `radio`, `types` |
| `tests/test_audio_bridge.py` | _bridge_metrics, _bridge_state, audio, audio_bridge, audio_bus | `_bridge_metrics`, `_bridge_state`, `audio.backend`, `audio.lan_stream`, `audio_bridge`, `audio_bus` |
| `tests/test_audio_fullduplex.py` | audio, exceptions, radio, types | `audio`, `exceptions`, `radio`, `types` |
| `tests/test_audio_transcoder.py` | _audio_transcoder, audio, exceptions, radio | `_audio_transcoder`, `audio`, `exceptions`, `radio` |
| `tests/test_backend_contract_matrix.py` | backends, commands, radio, types | `backends.icom7610.drivers.serial_stub`, `commands`, `radio`, `types` |
| `tests/test_bsr_band_switching.py` | _civ_rx, profiles, radio_state, rigctld, web | `_civ_rx`, `profiles`, `radio_state`, `rigctld.state_cache`, `web.radio_poller` |
| `tests/test_civ_rx_coverage.py` | _civ_rx, civ, commander, commands, exceptions, radio, radio_state, scope, types | (9 subpkgs — broadest coverage test) |
| `tests/test_civ_rx_dispatch_golden.py` | commands, radio, radio_state, types | golden vector test |
| `tests/test_cli_coverage.py` | audio_bridge, backends, cli, radio_protocol, scope | CLI smoke crosses runtime layers |
| `tests/test_data_mode.py` | commands, exceptions, rigctld, types | `commands`, `exceptions`, `rigctld.contract`, `rigctld.handler`, `rigctld.poller`, `rigctld.state_cache`, `types` |
| `tests/test_dsp_filter_family.py` | backends, commands, exceptions, profiles, radio, radio_protocol, rig_loader, rigctld, types | (9 subpkgs — feature-level e2e for DSP filters) |
| `tests/test_ftx1_audio.py` | audio, audio_bus, backends, exceptions | yaesu audio path |
| `tests/test_ftx1_radio.py` | backends, command_spec, exceptions, profiles, radio_protocol, radio_state, rig_loader, types | yaesu vendor e2e |
| `tests/test_golden_protocol.py` | exceptions, radio_protocol, rigctld, types | `radio_protocol`, `rigctld.contract/handler/protocol/state_cache`, `types` |
| `tests/test_handlers_coverage.py` | audio_bus, profiles, radio_protocol, scope, types, web | broad handler coverage |
| `tests/test_icom7610_serial_radio.py` | backends, commands, exceptions, types | serial backend e2e |
| `tests/test_lifecycle_diagnostics.py` | _connection_state, backends, radio, rigctld, web | lifecycle integration |
| `tests/test_memory_commands.py` | commands, radio, radio_protocol, rig_loader, types, web | memory feature surface |
| `tests/test_profiles_routing.py` | exceptions, profiles, rigctld, web | profiles → web/rigctld routing |
| `tests/test_radio.py` | commands, exceptions, radio, radio_protocol, types | central Radio test |
| `tests/test_radio_connect.py` | exceptions, radio, transport, types | connection lifecycle |
| `tests/test_radio_coverage.py` | _connection_state, commander, commands, exceptions, radio, scope, transport, types | broadest single-radio coverage; 13× `_connection_state` reach-throughs |
| `tests/test_radio_extended.py` | commander, commands, exceptions, radio, types | radio extension cases |
| `tests/test_radio_poller_coverage.py` | exceptions, profiles, radio_protocol, radio_state, rigctld, web | poller coverage |
| `tests/test_radio_protocol.py` | backends, radio, radio_protocol, radio_state | protocol contract + impls |
| `tests/test_rigctld_handler.py` | backends, exceptions, radio_state, rigctld, types | rigctld handler coverage |
| `tests/test_scope.py` | commands, radio, scope, types | scope feature |
| `tests/test_scope_stress.py` | commands, radio, scope, types | scope stress |
| `tests/test_selected_freq_mode.py` | commands, profiles, radio, radio_state, rigctld, types, web | selected-receiver feature |
| `tests/test_serial_backend_smoke.py` | audio_bridge, backends, rigctld, web | serial backend × server smoke |
| `tests/test_tx_band_edge.py` | _civ_rx, commands, radio_state, types | tx band-edge feature |
| `tests/test_vfo_dual_watch.py` | commands, exceptions, radio, radio_protocol, types | dual-watch feature |
| `tests/test_vox_tone_state.py` | commands, profiles, radio, radio_protocol, radio_state, rigctld, types, web | (8 subpkgs) state propagation feature |
| `tests/test_web_server.py` | audio_bus, backends, profiles, radio_protocol, radio_state, rigctld, scope, types, web | (9 subpkgs — biggest cross-cutting test) |
| `tests/test_yaesu_audio.py` | audio, backends, exceptions, radio_protocol, types | yaesu audio path |

These are not bugs — they are integration-style tests that legitimately span
layers. Listed here because they are the most-affected tests during any
multi-layer migration step.

---

## 3. Untested modules (no direct test importer)

44 source modules under `src/icom_lan/` (excluding `__init__.py`/`__main__.py`)
have no test file that imports them directly. This is a heuristic — many of
these are imported transitively from a parent package or runtime entrypoint and
are exercised at runtime by tests that import the parent. Treat the list as a
"these modules will not show up in test grep" signal, not as a "no coverage"
verdict.

| module | likely-tested-via |
| --- | --- |
| `icom_lan._audio_recovery` | `icom_lan.audio` runtime; `tests/test_audio_recovery.py` exists but imports `icom_lan.audio` and `icom_lan.radio`, not `_audio_recovery` |
| `icom_lan._audio_runtime_mixin` | runtime mixin, exercised through `icom_lan.radio` |
| `icom_lan._control_phase` | runtime FSM, via `icom_lan.radio` |
| `icom_lan._dual_rx_runtime` | via `icom_lan.radio` |
| `icom_lan._optional_deps` | optional-deps helper; called from `cli`, factory paths |
| `icom_lan._queue_pressure` | bounded-queue helper; via `icom_lan.audio`/`icom_lan.web.radio_poller` |
| `icom_lan._runtime_protocols` | Protocol types; imported by `radio` and `radio_protocol` but not by tests |
| `icom_lan._scope_runtime` | scope runtime; via `icom_lan.scope` |
| `icom_lan._state_cache` | local state-cache helper; consumed by `rigctld.state_cache` |
| `icom_lan.audio._macos_uid` | platform helper; via `icom_lan.audio.usb_driver` |
| `icom_lan.backends._icom_serial_base` | base class, via concrete `icom_lan.backends.<rig>.serial` |
| `icom_lan.backends.ic705.core` | mixin/core, via `ic705.serial` |
| `icom_lan.backends.ic7300.core` | mixin/core, via `ic7300.serial` |
| `icom_lan.backends.ic9700.core` | mixin/core, via `ic9700.serial` |
| `icom_lan.backends.icom7610.drivers.serial_session` | session manager, via `icom_lan.backends.icom7610` |
| `icom_lan.backends.icom7610.lan` | LAN driver, exercised through `icom_lan` integration / not unit-tested |
| `icom_lan.commands._builders` | builder helpers; via `icom_lan.commands` |
| `icom_lan.commands.antenna` | via `icom_lan.commands` |
| `icom_lan.commands.config` | via `icom_lan.commands` |
| `icom_lan.commands.cw` | via `icom_lan.commands` |
| `icom_lan.commands.dsp` | via `icom_lan.commands` |
| `icom_lan.commands.freq` | via `icom_lan.commands` |
| `icom_lan.commands.levels` | via `icom_lan.commands` |
| `icom_lan.commands.memory` | via `icom_lan.commands` |
| `icom_lan.commands.meters` | via `icom_lan.commands` |
| `icom_lan.commands.mode` | via `icom_lan.commands` |
| `icom_lan.commands.power` | via `icom_lan.commands` |
| `icom_lan.commands.ptt` | via `icom_lan.commands` |
| `icom_lan.commands.scope` | via `icom_lan.commands` |
| `icom_lan.commands.speech` | via `icom_lan.commands` |
| `icom_lan.commands.system` | via `icom_lan.commands` |
| `icom_lan.commands.tone` | via `icom_lan.commands` |
| `icom_lan.commands.vfo` | via `icom_lan.commands` |
| `icom_lan.dsp.nodes.base` | base class for DSP nodes; via `icom_lan.dsp.nodes`/`pipeline` |
| `icom_lan.radio_initial_state` | via `icom_lan.radio` |
| `icom_lan.radio_reconnect` | via `icom_lan.radio` (`tests/test_reconnect.py` imports `icom_lan.radio`, not `radio_reconnect`) |
| `icom_lan.radio_state_snapshot` | via `icom_lan.radio_state` |
| `icom_lan.rigctld.routing` | via `icom_lan.rigctld.server` |
| `icom_lan.startup_checks` | via `icom_lan.cli` and `icom_lan.web.web_startup` |
| `icom_lan.web.band_plan` | static data; loaded by `icom_lan.web` runtime |
| `icom_lan.web.eibi` | static data; loaded by `icom_lan.web` runtime |
| `icom_lan.web.handlers.scope` | scope handler; via `icom_lan.web.handlers` |
| `icom_lan.web.web_routing` | route registry; via `icom_lan.web.server` |
| `icom_lan.web.web_startup` | startup glue; via `icom_lan.web.server` / cli |

Implication for migration: when any of these moves, no test will fail purely
because of an import path break. The risk is exactly that — a silently-broken
module path that no test catches. Phase 2 / Phase 4 should explicitly check
that runtime-only consumers (radio, web/server, cli) still resolve these paths.

---

## 4. Conftest cross-layer fixtures

Files scanned: every `conftest.py` under `tests/`.

```
tests/conftest.py
tests/integration/conftest.py
```

Imports of `icom_lan.*` in conftest files (verbatim):

```
tests/conftest.py:10:from icom_lan.radio import IcomRadio  # noqa: TID251
tests/conftest.py:11:from icom_lan.types import HEADER_SIZE, PacketType
tests/integration/conftest.py:32:from icom_lan import IcomRadio  # noqa: E402
```

**Internal-symbol imports in conftest: 0.** No conftest reaches into `icom_lan._*`
or any `icom_lan.<sub>._*`. The conftests pin only public surface
(`icom_lan.IcomRadio`, `icom_lan.radio.IcomRadio`, `icom_lan.types.{HEADER_SIZE,
PacketType}`).

Migration implication: as long as `IcomRadio` remains importable from both
`icom_lan` and `icom_lan.radio` (already a non-negotiable per the orchestrator
brief), and `HEADER_SIZE` / `PacketType` remain in `icom_lan.types`, the
conftests need no changes. Both fixture modules already follow the public
contract.

---

## 5. Methodology notes / caveats

- A test "covers" a source module when it directly imports the module's exact
  dotted path. Tests that import the parent package and access the submodule
  via attribute lookup are not counted. This is intentional: for the
  modularization, the dotted-path import is the migration touch point.
- The orphan threshold (4+ distinct top-level subpackages) is heuristic. Five
  of the listed orphans span 7+ subpackages and are flagged separately above
  (`test_civ_rx_coverage.py`, `test_dsp_filter_family.py`, `test_vox_tone_state.py`,
  `test_selected_freq_mode.py`, `test_web_server.py`). These are the highest-risk
  files for any multi-step move.
- "Untested" means **no direct test importer**. It does not mean "uncovered at
  runtime"; runtime coverage flows from parent-package or entrypoint imports.
- Test count baseline at this branch (verified): **5210** unit tests collected
  via `uv run pytest tests/ --collect-only -q --ignore=tests/integration`.
