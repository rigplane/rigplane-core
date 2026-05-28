---
robots: noindex, follow
---

# rigplane — Project Documentation

## Goal

Create a Python library for direct control of transceivers through a shared
radio core:
- native Icom LAN backend (UDP, native Icom protocol)
- native Icom serial backend (USB CI-V + exported USB audio devices)
- native Yaesu CAT paths where RigPlane can provide richer state, audio, or diagnostics
- Hamlib-backed provider path for broader long-tail serial CAT coverage

RigPlane owns the public UX, state model, audio path, diagnostics, and
`rigctld`-compatible client surface. Native providers stay direct where that is
valuable; long-tail CAT coverage can use Hamlib underneath RigPlane's provider
boundary.

### Objectives
- Connect to Icom over network (authentication, keep-alive)
- Send/receive CI-V commands (frequency, mode, power, meters)
- Receive/transmit audio stream (PCM default, Opus optional)
- Simple Pythonic API (sync + async)
- Keep one `Radio` contract for API/CLI/Web/rigctld consumers
- Support IC-7610 first, then expand to other models/families via provider/profile architecture

### Non-goals (for now)
- Full wfview replacement
- Leaking Hamlib model IDs, command names, or raw error semantics into the Web UI/API
- Cross-platform USB/audio polish beyond macOS-first rollout

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                           rigplane                           │
│                                                              │
│  Consumers: API / CLI / Web / rigctld                       │
│                 │                                            │
│                 ▼                                            │
│      radio_protocol.Radio (+ capability protocols)           │
│                 │                                            │
│                 ▼                                            │
│            backends.factory.create_radio()                   │
│                 │                                            │
│                 ▼                                            │
│     CoreRadio (shared commander/state/civ routing)   │
│          ┌───────────────────────────────┴─────────────────┐ │
│          ▼                                                 ▼ │
│  Icom7610LanRadio                                Icom7610SerialRadio
│  - control/auth/keepalive (UDP)                  - serial session
│  - CI-V over UDP                                 - CI-V over USB serial
│  - LAN audio (Opus/PCM)                          - USB audio devices
└──────────┬───────────────────────────────────────────────┬───┘
           │                                               │
           ▼                                               ▼
   IC-7610 over LAN                                IC-7610 over USB
