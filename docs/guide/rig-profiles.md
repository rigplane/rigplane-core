---
description: Add a new radio to RigPlane by writing a TOML rig profile — capabilities, CI-V commands, hardware parameters, and registration in the rig loader.
---

# Adding a New Radio (Rig Profiles)

## Overview

rigplane uses **TOML rig files** to define radio capabilities, protocol type, CI-V commands,
and hardware parameters. Adding a new radio means adding a new `.toml` file — no Python
changes required for most radios.

Rig files live in `rigs/`. Reference files:

- `rigs/ic7610.toml` — IC-7610 (dual receiver, LAN, CI-V, full feature set)
- `rigs/ic7300.toml` — IC-7300 (single receiver, USB serial, CI-V)
- `rigs/ftx1.toml` — Yaesu FTX-1 (Yaesu CAT protocol, dual RX)
- `rigs/x6100.toml` — Xiegu X6100 (CI-V, IC-705 compatible subset, QRP)
- `rigs/tx500.toml` — Lab599 TX-500 (Kenwood CAT protocol, QRP)

## Quick Start

```bash
# 1. Copy the closest reference rig file
cp rigs/ic7610.toml rigs/ic9700.toml   # for Icom CI-V
cp rigs/tx500.toml rigs/ts890s.toml    # for Kenwood CAT
cp rigs/ftx1.toml rigs/ft710.toml      # for Yaesu CAT

# 2. Edit [radio] and [protocol] sections
# 3. Update [capabilities] to match your radio
# 4. Update [commands] (CI-V radios) or leave empty (Kenwood/Yaesu CAT)
# 5. Add [controls], [meters], [[rules]] as needed
# 6. Run the loader tests
uv run pytest tests/test_rig_loader.py tests/test_rig_multi_vendor.py -v
```

## Supported Protocols

| Protocol | Type string | Examples | Description |
|----------|------------|----------|-------------|
| Icom CI-V | `"civ"` | IC-7610, IC-7300, Xiegu X6100/X6200 | Binary CI-V frames |
| Kenwood CAT | `"kenwood_cat"` | Lab599 TX-500, Kenwood TS-890S | Text `"CMD params;"` |
| Yaesu CAT | `"yaesu_cat"` | Yaesu FTX-1, FT-710 | Yaesu text protocol |

```toml
[protocol]
type = "civ"  # or "kenwood_cat" or "yaesu_cat"
```

For CI-V radios, `[radio].civ_addr` is required. For Kenwood/Yaesu, omit it.

## TOML Schema Reference

The sections below provide complete field documentation.

## Section-by-Section Walkthrough

### `[radio]` — Model Identity

```toml
[radio]
id = "icom_ic9700"        # unique snake_case ID
model = "IC-9700"         # human-readable, used in UI and logs
civ_addr = 0xA2           # CI-V address (required for civ, omit for others)
receiver_count = 2        # 1 or 2 independent receivers
has_lan = true            # Ethernet port built-in?
has_wifi = false          # WiFi built-in?
default_baud = 115200     # optional: default serial baud rate
```

`id` must be globally unique. Convention: `<vendor>_<model_lower>` (e.g. `icom_ic7300`,
`yaesu_ftx1`, `lab599_tx500`).

### `[protocol]` — Communication Protocol

```toml
[protocol]
type = "civ"       # "civ" | "kenwood_cat" | "yaesu_cat"
address = 0xA2     # optional: override civ_addr for protocol
baud = 115200      # optional: protocol-specific baud rate
```

Omit this section entirely for CI-V radios — defaults to `type = "civ"`.

### `[capabilities]` — Feature Flags

```toml
[capabilities]
features = [
    "audio",      # RX/TX audio streaming
    "scope",      # spectrum scope
    "dual_rx",    # dual independent receivers
    "meters",     # S-meter, SWR, ALC, etc.
    "tx",         # transmit capability
    "attenuator", # ATT control
    "preamp",     # preamplifier
    # ... see _schema.md for full list
]
```

The capability list controls Web UI guards — features not listed are hidden.

### `[controls]` — UI Control Styles (new)

Defines **how** controls render in the UI. Each sub-table corresponds to a capability:

