---
robots: noindex, follow
---

# Capabilities Matrix — Verified from CI-V Reference

Sources:
- IC-7300MK2 CI-V Reference Guide (PDF)
- IC-7610 wfview rig file (`.rig` format, verified against wfview 2.20)
- Actual hardware testing (IC-7610 firmware 1.42, IC-7300)

## Receiver Architecture

| Feature | IC-7610 | IC-7300 | TOML key |
|---------|---------|---------|----------|
| Receivers | 2 (MAIN+SUB) | 1 | `[radio] receiver_count` |
| VFO scheme | Main/Sub | A/B | `[vfo] scheme` |
| Dual watch | ✅ (hardware) | ❌ | `dual_watch` in features |
| LAN | ✅ | ❌ | `[radio] has_lan` |
| Command29 | ✅ (dual-receiver targeting) | ❌ | `[cmd29]` section |

## Antenna System

| Feature | IC-7610 | IC-7300 | Notes |
|---------|---------|---------|-------|
| TX antennas | 2 (ANT1, ANT2) | 1 | CI-V 0x12: Max=1 on 7610 (0=ANT1, 1=ANT2) |
| RX-only antenna | ✅ (0x16 0x53, 0/1) | ❌ | Separate RX antenna input |
| Antenna per-band | ✅ | N/A | Per-band antenna memory |

**Correction:** IC-7610 has **2 TX + 1 RX antenna**, not "3 antennas".

**IC-7610 CI-V 0x12 Antenna detail:**
- `0x12 0x00` — Select/read ANT1. Sub-data: `0x00`=RX ANT OFF, `0x01`=RX ANT ON
- `0x12 0x01` — Select/read ANT2. Sub-data: `0x00`=RX ANT OFF, `0x01`=RX ANT ON
- With Command29 support
- `0x16 0x53` — ANT-RX I/O on/off (separate RX antenna connector)
- `0x1A 0x05 0x02 0x75` — RX-ANT Connectors type setting (receive antenna vs external device)
- `0x1A 0x05 0x02 0x76-0x87` — Per-band antenna memory (12 frequency ranges)
- `0x1A 0x05 0x02 0x89` — Antenna selection mode (OFF/Manual/Auto)

TOML:
```toml
[antenna]
tx_count = 2        # ANT1, ANT2
has_rx_ant = true   # Separate RX antenna jack (0x16 0x53)
has_ant_memory = true  # Per-band antenna memory
ant_mode = ["off", "manual", "auto"]  # Selection modes
```
vs IC-7300:
```toml
[antenna]
tx_count = 1
has_rx_ant = false
has_ant_memory = false
```

## RF Front End

| Feature | IC-7610 | IC-7300 | CI-V | TOML |
|---------|---------|---------|------|------|
| Attenuator | 0/3/6/9/12/15/18/21/24/27/30/33/36/39/42/45 dB | 0/20 dB | 0x11 | `[attenuator] values` |
| Preamp | OFF/P1/P2 | OFF/P1/P2 | 0x16 0x02 (0/1/2) | `[preamp] values` |
| RF Gain | 0-255 | 0-255 | 0x14 0x02 | `rf_gain` in features |
| DIGI-SEL | ✅ | ❌ | 0x16 0x4E | `digisel` in features |
| IP+ | ✅ | ❌ | 0x16 0x65 | `ip_plus` in features |

**IC-7610 ATT:** CI-V reference shows 16 discrete values: 0/3/6/9/12/15/18/21/24/27/30/33/36/39/42/45 dB. Each sent as its dB value (e.g. 0x00=OFF, 0x03=3dB, 0x06=6dB, ..., 0x45=45dB). The current TOML `[0, 6, 12, 18]` is **WRONG** — must be updated to full range.
**IC-7300 ATT:** Only 0x00=OFF, 0x20=ON (20 dB). Binary toggle.

## DSP / Noise

| Feature | IC-7610 | IC-7300 | CI-V | Notes |
|---------|---------|---------|------|-------|
| NR on/off | 0x16 0x40 (0/1) | 0x16 0x40 (0/1) | Same | Simple ON/OFF on both |
| NR level | 0x14 0x06 (0-255) | 0x14 0x06 (0-255) | Same | Continuous 0-255 |
| NB on/off | 0x16 0x22 (0/1) | 0x16 0x22 (0/1) | Same | |
| NB level | 0x14 0x12 (0-255) | 0x14 0x12 (0-255) | Same | |
| NB depth | ❓ | 0-9 (0x1A 0x05 menu) | IC-7300 menu | |
| NB width | ❓ | 0-255 (0x1A 0x05 menu) | IC-7300 menu | |
| Auto notch | 0x16 0x41 (0/1) | 0x16 0x41 (0/1) | Same | |
| Manual notch | 0x16 0x48 (0/1) | 0x16 0x48 (0/1) | Same | |
| Notch freq | 0x14 0x0D (0-255) | 0x14 0x0D (0-255) | Same | |
| APF | 0x16 0x32 (0/1/2/3) | 0x16 0x32 (0/1) | Different! | IC-7610: OFF/WIDE/MID/NAR. IC-7300: ON/OFF |
| Twin Peak | 0x16 0x4F (0/1) | 0x16 0x4F (0/1) | Same | |

