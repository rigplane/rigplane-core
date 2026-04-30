# Top-level orphan candidates

Files directly under `src/icom_lan/` (not inside a subpackage), each tagged
with a *tentative* layer bucket from a filename + summary heuristic. Buckets
marked `unsure:<X>` mean the heuristic was not confident. **This is input to
Phase 2, not a final assignment.**

| Path | Inbound | Outbound | Bucket | Summary |
|---|---:|---:|---|---|
| `src/icom_lan/_audio_codecs.py` | 1 | 0 | `audio` | Pure-Python audio codec utilities. |
| `src/icom_lan/_audio_recovery.py` | 1 | 2 | `audio` | Audio snapshot/resume logic for IcomRadio reconnect scenarios. |
| `src/icom_lan/_audio_runtime_mixin.py` | 1 | 6 | `audio` | AudioRuntimeMixin — audio streaming methods extracted from CoreRadio. |
| `src/icom_lan/_audio_transcoder.py` | 3 | 1 | `audio` | Internal PCM <-> Opus transcoder utilities. |
| `src/icom_lan/audio_analyzer.py` | 1 | 0 | `audio` | Lightweight audio analyzer — realtime SNR estimation from PCM stream. |
| `src/icom_lan/audio_bridge.py` | 2 | 6 | `audio` | Audio bridge — bidirectional PCM bridge between radio and a system audio device. |
| `src/icom_lan/audio_bus.py` | 3 | 1 | `audio` | AudioBus — pub/sub distribution for radio audio streams. |
| `src/icom_lan/audio_fft_scope.py` | 1 | 2 | `audio` | Audio FFT Scope — derive IF panadapter from RX audio PCM stream. |
| `src/icom_lan/usb_audio_resolve.py` | 1 | 0 | `audio` | Resolve USB Audio devices associated with a serial CI-V port. |
| `src/icom_lan/cli.py` | 1 | 18 | `cli` | icom-lan CLI — command-line interface for Icom LAN control. |
| `src/icom_lan/command_map.py` | 18 | 0 | `commands` | CommandMap — frozen lookup for CI-V wire bytes by command name. |
| `src/icom_lan/command_spec.py` | 2 | 0 | `commands` | Command specification types for multi-protocol radio control. |
| `src/icom_lan/commander.py` | 3 | 2 | `commands` | classes: Priority, _QueueItem, IcomCommander |
| `src/icom_lan/_civ_rx.py` | 2 | 9 | `core` | CI-V receive pump and event dispatch for IcomRadio. |
| `src/icom_lan/_connection_state.py` | 4 | 0 | `core` | Radio-level connection state machine. |
| `src/icom_lan/_control_phase.py` | 1 | 7 | `core` | Auth/login FSM, token renewal, watchdog, and reconnect for IcomRadio. |
| `src/icom_lan/auth.py` | 1 | 0 | `core` | Authentication logic for the Icom LAN protocol. |
| `src/icom_lan/capabilities.py` | 9 | 0 | `core` | Unified capability constants and known-capability registry. |
| `src/icom_lan/civ.py` | 3 | 1 | `core` | CI-V event routing and request tracking utilities. |
| `src/icom_lan/discovery.py` | 1 | 3 | `core` | Serial port enumeration, candidate filtering, and multi-protocol radio discovery. |
| `src/icom_lan/env_config.py` | 2 | 0 | `core` | Environment variable configuration helpers for icom-lan. |
| `src/icom_lan/exceptions.py` | 20 | 0 | `core` | Custom exception hierarchy for icom-lan. |
| `src/icom_lan/protocol.py` | 0 | 1 | `core` | Packet parsing and serialization for the Icom LAN UDP protocol. |
| `src/icom_lan/transport.py` | 6 | 4 | `core` | Async UDP transport for the Icom LAN protocol. |
| `src/icom_lan/types.py` | 45 | 1 | `core` | Enums, dataclasses, and helper functions for the Icom LAN protocol. |
| `src/icom_lan/profiles.py` | 11 | 1 | `profiles` | Radio profile and capability matrix for runtime routing and guards. |
| `src/icom_lan/profiles_runtime.py` | 1 | 1 | `profiles` | Generic capability-aware radio profile system. |
| `src/icom_lan/rig_loader.py` | 4 | 5 | `profiles` | TOML rig config loader — parse, validate, and build runtime objects. |
| `src/icom_lan/_bounded_queue.py` | 5 | 0 | `runtime` | Minimal bounded async queue helper used by inter-task buffers. |
| `src/icom_lan/_bridge_metrics.py` | 1 | 0 | `runtime` | BridgeMetrics — structured audio bridge telemetry. |
| `src/icom_lan/_bridge_state.py` | 1 | 0 | `runtime` | Bridge connection state machine. |
| `src/icom_lan/_dual_rx_runtime.py` | 1 | 4 | `runtime` | DualRxRuntimeMixin — dual-receiver routing methods extracted from CoreRadio. |
| `src/icom_lan/_poller_types.py` | 2 | 0 | `runtime` | Shared command types and CommandQueue for radio pollers. |
| `src/icom_lan/_queue_pressure.py` | 2 | 0 | `runtime` | Queue pressure threshold for transport and poller coordination. |
| `src/icom_lan/_runtime_protocols.py` | 4 | 10 | `runtime` | Internal runtime host Protocols (P0 decomposition). |
| `src/icom_lan/_shared_state_runtime.py` | 1 | 3 | `runtime` | Shared state polling/cache helpers used by web and rigctld. |
| `src/icom_lan/_state_cache.py` | 6 | 0 | `runtime` | Shared radio state cache. |
| `src/icom_lan/_state_queries.py` | 2 | 1 | `runtime` | Shared state query list for populating RadioState. |
| `src/icom_lan/cw_auto_tuner.py` | 1 | 1 | `runtime` | CW Auto Tuner — FFT-based CW tone frequency detection. |
| `src/icom_lan/ic705.py` | 1 | 2 | `runtime` | IC-705 convenience helpers for data and packet-mode workflows. |
| `src/icom_lan/meter_cal.py` | 2 | 0 | `runtime` | Shared meter calibration helpers. |
| `src/icom_lan/proxy.py` | 1 | 0 | `runtime` | Transparent UDP relay proxy for Icom LAN protocol. |
| `src/icom_lan/radio.py` | 13 | 27 | `runtime` | IcomRadio — high-level async API for Icom transceivers over LAN. |
| `src/icom_lan/radio_initial_state.py` | 1 | 2 | `runtime` | Initial-state fetch orchestration for :class:`IcomRadio`. |
| `src/icom_lan/radio_protocol.py` | 19 | 5 | `runtime` | Abstract Radio Protocol — multi-backend radio control interface. |
| `src/icom_lan/radio_reconnect.py` | 1 | 4 | `runtime` | Watchdog and reconnect loops for :class:`IcomRadio`. |
| `src/icom_lan/radio_state.py` | 15 | 1 | `runtime` | RadioState — dual-receiver radio state model. |
| `src/icom_lan/radio_state_snapshot.py` | 1 | 2 | `runtime` | Snapshot/restore helpers for :class:`IcomRadio` state. |
| `src/icom_lan/radios.py` | 1 | 0 | `runtime` | Radio model presets with CI-V addresses and capabilities. |
| `src/icom_lan/startup_checks.py` | 4 | 1 | `runtime` | functions: assert_radio_startup_ready, wait_for_radio_startup_ready |
| `src/icom_lan/sync.py` | 0 | 5 | `runtime` | Synchronous (blocking) wrapper around :class:`~icom_lan.radio.IcomRadio`. |
| `src/icom_lan/_scope_runtime.py` | 1 | 6 | `scope` | ScopeRuntimeMixin — scope/waterfall methods extracted from CoreRadio. |
| `src/icom_lan/scope.py` | 8 | 1 | `scope` | Scope/waterfall frame assembly for Icom transceivers. |
| `src/icom_lan/scope_render.py` | 1 | 2 | `scope` | Scope/waterfall frame rendering for Icom transceivers. |
| `src/icom_lan/__main__.py` | 0 | 1 | `unsure` | Allow running as: python -m icom_lan |
| `src/icom_lan/_optional_deps.py` | 7 | 0 | `unsure` | Helpers for optional / heavy dependencies. |

## Bucket distribution

- `audio`: 9
- `cli`: 1
- `commands`: 3
- `core`: 12
- `profiles`: 3
- `runtime`: 23
- `scope`: 3
- `unsure`: 2

Total top-level orphan files: **56**.
