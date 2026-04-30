# Internal Modularization — Phase 1 Discovery

**Date:** 2026-04-29
**Branch:** `refactor/modularization-discovery`
**Brief:** [`/Users/moroz/Projects/icom-lan-research/2026-04-29-internal-modularization-orchestrator.md`](../../) (out of repo)
**Status:** ready for maintainer review — Phase 2 cannot start without sign-off
**Scope:** observation only. No design decisions. No source-code changes.

This document is the Phase 1 deliverable of the internal-modularization
effort. It maps the current state of `src/icom_lan/` so Phase 2 can plan
the migration with no surprises. Numbers throughout reference the raw
artifacts under [`discovery-artifacts/`](./discovery-artifacts/) — that
directory is the machine-readable source of truth; this doc is the
synthesis.

---

## 0. Baseline (must hold across the entire effort)

| Check | Value at start of Phase 1 |
|---|---|
| `uv run pytest tests/ --collect-only -q --ignore=tests/integration` | **5210 tests collected** (2 deselected) |
| `uv run ruff check src/ tests/` | All checks passed |
| `uv run mypy src/` | Success: no issues found in 151 source files |
| `git log main..HEAD` | only documentation + tooling artifacts on this branch |

> **Note for Phase 5:** `ARCHITECTURE.md` claims "~4796 unit tests".
> Actual count is **5210**. ARCHITECTURE.md is out of date and must be
> refreshed during Phase 5; that delta is not a structural concern, just
> a stale reference.

---

## 1. File inventory

`src/icom_lan/` contains **151 Python files** across the top level and
six subpackages. Distribution:

| Location | Files |
|---|---:|
| Top-level (`src/icom_lan/*.py`, incl. `__init__.py` + `__main__.py`) | **57** |
| `audio/` | 8 |
| `backends/` | 27 |
| `commands/` | 21 |
| `dsp/` | 8 |
| `rigctld/` | 11 |
| `web/` | 19 |
| **Total** | **151** |

Machine-readable form: [`discovery-artifacts/file-inventory.json`](./discovery-artifacts/file-inventory.json)
— per-module: dotted path, source path, one-line summary (from docstring
or top-level symbols), `__all__` value, PEP 562 status, dynamic-import
sites, and `__init__.py` side-effects flagged outside an import-only
allowlist.

Inventory generator: [`discovery-artifacts/discovery_graph.py`](./discovery-artifacts/discovery_graph.py)
— stdlib-only AST walker, deterministic output, runs in <1s. Re-run with:

```bash
# Run from repo root.
uv run python docs/plans/discovery-artifacts/discovery_graph.py
```

The full table inline below is reproduced from
[`discovery-artifacts/file-inventory.md`](./discovery-artifacts/file-inventory.md)
(rendered by `build_orphan_report.py` from the same JSON). One-line
summaries derive from the module docstring; `(no docstring; …)` rows
are placeholders showing top-level symbols where no docstring exists.