**NR modes:** Both IC-7610 and IC-7300 have NR as simple ON/OFF via CI-V (0x16 0x40).
The "NR1/NR2/NR3" seen in front panels is a **menu setting**, not a direct CI-V toggle.
NR level (0x14 0x06) controls the strength. For UI: show NR as ON/OFF + level slider.

## Filter

| Feature | IC-7610 | IC-7300 | CI-V | Notes |
|---------|---------|---------|------|-------|
| PBT Inner | 0x14 0x07 (0-255) | 0x14 0x07 (0-255) | Same | Center=128, ±range |
| PBT Outer | 0x14 0x08 (0-255) | 0x14 0x08 (0-255) | Same | Center=128, ±range |
| IF Shift | N/A (use PBT) | N/A (use PBT) | — | PBT acts as IF shift |
| Filter shape | 0x16 0x56 | 0x16 0x56 | Same | SOFT/SHARP |
| Filter select | 0x06 (mode+filter) | 0x06 (mode+filter) | Same | FIL1/FIL2/FIL3 |

**Note:** IC-7610 and IC-7300 both use Twin PBT (Inner+Outer), not IF Shift.
PBT range: 0-255 BCD (00 00 ~ 02 55). Center = 128 (01 28). This maps to roughly ±1200 Hz.

## RIT / XIT

| Feature | IC-7610 | IC-7300 | CI-V | Notes |
|---------|---------|---------|------|-------|
| RIT frequency | 0x21 0x00 | 0x21 0x00 | Same | ±9.999 kHz (wfview: ±999) |
| RIT on/off | 0x21 0x01 | 0x21 0x01 | Same | |
| ∂TX (XIT) | 0x21 0x02 | 0x21 0x02 | Same | Called "∂TX" in CI-V |

**Both radios have RIT AND ∂TX (XIT)**. The IC-7300 CI-V Reference confirms 0x21 0x02 (∂TX on/off).
RIT frequency range: ±9.999 kHz per wfview (Min=-999, Max=999 in 10 Hz steps).

TOML: both get `rit` and `xit` in features.

## TX Controls

| Feature | IC-7610 | IC-7300 | CI-V | Notes |
|---------|---------|---------|------|-------|
| PTT | 0x1C 0x00 (0/1) | 0x1C 0x00 (0/1) | Same | |
| Tuner/ATU | 0x1C 0x01 (0/1/2) | 0x1C 0x01 (0/1/2) | Same | 0=off, 1=on, 2=tuning |
| Mic gain | 0x14 0x0B (0-255) | 0x14 0x0B (0-255) | Same | |
| RF power | 0x14 0x0A (0-255) | 0x14 0x0A (0-255) | Same | |
| VOX on/off | 0x16 0x46 (0/1) | 0x16 0x46 (0/1) | Same | |
| VOX gain | 0x14 0x16 (0-255) | 0x14 0x16 (0-255) | Same | |
| Anti-VOX | 0x14 0x17 (0-255) | 0x14 0x17 (0-255) | Same | |
| Compressor | 0x16 0x44 (0/1) | 0x16 0x44 (0/1) | Same | |
| Comp level | 0x14 0x0E (0-255) | 0x14 0x0E (0-255) | Same | |
| Monitor | 0x16 0x45 (0/1) | 0x16 0x45 (0/1) | Same | |
| Monitor gain | 0x14 0x15 (0-255) | 0x14 0x15 (0-255) | Same | |
| Break-in | 0x16 0x47 (0/1/2) | 0x16 0x47 (0/1/2) | Same | 0=off, 1=semi, 2=full |
| Drive gain | 0x14 0x14 (0-255) | ❌ | 7610 only | |
| Split | 0x0F (0/1) | 0x0F (0/1) | Same | |
| Data mode | 0x1A 0x06 | 0x1A 0x06 | Same | |
| CW send | 0x17 | 0x17 | Same | |
| CW pitch | 0x14 0x09 (0-255) | 0x14 0x09 (0-255) | Same | Maps to 300-900 Hz |
| Key speed | 0x14 0x0C (0-255) | 0x14 0x0C (0-255) | Same | |

