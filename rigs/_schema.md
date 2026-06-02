# Rig TOML Schema Specification

This document describes the format of `.toml` rig configuration files used by
`rigplane` to define radio profiles in a data-driven way.

Adding a new radio = writing one TOML file, zero Python changes.

---

## `[radio]` — Model Identity

| Field            | Type   | Required | Description                                     |
|------------------|--------|----------|-------------------------------------------------|
| `id`             | string | yes      | Unique profile identifier (e.g. `"icom_ic7610"`) |
| `model`          | string | yes      | Human-readable model name (e.g. `"IC-7610"`)    |
| `civ_addr`       | int    | no       | Default CI-V address (0x00–0xFF). Required for `civ` protocol, default 0 for others. |
| `receiver_count` | int    | yes      | Number of independent receivers (1 or 2)        |
| `has_lan`        | bool   | yes      | Whether the radio has a LAN (Ethernet) port     |
| `has_wifi`       | bool   | yes      | Whether the radio has built-in WiFi             |
| `default_baud`   | int    | no       | Default serial baud rate (e.g. `115200`). Used by CLI `--model` auto-config. |

## `[protocol]` — Communication Protocol

Optional section. Defaults to `type = "civ"` if omitted.

| Field     | Type   | Required | Description                                       |
|-----------|--------|----------|---------------------------------------------------|
| `type`    | string | no       | Protocol type: `"civ"`, `"kenwood_cat"`, or `"yaesu_cat"` |
| `address` | int    | no       | Protocol-specific address (e.g. CI-V addr)        |
| `baud`    | int    | no       | Override baud rate for this protocol              |

Protocol types:
- **`civ`** — Icom CI-V binary protocol (IC-7610, IC-7300, Xiegu X6100/X6200)
- **`kenwood_cat`** — Kenwood text-based CAT (Lab599 TX-500, TS-890S). Commands: `"FA14200000;"`, `"MD2;"`
- **`yaesu_cat`** — Yaesu text-based CAT (FTX-1, FT-710, FT-DX101)

## `[audio]` — LAN Audio Policy

Optional section. Defines radio-native LAN audio defaults and browser consumer
transport policy. These fields are used by the LAN audio stream resolver when
the caller did not explicitly override codec or sample rate.

| Field                         | Type              | Required | Description |
|-------------------------------|-------------------|----------|-------------|
| `codec_preference`            | string[]          | no       | Ordered RX codec preference. Values must match `AudioCodec` names. The first supported codec is used unless the caller passed an explicit codec. |
| `tx_codec`                    | string            | no       | Radio-native TX codec name from `AudioCodec`. Direct LAN TX profiles should prefer mono PCM unless hardware evidence proves otherwise. |
| `default_sample_rate_hz`      | int               | no       | Profile default sample rate when `sample_rate_by_codec` has no entry for the selected codec. |
| `supported_sample_rates_hz`   | int[]             | no       | Evidence-backed sample rates accepted by the radio profile. |
| `sample_rate_by_codec`        | table string→int  | no       | Per-codec default sample rate used by the resolver for RX and TX. Keys must be `AudioCodec` names. |
| `browser_rx_transport`        | string            | no       | Browser consumer transport policy: `"auto"`, `"pcm"`, or `"opus"`. |
| `browser_rx_transcode_to_opus`| bool              | no       | Whether browser RX may transcode radio-native audio to Opus. This is not a radio-native codec setting. |

Example:

```toml
[audio]
codec_preference = ["PCM_2CH_16BIT", "PCM_1CH_16BIT", "ULAW_2CH", "ULAW_1CH"]
tx_codec = "PCM_1CH_16BIT"
default_sample_rate_hz = 48000
sample_rate_by_codec = { PCM_2CH_16BIT = 48000, PCM_1CH_16BIT = 48000 }
browser_rx_transport = "auto"
browser_rx_transcode_to_opus = true
```

Profile defaults must be backed by radio evidence, not copied from generic
codec lists. Prefer pass-only artifacts from `rigplane audio probe`; for radios
with LAN session cooldown behavior, include a conservative candidate cooldown in
the validation command. Opus must not be used as a stock radio LAN default unless
the endpoint or protocol explicitly proves native Opus support. Browser Opus is
only a server-to-browser transport policy after radio-native audio is received.

## `[capabilities]` — Feature Flags

| Field      | Type     | Required | Description                    |
|------------|----------|----------|--------------------------------|
| `features` | string[] | yes      | List of supported capabilities |

Known capability strings (grouped by area):

**Receiver:** `audio`, `dual_rx`, `dual_watch`, `af_level`, `rf_gain`, `squelch`, `af_mute`

