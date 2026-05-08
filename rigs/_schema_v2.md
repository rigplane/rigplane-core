# Rig Configuration Schema v2

Declarative rig profiles for rigplane. Goal: **zero radio-dependent logic in code**.
Adding a new radio = writing one TOML file.

## Architecture: 4 Layers

```
Layer 0: PROTOCOL     — how to talk to the radio (CI-V / Kenwood CAT / SDRplay API / ...)
Layer 1: CAPABILITIES — what the radio can do (feature tags)
Layer 2: CONTROLS     — how each feature looks in the UI (style, values, ranges)
Layer 3: RULES        — how features interact (constraints, mutex, dependencies)
```

Plus cross-cutting sections: METERS, VFO, MODES, BANDS, TONES, SPECTRUM.

---

## Layer 0: Protocol

```toml
[radio]
id = "unique_id"             # Internal identifier
model = "IC-7610"            # Display name
manufacturer = "Icom"        # "Icom" | "Yaesu" | "Kenwood" | "Xiegu" | "Lab599" | "SDRplay" | ...
civ_addr = 0x98              # CI-V address (CI-V protocol only)
default_baud = 115200        # Serial baud rate
receiver_count = 2           # 1 or 2 — number of simultaneous receivers in one transceiver
transceiver_count = 1        # 1 or 2 — number of independent transceivers (defaults to 1;
                             # set to 2 for dual-transceiver rigs like Yaesu FTX-1)
has_lan = true
has_wifi = false
has_ethernet = false

[protocol]
type = "civ"                 # Protocol type (see below)
variant = "standard"         # Protocol variant/extensions
```

### Audio Policy

```toml
[audio]
codec_preference = ["PCM_2CH_16BIT", "PCM_1CH_16BIT"]  # RX codec names from AudioCodec
tx_codec = "PCM_1CH_16BIT"                             # TX codec name from AudioCodec
default_sample_rate_hz = 16000
supported_sample_rates_hz = [8000, 12000, 16000, 24000, 48000]
sample_rate_by_codec = { PCM_2CH_16BIT = 16000, PCM_1CH_16BIT = 16000 }
browser_rx_transport = "auto"                          # auto | pcm | opus
browser_rx_transcode_to_opus = true                    # Browser consumer policy only
```

Codec names must match `AudioCodec`. Sample rates must be positive supported
audio rates from `8000`, `12000`, `16000`, `24000`, or `48000`. For direct Icom
LAN profiles, Opus must not be used as the radio-native default; browser Opus is
only a consumer/web transport policy.

### Protocol Types

| Type | Format | Radios | Notes |
|------|--------|--------|-------|
| `civ` | Binary frames, `FE FE ... FD` | Icom, Xiegu | Standard CI-V |
| `civ` + variant `yaesu_enhanced` | Binary CI-V + Yaesu extensions | Yaesu FTX-1, FT-710 | CI-V framing, different sub-commands |
| `civ` + variant `xiegu` | Binary CI-V, IC-705 subset | Xiegu X6100/X6200 | Limited command set |
| `kenwood_cat` | ASCII text, semicolon-terminated | Lab599 TX-500, Kenwood TS-890S | `FA00014200000;` format |
| `kenwood_cat` + variant `lab599` | ASCII + Lab599 extensions | Lab599 TX-500 MP | Extended command set |
| `sdrplay_api` | Native C API, USB | SDRplay RSPdx/RSP1x | No serial/socket protocol |
| `hamlib` | rigctld TCP text | Any hamlib-supported rig | Abstraction via rigctld |

### Protocol Command Map (for non-CI-V)

```toml
[protocol.commands]
get_freq_a = "FA;"
set_freq_a = "FA{freq11};"
# Template variables: {freq11}, {mode1}, {val}, {vfo}
```

CI-V radios don't need this — commands are inferred from CI-V sub-commands.

---

## Layer 1: Capabilities

```toml
[capabilities]
features = [
    "attenuator", "preamp", "nb", "nr", "notch", "apf",
    "tx", "split", "vox", "compressor", ...
]
```

Feature tags are the **single source of truth** for UI rendering.
If `"digisel"` is not in features → UI doesn't show DIGI-SEL control.

### Standard Feature Tags

**Receiver:** audio, dual_rx, dual_watch, af_level, rf_gain, squelch
**RF Front End:** attenuator, preamp, digisel, ip_plus, lna
**Antenna:** antenna, rx_antenna, antenna_select
**DSP/Noise:** nb, nr, notch, apf, twin_peak
**Filter:** pbt, filter_width, filter_shape
**TX:** tx, split, vox, compressor, monitor, drive_gain, ssb_tx_bw
**CW:** cw, break_in
**RIT/XIT:** rit, xit
**Tuner:** tuner
**Metering:** meters
**Scope:** scope
**Tone:** repeater_tone, tsql, dtcs
**Data:** data_mode, c4fm
**System:** power_control, dial_lock, scan, bsr, main_sub_tracking, memory_channels
**SDR-specific:** agc, bias_tee, hdr_mode, sample_rate
**Audio:** audio_eq