```

## Icom LAN Protocol (based on wfview research)

### Overview
Icom uses a proprietary UDP protocol for LAN connectivity. Not officially documented. Fully reverse-engineered by the wfview team.

### Ports
| Port | Purpose |
|------|---------|
| 50001 | Control — authentication, connection management |
| 50002 | CI-V Serial — CI-V command passthrough |
| 50003 | Audio — bidirectional audio stream (PCM default, Opus optional) |

### Connection Phases
1. **Discovery** — optional, searching for radios on the network
2. **Login** — sending credentials (username/password)
3. **Auth** — receiving token/confirmation
4. **Keep-alive** — periodic ping (~500ms), otherwise the radio drops the connection
5. **CI-V** — sending/receiving CI-V commands via UDP wrapper
6. **Audio** — audio streaming (PCM default, Opus optional; 8/16/24/48 kHz)
7. **Disconnect** — graceful shutdown

### Packet Structure
Each UDP packet has a fixed-format header (see `packettypes.h` in wfview):
- Packet length (2 bytes, LE)
- Packet type (2 bytes)
- Sequence number (2 bytes)
- Sender ID (4 bytes)
- Receiver ID (4 bytes)
- Payload (variable length)

### Key wfview Source Files (reference)
| File | Lines | Description |
|------|-------|-------------|
| `include/packettypes.h` | 684 | Packet structures, type constants |
| `src/radio/icomudpbase.cpp` | 585 | Base UDP: connection, keep-alive, retransmit |
| `src/radio/icomudphandler.cpp` | 690 | Main handler: login, auth, routing |
| `src/radio/icomudpcivdata.cpp` | 248 | CI-V data over UDP |
| `src/radio/icomudpaudio.cpp` | 303 | Audio streaming |
| `src/radio/icomcommander.cpp` | 3533 | CI-V commands (frequency, mode, meters, etc.) |
| `src/rigcommander.cpp` | 256 | High-level radio interface |
| **Total** | **~6300** | |

## Development Phases

### Phase 1 — Transport (MVP) ✅ COMPLETE
**Goal:** Establish UDP connection with the radio, complete authentication, maintain keep-alive.

- [x] Parse packet format from `packettypes.h`
- [x] Implement UDP transport (asyncio)
- [x] Discovery handshake (Are You There → I Am Here → Are You Ready)
- [x] Login + auth handshake
- [x] Token acknowledgement
- [x] Conninfo exchange (obtain CI-V/audio ports)
- [x] Dual-port architecture (control port 50001 + CI-V port 50002)
- [x] Keep-alive loop (ping + retransmit)
- [x] Graceful disconnect
- [x] Test: connect to IC-7610 at 192.168.55.40

**Result:** `radio.connect()` / `radio.disconnect()` work. ✅

### Phase 2 — CI-V Commands ✅ COMPLETE
**Goal:** Send and receive CI-V commands over the network connection.

- [x] Wrap CI-V in UDP packets (per `icomudpcivdata.cpp`)
- [x] Open CI-V data stream (OpenClose packet)
- [x] Filter waterfall/echo packets
- [x] Basic commands: get/set frequency, mode, power
- [x] Read meters: S-meter, SWR, ALC, power
- [x] PTT on/off
- [x] Test: read and set frequency on IC-7610

**Result:** `radio.get_frequency()`, `radio.get_mode()`, `radio.get_s_meter()` work. ✅

### Phase 3 — Audio Streaming ✅ COMPLETE
**Goal:** Receive and transmit audio.

- [x] Opus decode/encode (RX/TX)
- [x] PCM transcoder layer (high-level API)
- [x] Callback API for audio
- [x] Buffering (JitterBuffer) and flow control
- [x] Full-duplex audio
- [x] Audio auto-recovery after reconnect
- [x] Runtime audio stats API
- [x] Audio capability negotiation
- [x] CLI: `rigplane audio rx/tx/loopback`

**Result:** Full audio stack — Opus and PCM API, CLI, stats, auto-recovery. ✅

### Phase 4 — Polish & Publish ✅ COMPLETE
**Goal:** Production-ready library for PyPI.

- [x] Sync + async API
- [x] Autodiscovery of radios on the network
- [x] Multi-model support (IC-7610, IC-705, IC-7300, IC-9700)
- [x] CLI utility (`rigplane status`, `rigplane freq 14074000`)
- [x] Documentation + MkDocs site
- [x] PyPI publication (v0.8.0)

### Phase 5 — Hamlib NET rigctld ✅ COMPLETE
**Goal:** Drop-in rigctld replacement for WSJT-X, JS8Call, fldigi.

- [x] TCP server skeleton (asyncio)
- [x] MVP command set (f/F/m/M/t/T/v/V/s/S/l/q)
- [x] Read-only safety mode
- [x] Structured logging + guardrails
- [x] Golden protocol response suite (45 fixtures)
- [x] WSJT-X compatibility (--wsjtx-compat)
- [x] DATA mode semantics fix
- [x] CI-V desync fix + circuit breaker

### Phase 6 — Scope/Waterfall ✅ COMPLETE (v0.6.0)

### Phase 7 — Platform Foundation (M2) ✅ COMPLETE
**Goal:** Backend-neutral architecture for stable platform evolution.

- [x] Extract shared IC-7610 executable core (`CoreRadio`) with LAN compatibility wrapper
- [x] Introduce profile-driven model/capability abstraction (`RadioProfile`)
- [x] Establish backend factory/config wiring (`create_radio`, backend config objects)
- [x] Add serial CI-V link foundation + deterministic serial test matrix
- [x] Expand reliability matrix and stabilize connect/recovery behavior

### Phase 8 — IC-7610 USB Backend MVP (M3) ✅ COMPLETE
**Goal:** Complete IC-7610 serial backend (control + audio + scope) and wire all consumers.

- [x] `#144` Serial radio wrapper/session
- [x] `#145` USB audio driver
- [x] `#146` Scope/waterfall on serial with guardrails (live hardware validated, 2026-03-06)
- [x] `#147` CLI backend selection and serial/audio flags
- [x] `#148` Web backend-neutral integration
- [x] `#149` rigctld backend-neutral integration
- [x] `#151` Docs/migration/capability matrix (2026-03-06)

