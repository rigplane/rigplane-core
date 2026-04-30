# File inventory

Every `.py` file under `src/icom_lan/`, ordered by current location, with
a one-line responsibility summary derived from the module docstring (or
the top-level class/function names where no docstring is present).

Rendered from [`file-inventory.json`](./file-inventory.json) by
[`build_orphan_report.py`](./build_orphan_report.py).

## top-level (`src/icom_lan/`) — 57 files

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

## `src/icom_lan/audio/` (and below) — 8 files

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

## `src/icom_lan/backends/` (and below) — 27 files

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

## `src/icom_lan/commands/` (and below) — 21 files

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

## `src/icom_lan/dsp/` (and below) — 8 files

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

## `src/icom_lan/rigctld/` (and below) — 11 files

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

## `src/icom_lan/web/` (and below) — 19 files

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