**RF Front End:** `attenuator`, `preamp`, `digisel`, `ip_plus`

**Antenna:** `antenna`, `rx_antenna`

**DSP / Noise:** `nb`, `nr`, `notch`, `apf`, `twin_peak`

**Filter:** `pbt`, `filter_width`, `filter_shape`

**TX:** `tx`, `split`, `vox`, `compressor`, `monitor`, `drive_gain`, `ssb_tx_bw`, `tx_inhibit`, `dpd`

**CW:** `cw`, `break_in`

**RIT / XIT:** `rit`, `xit`

**Tuner:** `tuner`

**Metering:** `meters`

**Scope:** `scope`

**Tone:** `repeater_tone`, `tsql`

**Data:** `data_mode`

**System:** `power_control`, `dial_lock`, `scan`, `bsr`, `main_sub_tracking`, `lcd_backlight`

## `[state_acquisition]` — State Capability And Policy Metadata

Optional section. Defines provider-specific state acquisition behavior as data
for future scheduler/adapters. Web and rigctld delivery code must not branch on
these fields directly.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `provider` | string | no | Lowercase provider identifier such as `"icom_civ"`, `"yaesu_cat"`, `"xiegu_civ"`, or `"external_rigctld"`. Defaults to `"profile"`. |
| `default_cadence_seconds` | float | no | Conservative default polling cadence for supported fields. Defaults to `5.0`. |
| `default_freshness_ttl_seconds` | float | no | TTL before a value should be considered stale. Must be greater than or equal to cadence. Defaults to `15.0`. |
| `default_reconciliation_priority` | string | no | `"unsolicited"`, `"command_response"`, `"poll"`, or `"last_observation"`. Defaults to `"poll"`. |
| `adaptive_decay` | bool | no | Whether a scheduler may widen cadence while idle. Defaults to `false`. |
| `adaptive_decay_idle_multiplier` | float | no | Multiplier for idle cadence when adaptive decay is enabled. Must be greater than `1.0` when enabled. |
| `adaptive_decay_max_cadence_seconds` | float | no | Maximum widened cadence. |
| `external_cat_pause` | string | no | `"pause_polling"`, `"coalesce_meters_only"`, or `"continue"`. Defaults to `"pause_polling"`. |
| `meter_coalescing_window_seconds` | float | no | Default short coalescing window for stream-like meter observations. |

### `[state_acquisition.capabilities]`

Each field path uses the canonical `FieldPath` strings from
`rigplane.core.state_pipeline_contracts`.

| Field | Type | Description |
|-------|------|-------------|
| `unsolicited_push` | string[] | Fields the provider may push without an explicit poll. |
| `polling_only` | string[] | Fields acquired by explicit polling. |
| `stream_like_meters` | string[] | Meter fields whose observations may arrive frequently and be coalesced. Each path must use the `meters` family. |
| `command_response_observable` | string[] | Fields where command responses can confirm state after a write. |
| `supported_controls` | string[] | Writable/control fields supported by the provider profile. |
| `unsupported` | string[] | Known-unavailable fields. These must not also appear in acquisition lists. |
| `unknown` | string[] | Fields without enough evidence. Schedulers should diagnose them as unknown instead of polling forever. |

### `[state_acquisition.field_policies."<field-path>"]`

Optional per-field policy overrides. Supported keys are `cadence_seconds`,
`freshness_ttl_seconds`, `reconciliation_priority`, `external_cat_pause`,
`adaptive_decay`, `adaptive_decay_idle_multiplier`,
`adaptive_decay_max_cadence_seconds`, and `meter_coalescing_window_seconds`.
Field-specific `meter_coalescing_window_seconds` is valid only for meter paths.

Example:

```toml
[state_acquisition]
provider = "xiegu_civ"
default_cadence_seconds = 2.0
default_freshness_ttl_seconds = 8.0
default_reconciliation_priority = "poll"
external_cat_pause = "pause_polling"

[state_acquisition.capabilities]
polling_only = [
    "receiver.main.active.freq_mode.freq_hz",
    "receiver.main.active.freq_mode.mode",
]
command_response_observable = ["receiver.main.active.freq_mode.mode"]
unsupported = ["global.tx_state.power_on"]

[state_acquisition.field_policies."receiver.main.active.freq_mode.mode"]
cadence_seconds = 1.0
freshness_ttl_seconds = 4.0
reconciliation_priority = "command_response"
```

## `[attenuator]` — Attenuator Steps

Optional section. Defines available attenuator values for the radio.

