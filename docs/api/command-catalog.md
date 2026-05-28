---
robots: noindex, follow
---

# HTTP / WebSocket Command Catalog

Complete reference for every structured command accepted by:

- `POST /api/v1/commands` — single command over HTTP;
- `POST /api/v1/commands/batch` — ordered stateless batch over HTTP;
- `/api/v1/ws` — `cmd` envelope on the control WebSocket channel.

All three surfaces share the same command names and `params` shapes. See
[Web Server API](web.md) for envelope format, error codes, batch semantics, and
protocol examples.

## Reading this catalog

| Column | Meaning |
|--------|---------|
| **Command** | Exact `name` string to send. Canonical names preferred; deprecated aliases noted. |
| **Params** | Required fields (no `?`) and optional fields (`?`, with default). |
| **Capability** | Capability flag that must appear in `GET /api/v1/capabilities` for this command to succeed. `—` means no gate. |
| **Batch** | `Yes` — goes through the ordered command queue; eligible for `POST /api/v1/commands/batch`. `No` — bypasses the queue; use single-command `POST /api/v1/commands` or WebSocket. |
| **Notes** | Aliases, caveats, stability. |

**`receiver` parameter:** where listed, `0` = MAIN (or the only receiver), `1` = SUB. Values are validated against the active profile's receiver count; out-of-range values fail with `command_failed`.

**Response shape:** on success the `result` object echoes accepted parameter values. Non-trivial response fields are listed in the Notes column.

## Rate limiting

`set_*` commands over WebSocket are rate-limited to one per 50 ms per client. Commands arriving before the interval expires receive an immediate ACK with `{"throttled": true}` and are not enqueued. HTTP endpoints are not throttled at this layer.

## Batch eligibility rules

A command is batch-eligible when all of the following are true:

1. It is in `ControlHandler._COMMANDS`.
2. It is **not** in `ControlHandler._READ_ONLY_HANDLERS` (i.e., it goes through the ordered command queue).
3. It produces exactly one queued command.

Commands marked `Batch: No` in the table return error `unsupported_in_batch` when used in a batch. Send those via `POST /api/v1/commands` instead. Note: `get_quick_split` and `get_quick_dual_watch` have `Batch: Yes` despite the `get_` prefix — they enqueue write operations and are queue-backed.

Maximum batch size: 128 steps. Per-step timeout: 10 seconds.

---

<!-- catalog:begin -->

