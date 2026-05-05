# rigplane — High-Level Architecture

**Краткое описание:** мульти-вендорная Python-библиотека + Web UI для управления любительскими трансиверами — Icom (CI-V over LAN/USB), Yaesu (CAT) и совместимыми (Kenwood-style CAT).

---

## Layered package structure

`src/rigplane/` is organised into 11 layered Python packages with
explicit `import-linter`-enforced boundaries. Higher layers depend on
lower ones; siblings are independent. See
[`docs/plans/2026-04-29-modularization-plan.md`](docs/plans/2026-04-29-modularization-plan.md)
§1 for the full layer matrix and `LAYER.md` inside each package for
charter, public API, and forbidden patterns.

```
┌────────────────────────────────────────────────────────────────────┐
│ cli/                       — Command-line entrypoints              │
├────────────────────────────────────────────────────────────────────┤
│ web/        rigctld/       — UI servers (siblings, independent)    │
├────────────────────────────────────────────────────────────────────┤
│ backends/                  — Factory + per-radio assembly          │
├────────────────────────────────────────────────────────────────────┤
│ runtime/                   — IcomRadio + state + mixins + pollers  │
├────────────────────────────────────────────────────────────────────┤
│ profiles/   audio/         — Rig profiles · Audio subsystem        │
├────────────────────────────────────────────────────────────────────┤
│ commands/   scope/   dsp/  — CI-V builders · scope · DSP pipeline  │
├────────────────────────────────────────────────────────────────────┤
│ core/                      — types, transport, civ, contracts      │
└────────────────────────────────────────────────────────────────────┘
```

**Rules of thumb (CLAUDE.md "Layer boundaries" section is canonical):**

- Adding a new radio backend → conform to relevant Capability Protocols
  (`AudioCapable`, `StatePollable`, `RigctldRoutable`, `UsbAudioCapable`,
  …) in `core.radio_protocol`. Zero upper-layer changes if Protocols
  are honoured.
- New cross-layer imports must respect the matrix; `import-linter`
  catches violations at CI.
- The `Radio` Protocol (in `core.radio_protocol`) is the stable public
  contract surfaced via `rigplane` Tier 1; capability protocols
  (`*Capable`, `StatePollable`, `StatePoller`, `RigctldRoutable`,
  `UsbAudioCapable`) drive `isinstance`-based feature detection — see
  [`docs/plans/2026-04-29-modularization-plan.md`](docs/plans/2026-04-29-modularization-plan.md)
  §2 and `docs/api/public-api-surface.md`.

`import-linter` (config at repo root `.importlinter`, run via
`uv run lint-imports`) enforces one layered contract plus three
sibling-independence contracts (`web`⊥`rigctld`,
`profiles`⊥`audio`, `commands`⊥`scope`⊥`dsp`).

---

## Data Flow: TOML → Web UI

### 1️⃣ **TOML файл** (`rigs/ic7610.toml`)

Декларативное описание возможностей радио:

```toml
[radio]
id = "icom_ic7610"
model = "IC-7610"
civ_addr = 0x98
receiver_count = 2
has_lan = true

[capabilities]
dual_rx = true
scope = true
nb = true
nr = true
digisel = true

[modes]
values = ["USB", "LSB", "CW", "AM", "FM", "RTTY"]

[filters]
values = ["FIL1", "FIL2", "FIL3"]

[commands]
get_s_meter_sql_status = [0x15, 0x01]  # Override IC-7610 command
```

**Зачем TOML?**
- Добавить новое радио = написать TOML файл (не нужен Python код)
- Централизация различий между моделями (IC-7610 vs IC-7300)
- Command overrides (разные wire bytes для одних и тех же функций)

---

### 2️⃣ **Rig Loader** (`src/rigplane/profiles/rig_loader.py`)

Парсит TOML → валидирует → создаёт runtime объекты:

```python
from rigplane.profiles.rig_loader import load_rig
# (`from rigplane.rig_loader import load_rig` also still works via shim)

rig = load_rig("rigs/ic7610.toml")
profile: RadioProfile = rig.to_profile()      # Runtime profile
cmd_map: CommandMap = rig.to_command_map()    # Command overrides
```

**Выходные объекты:**
- `RadioProfile` — capabilities, modes, filters, band stack, freq ranges
- `CommandMap` — словарь wire bytes для команд (используется в `commands/`)

---

### 3️⃣ **Radio API** (`src/rigplane/runtime/radio.py`)

Высокоуровневый async API:

```python
async with IcomRadio("192.168.1.100", username="u", password="p") as radio:
    freq = await radio.get_frequency()            # → commands.get_freq()
    await radio.set_mode("USB")                   # → commands.set_mode()
    s_meter = await radio.get_s_meter()           # → commands.get_s_meter()
```