| Field    | Type  | Required | Description                                         |
|----------|-------|----------|-----------------------------------------------------|
| `values` | int[] | yes      | Available ATT values in dB (e.g. `[0, 3, 6, 9, ...]`) |

`0` = OFF, other values = attenuation in dB. The frontend cycles through these
values in order. Different radios have very different step sizes:
- IC-7610: 16 steps × 3 dB = `[0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45]`
- IC-7300: binary toggle = `[0, 20]`
- FTX-1: 4 discrete levels = `[0, 1, 2, 3]`

## `[preamp]` — Preamplifier Steps

Optional section. Defines available preamp settings.

| Field    | Type  | Required | Description                                       |
|----------|-------|----------|---------------------------------------------------|
| `values` | int[] | yes      | Available preamp settings (e.g. `[0, 1, 2]`)     |

`0` = OFF, `1` = PREAMP 1, `2` = PREAMP 2. The frontend cycles through these.

## `[agc]` — AGC Modes

Optional section. Defines available AGC modes and their display labels.

| Field    | Type                | Required | Description                                     |
|----------|---------------------|----------|-------------------------------------------------|
| `modes`  | int[]               | yes      | Available AGC mode values (e.g. `[1, 2, 3]`)   |
| `labels` | table (string→string) | yes    | Map of mode value to display label              |

Example:

```toml
[agc]
modes = [1, 2, 3]
labels = { "1" = "FAST", "2" = "MID", "3" = "SLOW" }
```

## `[controls]` — Control Styles

Optional section. Defines how UI controls render for each feature.
Each sub-table is named after a feature (e.g. `[controls.attenuator]`).

| Field       | Type   | Required | Description                                    |
|-------------|--------|----------|------------------------------------------------|
| `style`     | string | no       | Control rendering style (see below)            |
| `range_min` | int    | no       | Minimum value (for level controls)             |
| `range_max` | int    | no       | Maximum value (for level controls)             |

Valid styles:
- **`toggle`** — Simple ON/OFF (IC-7300 ATT, X6100 ATT)
- **`stepped`** — Discrete steps with +/- buttons and dropdown (IC-7610 ATT)
- **`selector`** — Named discrete levels (FTX-1 ATT: 0/1/2/3)
- **`toggle_and_level`** — Separate ON/OFF toggle + level slider (IC-7610 NB/NR)
- **`level_is_toggle`** — Level=0 is OFF, level>0 is ON+level (FTX-1 NB/NR)

Example:

```toml
[controls.attenuator]
style = "stepped"

[controls.nb]
style = "level_is_toggle"
range_min = 0
range_max = 10
```

## `[meters]` — Meter Calibration

Optional section. Defines non-linear calibration tables for meters.
Each sub-table is named after a meter source (e.g. `[meters.s_meter]`).

| Field         | Type  | Required | Description                                  |
|---------------|-------|----------|----------------------------------------------|
| `redline_raw` | int   | no       | Raw value where meter enters "danger" zone   |
| `calibration` | array | no       | Array of calibration points (see below)      |

### `[[meters.<name>.calibration]]` — Calibration Points

| Field    | Type   | Required | Description                          |
|----------|--------|----------|--------------------------------------|
| `raw`    | int    | yes      | Raw meter value (0–255)              |
| `actual` | float  | yes      | Actual value (dBm, watts, etc.)      |
| `label`  | string | yes      | Display label (e.g. `"S9"`, `"S9+20"`) |

Example:

```toml
[meters.s_meter]
redline_raw = 130

[[meters.s_meter.calibration]]
raw = 0
actual = -54.0
label = "S0"

[[meters.s_meter.calibration]]
raw = 130
actual = 0.0
label = "S9"
```

The frontend interpolates between calibration points for values not exactly matching.
This handles both linear (Icom) and non-linear (Yaesu) meter scales.

## `[[rules]]` — Constraint Rules

Optional top-level array of tables. Defines interaction constraints between features.
The rule engine evaluates these to enable/disable controls in the UI and prevent
invalid hardware states.

| Field         | Type     | Required | Description                              |
|---------------|----------|----------|------------------------------------------|
| `kind`        | string   | yes      | Rule type (see below)                    |
| `fields`      | string[] | varies   | Affected feature fields                  |
| `when_active` | string   | varies   | Triggering feature                       |
| `disables`    | string[] | varies   | Features to disable                      |
| `requires`    | string   | varies   | Required feature                         |
| `reason`      | string   | no       | Human-readable explanation               |