```toml
[controls.attenuator]
style = "stepped"          # IC-7610: 16 discrete steps with stepper UI

[controls.nb]
style = "toggle_and_level" # IC-7610: separate ON/OFF + level slider
```

Styles:
- `toggle` — ON/OFF (IC-7300 ATT, X6100 ATT)
- `stepped` — Discrete steps with \[−\]\[dropdown\]\[+\] (IC-7610 ATT)
- `selector` — Dropdown/selector (FTX-1 ATT: 4 named levels)
- `toggle_and_level` — Separate toggle + slider (IC-7610 NB/NR)
- `level_is_toggle` — 0=OFF, >0=level (FTX-1 NB/NR)

### `[meters]` — Calibration Tables (new)

Non-linear raw→actual calibration for meters. The frontend interpolates between points:

```toml
[meters.s_meter]
redline_raw = 130   # where the red zone starts

[[meters.s_meter.calibration]]
raw = 0
actual = -54.0
label = "S0"

[[meters.s_meter.calibration]]
raw = 130
actual = 0.0
label = "S9"
```

### `[[rules]]` — Constraint Rules (new)

Defines how capabilities interact (mutual exclusion, dependencies):

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

Rule kinds: `mutex`, `disables`, `requires`, `value_limit`.

### `[vfo]` — VFO Scheme

Four schemes supported:

| Scheme | Receivers | VFOs | Example |
|--------|----------|------|---------|
| `main_sub` | 2 | 2 | IC-7610 |
| `ab` | 1 | 2 | IC-7300, X6100, TX-500 |
| `ab_shared` | 2 | 1 | FTX-1 |
| `single` | 1 | 1 | Simple QRP rigs |

```toml
[vfo]
scheme = "main_sub"
main_select = [0xD0]
sub_select = [0xD1]
swap = [0xB0]
equal = [0xB1]
```

### `[commands]` — Wire Bytes

Required for CI-V radios. Optional (may be empty) for Kenwood/Yaesu CAT:

```toml
[commands]
get_freq = [0x03]
set_freq = [0x05]
get_af_level = [0x14, 0x01]
# ... full list in ic7610.toml

[commands.overrides]
# Model-specific differences
```

For Kenwood/Yaesu radios, commands are handled by the protocol adapter, not CI-V bytes.

### Other Sections

See the full schema for: `[attenuator]`, `[preamp]`, `[agc]`, `[spectrum]`,
`[[freq_ranges.ranges]]`, `[cmd29]`, `[antenna]`, `[apf]`, `[notch]`,
`[ssb_tx_bw]`, `[break_in]`, `[rit]`, `[cw]`, `[nb]`, `[power]`.

## Testing Your Rig File

```bash
# Basic load + validation
uv run python -c "
from pathlib import Path
from rigplane.rig_loader import load_rig
cfg = load_rig(Path('rigs/ic9700.toml'))
print(cfg.model, cfg.protocol_type, cfg.receiver_count)
print('capabilities:', cfg.capabilities)
print('controls:', cfg.controls)
print('rules:', cfg.rules)
"

# Full test suite
uv run pytest tests/ -x -q

# Multi-vendor specific tests
uv run pytest tests/test_rig_multi_vendor.py -v
```

## Common Mistakes

| Mistake | Error message |
|---------|--------------|
| Missing `[capabilities].features` | `features must not be empty` |
| Unknown capability | `unknown capability 'xyz'. Known: [...]` |
| Invalid VFO scheme | `scheme must be one of {'ab', 'main_sub', 'ab_shared', 'single'}` |
| Invalid protocol type | `type must be one of {'civ', 'kenwood_cat', 'yaesu_cat'}` |
| Invalid control style | `style must be one of {'toggle', 'stepped', ...}` |
| Invalid rule kind | `kind must be one of {'mutex', 'disables', ...}` |
| CI-V addr missing for CI-V radio | Parser defaults to 0 — may cause runtime errors |

## See Also

- [`docs/api/rig-loader.md`](../api/rig-loader.md) — `load_rig()` / `discover_rigs()` API
- [`docs/guide/radios.md`](radios.md) — supported radios and backend comparison
- [`docs/api/commands.md`](../api/commands.md) — using `CommandMap` with command functions
