# Roadmap

## Completed ✅

### Core Protocol & Architecture
- [x] UDP LAN protocol (control/CI-V/audio ports)
- [x] USB serial backend (IC-7610 validated)
- [x] CI-V command layer with wfview-style fire-and-forget
- [x] Async API (asyncio)
- [x] Commander queue (serialized, paced, retry, dedupe)
- [x] Abstract Radio Protocol (multi-radio architecture)
  - [x] `AudioCapable`, `ScopeCapable`, `DualReceiverCapable` protocols
- [x] Network discovery
- [x] Session management (connect/reconnect/soft-reconnect/disconnect)
- [x] Zero external dependencies (stdlib only)

### Audio (issues #1-#10)
- [x] PCM transcoder layer
- [x] RX high-level API (`start_audio_rx_pcm`)
- [x] TX high-level API (`start_audio_tx_pcm`, `push_audio_tx_pcm`)
- [x] CLI audio subcommands (`rx`, `tx`, `loopback`)
- [x] E2E tests for PCM API and CLI
- [x] Runtime audio stats (`get_audio_stats`)
- [x] Auto-recover audio streams after reconnect (#7)
- [x] Capability negotiation UX + `rigplane audio caps`
- [x] Task-oriented docs/recipes
- [x] API naming consistency + deprecation plan
- [x] AudioBus pub/sub (v0.12.0, #106)
- [x] Virtual audio bridge (BlackHole/Loopback, v0.12.0)
- [x] Browser audio TX (Opus, v0.9.0)

### Hamlib NET rigctld (issues #16-#22, #27, #32)
- [x] TCP server skeleton (`asyncio.start_server`)
- [x] MVP command set (f/F/m/M/t/T/v/V/s/S/l/q + long-form)
- [x] Read-only safety mode (`--read-only`, RPRT -22)
- [x] Structured logging + guardrails (max clients, idle timeout, OOM guard)
- [x] Golden protocol response suite (45 fixtures)
- [x] TCP wire integration tests
- [x] WSJT-X/rigctl setup docs
- [x] `--wsjtx-compat` DATA mode pre-warm
- [x] DATA mode semantics fix (PKT*, RTTY/PKTRTTY)
- [x] CI-V desync fix + state cache + circuit breaker (#27)

### Web UI (v0.9.0–v0.11.0)
- [x] Spectrum/waterfall display (real-time scope data)
- [x] Full control panel (freq/mode/filter/power/meters)
- [x] Band selector (160m–10m one-click)
- [x] Dual-receiver display (MAIN/SUB state, v0.10.0)
- [x] VFO swap (Main↔Sub, v0.11.0)
- [x] Per-receiver controls (ATT/PRE/NB/NR/DSEL/IP+)
- [x] AF/RF/Squelch sliders
- [x] Meters (S-meter, SWR, ALC, Power, Vd, Id)
- [x] Browser audio RX/TX (Opus codec)
- [x] REST API (`/api/v1/state`, `/api/v1/bridge`)
- [x] DX Cluster integration (#108)
  - [x] Telnet client + spot overlay on waterfall
  - [x] Click-to-tune
  - [x] Deduplication (call+freq)
  - [x] Modal badge

### Dual-Receiver Support (#92, v0.11.0)
- [x] VFO Swap (Main↔Sub) — `0x07 0xB0`
- [x] cmd29 receiver routing for per-receiver commands
- [x] Receiver-aware frontend (15 per-receiver calls)
- [x] SUB scope switching (MAIN/SUB badge, auto-fallback)
- [x] Bidirectional sync (active receiver, Dual Watch)
- [x] Freq/mode on SUB via VFO-switch pattern

### IC-7610 Command Parity (Epic #140, v0.11.0 COMPLETE)
#### All command families complete:
- [x] #130: DSP level controls (APF/NR/PBT/NB/filter/AF-mute)
- [x] #131: Operator toggles/status (AGC/VOX/ANF/compressor/break-in)
- [x] #132: VFO/dual-watch/scanning
- [x] #133: Memory and band-stacking
- [x] #134: Repeater/tone (tone+TSQL)
- [x] #135: System/config (antenna, CI-V options, mod routing, time)
- [x] #136: Transceiver/RIT/TX status
- [x] #137: Advanced scope controls (center/during-TX/fixed-edge)
- [x] #138: Expose parity commands across API/CLI/Web
- [x] #139: Command parity matrix test+docs gate

**Parity status** (2026-03-22):
- **134/134 wfview commands implemented (100%)**
- Zero partial implementations
- Zero missing commands
- Known hardware limitation: #153 (TX Freq Monitor not hardware-supported on IC-7610)

### Testing & Quality
- [x] 5073+ unit/integration/mock tests (comprehensive coverage)
  - Full test suite across all backends and integrations
- [x] Golden protocol response suite (45+ fixtures)
- [x] Integration tests with real IC-7610
- [x] Contract tests for IC-705, IC-7300, IC-9700 backends
- [x] Soak tests and reliability validation
- [x] CI parity matrix gate
- [x] Type annotations (`py.typed`)

### Documentation
- [x] MkDocs site (rigplane.dev)
- [x] Protocol internals deep-dive
- [x] CLI reference
- [x] API reference
- [x] IC-7610 USB serial setup guide
- [x] Security docs
- [x] Session reports in `docs/sessions/` (RAG-indexed)
- [x] Backend architecture docs

### v0.12.0 → v0.18.0 Milestones (Completed)
- [x] Epic #140 (IC-7610 Command Parity) — all 134/134 commands complete
- [x] v0.13.0 through v0.18.0 releases shipped
- [x] Web UI refactoring (runtime layers, adapter pattern, skin registry)
- [x] Frontend architecture modernization (#1024–#1029)
- [x] Security hardening (CSP, token handling, read-only guards)
- [x] Bug fixes and reliability improvements

## Future (Post-Parity)

### Multi-Radio Support (M5, v0.11.0 COMPLETE)
- [x] IC-705 backend (serial + LAN-capable, CI-V 0xA4, single-receiver)
- [x] IC-7300 backend (serial only, CI-V 0x94, single-receiver)
- [x] IC-9700 backend (serial + LAN-capable, CI-V 0xA2, dual-receiver)
- [x] Factory model-based routing with case-insensitive matching
- [x] Profile-driven radio abstraction (#119) — ic705.toml, ic7300.toml, ic9700.toml
- [x] 3365 tests passing (+16 multi-model tests)
- [ ] Hardware validation (IC-705, IC-7300, IC-9700 procurement pending)

### Protocol Completeness
- [ ] Mock Radio Server — UDP emulator for CI without hardware
- [ ] Extended response protocol (per-session `extended_mode`)
- [ ] rigctld: `\set_level` (RFPOWER)
- [ ] rigctld: RIT/XIT (`J`/`Z`)
- [ ] rigctld: Tuner control
- [ ] rigctld: `\dump_state` protocol v1

### Web UI & Integrations
- [ ] Web UI: frequency memory presets
- [ ] Web UI: CW keyer interface
- [ ] Web UI: remote PTT/foot-switch emulation
- [ ] Async event/notification stream (S-meter polling, band changes)
- [ ] WSJT-X/JS8Call/fldigi full integration testing
- [ ] Logging integration (ADIF export)

### Hardening & Reliability
- [ ] Integration reliability backlog (#129)
- [ ] Connection state machine refactor
- [ ] Command retry strategies
- [ ] Graceful degradation on partial failures

### Long-term (Research)
- [ ] Universal Radio Bridge (RPi) — USB radios → LAN control
- [ ] Rust core prototype (performance spike)
- [ ] Windows/Linux binary releases
- [ ] Mobile app (React Native?)

---

*Updated: 2026-04-23 (v0.18.0)*
**Current version: 0.18.0 — All milestones complete. See [CHANGELOG.md](CHANGELOG.md) for v0.12.0–v0.18.0 details.**