### Phase 9 — IC-7610 wfview Command Parity (M4) ✅ COMPLETE
**Goal:** Close the remaining IC-7610 command parity gap against wfview using a maintained command matrix and regression gate.

- [x] `#139` parity matrix + regression gate (`docs/parity/ic7610_command_matrix.json`)
- [x] `#130` DSP / level command family
- [x] `#131` operator toggles / status family
- [x] `#132` VFO / dual-watch / scanning family (10 commands)
- [x] `#133` memory + band-stacking family (6 commands)
- [x] `#134` repeater / tone family (4 commands)
- [x] `#135` system / configuration family (16 commands)
- [x] `#136` transceiver / RIT / TX status family (11 commands)
- [x] `#137` advanced scope controls
- [x] `#138` cross-surface exposure (API / CLI / Web / rigctld) — Phase A complete (49 Protocol methods + exemplar CLI); follow-up surfaces (Web UI, additional CLI, rigctld, docs) deferred as optional incremental work

### Phase 10 — Multi-Radio Expansion (M5) ✅ COMPLETE (2026-03-23)
**Goal:** Establish backend architecture and implementations for IC-705, IC-7300, IC-9700 radios using shared CoreRadio foundation.

#### M5.1 IC-705 Backend ✅ COMPLETE (2026-03-23)
- [x] Profile research (ic705.toml with full capabilities)
- [x] Ic705SerialRadio class (inherits CoreRadio)
- [x] Factory routing (model="IC-705" detection)
- [x] Test suite (5 tests: factory, profile, inheritance)
- **Blocker:** IC-705 hardware not yet procured

#### M5.2 IC-7300 Backend ✅ COMPLETE (2026-03-23)
- [x] Profile research (ic7300.toml with full capabilities)
- [x] Ic7300SerialRadio class (inherits CoreRadio)
- [x] Factory routing (case-insensitive model routing)
- [x] Test suite (5 tests: factory, profile, case-insensitive routing)
- **Blocker:** IC-7300 hardware not yet procured

#### M5.3 IC-9700 Backend ✅ COMPLETE (2026-03-23)
- [x] Profile research + TOML definition (ic9700.toml, CI-V addr 0xA2, dual-receiver)
- [x] Ic9700SerialRadio class (inherits CoreRadio)
- [x] Factory routing (model="IC-9700" detection, case-insensitive)
- [x] Test suite (6 tests: factory, profile, dual-receiver, case-insensitive, inheritance)
- **Blocker:** IC-9700 hardware not yet procured

#### Multi-Model Architecture Features ✅
- Factory.create_radio() routes by model parameter
- Profile-driven CI-V address resolution
- Shared command logic (CoreRadio base)
- Case-insensitive model matching
- Extensible pattern for future models
- Zero code duplication (drivers reused)
- 3365 tests passing (+16 multi-model tests)