---

## Layer 2: Controls

Each control has a `style` that determines UI rendering.

### Control Styles

| Style | UI Component | Example |
|-------|-------------|---------|
| `toggle` | ON/OFF switch or button | ATT on IC-7300, Preamp on TX-500 |
| `stepped` | −/dropdown/+ stepper or segmented buttons | ATT on IC-7610 (16 values) |
| `selector` | Dropdown or button group | Preamp (OFF/P1/P2), AGC (FAST/MID/SLOW) |
| `slider` | Horizontal slider + value | RF Power, AF Gain |
| `level_is_toggle` | Slider where 0=OFF | NB/NR on Yaesu (0=disabled) |
| `toggle_with_level` | Toggle + slider | NB with ON/OFF + depth |
| `continuous` | Free-range slider | SDR filter width (50-10000 Hz) |
| `per_mode` | Changes with current mode | Filter widths (SSB vs CW) |
| `multi_band` | Multi-slider EQ | 3-band audio EQ on TX-500 |

### Examples

```toml
[controls.attenuator]
style = "stepped"
values = [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45]
unit = "dB"
default = 0

[controls.nb]
style = "level_is_toggle"    # 0 = OFF, 1-10 = ON at level
range = [0, 10]
default = 0
implicit_off_below = 1       # Values < 1 mean OFF

[controls.filter_width]
style = "per_mode"

[controls.filter_width.ssb]
widths_hz = [300, 400, 600, 850, ..., 4000]

[controls.filter_width.cw]
widths_hz = [50, 100, 150, ..., 4000]

[controls.lna]
style = "selector"
values = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
default = 0
[controls.lna.gain_tables]   # Per-band gain reduction (dB)
"0-12MHz" = [0, 3, 6, 9, 12, 15, 18, 21, 24, 27]
"420-1000MHz" = [0, 7, 10, 13, 16, 19, 22, 25, 31, 34]
```

---

## Layer 3: Rules

Declarative constraints between controls. Each rule has a `kind`.

### Rule Kinds

| Kind | Meaning | Example |
|------|---------|---------|
| `mutex` | Mutually exclusive (A active → B off) | ATT ↔ PREAMP |
| `disables` | Feature A disables feature B | DIGI-SEL → PREAMP disabled |
| `requires` | Feature A needs feature B | Dual Watch needs Dual RX |
| `value_limit` | Limit values by condition | HDR mode only < 2 MHz |
| `auto_set` | Auto-set value when condition met | ATT > 0 → PREAMP = 0 |

### Examples

```toml
# Mutual exclusion
[[rules]]
kind = "mutex"
fields = ["attenuator", "preamp"]

# Feature disables another
[[rules]]
kind = "disables"
when_active = "digisel"
disables = ["preamp"]
reason = "DIGI-SEL overrides preamp"

# Dependency
[[rules]]
kind = "requires"
feature = "dual_watch"
requires = ["dual_rx"]

# Conditional value limit
[[rules]]
kind = "value_limit"
when = "frequency > 2000000"
field = "hdr_mode"
force = false
reason = "HDR mode only available below 2 MHz"
```

### Rule Evaluation

**Backend** (`rules_engine.py`):
1. On SET command: check all rules → reject if violated → return error + reason
2. On auto_set: execute side effect (set B when A changes)

**Frontend** (`rules-engine.ts`):
1. On state change: evaluate rules → disable/grey out controls
2. Show tooltip with `reason` on disabled controls

---

## Meters

Two meter types: `linear` (simple scaling) and `lookup` (calibration table).

```toml
[meters.s_meter]
type = "lookup"
unit = "dBm"
[[meters.s_meter.calibration]]
raw = [0, 12, 27, ..., 255]
actual_db = [-54, -48, -42, ..., 60]
redline_raw = 130

[meters.power]
type = "linear"
raw_max = 255
max_watts = 100
unit = "W"

[meters.signal_level]
type = "computed"        # Calculated in software (SDR)
unit = "dBFS"
```

---

## VFO Schemes

The public contract models a radio as `Transceiver → Receiver → VFO` (see
`.claude/architecture/protocol.md` "Receiver tier"). The TOML `[vfo]` block
declares how per-receiver and per-slot operations are routed on the wire.

| Scheme | Receivers | VFOs | Radios |
|--------|-----------|------|--------|
| `main_sub` | 2 | 2 (A/B each) | IC-7610 (cmd29), IC-9700 (no cmd29; receiver-select via `0x07 0xD0/0xD1`) |
| `ab` | 1 | 2 | IC-7300, IC-705, TX-500 (A/B, select+send) |
| `ab_shared` | 2 | 1 | FTX-1 (2 rx share VFO set) |
| `single` | 1 | 1 | RSPdx (SDR, tuner only) |