### 1.1 top-level (`src/icom_lan/`) — 57 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/__init__.py` | `icom_lan` | icom-lan: Python library for controlling Icom transceivers over LAN. |
| `src/icom_lan/__main__.py` | `icom_lan.__main__` | Allow running as: python -m icom_lan |
| `src/icom_lan/_audio_codecs.py` | `icom_lan._audio_codecs` | Pure-Python audio codec utilities. |
| `src/icom_lan/_audio_recovery.py` | `icom_lan._audio_recovery` | Audio snapshot/resume logic for IcomRadio reconnect scenarios. |
| `src/icom_lan/_audio_runtime_mixin.py` | `icom_lan._audio_runtime_mixin` | AudioRuntimeMixin — audio streaming methods extracted from CoreRadio. |
| `src/icom_lan/_audio_transcoder.py` | `icom_lan._audio_transcoder` | Internal PCM <-> Opus transcoder utilities. |
| `src/icom_lan/_bounded_queue.py` | `icom_lan._bounded_queue` | Minimal bounded async queue helper used by inter-task buffers. |
| `src/icom_lan/_bridge_metrics.py` | `icom_lan._bridge_metrics` | BridgeMetrics — structured audio bridge telemetry. |
| `src/icom_lan/_bridge_state.py` | `icom_lan._bridge_state` | Bridge connection state machine. |
| `src/icom_lan/_civ_rx.py` | `icom_lan._civ_rx` | CI-V receive pump and event dispatch for IcomRadio. |
| `src/icom_lan/_connection_state.py` | `icom_lan._connection_state` | Radio-level connection state machine. |
| `src/icom_lan/_control_phase.py` | `icom_lan._control_phase` | Auth/login FSM, token renewal, watchdog, and reconnect for IcomRadio. |
| `src/icom_lan/_dual_rx_runtime.py` | `icom_lan._dual_rx_runtime` | DualRxRuntimeMixin — dual-receiver routing methods extracted from CoreRadio. |
| `src/icom_lan/_optional_deps.py` | `icom_lan._optional_deps` | Helpers for optional / heavy dependencies. |
| `src/icom_lan/_poller_types.py` | `icom_lan._poller_types` | Shared command types and CommandQueue for radio pollers. |
| `src/icom_lan/_queue_pressure.py` | `icom_lan._queue_pressure` | Queue pressure threshold for transport and poller coordination. |
| `src/icom_lan/_runtime_protocols.py` | `icom_lan._runtime_protocols` | Internal runtime host Protocols (P0 decomposition). |
| `src/icom_lan/_scope_runtime.py` | `icom_lan._scope_runtime` | ScopeRuntimeMixin — scope/waterfall methods extracted from CoreRadio. |
| `src/icom_lan/_shared_state_runtime.py` | `icom_lan._shared_state_runtime` | Shared state polling/cache helpers used by web and rigctld. |
| `src/icom_lan/_state_cache.py` | `icom_lan._state_cache` | Shared radio state cache. |
| `src/icom_lan/_state_queries.py` | `icom_lan._state_queries` | Shared state query list for populating RadioState. |
| `src/icom_lan/audio_analyzer.py` | `icom_lan.audio_analyzer` | Lightweight audio analyzer — realtime SNR estimation from PCM stream. |
| `src/icom_lan/audio_bridge.py` | `icom_lan.audio_bridge` | Audio bridge — bidirectional PCM bridge between radio and a system audio device. |
| `src/icom_lan/audio_bus.py` | `icom_lan.audio_bus` | AudioBus — pub/sub distribution for radio audio streams. |
| `src/icom_lan/audio_fft_scope.py` | `icom_lan.audio_fft_scope` | Audio FFT Scope — derive IF panadapter from RX audio PCM stream. |
| `src/icom_lan/auth.py` | `icom_lan.auth` | Authentication logic for the Icom LAN protocol. |
| `src/icom_lan/capabilities.py` | `icom_lan.capabilities` | Unified capability constants and known-capability registry. |
| `src/icom_lan/civ.py` | `icom_lan.civ` | CI-V event routing and request tracking utilities. |
| `src/icom_lan/cli.py` | `icom_lan.cli` | icom-lan CLI — command-line interface for Icom LAN control. |
| `src/icom_lan/command_map.py` | `icom_lan.command_map` | CommandMap — frozen lookup for CI-V wire bytes by command name. |
| `src/icom_lan/command_spec.py` | `icom_lan.command_spec` | Command specification types for multi-protocol radio control. |
| `src/icom_lan/commander.py` | `icom_lan.commander` | classes: Priority, _QueueItem, IcomCommander |
| `src/icom_lan/cw_auto_tuner.py` | `icom_lan.cw_auto_tuner` | CW Auto Tuner — FFT-based CW tone frequency detection. |
| `src/icom_lan/discovery.py` | `icom_lan.discovery` | Serial port enumeration, candidate filtering, and multi-protocol radio discovery. |
| `src/icom_lan/env_config.py` | `icom_lan.env_config` | Environment variable configuration helpers for icom-lan. |
| `src/icom_lan/exceptions.py` | `icom_lan.exceptions` | Custom exception hierarchy for icom-lan. |
| `src/icom_lan/ic705.py` | `icom_lan.ic705` | IC-705 convenience helpers for data and packet-mode workflows. |
| `src/icom_lan/meter_cal.py` | `icom_lan.meter_cal` | Shared meter calibration helpers. |
| `src/icom_lan/profiles.py` | `icom_lan.profiles` | Radio profile and capability matrix for runtime routing and guards. |
| `src/icom_lan/profiles_runtime.py` | `icom_lan.profiles_runtime` | Generic capability-aware radio profile system. |
| `src/icom_lan/protocol.py` | `icom_lan.protocol` | Packet parsing and serialization for the Icom LAN UDP protocol. |
| `src/icom_lan/proxy.py` | `icom_lan.proxy` | Transparent UDP relay proxy for Icom LAN protocol. |
| `src/icom_lan/radio.py` | `icom_lan.radio` | IcomRadio — high-level async API for Icom transceivers over LAN. |
| `src/icom_lan/radio_initial_state.py` | `icom_lan.radio_initial_state` | Initial-state fetch orchestration for :class:`IcomRadio`. |
| `src/icom_lan/radio_protocol.py` | `icom_lan.radio_protocol` | Abstract Radio Protocol — multi-backend radio control interface. |
| `src/icom_lan/radio_reconnect.py` | `icom_lan.radio_reconnect` | Watchdog and reconnect loops for :class:`IcomRadio`. |
| `src/icom_lan/radio_state.py` | `icom_lan.radio_state` | RadioState — dual-receiver radio state model. |
| `src/icom_lan/radio_state_snapshot.py` | `icom_lan.radio_state_snapshot` | Snapshot/restore helpers for :class:`IcomRadio` state. |
| `src/icom_lan/radios.py` | `icom_lan.radios` | Radio model presets with CI-V addresses and capabilities. |
| `src/icom_lan/rig_loader.py` | `icom_lan.rig_loader` | TOML rig config loader — parse, validate, and build runtime objects. |
| `src/icom_lan/scope.py` | `icom_lan.scope` | Scope/waterfall frame assembly for Icom transceivers. |
| `src/icom_lan/scope_render.py` | `icom_lan.scope_render` | Scope/waterfall frame rendering for Icom transceivers. |
| `src/icom_lan/startup_checks.py` | `icom_lan.startup_checks` | functions: assert_radio_startup_ready, wait_for_radio_startup_ready |
| `src/icom_lan/sync.py` | `icom_lan.sync` | Synchronous (blocking) wrapper around :class:`~icom_lan.radio.IcomRadio`. |
| `src/icom_lan/transport.py` | `icom_lan.transport` | Async UDP transport for the Icom LAN protocol. |
| `src/icom_lan/types.py` | `icom_lan.types` | Enums, dataclasses, and helper functions for the Icom LAN protocol. |
| `src/icom_lan/usb_audio_resolve.py` | `icom_lan.usb_audio_resolve` | Resolve USB Audio devices associated with a serial CI-V port. |

### 1.2 `src/icom_lan/audio/` — 8 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/audio/__init__.py` | `icom_lan.audio` | Universal audio subsystem for icom-lan. |
| `src/icom_lan/audio/_macos_uid.py` | `icom_lan.audio._macos_uid` | macOS CoreAudio device UID lookup via ctypes. |
| `src/icom_lan/audio/backend.py` | `icom_lan.audio.backend` | AudioBackend protocol and implementations. |
| `src/icom_lan/audio/config.py` | `icom_lan.audio.config` | Optional audio.toml configuration — persists device selection across runs. |
| `src/icom_lan/audio/dsp.py` | `icom_lan.audio.dsp` | Optional DSP pipeline for audio bridge — noise gate, RMS normalization, limiter. |
| `src/icom_lan/audio/lan_stream.py` | `icom_lan.audio.lan_stream` | Audio streaming for Icom transceivers over LAN (UDP). |
| `src/icom_lan/audio/resample.py` | `icom_lan.audio.resample` | Optional PCM resampling using numpy linear interpolation. |
| `src/icom_lan/audio/usb_driver.py` | `icom_lan.audio.usb_driver` | Universal USB audio driver for all serial-connected radios (macOS-first). |