### Current Status
**Package version in `pyproject.toml`: `0.18.0`.**
**Reliability integration backlog (items 1-13) completed on 2026-03-05.**
**Latest full regression:** green; test count maintained as test suite evolves (~4784 tests as of 2026-04-23).
- **M2 Platform Foundation (step #141):** extracted shared IC-7610 executable core (`CoreRadio`) with LAN compatibility wrapper (`IcomRadio`) and no behavior changes.
- **M2 profile abstraction (issue #119):** runtime `RadioProfile` matrix added for multi-model behavior; `model`/`capabilities` and receiver/cmd29 routing are now profile-driven with explicit unsupported-operation guards.
- **M3 serial scope guardrails (issue #146, 2026-03-06):** serial backend keeps the shared error contract in disconnected state (`ConnectionError` before low-baud guardrail evaluation), includes deterministic low-baud guardrail with explicit override (`allow_low_baud_scope` / `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD`), and now has dedicated serial integration scope profile/gating (`ICOM_SERIAL_DEVICE`, `ICOM_SERIAL_BAUDRATE`, `ICOM_SERIAL_RADIO_ADDR`) alongside serial-specific CI-V pacing (`ICOM_SERIAL_CIV_MIN_INTERVAL_MS`) while LAN scope behavior remains unchanged.
- **M3 CLI integration (issue #147, 2026-03-06):** unified CLI backend selection now routes through `create_radio(...)`, includes serial/audio flags, supports JSON audio-device listing, and preserves backward-compatible LAN defaults.
- **M3 web integration (issue #148, 2026-03-06):** web startup/runtime now stays on the shared factory/config path for both LAN and serial radios, removes backend-specific state pokes from `web/`, gates scope/audio behavior via runtime capability protocols, and adds serial-focused smoke/contract coverage so LAN-only assumptions are caught in CI.
- **M3 rigctld integration (issue #149, 2026-03-06):** rigctld startup now reuses the shared factory/config path for `--backend lan` and `--backend serial`, shares backend-provided state cache when available, prefers backend-native mode introspection via `radio_protocol.ModeInfoCapable` while falling back to the core `Radio.get_mode()/set_mode(str, ...)` contract, and adds serial TCP smoke coverage for read/write rigctld commands while keeping audit logging and circuit-breaker behavior unchanged.
- **M3 documentation (issue #151, 2026-03-06):** comprehensive IC-7610 USB serial backend setup guide (macOS-first), backend capability matrix (LAN vs Serial), migration/backward-compatibility section, troubleshooting for serial CI-V and USB audio, and critical hardware finding (`CI-V USB Port` must be `Link to [CI-V]`, not `[REMOTE]`) documented across guide/radios.md, guide/troubleshooting.md, radio-protocol.md, and new guide/ic7610-usb-setup.md.
- **M3 status:** complete (epic #152 closed-out).
- **M4 status:** complete (2026-03-22); all 134 IC-7610 parity commands implemented; Protocol interface exposure delivered (49 methods); optional surface expansion (Web UI, CLI, rigctld, docs) deferred as incremental follow-up work.
- **M5.1 IC-705 Multi-Radio Backend (2026-03-23):** IC-705 serial backend complete with Ic705SerialRadio class, profile-driven routing (ic705.toml, CI-V addr 0xA4), factory integration, and 5 new backend tests. Commit 2e10765. **Blocked on hardware procurement** (research complete).
- **M5.2 IC-7300 Multi-Radio Backend (2026-03-23):** IC-7300 serial backend complete with Ic7300SerialRadio class, case-insensitive model routing, factory update (dual-model support validated), and 5 new backend tests. Commit 01dfb1b. **Blocked on hardware procurement** (research complete).
- **M5.3 IC-9700 Multi-Radio Backend (2026-03-23):** IC-9700 serial backend complete with Ic9700SerialRadio class, dual-receiver support (receiver_count=2, unique to IC-9700), LAN-capable profile (ic9700.toml, CI-V addr 0xA2), factory routing with case-insensitive matching, and 6 new backend tests validating dual-receiver capability. **Blocked on hardware procurement** (research and profile complete).
- **Multi-model factory architecture (2026-03-23):** Factory.create_radio() now routes by model parameter: IC-7610 → Icom7610SerialRadio (default), IC-705 → Ic705SerialRadio, IC-7300 → Ic7300SerialRadio, IC-9700 → Ic9700SerialRadio. All backends inherit from CoreRadio (shared command logic). Profile-driven CI-V address resolution (0x80, 0xA4, 0x94, 0xA2). Extensible pattern for future models (IC-705 and IC-9700 are LAN-capable).
- **State contract unification (issue #301, 2026-03-17):** web HTTP/WS public state and the web runtime path now derive from canonical `RadioState` without a web-side `StateCache` runtime dependency; default `rigctld` reads are `RadioState`-first with only handler-local fallback/optimistic state, and default server startup no longer binds consumer layers to backend-shared `StateCache`/poller state.

### Phase 11 — M6 Productization (M6) 🚧 IN PROGRESS (3/4 CORE + ALL OPTIMIZATIONS COMPLETE)

**Goal:** Production-ready library with audio codec support, documentation, and performance optimization.
**Status**: Core tasks 3/4 complete (M6.1, M6.3, M6.P2); M6.2 research phase complete, implementation blocked on hardware testing.

#### M6.1 ulaw→pcm Audio Codec Decoder ✅ COMPLETE (2026-03-23)
- [x] Pure-Python ulaw→PCM16 decoder (`_audio_codecs.py`) with standard 256-entry lookup table
- [x] No external dependencies (zero new imports)
- [x] Integration into `AudioBroadcaster._relay_loop()` with graceful fallback
- [x] Radios with `ULAW_1CH` / `ULAW_2CH` codecs now stream correctly to web clients
- [x] 11 comprehensive unit tests covering all 256 byte values, edge cases, format verification
- [x] 3384 tests passing (+11 audio codec tests)
- **Result:** Web audio streaming now supports all Icom audio codecs; resolves issue with ulaw-returning radios

#### M6.2 Extended Response Protocol Support 🔍 IN PROGRESS
- [ ] Research complete: documented 5 possible interpretations and existing implementation status
- [ ] Finding: Current codebase already implements most response handling (134 IC-7610 commands, multi-frame scope/audio, profile-driven extended commands)
- [ ] Blockers: Awaiting hardware testing or clarification on specific gaps to address
- **Status:** Research phase (`docs/EXTENDED_PROTOCOL_RESEARCH.md`)

#### M6.3 Performance Analysis & Regression Tests ✅ COMPLETE (2026-03-23)
- [x] Comprehensive performance baseline (514 unit tests in 1.88s, 3.6ms median)
- [x] Full test suite profiling (3384 tests in ~79s, 23ms median)
- [x] Identified 5 optimization areas with ROI/effort analysis
- [x] Performance regression test suite (`test_performance_regressions.py`) with 7 tests covering:
  - CI-V frame parsing latency
  - BCD encoding performance
  - Frame building performance
  - End-to-end CI-V pipeline SLO validation
- [x] Documentation: `docs/PERFORMANCE.md` with SLO definitions and recommendations
- [x] Confirmed: Current performance already strong; pytest-xdist incompatible with asyncio
- **Result:** Established performance baselines, regression guards, and optimization roadmap
- **Regression testing:** Parity smoke profile `integration and ic7610_parity` covers baseline_core and advanced_scope lifecycle on LAN/serial backends; profiles defined in `tests/integration/conftest.py` with explicit markers (`@pytest.mark.ic7610_parity`) in regression test files

IC-7610 parity matrix (issue #139, 2026-03-06): 134 implemented, 0 partial, 0 missing

#### M6 Optimization Roadmap (Priority 2: Medium ROI, Medium Effort)
- [x] **M6.P2.1: Delta Encoding for Web State Updates** ✅ COMPLETE (2026-03-23)
  - DeltaEncoder module with efficient diff/patch logic
  - 10-50x payload reduction for state broadcasts (~2KB → ~50-100 bytes per update)
  - Full state refresh every 100 updates prevents client/server drift
  - 22 comprehensive unit tests covering all encoding/decoding paths
  - Integrated into WebSocketServer state broadcasting
  - **Result:** Reduced network bandwidth and improved web client responsiveness

- [x] **M6.P2.2: Audio Buffer Pooling** ✅ COMPLETE (2026-03-23)
  - AudioBufferPool: Thread-safe object pool for bytearray buffers
  - Pre-allocates buffers for common audio frame sizes (16kHz/48kHz mono/stereo at 20ms)
  - LIFO reuse strategy for cache locality
  - 15 comprehensive unit tests covering pool mechanics, thread safety, concurrent access
  - Performance: >50k acquire/release ops/sec, >30k ops/sec under concurrent load
  - Integrated into AudioBroadcaster with infrastructure for codec optimization
  - **Result:** Reduced GC pressure in high-frequency audio streaming paths

- [x] **M6.P2.3: Web Audio Streaming Profiling** ✅ COMPLETE (2026-03-24)
  - Comprehensive benchmark suite: 10 tests covering codecs, relay loop, full pipeline
  - **Results**: All operations exceed SLOs with 18-588× headroom
    - ulaw decode: 8.67µs latency, 18.84M samples/sec throughput
    - Frame encode: 0.17µs latency, 8.4M frames/sec throughput
    - Full pipeline: 25.5µs p50, no bottlenecks identified
  - Buffer pool efficiency: 99.5% allocation reduction in realistic streaming
  - **Documentation**: docs/AUDIO_STREAMING_PROFILE.md with detailed analysis
  - **Result:** Pipeline is production-ready; no optimizations needed

### Phase 12 — Dual VFO / Dual Receiver architecture overhaul (#708) 🚧 IN PROGRESS

**Goal:** Public `Radio` protocol expresses `Transceiver → Receiver → VFO` unambiguously across IC-7610, IC-9700, IC-7300, IC-705 and FTX-1, with no model-specific branches at call sites. Audit: `.claude/workflow/dual-vfo-audit.md`; architecture: `.claude/architecture/protocol.md` "Receiver tier"; schema: `rigs/_schema_v2.md`. Landed foundation: #709 (`VfoSlotState` + per-receiver `active_slot` on `ReceiverState`), #710 (split TOML `swap_ab`/`equal_ab` vs `swap_main_sub`/`equal_main_sub` with legacy `swap`/`equal` deprecation), #711 (`ReceiverBankCapable` + `VfoSlotCapable` runtime-checkable Protocols), #713 (IC-9700 profile rewritten as `scheme = "main_sub"` with empty `cmd29.routes` per wfview `HasCommand29=false`). Pending: #712 `TransceiverBankCapable` for FTX-1, plus the phase 2–4 backend, frontend and rigctld wiring tracked under epic #708.

### Reliability Test Expansion (2026-03-05)
- Added extended integration coverage scaffolding for:
  - transport sequence wrap-around and ACK mixed stress,
  - session longevity/contention/readiness transitions,
  - control API roundtrips (DATA/RF/AF/squelch/NB/NR/IP+/state),
  - PCM audio path and scope lifecycle,
  - negative auth/connect paths and legacy script migration to pytest.
- Added regression matrix gate for shared-core LAN + serial-ready architecture:
  - backend-agnostic contract tests (LAN fixture + deterministic serial mock fixture),
  - deterministic serial framing/stability unit tests (partial frames/timeouts/overflow),
  - USB audio driver unit tests (selection/lifecycle/error paths),
  - web/rigctld smoke tests on serial mock backend to guard against LAN-only assumptions.
- Added production `SerialCivLink` driver for IC-7610 USB CI-V with robust FE FE ... FD framing

---

## Phase 12 — M7: Post-Productization (Future)

**Goal:** Expand platform support, enhance ecosystem, and plan long-term direction.

### M7 Planning (2026-03-24)

**Scope Options (TBD based on priorities):**

#### Option A: Hardware Expansion (Medium ROI, High Effort)
- **M7.1**: Complete IC-705 hardware validation (requires transceiver procurement ~$1k)
  - Serial backend testing on real IC-705
  - LAN backend testing on real IC-705
  - Command parity verification (CI-V 0xA4)
- **M7.2**: Complete IC-7300 hardware validation (requires transceiver ~$2k)
  - Serial-only backend (CI-V 0x94)
  - Command coverage analysis
- **M7.3**: Complete IC-9700 hardware validation (requires transceiver ~$4k)
  - LAN + serial backend (CI-V 0xA2)
  - Dual-receiver support testing

**Blocker:** Hardware procurement required; currently all multi-model backends are code-complete but untested

#### Option B: Feature Expansion (High ROI, Medium Effort)
- **M7.F1**: TX audio streaming support
  - Microphone capture integration
  - Audio encoding (PCM16 → Opus/ulaw)
  - Round-trip latency optimization
- **M7.F2**: Extended CI-V command support
  - Voice recorder control
  - Memory channel management
  - Band stacking
- **M7.F3**: Transceiver discovery & auto-connect
  - mDNS/Bonjour discovery
  - Auto-detect credentials from network
  - Connection profiling

#### Option C: Quality & Ecosystem (High ROI, Low Effort)
- **M7.Q1**: PyPI release preparation
  - Package versioning strategy
  - Changelog generation
  - Release notes automation
- **M7.Q2**: Documentation expansion
  - User guide (how to connect, basic operations)
  - API reference (auto-generated from docstrings)
  - Troubleshooting guide
- **M7.Q3**: Community engagement
  - Example applications (CLI, web UI, integrations)
  - Contribution guidelines
  - Issue templates & CI/CD setup

#### Option D: Infrastructure (Low ROI, High Effort)
- **M7.I1**: Performance optimization tier 2
  - Async codec operations (thread pool)
  - Connection pooling for multi-radio scenarios
  - Advanced caching strategies
- **M7.I2**: Cross-platform support
  - Windows USB audio driver integration
  - Linux serial device handling
  - CI/CD matrix for all platforms
- **M7.I3**: GUI application
  - Desktop app with PyQt/Tkinter
  - Real-time frequency/mode display
  - Meter visualization
  - Audio monitoring

### M7 Recommendation

**Start with Option C (Ecosystem/Quality)** before hardware expansion:
1. Release v0.12.0 to PyPI (v0.11.0 currently released)
2. Expand documentation for current hardware (IC-7610 LAN + serial)
3. Create example integrations
4. Gather community feedback
5. Then use feedback to prioritize Option A/B for next iteration

**Timeline**: Plan Option C for 2-3 weeks, then reassess based on user demand and hardware availability.

**Decision Point**: After M7 planning → decide on hardware procurement (Option A) vs. feature expansion (Option B) based on:
- Community interest in multi-radio support
- Budget constraints for hardware
- Team capacity for feature development
  recovery, collision/abort handling, timeout/overflow guardrails, writer backpressure handling,
  and optional dependency guard (`pip install rigplane[serial]`).
- Added production `UsbAudioDriver` for IC-7610 serial backend with deterministic device probe/
  selection (auto-detect + explicit RX/TX overrides), RX/TX lifecycle guardrails, actionable
  optional dependency errors (`sounddevice`/`numpy`), and serial-audio contract coverage for web
  audio channel + bridge flows.

## Test Equipment

- **Icom IC-7610** at `192.168.55.40`
- LAN ports: 50001 (control), 50002 (CI-V), 50003 (audio)
- USB path: CI-V serial device + exported RX/TX audio devices
- Local development host on the same LAN (IP redacted)

## License Notes

- wfview: **GPLv3** — used only as reference for understanding the protocol
- Our code: **MIT** — clean independent implementation, not copy-paste
- We don't copy wfview code, only study the packet format and protocol logic
- This is legal: protocol reverse engineering for interoperability is protected by law (EU Directive 2009/24/EC, US DMCA interoperability exception)