## Frequency, mode, VFO, RIT, split, data mode

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_freq` | `freq: int` (Hz), `receiver?: int=0` | — | Yes | |
| `set_band` | `band: int` (BSR code) | — | Yes | BSR code from `freqRanges[].bands[].bsrCode` in capabilities. See BSR workflow below. |
| `set_mode` | `mode: str`, `receiver?: int=0` | — | Yes | Mode strings from profile `modes[]`, e.g. `"USB"`, `"FM"`, `"CW"`. |
| `set_filter` | `filter?: str="FIL1"`, `receiver?: int=0` | — | Yes | Accepted: `"FIL1"`, `"FIL2"`, `"FIL3"`. |
| `set_filter_width` | `width: int` (Hz), `receiver?: int=0` | — | Yes | Passband width; radio-specific valid range. |
| `set_filter_shape` | `shape: int`, `receiver?: int=0` | `filter_shape` | Yes | |
| `set_if_shift` | `offset: int`, `receiver?: int=0` | `if_shift` | Yes | Offset relative to center in Hz. |
| `set_rit_status` | `on?: bool=false` | `rit` | Yes | |
| `set_rit_tx_status` | `on?: bool=false` | `rit` | Yes | |
| `set_rit_frequency` | `freq?: int=0` (Hz offset) | `rit` | Yes | |
| `set_split` | `on?: bool=false` | `split` | Yes | |
| `set_vfo` | `vfo?: str="A"` | — | Yes | Canonical. Values: `"A"` or `"B"`. |
| `select_vfo` | `vfo?: str="A"` | — | Yes | Alias for `set_vfo`. |
| `vfo_swap` | — | — | Yes | Swap VFO A↔B (or MAIN↔SUB). |
| `vfo_equalize` | — | — | Yes | Copy active VFO frequency to the inactive VFO. |
| `set_data_mode` | `mode: int`, `receiver?: int=0` | `data_mode` | Yes | `mode`: 0=DATA-OFF, 1=DATA1, 2=DATA2, 3=DATA3. |

### `set_band` BSR workflow

1. Backend sends CI-V `0x1A 0x01 <band> 0x01` to recall the Band Stack Register.
2. On a valid response the radio restores the saved frequency, mode, and filter for that band.
3. On timeout or a short response the backend falls back to the profile's `default_hz` for the matching `bsr_code`.
4. If no band with that `bsr_code` exists in the profile, no retune is applied.

Use `set_freq` directly for bands that have no `bsrCode` in capabilities.

---

## Power, PTT, drive

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `ptt` | `state: bool` | — | Yes | `true`=TX on, `false`=TX off. Rejected in read-only mode. |
| `ptt_on` | — | — | Yes | Equivalent to `ptt` with `state: true`. Rejected in read-only mode. |
| `ptt_off` | — | — | Yes | Equivalent to `ptt` with `state: false`. Rejected in read-only mode. |
| `set_rf_power` | `level: int` | `power_control` | Yes | Canonical. Level scale: 0–255 raw (Icom CI-V); watts (Yaesu CAT). Requires `PowerControlCapable`. |
| `set_power` | `level: int` | `power_control` | Yes | Alias for `set_rf_power`. |
| `set_powerstat` | `on?: bool=true` | `power_control` | Yes | Power the radio on or off via CI-V. |
| `set_drive_gain` | `level: int` | `drive_gain` | Yes | Drive gain; radio-specific range. |

---

## DSP — NR, NB, AGC, notch, PBT, APF, DIGI-SEL, IP+, twin peak

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_nr` | `on?: bool=false`, `receiver?: int=0` | `nr` | Yes | Noise reduction on/off. |
| `set_nr_level` | `level: int`, `receiver?: int=0` | `nr` | Yes | |
| `set_nb` | `on?: bool=false`, `receiver?: int=0` | `nb` | Yes | Noise blanker on/off. |
| `set_nb_level` | `level: int`, `receiver?: int=0` | `nb` | Yes | |
| `set_nb_depth` | `level: int`, `receiver?: int=0` | `nb` | Yes | |
| `set_nb_width` | `level: int`, `receiver?: int=0` | `nb` | Yes | |
| `set_auto_notch` | `on?: bool=false`, `receiver?: int=0` | `notch` | Yes | |
| `set_manual_notch` | `on?: bool=false`, `receiver?: int=0` | `notch` | Yes | |
| `set_notch_filter` | `value: int` | `notch` | Yes | No `receiver` param. |
| `set_manual_notch_width` | `value: int`, `receiver?: int=0` | `notch` | Yes | |
| `set_digisel` | `on?: bool=false`, `receiver?: int=0` | `digisel` | Yes | IC-7610 DIGI-SEL on/off. |
| `set_digisel_shift` | `level: int`, `receiver?: int=0` | `digisel` | Yes | |
| `set_ip_plus` | `on?: bool=false`, `receiver?: int=0` | `ip_plus` | Yes | Canonical. |
| `set_ipplus` | `on?: bool=false`, `receiver?: int=0` | `ip_plus` | Yes | Alias for `set_ip_plus`. |
| `set_pbt_inner` | `value: int`, `receiver?: int=0` | `pbt` | Yes | Passband tuning inner. |
| `set_pbt_outer` | `value: int`, `receiver?: int=0` | `pbt` | Yes | Passband tuning outer. |
| `set_agc` | `mode: int`, `receiver?: int=0` | — | Yes | AGC mode; 0=OFF, 1=FAST, 2=MID, 3=SLOW (radio-specific). |
| `set_agc_time_constant` | `value: int`, `receiver?: int=0` | — | Yes | |
| `set_apf` | `mode: int`, `receiver?: int=0` | `apf` | Yes | Audio Peak Filter mode. |
| `set_audio_peak_filter` | `on?: bool=false`, `receiver?: int=0` | `apf` | Yes | APF on/off toggle. |
| `set_twin_peak` | `on?: bool=false`, `receiver?: int=0` | `twin_peak` | Yes | |