### 1.3 `src/icom_lan/backends/` — 27 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/backends/__init__.py` | `icom_lan.backends` | Backend-specific radio implementations and assembly helpers. |
| `src/icom_lan/backends/_icom_serial_base.py` | `icom_lan.backends._icom_serial_base` | Shared base class for Icom serial (USB CI-V) radio backends. |
| `src/icom_lan/backends/config.py` | `icom_lan.backends.config` | Typed backend configuration models for radio assembly. |
| `src/icom_lan/backends/factory.py` | `icom_lan.backends.factory` | Backend factory for assembling radio implementations from typed config. |
| `src/icom_lan/backends/ic705/__init__.py` | `icom_lan.backends.ic705` | IC-705 backend implementations (serial). |
| `src/icom_lan/backends/ic705/core.py` | `icom_lan.backends.ic705.core` | Shared executable core for IC-705 behavior. |
| `src/icom_lan/backends/ic705/serial.py` | `icom_lan.backends.ic705.serial` | Serial adaptation layer for the IC-705 backend. |
| `src/icom_lan/backends/ic7300/__init__.py` | `icom_lan.backends.ic7300` | IC-7300 backend implementations (serial). |
| `src/icom_lan/backends/ic7300/core.py` | `icom_lan.backends.ic7300.core` | Shared executable core for IC-7300 behavior. |
| `src/icom_lan/backends/ic7300/serial.py` | `icom_lan.backends.ic7300.serial` | Serial adaptation layer for the IC-7300 backend. |
| `src/icom_lan/backends/ic9700/__init__.py` | `icom_lan.backends.ic9700` | IC-9700 backend implementations (serial and LAN). |
| `src/icom_lan/backends/ic9700/core.py` | `icom_lan.backends.ic9700.core` | Shared executable core for IC-9700 behavior. |
| `src/icom_lan/backends/ic9700/serial.py` | `icom_lan.backends.ic9700.serial` | Serial adaptation layer for the IC-9700 backend. |
| `src/icom_lan/backends/icom7610/__init__.py` | `icom_lan.backends.icom7610` | IC-7610 backend exports. |
| `src/icom_lan/backends/icom7610/drivers/__init__.py` | `icom_lan.backends.icom7610.drivers` | Internal driver contracts for IC-7610 backend. |
| `src/icom_lan/backends/icom7610/drivers/contracts.py` | `icom_lan.backends.icom7610.drivers.contracts` | Internal backend driver contracts for IC-7610 core orchestration. |
| `src/icom_lan/backends/icom7610/drivers/serial_civ_link.py` | `icom_lan.backends.icom7610.drivers.serial_civ_link` | Production Serial CI-V link for IC-7610 USB serial backend. |
| `src/icom_lan/backends/icom7610/drivers/serial_session.py` | `icom_lan.backends.icom7610.drivers.serial_session` | Serial session driver and transport adapters for IC-7610 shared core. |
| `src/icom_lan/backends/icom7610/drivers/serial_stub.py` | `icom_lan.backends.icom7610.drivers.serial_stub` | Deterministic serial-ready test doubles for backend regression gates. |
| `src/icom_lan/backends/icom7610/drivers/usb_audio.py` | `icom_lan.backends.icom7610.drivers.usb_audio` | Backward-compatible re-export — driver moved to icom_lan.audio.usb_driver. |
| `src/icom_lan/backends/icom7610/lan.py` | `icom_lan.backends.icom7610.lan` | LAN adaptation layer for the IC-7610 backend. |
| `src/icom_lan/backends/icom7610/serial.py` | `icom_lan.backends.icom7610.serial` | Serial adaptation layer for the IC-7610 backend. |
| `src/icom_lan/backends/yaesu_cat/__init__.py` | `icom_lan.backends.yaesu_cat` | Yaesu CAT backend for icom-lan. |
| `src/icom_lan/backends/yaesu_cat/parser.py` | `icom_lan.backends.yaesu_cat.parser` | Yaesu CAT command formatter and parser. |
| `src/icom_lan/backends/yaesu_cat/poller.py` | `icom_lan.backends.yaesu_cat.poller` | YaesuCatPoller — polling scheduler for YaesuCatRadio. |
| `src/icom_lan/backends/yaesu_cat/radio.py` | `icom_lan.backends.yaesu_cat.radio` | Yaesu CAT radio backend — FTX-1 and compatible transceivers. |
| `src/icom_lan/backends/yaesu_cat/transport.py` | `icom_lan.backends.yaesu_cat.transport` | Yaesu CAT serial transport — bulletproof async line protocol. |

### 1.4 `src/icom_lan/commands/` — 21 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/commands/__init__.py` | `icom_lan.commands` | CI-V command encoding and decoding for Icom transceivers. |
| `src/icom_lan/commands/_builders.py` | `icom_lan.commands._builders` | Shared builder templates used by multiple leaf modules. |
| `src/icom_lan/commands/_codec.py` | `icom_lan.commands._codec` | BCD and level encode/decode helpers. |
| `src/icom_lan/commands/_frame.py` | `icom_lan.commands._frame` | CI-V frame builders, parser, and all command/sub-command constants. |
| `src/icom_lan/commands/antenna.py` | `icom_lan.commands.antenna` | Antenna selection / RX-ANT commands (0x12). |
| `src/icom_lan/commands/config.py` | `icom_lan.commands.config` | Configuration commands: mod levels, mod input routing, CI-V options. |
| `src/icom_lan/commands/cw.py` | `icom_lan.commands.cw` | CW keying commands (send_cw, stop_cw). |
| `src/icom_lan/commands/dsp.py` | `icom_lan.commands.dsp` | DSP commands: ATT, preamp, NB, NR, IP+, AGC, notch, compressor, VOX, break-in, etc. |
| `src/icom_lan/commands/freq.py` | `icom_lan.commands.freq` | Frequency commands (0x03/0x05/0x25/0x26). |
| `src/icom_lan/commands/levels.py` | `icom_lan.commands.levels` | All 0x14-family level get/set commands + parse_level_response. |
| `src/icom_lan/commands/memory.py` | `icom_lan.commands.memory` | Memory mode/write/clear/contents and band stacking register commands. |
| `src/icom_lan/commands/meters.py` | `icom_lan.commands.meters` | All 0x15-family meter read commands. |
| `src/icom_lan/commands/mode.py` | `icom_lan.commands.mode` | Mode commands (0x04/0x06), data mode, filter shape/width, SSB BW, AGC time constant. |
| `src/icom_lan/commands/power.py` | `icom_lan.commands.power` | Power on/off and powerstat commands. |
| `src/icom_lan/commands/ptt.py` | `icom_lan.commands.ptt` | PTT on/off commands. |
| `src/icom_lan/commands/scope.py` | `icom_lan.commands.scope` | Spectrum / waterfall scope commands (0x27 family). |
| `src/icom_lan/commands/speech.py` | `icom_lan.commands.speech` | Speech announcement command (0x13). |
| `src/icom_lan/commands/system.py` | `icom_lan.commands.system` | System commands: transceiver ID, band edge, tuner, XFC, TX freq monitor, RIT/XIT. |
| `src/icom_lan/commands/tone.py` | `icom_lan.commands.tone` | Repeater tone/TSQL commands (0x1B family, 0x16 0x42/0x43). |
| `src/icom_lan/commands/tx_band.py` | `icom_lan.commands.tx_band` | TX band edge commands (0x1E). |
| `src/icom_lan/commands/vfo.py` | `icom_lan.commands.vfo` | VFO, scan, dual watch, split, tuning step commands. |