Valid rule kinds:
- **`mutex`** — Mutual exclusion. Only one of `fields` can be active. Example: ATT + PREAMP on IC-7610.
- **`disables`** — When `when_active` is on, `disables` features become unavailable. Example: DIGI-SEL disables PREAMP.
- **`requires`** — Feature requires another to be active first.
- **`value_limit`** — Limits values of a feature based on another feature's state.

Example:

```toml
[[rules]]
kind = "mutex"
fields = ["attenuator", "preamp"]

[[rules]]
kind = "disables"
when_active = "digisel"
disables = ["preamp"]
reason = "DIGI-SEL overrides preamp"
```

## `[spectrum]` — Scope Parameters

Optional section for radios that support the spectrum scope.

| Field          | Type | Required | Description                                  |
|----------------|------|----------|----------------------------------------------|
| `seq_max`      | int  | yes      | Maximum scope sequence number                |
| `amp_max`      | int  | yes      | Maximum amplitude value in scope data        |
| `data_len_max` | int  | yes      | Maximum number of data points per scope frame |

## `[modes]` — Operating Modes

| Field  | Type     | Required | Description                                        |
|--------|----------|----------|----------------------------------------------------|
| `list` | string[] | yes      | Supported operating modes (e.g. `"USB"`, `"CW"`)  |

## `[filters]` — IF Filters

| Field  | Type     | Required | Description                                      |
|--------|----------|----------|--------------------------------------------------|
| `list` | string[] | yes      | Available IF filter names (e.g. `"FIL1"`)        |

Additional optional fields:

| Field            | Type   | Required | Description                                      |
|------------------|--------|----------|--------------------------------------------------|
| `style`          | string | no       | `"named_slots"` (default) or `"per_mode"`        |
| `encoding`       | string | no       | `"segmented_bcd_index"` (default, all Icom rigs) or `"table_index"` (Yaesu) |
| `width_min_hz`   | int    | no       | Minimum IF filter width in Hz                    |
| `width_max_hz`   | int    | no       | Maximum IF filter width in Hz                    |

Profile-specific width contracts can be described under `[filters.width.<MODE>]`.

```toml
[filters]
list = ["FIL1", "FIL2", "FIL3"]
encoding = "segmented_bcd_index"

[filters.width.USB]
defaults = [3000, 2400, 1800]
segments = [
	{ hz_min = 50, hz_max = 500, step_hz = 50, index_min = 0 },
	{ hz_min = 600, hz_max = 3600, step_hz = 100, index_min = 10 },
]

[filters.width.FM]
fixed = true
defaults = [15000, 10000, 7000]
```

## `[vfo]` — VFO Configuration

| Field             | Type   | Required    | Description                                                  |
|-------------------|--------|-------------|--------------------------------------------------------------|
| `scheme`          | string | yes         | VFO scheme (see below)                                       |
| `main_select`     | int[]  | if main_sub | Wire bytes to select Main VFO (e.g. `[0xD0]`)                |
| `sub_select`      | int[]  | if main_sub | Wire bytes to select Sub VFO (e.g. `[0xD1]`)                 |
| `swap_ab`         | int[]  | no          | A↔B swap within the selected receiver (e.g. `[0x07, 0xB0]`)  |
| `equal_ab`        | int[]  | no          | A=B equalize within the selected receiver                    |
| `swap_main_sub`   | int[]  | no          | MAIN↔SUB swap across receivers (dual-RX rigs, e.g. `[0xB0]`) |
| `equal_main_sub`  | int[]  | no          | MAIN=SUB equalize across receivers                           |
| `swap` *(legacy)* | int[]  | no          | **Deprecated.** Maps to `swap_main_sub` if `scheme = "main_sub"`, else `swap_ab`. Emits a `DeprecationWarning` per load. |
| `equal` *(legacy)*| int[]  | no          | **Deprecated.** Same mapping as `swap`.                      |

The A↔B vs MAIN↔SUB split was introduced in issue #710: on dual-receiver rigs
(IC-7610) `0x07 0xB0` swaps MAIN/SUB, while on single-receiver rigs (IC-7300)
the same nibble swaps VFO A/B. The legacy `swap`/`equal` keys overloaded both
meanings; prefer the explicit fields for new rig files.

Valid VFO schemes:
- **`ab`** — 2 VFOs (A/B), 1 receiver (IC-7300, X6100, TX-500)
- **`main_sub`** — 2 VFOs + 2 receivers (IC-7610, IC-R8600)
- **`ab_shared`** — 1 VFO shared between 2 receivers (FTX-1)
- **`single`** — 1 VFO, 1 receiver (simple QRP rigs)

## `[[freq_ranges.ranges]]` — Frequency Ranges

Array of tables, each defining a frequency coverage range.