**Под капотом:**
- `Commander` queue — сериализация CI-V команд (одна за раз)
- `StateCache` — TTL-кэш для снижения нагрузки на радио
- Retry logic + timeout handling
- Command29 routing для dual-receiver (MAIN/SUB)

**Scope API:**
```python
def on_scope_data(data: bytes):
    print(f"Spectrum frame: {len(data)} bytes")

await radio.enable_scope(on_scope_data)
```

**Audio API:**
```python
await radio.start_audio_rx()
audio_frame = await radio.recv_audio()  # Opus/PCM bytes
```

---

### 4️⃣ **Commands Layer** (`src/rigplane/commands/`)

**134 функции-builders** для CI-V команд:

```python
# Builder
frame = get_frequency(to_addr=0x98, from_addr=0xE0)
# → b'\xfe\xfe\x98\xe0\x03\xfd'

# Parser
freq_hz = parse_frequency_response(b'\x00\x40\x07\x14')
# → 14074000 (14.074 MHz, BCD encoded)
```

**Command Map routing:**
```python
# IC-7610 default
get_s_meter_sql_status(to_addr=0x98)
# → (0x15, 0x01)

# IC-7300 override (из TOML)
cmd_map = CommandMap({"get_s_meter_sql_status": (0x16, 0x43)})
get_s_meter_sql_status(to_addr=0x94, cmd_map=cmd_map)
# → (0x16, 0x43)  # Использован override
```

**Command29 wrapper** (dual-receiver):
```python
# Main receiver
get_frequency(receiver=RECEIVER_MAIN)  # → 0x07 0xD0 prefix

# Sub receiver
get_frequency(receiver=RECEIVER_SUB)   # → 0x07 0xD1 prefix
```

---

### 5️⃣ **Backend Layer** (Transport)

**LAN Backend** (`src/rigplane/backends/icom7610/`):
- UDP port 50001 → control (auth, token renewal)
- UDP port 50002 → CI-V commands
- UDP port 50003 → audio (Opus/PCM)

**Serial Backend:**
- USB serial → CI-V commands (19200 baud)
- USB audio device → CoreAudio (macOS IORegistry matching)

**Connection FSM:**
```
IDLE → AUTH → TOKEN_RENEW → PORTS_READY → AUDIO_NEG → READY
```

---

### 6️⃣ **Web Server** (`src/rigplane/web/server.py`)

**HTTP API:**
```
GET /api/v1/capabilities
  → {model, modes, filters, capabilities[], freq_ranges[]}

GET /api/v1/state
  → {main:{freq,mode,filter,meters}, sub:{...}, ptt, split, ...}
     124 полей, 200ms polling
```

**WebSocket channels:**
```
/api/v1/ws          → state_update broadcasts + control commands (set freq/mode/power)
/api/v1/scope       → binary spectrum data (WebAssembly decoder)
/api/v1/audio       → Opus audio stream (RX/TX)
```

**RadioPoller** (`radio_poller.py`):
- 200ms таймер → `get_all_state()`
- Собирает 30+ команд (freq, mode, meters, toggles)
- DeltaEncoder → delta-encoded state_update events over WS
- HTTP /api/v1/state remains as fallback (initial load, offline recovery)

---

### 7️⃣ **Frontend** (`src/rigplane/web/static/`)

**Svelte 5 + TypeScript**

**State sync:**
```typescript
// WebSocket state_update (delta-encoded, ~50ms latency)
ws.on('message', (event) => {
  if (event.type === 'state_update') {
    state = applyDelta(state, event.data);
  }
});

// Derived UI state (reactive Svelte stores)
$: freqMhz = state.main.freqHz / 1e6;
$: modeLabel = state.main.mode;
```

**Command dispatch:**
```typescript
// WebSocket send
ws.send(JSON.stringify({
  channel: 'control',
  command: 'set_frequency',
  args: { freq_hz: 14074000 }
}));
```

**Components** (organized under `components-v2/`):
- `layout/` — `RadioLayout.svelte` (desktop), responsive frame
- `panels/` — `VfoPanel.svelte`, `MetersDockPanel.svelte`, `ControlPanel.svelte` (sliders, toggles)
- `vfo/` — dual-receiver VFO UI with bridge controls
- `display/` — meters, indicators, DX cluster
- `controls/` — buttons, switches, mode/filter selectors
- `wiring/` — state-adapter + command-bus (adapter layer per CLAUDE.md)
- `theme/` — skin registry, visual system

**State management:**
- Server state = single source of truth (via WS state_update)
- FrontendRuntime singleton + Svelte stores (see CLAUDE.md frontend layering)
- Pending actions tracked locally for optimistic UI
- No Redux/Vuex — delta-encoded WS + command dispatch

---

## Ключевые паттерны

### 🔹 Data-driven rig profiles
- TOML файл = source of truth для нового радио
- Никаких hardcoded if/else в коде
- Command overrides через `CommandMap`