### 1.5 `src/icom_lan/dsp/` — 8 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/dsp/__init__.py` | `icom_lan.dsp` | DSP pipeline core abstractions for real-time audio processing. |
| `src/icom_lan/dsp/exceptions.py` | `icom_lan.dsp.exceptions` | DSP pipeline exceptions. |
| `src/icom_lan/dsp/nodes/__init__.py` | `icom_lan.dsp.nodes` | Concrete DSP nodes for audio processing pipelines. |
| `src/icom_lan/dsp/nodes/base.py` | `icom_lan.dsp.nodes.base` | Base DSP nodes: PassthroughNode and GainNode. |
| `src/icom_lan/dsp/nodes/nr_scipy.py` | `icom_lan.dsp.nodes.nr_scipy` | NRScipyNode — spectral subtraction noise reduction. |
| `src/icom_lan/dsp/pipeline.py` | `icom_lan.dsp.pipeline` | DSP pipeline core: DSPNode protocol and DSPPipeline orchestrator. |
| `src/icom_lan/dsp/resample.py` | `icom_lan.dsp.resample` | Inter-node resample utility for the DSP pipeline. |
| `src/icom_lan/dsp/tap_registry.py` | `icom_lan.dsp.tap_registry` | Multi-consumer PCM audio tap registry. |

### 1.6 `src/icom_lan/rigctld/` — 11 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/rigctld/__init__.py` | `icom_lan.rigctld` | Hamlib NET rigctld-compatible TCP server for icom-lan. |
| `src/icom_lan/rigctld/audit.py` | `icom_lan.rigctld.audit` | Structured per-command audit logging for the rigctld server. |
| `src/icom_lan/rigctld/circuit_breaker.py` | `icom_lan.rigctld.circuit_breaker` | Circuit breaker for CI-V command resilience. |
| `src/icom_lan/rigctld/contract.py` | `icom_lan.rigctld.contract` | Shared contracts between rigctld modules. |
| `src/icom_lan/rigctld/handler.py` | `icom_lan.rigctld.handler` | Rigctld command handler — dispatches parsed commands to IcomRadio. |
| `src/icom_lan/rigctld/poller.py` | `icom_lan.rigctld.poller` | Autonomous radio state poller for the rigctld server. |
| `src/icom_lan/rigctld/protocol.py` | `icom_lan.rigctld.protocol` | Rigctld wire protocol parser and formatter. |
| `src/icom_lan/rigctld/routing.py` | `icom_lan.rigctld.routing` | Rigctld vendor-specific routing strategies. |
| `src/icom_lan/rigctld/server.py` | `icom_lan.rigctld.server` | Rigctld TCP server — asyncio.start_server transport layer. |
| `src/icom_lan/rigctld/state_cache.py` | `icom_lan.rigctld.state_cache` | (no docstring; no public top-level defs) |
| `src/icom_lan/rigctld/utils.py` | `icom_lan.rigctld.utils` | functions: get_mode_reader |

### 1.7 `src/icom_lan/web/` — 19 files

| Path | Module | Summary |
|---|---|---|
| `src/icom_lan/web/__init__.py` | `icom_lan.web` | icom-lan Web UI — WebSocket + HTTP server package. |
| `src/icom_lan/web/_delta_encoder.py` | `icom_lan.web._delta_encoder` | Delta encoding for web state updates — reduce payload for frequent broadcasts. |
| `src/icom_lan/web/band_plan.py` | `icom_lan.web.band_plan` | Band plan registry — load TOML files and serve via REST. |
| `src/icom_lan/web/discovery.py` | `icom_lan.web.discovery` | UDP Discovery Responder for icom-lan. |
| `src/icom_lan/web/dx_cluster.py` | `icom_lan.web.dx_cluster` | DX cluster client: spot parsing, telnet client, spot buffer. |
| `src/icom_lan/web/eibi.py` | `icom_lan.web.eibi` | EiBi broadcast station database — fetch, parse, cache, query. |
| `src/icom_lan/web/handlers/__init__.py` | `icom_lan.web.handlers` | Route handlers for WebSocket channels and HTTP endpoints. |
| `src/icom_lan/web/handlers/audio.py` | `icom_lan.web.handlers.audio` | Audio WebSocket handlers — broadcaster + per-client handler. |
| `src/icom_lan/web/handlers/control.py` | `icom_lan.web.handlers.control` | Control WebSocket handler — JSON commands, events, state. |
| `src/icom_lan/web/handlers/scope.py` | `icom_lan.web.handlers.scope` | Scope WebSocket handler — binary scope frame channel with backpressure. |
| `src/icom_lan/web/protocol.py` | `icom_lan.web.protocol` | Web UI binary frame protocol for scope and audio data. |
| `src/icom_lan/web/radio_poller.py` | `icom_lan.web.radio_poller` | RadioPoller — fire-and-forget CI-V serialiser. |
| `src/icom_lan/web/rtc.py` | `icom_lan.web.rtc` | WebRTC signaling support for icom-lan. |
| `src/icom_lan/web/runtime_helpers.py` | `icom_lan.web.runtime_helpers` | functions: runtime_capabilities, radio_ready, build_public_state_payload |
| `src/icom_lan/web/server.py` | `icom_lan.web.server` | WebSocket + HTTP server for the icom-lan Web UI. |
| `src/icom_lan/web/tls.py` | `icom_lan.web.tls` | TLS certificate management for the web server. |
| `src/icom_lan/web/web_routing.py` | `icom_lan.web.web_routing` | HTTP method/path dispatch for :class:`icom_lan.web.server.WebServer`. |
| `src/icom_lan/web/web_startup.py` | `icom_lan.web.web_startup` | Startup/shutdown orchestration for :class:`icom_lan.web.server.WebServer`. |
| `src/icom_lan/web/websocket.py` | `icom_lan.web.websocket` | RFC 6455 WebSocket + RFC 7692 permessage-deflate (stdlib only). |

