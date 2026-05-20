---
description: Declarative operating profiles for RigPlane — describe desired radio state (frequency, mode, data, VOX) and let the library reconcile it for you.
---

# Radio Profiles

Radio profiles let you describe the **desired radio state** declaratively — set only the fields you want to change, and the system figures out how to get there.

## Concept

Instead of writing imperative sequences (set freq → set mode → enable data → disable VOX…), you describe what state you want:

```python
profile = OperatingProfile(
    frequency_hz=14_074_000,
    mode="USB",
    data_mode=True,
    vox=False,
)
```

`apply_profile` takes your profile, snapshots the current state (so you can restore later), and applies each field. If the radio doesn't support a particular setter — for example a radio without VOX — the field is silently skipped with a `DEBUG` log message.

**Key principle:** fields set to `None` mean "don't change"; `False` means "explicitly disable".

## API

```python
from rigplane import OperatingProfile, apply_profile, PRESETS

async with create_radio(config) as radio:
    # Apply a profile — returns a snapshot for later restore
    profile = OperatingProfile(frequency_hz=145_500_000, mode="FM", data_mode=True, vox=False)
    snapshot = await apply_profile(radio, profile)

    # ... operate (APRS, packet, etc.) ...

    # Restore previous state
    await radio.restore_state(snapshot)
```

## OperatingProfile Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `frequency_hz` | `int \| None` | `None` | Tuning frequency in Hz |
| `mode` | `str \| None` | `None` | Operating mode: `"FM"`, `"USB"`, `"LSB"`, `"CW"`, etc. |
| `filter_width` | `int \| None` | `None` | Passband filter width in Hz (passed to `set_mode`) |
| `vox` | `bool \| None` | `None` | `True` = enable VOX, `False` = disable |
| `split` | `bool \| None` | `None` | `True` = enable split, `False` = disable |
| `vfo` | `str \| None` | `None` | Select VFO: `"A"` or `"B"` |
| `data_mode` | `bool \| None` | `None` | `True` = enable DATA mode, `False` = disable |
| `data_off_mod_input` | `int \| None` | `None` | DATA-OFF modulation input source index |
| `data1_mod_input` | `int \| None` | `None` | DATA-1 modulation input source index. Captured for compatibility; apply/restore do not rewrite it because DATA1 is user-owned. |
| `squelch_level` | `int \| None` | `None` | Squelch level (0 = open) |
| `equalize_vfo` | `bool` | `False` | Copy active VFO to both VFOs |
| `scope_enabled` | `bool \| None` | `None` | `True` = enable spectrum scope |
| `scope_mode` | `int \| None` | `None` | Scope centre/fixed mode index |
| `scope_span` | `int \| None` | `None` | Scope span index |
| `scope_output` | `bool` | `False` | Pass `output=True` to `enable_scope` |
| `scope_policy` | `ScopeCompletionPolicy \| str` | `FAST` | Scope completion policy |
| `scope_timeout` | `float` | `5.0` | Timeout for scope enable/verify |

## Built-in Presets

```python
from rigplane import apply_profile, PRESETS

# APRS on 2m (145.500 MHz FM, DATA mode, VOX off)
snapshot = await apply_profile(radio, PRESETS.aprs_vhf)

# FT8 on 20m (14.074 MHz USB, DATA mode, VOX off)
snapshot = await apply_profile(radio, PRESETS.ft8_20m)

# CW contest setup (VOX off, split off — freq/mode unchanged)
snapshot = await apply_profile(radio, PRESETS.cw_contest)

# SSB on 40m (7.040 MHz LSB)
snapshot = await apply_profile(radio, PRESETS.ssb_40m)
```

| Preset | Frequency | Mode | Key Settings |
|--------|-----------|------|--------------|
| `PRESETS.aprs_vhf` | 145.500 MHz | FM | DATA mode on, VOX off |
| `PRESETS.ft8_20m` | 14.074 MHz | USB | DATA mode on, VOX off |
| `PRESETS.cw_contest` | — | — | VOX off, split off |
| `PRESETS.ssb_40m` | 7.040 MHz | LSB | — |

## Custom Presets

Create your own for specific workflows:

```python
# SOTA activation on 2m FM
sota_2m = OperatingProfile(
    frequency_hz=144_200_000,
    mode="FM",
    squelch_level=0,
    vox=False,
)
snapshot = await apply_profile(radio, sota_2m)

# FT8 on 40m
ft8_40m = OperatingProfile(
    frequency_hz=7_074_000,
    mode="USB",
    filter_width=3000,
    data_mode=True,
    vox=False,
)
snapshot = await apply_profile(radio, ft8_40m)

# Contest station: tune + enable scope
contest_20m = OperatingProfile(
    frequency_hz=14_045_000,
    mode="CW",
    vox=False,
    scope_enabled=True,
    scope_span=5,
)
snapshot = await apply_profile(radio, contest_20m)
```

## Sync API (Synchronous Wrapper)

For non-async code, use the synchronous `IcomRadio` wrapper:

```python
from rigplane.sync import IcomRadio

radio = IcomRadio(config)
snapshot = radio.prepare_ic705_data_profile(frequency_hz=145_500_000)
# ... operate ...
radio.restore_ic705_data_profile(snapshot)
```

## IC-705 Convenience Helper (Backward Compatible)

The original IC-705 specific helper still works and now delegates to the generic system:

```python
from rigplane import prepare_ic705_data_profile, restore_ic705_data_profile

snapshot = await prepare_ic705_data_profile(
    radio,
    frequency_hz=145_500_000,
    mode="FM",
    data_off_mod_input=3,
)
# ... packet work ...
await restore_ic705_data_profile(radio, snapshot)
```

## How It Works

1. **Snapshot:** `apply_profile` calls `radio.snapshot_state()` to capture the current state
2. **Apply:** Each field maps to a setter method (`frequency_hz` → `set_freq`, `vox` → `set_vox`, etc.)
3. **Capability check:** `hasattr(radio, setter_name)` — if the radio lacks a setter, skip it
4. **Ordered execution:** VOX → VFO → split → frequency → mode → DATA → modulation inputs → VFO equalize → squelch → scope → final VFO re-select
5. **Restore:** `radio.restore_state(snapshot)` reverses all changes (best-effort per field)

## Application Order

Fields are applied in a specific order to avoid conflicts:

1. VOX (disable first to prevent accidental TX)
2. VFO selection
3. Split mode
4. Frequency
5. Mode (with optional filter width)
6. DATA mode
7. Modulation inputs (DATA-OFF, DATA-1)
8. VFO equalize
9. Squelch
10. Scope (enable + mode + span)
11. VFO re-select (ensure consistent state)