## Power / System

| Feature | IC-7610 | IC-7300 | CI-V | Notes |
|---------|---------|---------|------|-------|
| Power on/off | 0x18 (0x01/0x00) | 0x18 | Same | |
| Dial lock | 0x16 0x50 (0/1) | 0x16 0x50 (0/1) | Same | |
| Scan | 0x0E | 0x0E | Same | |
| Transceiver ID | 0x19 0x00 | 0x19 0x00 | Same | |

## Max TX Power

| Radio | Max Power | Notes |
|-------|-----------|-------|
| IC-7610 | 100W | HF/6m |
| IC-7300 | 100W | HF/6m |

## Capabilities Feature List — VERIFIED

### Common (both IC-7610 and IC-7300)
```
audio, scope, meters, tx, cw, attenuator, preamp, rf_gain, af_level,
squelch, nb, nr, rit, xit, tuner, split, notch, pbt, vox, compressor,
monitor, bsr, data_mode, power_control, break_in, apf, twin_peak,
dial_lock, scan, filter_shape, antenna
```

### IC-7610 only
```
dual_rx, digisel, ip_plus, dual_watch, rx_antenna, drive_gain,
main_sub_tracking, tx_inhibit, dpd, lcd_backlight
```

**IC-7610-specific CI-V commands not in IC-7300:**
- `0x16 0x4E` — DIGI-SEL on/off
- `0x16 0x65` — IP+ on/off
- `0x14 0x13` — DIGI-SEL shift level (0-255)
- `0x14 0x14` — DRIVE gain (0-255)
- `0x16 0x5E` — MAIN/SUB Tracking on/off
- `0x16 0x66` — TX Inhibit on/off
- `0x16 0x67` — DPD (Digital Pre-Distortion) on/off
- `0x14 0x19` — LCD Backlight brightness (0-255)
- `0x07 0xC0/0xC1/0xC2` — Dualwatch off/on/read
- `0x07 0xD0/0xD1/0xD2` — Main/Sub band select/read
- `0x16 0x53` — RX Antenna on/off (with Command29)
- `0x16 0x57` — Manual Notch Width (WIDE/MID/NAR) — **verify if IC-7300 has this**
- `0x16 0x58` — SSB TX Bandwidth (WIDE/MID/NAR)
- `0x27 0x12` — Scope Main/Sub receiver select

### Parameterized differences

| Parameter | IC-7610 | IC-7300 | FTX-1 | X6100 | TX-500 | TOML section |
|-----------|---------|---------|-------|-------|--------|-------------|
| Protocol | CI-V | CI-V | Yaesu CAT | CI-V | Kenwood CAT | `[protocol]` |
| CI-V addr | 0x98 | 0x94 | — | 0x70 | — | `[radio] civ_addr` |
| ATT values | [0,3,...,45] (16) | [0, 20] | [0,1,2,3] | [0, 1] | [0, 1] | `[attenuator]` |
| ATT style | stepped | toggle | selector | toggle | toggle | `[controls.attenuator]` |
| PRE values | [0, 1, 2] | [0, 1, 2] | [0, 1, 2] | [0, 1] | [0, 1] | `[preamp]` |
| AGC modes | [1, 2, 3] | [1, 2, 3] | — | — | — | `[agc]` |
| NB style | toggle+level | toggle+level | level_is_toggle | — | — | `[controls.nb]` |
| NR style | toggle+level | toggle+level | level_is_toggle | — | — | `[controls.nr]` |
| TX antennas | 2 | 1 | 1 | 1 | 1 | `[antenna] tx_count` |
| RX antenna | yes | no | no | no | no | `[antenna] has_rx_ant` |
| Max power W | 100 | 100 | 100 | 8 | 10 | `[power] max_watts` |
| Modes | 10 | 9 | 17 | 8 | 7 | `[modes]` |
| Receivers | 2 | 1 | 2 | 1 | 1 | `[radio] receiver_count` |
| VFO scheme | main_sub | ab | ab_shared | ab | ab | `[vfo] scheme` |
| Command29 | yes | no | no | no | no | `[cmd29]` section |
| Meter cal | ✅ | — | ✅ | — | — | `[meters]` |
| Rules | mutex, disables | — | — | — | — | `[[rules]]` |
| Scope | ✅ | ✅ | — | — | — | `[spectrum]` |
| LAN | ✅ | ❌ | ❌ | ❌ | ❌ | `[radio] has_lan` |
| WiFi | ❌ | ❌ | ❌ | ✅ | ❌ | `[radio] has_wifi` |