### 🔹 Protocol abstraction
- `Radio` protocol — backend-agnostic API (in `core.radio_protocol`)
- Capability protocols (`AudioCapable`, `ScopeCapable`,
  `MetersCapable`, `LevelsCapable`, `StatePollable`/`StatePoller`,
  `RigctldRoutable`, `UsbAudioCapable`, …) — `isinstance`-based
  feature detection so upper layers stay backend-agnostic; see
  `docs/plans/2026-04-29-modularization-plan.md` §2 and
  `docs/api/public-api-surface.md`
- LAN vs Serial — одинаковый high-level API

### 🔹 Commander queue
- Сериализованные CI-V команды (одна за раз)
- Retry + timeout + deduplication
- Pacing (1ms min gap between commands)

### 🔹 State push over WebSocket
- Server broadcasts delta-encoded state_update events over /api/v1/ws (~200ms interval)
- DeltaEncoder reduces bandwidth vs full-state JSON
- HTTP /api/v1/state kept as fallback (initial sync, offline recovery)
- Lower latency (~50ms vs 200ms poll) + reduced server load

### 🔹 Zero external dependencies
- Чистый Python stdlib (asyncio, socket, struct)
- Web UI = встроенный HTTP сервер (без Flask/FastAPI)
- WebSocket = RFC 6455 реализация (без библиотек)

---

## Примеры Flow

### 📡 **Set Frequency через Web UI**

```
Browser click "14.074" button
  ↓
WebSocket send: {command: "set_frequency", args: {freq_hz: 14074000}}
  ↓
ControlHandler.handle_set_frequency()
  ↓
radio.set_frequency(14074000)
  ↓
Commander queue: set_freq(14074000, cmd_map=profile.cmd_map)
  ↓
commands.set_frequency(14074000) → build CI-V frame
  ↓
Transport.send_civ(frame)
  ↓
UDP socket → radio IP:50002
  ↓
Radio executes, sends ACK
  ↓
Next poll (200ms): GET /api/v1/state
  ↓
Browser updates UI: freq = 14.074 MHz
```

### 📊 **Spectrum Data Flow**

```
radio.enable_scope(callback=on_scope_data)
  ↓
Send scope_on CI-V command (0x27 0x10 0x01)
  ↓
Radio starts streaming UDP packets (port 50002)
  ↓
_civ_rx.py: parse 0x27 0x14 frames
  ↓
Callback → ScopeHandler.on_scope_data()
  ↓
WebSocket broadcast → /api/v1/scope clients
  ↓
Browser: WebAssembly decoder → canvas render
```

### 🎛️ **TOML → Runtime**

```
rigs/ic7610.toml
  ↓
rig_loader.load_rig() → validate schema
  ↓
RigConfig dataclass
  ↓
RigConfig.to_profile() → RadioProfile
  ↓
RadioProfile passed to IcomRadio(profile=...)
  ↓
Used for:
  • Capability checks (if "scope" in profile.capabilities)
  • Mode/filter lists (frontend dropdown)
  • Command overrides (cmd_map in `commands/`)
  • Band stack (BSR codes)
```

---

## Точки расширения

### ✅ Добавить новое радио
1. Создать `rigs/ic9700.toml` (скопировать шаблон)
2. Заполнить capabilities, modes, filters
3. Добавить command overrides (если нужны)
4. Готово — библиотека автоматически поддержит модель

### ✅ Добавить новую команду
1. Добавить builder в `commands/<domain>.py`: `def get_new_feature()`
2. Добавить parser: `def parse_new_feature_response()`
3. Добавить метод в `IcomRadio`: `async def get_new_feature()`
4. Добавить в Web API handlers
5. Добавить UI компонент в Svelte

### ✅ Добавить новый transport
1. Реализовать `CivTransport` protocol
2. Добавить backend config (`WifiBackendConfig`)
3. Зарегистрировать в `create_radio()`

---

## Тестирование

- **5230 unit tests** collected (5218 passed, 2 skipped, 9 xfailed,
  1 xpassed) on `main` after epics #1283 (modularization) +
  #1322 (capability protocols)
  - Command builders/parsers, protocol roundtrips
  - Rig profile validation (TOML schema)
  - Web API (HTTP + WebSocket)
- **Mock radio classes** для integration tests (без hardware)
- **FakeAudioBackend** for audio pipeline tests
- **Full test suite enforced** before commits (see CLAUDE.md)

---

## Документация

- `docs/guide/` — user guides (installation, quickstart, troubleshooting)
- `docs/plans/` — architecture decisions, ADRs
- `docs/api/` — API documentation (CI-V protocol, WebSocket schema)
- `README.md` — project overview + API examples
- `ARCHITECTURE.md` — (этот файл) high-level overview
- `CLAUDE.md` — internal: workflow, testing, git conventions

---

**Итого:** TOML профили → rig_loader → Radio API → Commands layer → CI-V transport → Web Server → Frontend (Svelte). Каждый уровень независимый, с чёткими boundaries.