---

## 2. Dependency graph

Static intra-package import graph: [`discovery-artifacts/import-graph.dot`](./discovery-artifacts/import-graph.dot).

Edge semantics (locked in for Phase 1, see script docstring):

- `from X import …` → one edge to `X` if `X` resolves internally.
- `from . import a, b` → one edge per submodule `a`, `b` resolved against
  the enclosing package — this avoids fabricating spurious parent-package
  cycles from sibling re-exports.
- `import a.b.c` → one edge to the closest internal ancestor of `a.b.c`.
- ALL imports are collected — top-level, function-local, and
  `if TYPE_CHECKING:` alike. Classification by execution context happens
  separately (see §3).

Render to PNG/SVG with `dot -Tsvg import-graph.dot -o graph.svg`.

---

## 3. Cycle report — verified

Static analysis found **5 strongly-connected components of size > 1**.
Raw output: [`discovery-artifacts/cycles.txt`](./discovery-artifacts/cycles.txt).
Per-edge classification (top-level vs `TYPE_CHECKING` vs function-local
vs other-conditional):
[`discovery-artifacts/cycles-classified.md`](./discovery-artifacts/cycles-classified.md).

Classifier source: [`discovery-artifacts/classify_cycle_edges.py`](./discovery-artifacts/classify_cycle_edges.py).

| SCC | Nodes | Verdict |
|---|---:|---|
| `radio` ↔ {`_audio_runtime_mixin`, `_dual_rx_runtime`, `_scope_runtime`, `radio_initial_state`, `radio_reconnect`, `radio_state_snapshot`} | 7 | **Deferred-only** — `radio` imports the others top-level, all back-edges are `if TYPE_CHECKING:`. |
| `_civ_rx` ↔ `_runtime_protocols` | 2 | **Deferred-only** — both edges are `TYPE_CHECKING`. |
| `profiles` ↔ `rig_loader` | 2 | **Deferred-only** — `rig_loader → profiles` top-level; `profiles → rig_loader` function-local at line 266. |
| `rigctld.handler` ↔ `rigctld.routing` | 2 | **Deferred-only** — `handler → routing` top-level; `routing → handler` `TYPE_CHECKING`. |
| `web.server` ↔ {`web_routing`, `web_startup`} | 3 | **Deferred-only** — `server` reaches the others via function-local imports; back-edges are `TYPE_CHECKING` / function-local. |

**Verdict for Phase 2:** the codebase has **zero true runtime import
cycles**. Every static SCC dissolves once `TYPE_CHECKING` and function-
local imports are removed. This is a load-bearing finding: it means
moving files between layers is structurally safe so long as the new
layout preserves the existing deferred-import pattern. The brief's
"stop and ask" trigger for confirmed cycles is **not triggered**.

The two function-local back-edges (`profiles → rig_loader`,
`web.server → web_routing/startup`) are likely the original workarounds
for the cycles. They must be **carried over verbatim** during the
migration, not "cleaned up" — they exist for a reason.

---

## 4. Orphan classification

The current top level holds **56 non-init `.py` files** that more
naturally belong inside a layer subpackage. Full table with inbound /
outbound edge counts and tentative bucket per file:
[`discovery-artifacts/orphan-candidates.md`](./discovery-artifacts/orphan-candidates.md).

Tentative bucket distribution (heuristic from filename + summary —
**this is input to Phase 2, not a final assignment**):

| Bucket | Count | Notes |
|---|---:|---|
| `runtime` | 23 | The bulk of the orphans — runtime mixins, state snapshots, queues, bridges. |
| `core` | 12 | `civ`, `transport`, `protocol`, `_civ_rx`, `exceptions`, `auth`, `discovery`, `types`, etc. |
| `audio` | 9 | All `audio_*` and `_audio_*` modules + `usb_audio_resolve`. |
| `commands` | 3 | `commander`, `command_map`, `command_spec`. |
| `profiles` | 3 | `profiles`, `profiles_runtime`, `rig_loader`. |
| `scope` | 3 | `scope`, `scope_render`, `_scope_runtime`. |
| `cli` | 1 | `cli.py`. |
| `unsure` | 2 | `__main__.py` (entrypoint shim — may stay top-level), `_optional_deps.py` (foundational, used by 7 modules — likely `core`). |

Heuristic source: [`discovery-artifacts/build_orphan_report.py`](./discovery-artifacts/build_orphan_report.py).

**Implications for Phase 2:**

- The largest layer by file count is `runtime` (23 modules) — packing
  this is the biggest single migration step and likely needs splitting
  into 2–3 sub-steps to keep PR size reviewable.
- `core` has 12 candidates but several (e.g. `auth`, `discovery`) are
  arguably runtime concerns. Final placement is for Phase 2.
- `_optional_deps.py` is depended on by 7 modules across the package.
  Its placement constrains the dependency matrix — wherever it lives,
  it needs to be importable from every other layer. `core` is the
  only sane answer.

---

## 5. `__init__.py` snapshot — what the public contract looks like today

Every `__init__.py`'s `__all__` plus side-effect / PEP 562 status:
[`discovery-artifacts/init-snapshot.md`](./discovery-artifacts/init-snapshot.md).

### Top-level `icom_lan/__init__.py` — Tier 1 / Tier 2 layered API

This is the most important file in the migration. It implements a
**PEP 562 lazy-loading layer** that splits the public API into two
stability tiers (per file docstring):

- **Tier 1 (eager, semver-stable from v0.19):** 32 names — `Radio`,
  `IcomRadio`, all capability protocols, all backend configs,
  exceptions, `RadioProfile`, `Mode`, `AudioCodec`, etc.
- **Tier 2 (lazy via `__getattr__`):** 28 names — `IcomCommander`,
  `Priority`, `AudioStream`, `AudioBackend`, `PortAudioBackend`,
  `FakeAudioBackend`, `AudioConfig`, `NoiseGate`, `RmsNormalizer`,
  `Limiter`, `DspPipeline`, `UsbAudioDriver`, etc. — backed by an
  internal `_LAZY_MAP: dict[str, tuple[module, attr]]` evaluated at
  first attribute access.