### Fields

Parsed by `src/rigplane/rig_loader.py:575-605` into
`RadioProfile` (`src/rigplane/profiles.py:150-198`). Landed in #710.

| Field | Type | Example | When required |
|-------|------|---------|---------------|
| `scheme` | `str` | `"main_sub"`, `"ab"`, `"ab_shared"`, `"single"` | always |
| `main_select` | `list[int]` (CI-V bytes) | `[0xD0]` | `scheme = "main_sub"` |
| `sub_select`  | `list[int]` | `[0xD1]` | `scheme = "main_sub"` |
| `swap_main_sub`  | `list[int]` | `[0xB0]` | dual-RX rigs that support MAIN↔SUB swap |
| `equal_main_sub` | `list[int]` | `[0xB1]` | dual-RX rigs that support MAIN=SUB equalize |
| `swap_ab`  | `list[int]` | `[0xB0]` (single byte) or `[0x07, 0xB0]` | rigs with per-receiver VFO A↔B swap |
| `equal_ab` | `list[int]` | `[0xA0]` or `[0x07, 0xA0]` | rigs with per-receiver VFO A=B equalize |
| `vfo_a_select` (planned — #712/#715) | `list[int]` | `[0x07, 0x00]` | per-receiver VFO-A slot-select byte codes; planned for rigs that need explicit slot routing in addition to receiver selection |
| `vfo_b_select` (planned — #712/#715) | `list[int]` | `[0x07, 0x01]` | per-receiver VFO-B slot-select byte codes; not yet present in `RadioProfile` (parallel PR) |

### Example — dual-RX (IC-7610 / IC-9700 pattern)

```toml
[vfo]
scheme = "main_sub"
main_select   = [0xD0]
sub_select    = [0xD1]
swap_main_sub = [0xB0]   # MAIN ↔ SUB
equal_main_sub = [0xB1]  # MAIN = SUB
# A/B within the currently-selected receiver (optional, add when wiring VfoSlotCapable):
# swap_ab  = [0x07, 0xB0]
# equal_ab = [0x07, 0xA0]
```

### Example — single-RX (IC-7300 / IC-705 pattern)

```toml
[vfo]
scheme = "ab"
swap_ab  = [0xB0]   # VFO A ↔ B
equal_ab = [0xA0]   # VFO A = B
```

### Deprecated legacy keys

The flat keys `swap` and `equal` conflate MAIN↔SUB with VFO A↔B — the same
`0xB0` byte means different things depending on `scheme`.  They are still
accepted:

- When `scheme = "main_sub"`: `swap` → `swap_main_sub`, `equal` → `equal_main_sub`.
- Otherwise: `swap` → `swap_ab`, `equal` → `equal_ab`.

Loading a rig that uses them emits a `DeprecationWarning` once per file
(`src/rigplane/rig_loader.py:600-606`). Migrate to the explicit keys.

---

## Implementation Roadmap

### Phase 1: Schema + Parser (backend)
- Parse new TOML fields (controls, rules, meters, protocol)
- Validate against schema
- Expose via `/api/v1/capabilities` endpoint

### Phase 2: Rules Engine (backend + frontend)
- `rules_engine.py`: evaluate rules on SET commands
- `rules-engine.ts`: evaluate rules for UI state

### Phase 3: Control Renderer (frontend)
- Style → component mapping
- Auto-render controls from TOML definition

### Phase 4: Protocol Abstraction (backend)
- Abstract Radio Protocol → protocol-specific adapter
- CI-V adapter (current), Kenwood CAT adapter, SDRplay adapter

---

## Compatibility Matrix

| Radio | Protocol | ATT | Preamp | NB/NR | Filters | VFO | Meters | Scope |
|-------|----------|-----|--------|-------|---------|-----|--------|-------|
| IC-7610 | CI-V | 16-step | OFF/P1/P2 | toggle+level | FIL1/2/3 | main_sub | lookup | CI-V stream |
| IC-7300 | CI-V | ON/OFF | OFF/P1 | toggle+level | FIL1/2/3 | ab | lookup | CI-V stream |
| FTX-1 | CI-V (Yaesu) | 4-step | OFF/P1/P2 | level_is_toggle | per_mode | ab_shared | lookup | Yaesu format |
| X6200 | CI-V (Xiegu) | ON/OFF | OFF/P1/P2 | toggle+level | continuous | ab | linear | SDR internal |
| TX-500 | Kenwood CAT | ON/OFF | ON/OFF | toggle+level | 4 discrete | ab | lookup | none |
| RSPdx | SDRplay API | 10-step | LNA states | software | continuous | single | computed | native SDR |