| Field      | Type   | Required | Description                                  |
|------------|--------|----------|----------------------------------------------|
| `label`    | string | yes      | Range label (e.g. `"HF"`, `"6m"`, `"2m"`)   |
| `start_hz` | int    | yes      | Start frequency in Hz                        |
| `end_hz`   | int    | yes      | End frequency in Hz (must be > `start_hz`)   |
| `bands`    | array  | no       | Amateur band definitions within this range   |

### `[[freq_ranges.ranges.bands]]` — Band Definitions

| Field        | Type   | Required | Description                                 |
|--------------|--------|----------|---------------------------------------------|
| `name`       | string | yes      | Band name (e.g. `"20m"`, `"70cm"`)          |
| `start_hz`   | int    | yes      | Band start frequency in Hz                  |
| `end_hz`     | int    | yes      | Band end frequency in Hz                    |
| `default_hz` | int    | yes      | Default tuning frequency in Hz (within band)|
| `bsr_code`   | int    | no       | Band Stack Register code for CI-V 0x1A 0x01 |

## `[cmd29]` — Command 29 Routes

Optional section for dual-receiver radios that use Command 29 prefix for
receiver targeting. Single-receiver radios omit this section entirely.

| Field    | Type       | Required | Description                                        |
|----------|------------|----------|----------------------------------------------------|
| `routes` | int[][]    | yes      | List of `[cmd, sub]` or `[cmd]` (sub=None) entries |

Each entry is a 1- or 2-element integer array:
- `[0x11]` — command-only route (sub = None, e.g. ATT)
- `[0x14, 0x01]` — command + sub-command route (e.g. AF Gain)

## `[commands]` — Command Definitions

Optional section. Required for `civ` protocol radios, optional for others.
Each key maps a command name to its specification. The format depends on the
protocol type.

### Format 1: CI-V Wire Bytes (Icom)

For `civ` protocol radios, commands are arrays of bytes:

```toml
get_freq = [0x03]           # Single command byte
get_af_level = [0x14, 0x01] # Command + sub-command
```

All byte values must be integers in the range `0x00`–`0xFF`.

### Format 2: CAT Command Spec (Yaesu/Kenwood)

For `yaesu_cat` and `kenwood_cat` protocols, commands are inline tables with a
`cat` key containing template strings:

```toml
# Read-only command (query + parse response)
get_freq = { cat = { read = "FA;", parse = "FA{freq:09d};" } }

# Write-only command (set value)
set_freq = { cat = { write = "FA{freq:09d};" } }

# Read + write command (both query and set)
get_ptt = { cat = { read = "TX;", write = "TX{state};", parse = "TX{state};" } }
```

CAT spec fields:

| Field   | Type   | Required | Description                                      |
|---------|--------|----------|--------------------------------------------------|
| `read`  | string | no*      | Template for READ query (e.g. `"FA;"`)           |
| `write` | string | no*      | Template for WRITE/SET (e.g. `"FA{freq:09d};"`) |
| `parse` | string | no       | Template for parsing response (defaults to `read`) |

\* At least one of `read` or `write` must be present.

Template placeholders use Python-style format specs: `{name:format}`.

### Mixed Protocols

A single rig file uses only one format (CI-V or CAT), matching its `[protocol].type`.
The loader validates both formats uniformly.

### Naming Convention

The command name follows the pattern `get_<param>` / `set_<param>` for
read/write commands, or a verb like `ptt_on`, `scope_on`, `send_cw`.

### `[commands.overrides]` — Model-Specific Overrides

Commands in this sub-table override the defaults for a specific radio model.
Same format as `[commands]`: CI-V byte arrays or CAT inline tables.

## Additional Parameterized Sections

These optional sections provide detailed configuration for specific capabilities:

| Section        | Description                                    |
|----------------|------------------------------------------------|
| `[antenna]`    | TX antenna count, RX antenna, antenna modes    |
| `[apf]`        | Audio Peak Filter values and labels            |
| `[notch]`      | Manual notch width values and labels           |
| `[ssb_tx_bw]`  | SSB TX bandwidth values and labels             |
| `[break_in]`   | CW break-in mode values and labels             |
| `[rit]`        | RIT range in Hz                                |
| `[cw]`         | CW pitch, speed, dash ratio ranges             |
| `[nb]`         | Noise blanker depth and width ranges            |
| `[power]`      | Maximum TX power in watts                      |

## Wire Byte Format

All wire bytes are specified as arrays of integers in the range `0x00`–`0xFF`.
TOML supports hex integer literals natively: `0x14` is the same as `20`.