Total `__all__` size: **60 names**. Phase 2 must preserve every one of
these import paths (`from icom_lan import <name>`). The `_LAZY_MAP` is
a **migration shim plan dependency** — when a module moves, the map's
target tuple changes, but the imported name does not.

### Subpackage init files

`icom_lan.audio/__init__.py` repeats the same Tier 1 / Tier 2 pattern
internally for audio surface. The other subpackages (`backends`,
`commands`, `dsp`, `rigctld`, `web`, `commands.icom7610`,
`commands.icom7610.queries`, `commands.icom7610.params`,
`backends.icom705`, `backends.icom7610`, `backends.yaesu_cat`,
`backends.icom7610.drivers`) use plain explicit `__all__` exports. The
`rigctld/__init__.py` wraps its single export in a `try / except
ImportError` shim so optional install groups can omit the rigctld
TCP server cleanly — that pattern must be preserved.

### Side-effects (statements at top level outside the import-only allowlist)

| File | Statement | Real concern? |
|---|---|---|
| `icom_lan/__init__.py:81` | `_LAZY_MAP: dict[...] = {...}` (`AnnAssign`) | **Yes — intentional.** Tier 2 lazy-load registry. Must keep working through every migration step. |
| `icom_lan/__init__.py:245` | `def __dir__()` (`FunctionDef`) | **Yes — intentional.** PEP 562 `__dir__` for IDE / REPL completion of lazy names. |
| `icom_lan/audio/__init__.py:38` | analogous `_LAZY_MAP` | Same as above for the `audio` subpackage. |
| `icom_lan/audio/__init__.py:105` | analogous `__dir__` | Same. |
| `icom_lan/rigctld/__init__.py:13` | `try: …; __all__ = […]; except ImportError: __all__ = []` | **No** — script quirk. The `Try` body has both an Import AND an `__all__` assign, which trips the import-only allowlist. The pattern is correct and intentional. |

### Dynamic-import call sites (`importlib.import_module` / `__import__`)

| File | Line:Col | Purpose |
|---|---|---|
| `icom_lan/__init__.py` | 239:13 | PEP 562 lazy-loader (Tier 2). |
| `icom_lan/audio/__init__.py` | 99:13 | PEP 562 lazy-loader (audio Tier 2). |
| `icom_lan/backends/icom7610/drivers/serial_civ_link.py` | 362:25 | Driver-specific runtime resolution. Verify in Phase 2. |
| `icom_lan/web/rtc.py` | 49:12 | Optional WebRTC runtime resolution. Verify in Phase 2. |

> **Risk:** dynamic-import sites are invisible to import-graph linters
> like `import-linter` and `tach`. The Phase 2 layer matrix must be
> validated against these four sites manually; whichever layers
> `serial_civ_link.py` and `web/rtc.py` resolve into at runtime, the
> imports must be allowed.

---

## 6. External usage inventory

Catalogue of every place outside `src/icom_lan/` that imports from
`icom_lan.*`: [`discovery-artifacts/external-usage.md`](./discovery-artifacts/external-usage.md).

| Source | Files using `icom_lan` | Distinct paths | Total occurrences |
|---|---:|---:|---:|
| `tests/` | **177** | 101 | 1151 |
| `docs/` | small (Markdown examples only) | — | — |
| `frontend/` | **0 (verified)** — no Python files | 0 | 0 |
| `/Users/moroz/Projects/icom-lan-pro` (downstream) | **8** | 5 | **29** |

**icom-lan-pro escalation check:** the brief's "stop and ask" threshold
is `>30 import sites in icom-lan-pro`. Actual: **29**. The threshold
is **NOT exceeded by 1** — Phase 2 may proceed without a coordination
step with the downstream maintainer. The margin is thin; if Phase 2
discovers the count has grown, escalate.

**Top 5 icom-lan-pro import paths** (occurrences):

1. `icom_lan.audio.backend` — 19
2. `icom_lan.dsp.pipeline` — 4
3. `icom_lan.dsp.nodes.base` — 3
4. `icom_lan.dsp.exceptions` — 2
5. `icom_lan.audio.dsp` — 1

**Implication for shim design:** every icom-lan-pro import already
resolves to a path inside an existing subpackage (`audio/`, `dsp/`).
None of them touches a top-level orphan that will move. The downstream
contract is preserved **for free** as long as `audio/` and `dsp/` keep
their public surfaces unchanged. This is the single best piece of news
in the discovery.

### Internal-symbol leaks (private paths used by external code)

External code reaching into private-prefixed names like
`from icom_lan._foo import …`:

| Source | Files | Total occurrences |
|---|---:|---:|
| `tests/` | **15** | **43** |
| `docs/` | 0 | 0 |
| `icom-lan-pro` | 0 | 0 |

Worst offenders inside `tests/`:

- `icom_lan._connection_state` — 5 files, 22 occurrences (`test_radio_coverage.py` alone has 13 function-local re-imports).
- `icom_lan._civ_rx` — 4 files, 8 occurrences.
- `icom_lan._poller_types` — 4 occurrences in one file.

Phase 2 must add a **re-export shim** at every old top-level path that
any test file imports privately. The 15 test files anchor 43 import
sites — these are migration tripwires. The shim catalogue will be
finalised in Phase 2; this discovery names the shim sources.

### Dynamic imports of `icom_lan` from outside the package

One site: `tests/test_naming_parity.py:121` resolves `icom_lan.commands`
via `importlib.import_module`. It targets the public path, so it
survives the migration unchanged.

---

## 7. Test inventory

Test-to-source mapping, orphan tests, and untested-by-direct-import
modules: [`discovery-artifacts/test-inventory.md`](./discovery-artifacts/test-inventory.md).

Headline numbers:

| Metric | Value |
|---|---:|
| Test files | 185 (this worktree's `tests/` tree) |
| Tests collected (excluding `tests/integration`) | **5210** |
| Modules under `src/icom_lan/` (excl. `__init__`/`__main__`/`py.typed`) | 135 |
| Modules with **zero** test files importing them directly | **44** |
| Conftest cross-layer fixtures (private-path imports) | **0** |

**Conftest finding:** both conftest files (`tests/conftest.py`,
`tests/integration/conftest.py`) only import from public surface
(`IcomRadio`, `HEADER_SIZE`, `PacketType`). This means **no
fixture-level coupling to private modules** — Phase 2 does not need
to refactor conftest as part of file moves.

**Untested-by-direct-import modules** are the main migration risk
surface for tests: most of them are exercised transitively through
parent-package imports (`from icom_lan.commands import …`), so they
will silently break only if the *re-export shim* misses them. The
shim plan in Phase 2 must enumerate this list explicitly.

Notable cluster of untested-by-direct-import:
`icom_lan.commands.{antenna, freq, levels, mode, …}` — exercised via
the commander's import of `command_map`, not directly. These move as
a group.

---

## 8. Tooling assessment

Full empirical comparison of `tach` vs `import-linter` (both run in
dry-run mode against the current flat layout under the brief's
proposed allowed-dependency rules):
[`discovery-artifacts/tooling-assessment.md`](./discovery-artifacts/tooling-assessment.md).

Draft configs (kept under `discovery-artifacts/`, NOT at repo root, to
keep `pyproject.toml` clean):

- [`tach-config-draft.toml`](./discovery-artifacts/tach-config-draft.toml)
- [`importlinter-config-draft.ini`](./discovery-artifacts/importlinter-config-draft.ini)

### Recommendation: `import-linter`

**Sharpest reason:** the `layers` contract maps 1-to-1 onto the brief's
allowed-dependency table. One ~10-line block expresses what `tach`
needs ~50 `[[modules]]` entries to encode, and the transitive-chain
output (`A → B → C`) is the right diagnostic for a layered
architecture.

| Tool | Dry-run violations | Notes |
|---|---:|---|
| `tach` | **358 `[FAIL]` lines / 89 unique source→target pairs** | Top offenders dominated by `web → _poller_types` (122) and `backends → _poller_types` (49) — bucketing artefacts, not real architectural drift. Genuine layer violations live in `backends → audio/radio/commands` (~25). |
| `import-linter` | **8 contracts / 4 KEPT, 4 BROKEN, 16 unique forbidden pairs** | Layered contract surfaces 3 real subpackage violations (`backends → commands/scope/audio`); `forbidden` contracts add ~13 more (mainly `backends → radio_protocol/commander/audio_bus/_state_*` reached transitively via `icom_lan.radio`, plus direct `web.web_startup → backends.yaesu_cat.*`). |

### **Critical caveat (relevant to Phase 2 sequencing)**

The package is **genuinely flat**. Neither tool can enforce the
proposed rules over the 56 top-level `.py` files until Phase 4
physically moves them into subpackages. The `layers` contract today
reaches only the six real subpackages (`audio/`, `backends/`,
`commands/`, `dsp/`, `rigctld/`, `web/`).

**Therefore: do not introduce `import-linter` to CI before file moves
begin.** The right Phase 2 sequencing is:

1. Create the new layer directories (empty or near-empty).
2. Move files into them (the bulk of the migration).
3. **Then** introduce `import-linter` to pre-commit + CI, so it can
   help police the cleanup phase rather than just complaining about
   the existing flat layout.

### CI integration caveat

`uvx --from import-linter lint-imports` cannot find the package by
default — it silently reports 0 contracts. The CI command must be one
of:

- `uvx --with-editable . --from import-linter lint-imports` (works
  without polluting `pyproject.toml`).
- `uv run lint-imports` (cleaner, but requires declaring the dep —
  decide in Phase 2).

Otherwise: runtime <1s on this codebase; pre-commit hook timing also
needs Phase 2 verification — the official hook may not handle `uv`
workflows cleanly.

---

## 9. Risk log

Ranked by impact on Phase 4 execution. Each item names the discovery
artifact that surfaced it and a proposed mitigation for Phase 2 to
incorporate into the migration plan.

### R1 — PEP 562 lazy-loading (Tier 1 / Tier 2)  ★★★

The top-level `__init__.py` and `audio/__init__.py` implement a PEP
562 lazy-loader that hides backing-module imports behind
attribute-access dispatch. **`import-linter`'s static graph cannot
see these edges.** Concretely: `from icom_lan import IcomRadio`
*today* triggers no static edge to `icom_lan.radio`; it only happens
when an actual access to `icom_lan.IcomRadio` runs at runtime.

- **Surfaces in:** `init-snapshot.md`, `file-inventory.json`
  (`has_pep562_getattr` true for `icom_lan` and `icom_lan.audio`).
- **Mitigation in Phase 2:** the migration plan must include a small
  runtime-side check — exec each Tier 2 lazy attribute in a CI step
  to confirm it resolves. A 30-line script that does
  `for name in list(_LAZY_MAP): getattr(icom_lan, name)` is enough.
  Run it as part of the public-API smoke test on every step's PR.
- **Owner of the constraint:** Phase 2 plan + Phase 4 acceptance
  criteria.

### R2 — Internal-symbol leaks in tests (43 across 15 files)  ★★★

15 test files reach into `icom_lan._<private>` paths. These are
**real-world consumers** of internal layout from the test suite's
point of view. Every move of a private module must add a re-export
shim at the old path, OR rewrite the test imports to a stable path.

- **Surfaces in:** `external-usage.md` ("Internal-symbol leaks").
- **Worst offenders:** `_connection_state` (5 files / 22
  occurrences), `_civ_rx` (4 / 8), `_poller_types` (1 / 4).
- **Mitigation in Phase 2:** for each move, the migration step
  description must state explicitly: (a) the old path, (b) the new
  path, (c) whether a shim is added at the old path or the test
  imports are rewritten. Maintainer to choose policy in Phase 2 (the
  brief recommends shims with no `DeprecationWarning`).

### R3 — Function-local imports as cycle workarounds  ★★

Two function-local imports exist *because* a top-level import would
create a runtime cycle:

- `profiles.py:266` defers `import rig_loader`
- `web/server.py:920, 989, 1172` defer imports of `web_routing` /
  `web_startup`

If Phase 4 moves these files between layers without preserving the
deferred-import pattern, runtime ImportError will surface only when
the path is exercised (potentially after merge).

- **Surfaces in:** `cycles-classified.md`.
- **Mitigation in Phase 2:** mark these imports as **load-bearing**
  in the migration plan. Each step that touches these modules must
  preserve the deferred-import location verbatim.

### R4 — Untested-by-direct-import modules (44 of 135)  ★★

Modules exercised only through parent-package imports will not show
up in test grep when their paths change. If a re-export shim is
forgotten for one of them, the failure surfaces only at the next
test that walks the full `commander` path (or similar) — not at the
move's own smoke test.

- **Surfaces in:** `test-inventory.md`.
- **Mitigation in Phase 2:** the migration plan's per-step
  acceptance criteria must include the **full pytest run** (not just
  module-targeted tests). The orchestrator brief already mandates
  this; this risk is the *reason* it does.

### R5 — Dynamic imports invisible to linter  ★★

Four `importlib.import_module` / `__import__` sites:
`icom_lan/__init__.py`, `audio/__init__.py` (the PEP 562 loaders
themselves — covered by R1) plus
`backends/icom7610/drivers/serial_civ_link.py:362` and
`web/rtc.py:49`.

- **Surfaces in:** `file-inventory.json` (`dynamic_imports` field).
- **Mitigation in Phase 2:** read both call sites; whichever layers
  the resolved targets fall into, ensure those edges are explicitly
  permitted in the import-linter contracts. Annotate the call sites
  with a comment cross-referencing the contract once enforcement is
  on.

### R6 — Package is flat — cannot enforce until Phase 4  ★★

`tach` and `import-linter` cannot meaningfully police layer
boundaries over the 56 top-level files until they are physically
moved. Trying to add per-file rules to bridge the gap is as tedious
as the actual migration.

- **Surfaces in:** `tooling-assessment.md`.
- **Mitigation in Phase 2:** sequence enforcement adoption AFTER the
  bulk-move steps, not before. The brief flags this in Phase 2 item
  8 ("after the structure is in place but before the cleanup of
  imports") — the discovery confirms this is the correct order.

### R7 — `_optional_deps.py` is foundation-tier ★

Used by 7 modules across the package; whichever layer it lands in
must be importable from every layer above. Realistically `core`, but
the placement is constrained.

- **Surfaces in:** `orphan-candidates.md` (inbound count = 7,
  bucket = `unsure`).
- **Mitigation in Phase 2:** decide explicitly in the layer-charter
  step. Likely lives in `core/` and is imported from everywhere; if
  the rule says "core depends on nothing internal", `_optional_deps`
  itself must depend on nothing internal (verify that property in
  Phase 2).

### R8 — `__main__.py` placement ★

The `python -m icom_lan` entrypoint. May stay top-level for
discoverability, may move into `cli/`. Low-stakes decision.

- **Surfaces in:** `orphan-candidates.md` (bucket = `unsure`).
- **Mitigation in Phase 2:** trivial; pick one and document.

### R9 — `ARCHITECTURE.md` is stale ★

References ~4796 tests; actual count is 5210. Phase 5 already covers
refreshing this — no action in Phase 1 / 2.

---

## 10. Maintainer decisions required to unblock Phase 2

These need explicit answers before Phase 2 starts:

1. **Shim policy.** When a module moves, leave a one-liner re-export
   at the old path. Should it emit `DeprecationWarning`? *(Brief
   recommends staying silent; orchestrator concurs — tests would
   need updating to filter the warning, doubling the diff size for
   nothing. Default: silent shims, no warning.)*
2. **Tooling commit.** Confirm `import-linter` is the chosen tool.
   *(Orchestrator recommendation; agent C verified empirically.)*
3. **Test rewrite vs shim** for the 43 internal-symbol leaks. Prefer
   shim (zero test churn) or rewrite (tests stop reaching for
   privates)? *(Orchestrator recommendation: shim; revisit per-case
   only if a name clash in Phase 2 makes a shim impossible.)*
4. **Coordination with icom-lan-pro.** 29 sites — one short of the
   escalation threshold. Proceed without a sync, or send the
   downstream maintainer a heads-up before Phase 2? *(Orchestrator
   recommendation: ping with this discovery doc + planned end-state
   layer table once Phase 2 produces it; no coordination needed
   yet.)*

---

## 11. Exit criteria status

Per the brief's Phase 1 exit criteria:

- [x] Discovery doc committed to a branch (not main) — `refactor/modularization-discovery`.
- [ ] Maintainer has reviewed and signed off — **pending**.
- [x] Risk log items are either accepted, mitigated in plan, or escalated — see §9.

"Stop and ask" triggers:

- [x] No confirmed cycles (5 SCCs, all deferred-only). Not triggered.
- [x] icom-lan-pro usage 29 sites < 30. Not triggered.
- [x] No hidden side effects that prevent moving modules safely (the three flagged side effects are intentional and migrate-safe). Not triggered.

---

## Appendix — discovery artifact index

All under [`discovery-artifacts/`](./discovery-artifacts/):

| File | Source | Format |
|---|---|---|
| `discovery_graph.py` | orchestrator | Python (stdlib only) |
| `file-inventory.json` | `discovery_graph.py` | JSON |
| `import-graph.dot` | `discovery_graph.py` | DOT |
| `cycles.txt` | `discovery_graph.py` | text |
| `classify_cycle_edges.py` | orchestrator | Python (stdlib only) |
| `cycles-classified.md` | `classify_cycle_edges.py` | Markdown |
| `build_orphan_report.py` | orchestrator | Python (stdlib only) |
| `file-inventory.md` | `build_orphan_report.py` | Markdown (mirrors §1) |
| `init-snapshot.md` | `build_orphan_report.py` | Markdown |
| `orphan-candidates.md` | `build_orphan_report.py` | Markdown |
| `external-usage.md` | Agent B | Markdown |
| `test-inventory.md` | Agent B | Markdown |
| `tooling-assessment.md` | Agent C | Markdown |
| `tach-config-draft.toml` | Agent C | TOML |
| `importlinter-config-draft.ini` | Agent C | INI |

Every script is reproducible from the worktree:

```bash
# Run all three from repo root.
uv run python docs/plans/discovery-artifacts/discovery_graph.py
uv run python docs/plans/discovery-artifacts/classify_cycle_edges.py \
    docs/plans/discovery-artifacts/cycles.txt \
    docs/plans/discovery-artifacts/import-graph.dot \
    > docs/plans/discovery-artifacts/cycles-classified.md
uv run python docs/plans/discovery-artifacts/build_orphan_report.py
```