---

## Audio — AF level, squelch, monitor, mic, compressor, modulation inputs

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_af_level` | `level: int`, `receiver?: int=0` | `af_level` | Yes | AF volume; 0–255 raw scale. |
| `set_rf_gain` | `level: int`, `receiver?: int=0` | `rf_gain` | Yes | |
| `set_sql` | `level: int`, `receiver?: int=0` | `squelch` | Yes | Canonical. |
| `set_squelch` | `level: int`, `receiver?: int=0` | `squelch` | Yes | Alias for `set_sql`. |
| `set_mic_gain` | `level: int` | — | Yes | No `receiver` param. |
| `set_compressor_level` | `level: int` | `compressor` | Yes | No `receiver` param. |
| `set_comp` | `on?: bool=true` | — | Yes | Canonical. TX speech compressor on/off. |
| `set_compressor` | `on?: bool=true` | — | Yes | Alias for `set_comp`. |
| `set_monitor` | `on?: bool=false` | `monitor` | Yes | TX monitor on/off. |
| `set_monitor_gain` | `level: int` | `monitor` | Yes | |
| `set_af_mute` | `on: bool`, `receiver?: int=0` | — | Yes | `on` is required (no default). |
| `set_acc1_mod_level` | `level: int` | — | Yes | ACC1 modulation input level. |
| `set_usb_mod_level` | `level: int` | — | Yes | USB modulation input level. |
| `set_lan_mod_level` | `level: int` | — | Yes | LAN modulation input level. |
| `set_data_off_mod_input` | `source: int` | — | Yes | DATA-OFF modulation input source. |
| `set_data1_mod_input` | `source: int` | — | Yes | DATA 1 modulation input source. |
| `set_data2_mod_input` | `source: int` | — | Yes | DATA 2 modulation input source. |
| `set_data3_mod_input` | `source: int` | — | Yes | DATA 3 modulation input source. |
| `set_ssb_tx_bw` | `value: int` | `ssb_tx_bw` | Yes | SSB TX bandwidth selection. |

---

## Spectrum scope

All scope commands require the `scope` capability.

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `switch_scope_receiver` | `receiver?: int=0` | `scope` | Yes | |
| `set_scope_during_tx` | `on: bool` | `scope` | Yes | `on` is required. |
| `set_scope_center_type` | `center_type: int` | `scope` | Yes | |
| `set_scope_edge` | `edge: int` | `scope` | Yes | Edge preset index. |
| `set_scope_fixed_edge` | `edge: int`, `start_hz: int`, `end_hz: int` | `scope` | Yes | All three params required. |
| `set_scope_vbw` | `narrow?: bool=false` | `scope` | Yes | Video bandwidth narrow/wide. |
| `set_scope_rbw` | `rbw?: int=0` | `scope` | Yes | Resolution bandwidth preset. |
| `set_scope_dual` | `dual: bool` | `scope` | Yes | `dual` is required. |
| `set_scope_mode` | `mode: int` | `scope` | Yes | |
| `set_scope_span` | `span: int` | `scope` | Yes | Span in Hz; radio-specific valid values. |
| `set_scope_speed` | `speed: int` | `scope` | Yes | |
| `set_scope_ref` | `ref: int` | `scope` | Yes | Reference level. |
| `set_scope_hold` | `on: bool` | `scope` | Yes | `on` is required. |

---

## Antenna — attenuator, preamp, antenna select

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_att` | `level?: int=0` or `db?: int=0`, `receiver?: int=0` | `attenuator` | Yes | Canonical. Accepts either `level` or `db` key; `level` takes precedence. |
| `set_attenuator` | `level?: int=0` or `db?: int=0`, `receiver?: int=0` | `attenuator` | Yes | Alias for `set_att`. |
| `set_preamp` | `level: int`, `receiver?: int=0` | `preamp` | Yes | |
| `set_antenna_1` | `on?: bool=false` | — | Yes | TX antenna 1 selection. |
| `set_antenna_2` | `on?: bool=false` | — | Yes | TX antenna 2 selection. |
| `set_rx_antenna_ant1` | `on?: bool=false` | — | Yes | RX antenna 1 selection. |
| `set_rx_antenna_ant2` | `on?: bool=false` | — | Yes | RX antenna 2 selection. |
| `set_rx_antenna` | `antenna: int`, `on?: bool=false` | `rx_antenna` | Yes | |
| `set_civ_output_ant` | `on: bool` | — | Yes | CI-V output antenna selection. `on` is required. |

---

## System — date/time, CW, VOX, dial-lock, dual-watch, scan, tones, ref, UTC

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_system_date` | `year: int`, `month: int`, `day: int` | — | Yes | |
| `set_system_time` | `hour: int`, `minute: int` | — | Yes | |
| `set_cw_pitch` | `value: int` (Hz) | `cw` | Yes | Sidetone pitch; typically 300–900 Hz. |
| `set_key_speed` | `speed: int` (WPM) | `cw` | Yes | |
| `set_break_in` | `mode: int` | `break_in` | Yes | 0=OFF, 1=SEMI, 2=FULL. |
| `set_break_in_delay` | `level: int` | `break_in` | Yes | |
| `set_dash_ratio` | `value: int` | `cw` | Yes | |
| `set_vox` | `on?: bool=false` | `vox` | Yes | |
| `set_vox_gain` | `level: int` | `vox` | Yes | |
| `set_anti_vox_gain` | `level: int` | `vox` | Yes | |
| `set_vox_delay` | `level: int` | `vox` | Yes | |
| `speak` | `mode?: int=0` | — | Yes | Text-to-speech; mode is radio-specific. |
| `set_dial_lock` | `on?: bool=false` | — | Yes | |
| `set_dual_watch` | `on?: bool=false` | `dual_rx` | Yes | |
| `set_main_sub_tracking` | `on?: bool=false` | `main_sub_tracking` | Yes | |
| `scan_start` | `type?: int=0x01` | `scan` | Yes | Scan type byte; `0x01` = programmed scan. |
| `scan_stop` | — | `scan` | Yes | |
| `scan_set_df_span` | `span: int` | `scan` | Yes | `span` must be 0xA1–0xA7 (decimal 161–167). |
| `scan_set_resume` | `mode: int` | `scan` | Yes | `mode` must be 0xD0–0xD3 (decimal 208–211). |
| `set_repeater_tone` | `on?: bool=false`, `receiver?: int=0` | `repeater_tone` | Yes | |
| `set_tone_freq` | `freq: int`, `receiver?: int=0` | `repeater_tone` | Yes | CTCSS tone frequency. |
| `set_repeater_tsql` | `on?: bool=false`, `receiver?: int=0` | `tsql` | Yes | |
| `set_tsql_freq` | `freq: int`, `receiver?: int=0` | `tsql` | Yes | CTCSS squelch frequency. |
| `set_ref_adjust` | `value: int` | — | Yes | Reference frequency adjustment. |
| `set_civ_transceive` | `on: bool` | — | Yes | CI-V transceive mode. `on` is required. |
| `set_tuning_step` | `step: int` | — | Yes | Tuning step in Hz. |
| `set_utc_offset` | `hours: int`, `minutes: int`, `is_negative: bool` | — | Yes | All three params required. |

---

## Memory channels and BSR

All memory commands require the radio to implement `MemoryCapable`. Radios without
the memory protocol reject these commands.

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `set_memory_mode` | `channel: int` (1–101) | MemoryCapable | Yes | Switch to memory channel mode. |
| `memory_write` | — | MemoryCapable | Yes | Write current VFO state to the active memory channel. |
| `memory_to_vfo` | `channel: int` (1–101) | MemoryCapable | Yes | Recall channel to VFO. |
| `memory_clear` | `channel: int` (1–101) | MemoryCapable | Yes | Clear a memory channel. |
| `set_memory_contents` | `MemoryChannel` fields | MemoryCapable | Yes | Write memory fields; params must match the `MemoryChannel` dataclass. |
| `set_bsr` | `BandStackRegister` fields | MemoryCapable | Yes | Write Band Stack Register; params must match the `BandStackRegister` dataclass. |

---

## Miscellaneous — XFC, TX freq monitor, quick split/dual watch

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `send_civ` | `command: int`, `sub?: int`, `data?: str=""`, `wait_response?: bool=false` | CivCommandCapable | Yes | Fire-and-forget raw CI-V write through the ordered queue. `command` and `sub` are byte values; `data` is compact even-length hex. `wait_response: true` is rejected; use `POST /api/v1/civ/transaction` for ACK/data responses. Rejected in read-only mode. |
| `set_xfc_status` | `on: bool` | — | Yes | XFC on/off. `on` is required. |
| `set_tx_freq_monitor` | `on: bool` | — | Yes | TX frequency monitor. `on` is required. |
| `get_quick_split` | — | — | Yes | Enqueues `QuickSplit`. The `get_` prefix is a misnomer; no data is read. Experimental — see issue #774. |
| `set_quick_split` | — | — | Yes | Alias for `get_quick_split`. Experimental — see issue #774. |
| `get_quick_dual_watch` | — | — | Yes | Enqueues `QuickDualWatch`. Experimental — see issue #774. |
| `set_quick_dual_watch` | — | — | Yes | Alias for `get_quick_dual_watch`. Experimental — see issue #774. |
| `quick_dualwatch` | — | `dual_rx` | Yes | Composite trigger: equalize MAIN→SUB then enable dual watch (emulates front-panel long-press). |
| `quick_split` | — | `dual_rx` | Yes | Composite trigger: equalize MAIN→SUB then enable split (emulates front-panel long-press). |

---

## Read-only and queue-bypassing commands

These commands bypass the ordered command queue (they invoke the radio protocol
directly and/or use `asyncio`). They are **not** batch-eligible. Use
`POST /api/v1/commands` or the WebSocket `cmd` envelope for these one-off calls.

| Command | Params | Capability | Batch | Notes |
|---------|--------|------------|-------|-------|
| `get_system_date` | — | `system_settings` | No | Returns `{year, month, day}`. |
| `get_system_time` | — | `system_settings` | No | Returns `{hour, minute}`. |
| `get_dual_watch` | — | `dual_watch` | No | Returns `{on: bool}`. |
| `get_tuner_status` | — | `tuner` | No | Returns `{status: int, label: str}`. `label`: `"OFF"`, `"ON"`, or `"TUNING"`. |
| `send_cw_text` | `text: str` (≤512 chars) | `cw` | No | Sends CW keyed text. TX command — rejected in read-only mode. |
| `stop_cw_text` | — | `cw` | No | Stops CW text transmission. TX command — rejected in read-only mode. |
| `get_break_in_delay` | — | `break_in` | No | Returns `{level: int}`. |
| `get_dash_ratio` | — | `cw` | No | Returns `{value: int}`. |
| `get_acc1_mod_level` | — | `data_mode` | No | Returns `{level: int}`. |
| `get_usb_mod_level` | — | `data_mode` | No | Returns `{level: int}`. |
| `get_lan_mod_level` | — | `data_mode` | No | Returns `{level: int}`. |
| `get_data_off_mod_input` | — | `data_mode` | No | Returns `{source: int}`. |
| `get_data1_mod_input` | — | `data_mode` | No | Returns `{source: int}`. |
| `get_data2_mod_input` | — | `data_mode` | No | Returns `{source: int}`. |
| `get_data3_mod_input` | — | `data_mode` | No | Returns `{source: int}`. |
| `set_tuner_status` | `value: int` | `tuner` | No | Values: 0=OFF, 1=ON, 2=TUNING. Bypasses queue via direct radio call when possible. `value=2` rejected in read-only mode. Returns `{value, label}`. |
| `get_ref_adjust` | — | `system_settings` | No | Returns `{value: int}`. |
| `get_civ_transceive` | — | `system_settings` | No | Returns `{on: bool}`. |
| `get_civ_output_ant` | — | `antenna` | No | Returns `{on: bool}`. |
| `get_af_mute` | `receiver?: int=0` | `af_level` | No | Returns `{on: bool, receiver: int}`. |
| `get_tuning_step` | — | `tuning_step` | No | Returns `{step: int}`. |
| `get_utc_offset` | — | `system_settings` | No | Returns `{hours, minutes, is_negative}`. |
| `get_band_edge_freq` | — | `band_edge` | No | Returns `{freq: int}` (Hz). |
| `get_xfc_status` | — | `xfc` | No | Returns `{on: bool}`. |
| `get_tx_freq_monitor` | — | `tx` | No | Returns `{on: bool}`. |
| `cw_auto_tune` | — | — | No | Detects CW tone via audio FFT and shifts VFO to zero-beat. Requires active audio relay; times out after 3 s. On successful detection: `{detected: int, cw_pitch: int, delta: int, applied: bool}`. On timeout or no tone found: `{detected: null, applied: false}`. Experimental. |

<!-- catalog:end -->

---

## Profile-switching batch examples

Check `GET /api/v1/capabilities` before building model-specific batches. The
`receivers`, `modes`, `filters`, and feature flags differ between radio profiles.

### VARA-FM on IC-9700

```json
{
  "id": "vara-fm",
  "steps": [
    {"name": "set_freq",      "params": {"freq": 144030000, "receiver": 0}},
    {"name": "set_mode",      "params": {"mode": "FM",      "receiver": 0}},
    {"name": "set_data_mode", "params": {"mode": 1,         "receiver": 0}},
    {"name": "set_af_level",  "params": {"level": 72,       "receiver": 0}},
    {"name": "set_squelch",   "params": {"level": 0,        "receiver": 0}}
  ]
}
```

### Voice FM on IC-9700

```json
{
  "id": "fm-voice",
  "steps": [
    {"name": "set_mode",      "params": {"mode": "FM", "receiver": 0}},
    {"name": "set_data_mode", "params": {"mode": 0,    "receiver": 0}},
    {"name": "set_af_level",  "params": {"level": 50,  "receiver": 0}}
  ]
}
```

### HF data mode (IC-7610, FT8)

```json
{
  "id": "ft8-14mhz",
  "steps": [
    {"name": "set_freq",      "params": {"freq": 14074000, "receiver": 0}},
    {"name": "set_mode",      "params": {"mode": "USB",    "receiver": 0}},
    {"name": "set_data_mode", "params": {"mode": 1,        "receiver": 0}},
    {"name": "set_filter",    "params": {"filter": "FIL1", "receiver": 0}},
    {"name": "set_af_level",  "params": {"level": 50,      "receiver": 0}}
  ]
}
```

### Memory channel recall (IC-7610)

```json
{
  "id": "recall-ch10",
  "steps": [
    {"name": "memory_to_vfo", "params": {"channel": 10}}
  ]
}
```

---

## Stability notes

- **Stable:** all commands not listed as experimental below.
- **Experimental:** `cw_auto_tune` (requires audio relay; FFT peak detection; fails if RX audio is inactive). `get_quick_split`, `set_quick_split`, `get_quick_dual_watch`, `set_quick_dual_watch` (the `get_/set_quick_*` names send a config-flag CI-V frame — see issue #774 — rather than the intended toggle; prefer `quick_split` / `quick_dualwatch` for the composite trigger).
- **Deprecated aliases (kept for backwards compatibility):** `select_vfo`, `set_power`, `set_squelch`, `set_ipplus`, `set_attenuator`, `set_comp`, `set_compressor`. Use the canonical names listed above in new code.

---

## Catalog drift check

`tests/test_command_catalog_docs.py` validates this file against `ControlHandler._COMMANDS`. The test extracts first-column command names from the tables between the `<!-- catalog:begin -->` and `<!-- catalog:end -->` markers and asserts set equality with the live frozenset. A second test verifies that every `_READ_ONLY_HANDLERS` entry is marked `No` in the Batch column.

To add a new command: update `ControlHandler._COMMANDS` in `src/rigplane/web/handlers/control.py`, implement the dispatch branch, then add a row to the appropriate table in this catalog. CI fails if the catalog diverges from the source.

## See also

- [Web Server API](web.md) — endpoint reference, error codes, rate limiting, batch semantics
- [Web UI Guide](../guide/web-ui.md) — operational guide, quick-start, WebSocket examples
- [Capabilities Matrix](../capabilities-matrix.md) — per-radio feature support
