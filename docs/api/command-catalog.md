---
robots: noindex, follow
---

# HTTP/WS Command Catalog

Full reference for the structured command surface shared by:

- `POST /api/v1/commands` — single-command HTTP envelope
- `POST /api/v1/commands/batch` — ordered stateless batch
- `/api/v1/ws` — WebSocket `cmd` envelope

Command names and `params` objects are identical across all three interfaces.
See [Web Server API](web.md) for envelope shapes, auth, and batch semantics.

## Stability and Compatibility

Stable command names in this catalog are source-compatible within the same
`/api/v1` namespace. New optional `params` keys may be added without a version
bump. Removing params, changing param types, or adding new **required** params
requires a migration note.

Commands marked **⚠ compat alias** are accepted but callers should migrate to
the preferred name.

## Capability Gates

Several commands require a capability reported by `GET /api/v1/capabilities`.
Check the capability before sending the command in automation scripts:

```bash
curl http://127.0.0.1:8080/api/v1/capabilities | jq '.capabilities'
```

Commands with no capability gate work on all supported radios.

## Queue Model

Most commands are **queue-backed**: they enter RigPlane's ordered command queue
and are serialized through the radio backend. A few read-only and async
commands **bypass the queue** and execute directly — they are noted below.

**Batch-eligible**: all queue-backed commands may appear in
`POST /api/v1/commands/batch` steps.

**Queue-bypass commands** (`get_*`, `send_cw_text`, `stop_cw_text`,
`cw_auto_tune`, `set_tuner_status`) are not rejected from batch requests but
execute outside the batch ordering guarantee.

## Rate Limiting

`set_*` commands sent over WebSocket are rate-limited to one per 50 ms per
client (20 commands/s). Throttled commands receive an immediate ACK with
`"throttled": true`; the command is dropped. HTTP single-command and batch
paths are not rate-limited by this mechanism.

---

## Response Shape

All three interfaces return the same acknowledgement shape on success:

```json
{
  "type": "response",
  "id": "<caller-supplied id>",
  "ok": true,
  "result": { ... }
}
```

On error:

```json
{
  "type": "response",
  "id": "<caller-supplied id>",
  "ok": false,
  "error": "command_failed",
  "message": "human-readable detail"
}
```

The `result` object fields for each command are listed in the catalog below.

---

## Frequency / Mode / VFO

### `set_freq`

Set VFO frequency.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `freq` | int (Hz) | ✓ | — | Frequency in Hz, e.g. `14074000` |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB (requires `dual_rx`) |

Result: `{"freq": <Hz>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

```json
{"id": "1", "name": "set_freq", "params": {"freq": 14074000}}
```

---

### `set_band`

Recall band memory for the given Icom band index.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `band` | int | ✓ | — | Band index (radio-specific, e.g. `1`–`28`) |

Result: `{"band": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_mode`

Set operating mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | string | ✓ | — | `"LSB"`, `"USB"`, `"AM"`, `"FM"`, `"CW"`, `"CW-R"`, `"RTTY"`, `"RTTY-R"`, `"PSK"`, `"PSK-R"`, `"DV"` |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"mode": "<mode>", "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

```json
{"id": "2", "name": "set_mode", "params": {"mode": "USB"}}
```

---

### `set_filter`

Select filter preset (FIL1/FIL2/FIL3).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `filter` | string | — | `"FIL1"` | `"FIL1"`, `"FIL2"`, `"FIL3"` |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"filter": "<filter>", "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_filter_width`

Set filter passband width in Hz.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `width` | int (Hz) | ✓ | — | Filter width in Hz |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"width": <Hz>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `filter_width`

---

### `set_filter_shape`

Set filter shape (sharp/soft).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `shape` | int | ✓ | — | `0` = Soft, `1` = Sharp |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"shape": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `filter_shape`

---

### `set_if_shift`

Set IF shift offset in Hz.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `offset` | int (Hz) | ✓ | — | IF shift in Hz (negative = lower, positive = higher) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"offset": <Hz>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `if_shift`

---

### `set_data_mode`

Enable or disable DATA sub-mode on the active mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | `0` = DATA OFF, `1` = DATA ON (profile-specific numeric, not a boolean) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"mode": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `data_mode`

> **Note:** The value is a profile-specific integer, not a boolean.
> For current Icom profiles `0` = OFF, `1` = DATA.
> Do **not** use `"enabled": true/false` — that form is not accepted.

```json
{"name": "set_data_mode", "params": {"mode": 1}}
```

---

### `set_split`

Enable or disable split operation.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = split on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `split`

---

### `set_vfo` / `select_vfo`

Select active VFO. `select_vfo` is an accepted backward-compatible alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `vfo` | string | — | `"A"` | `"A"` or `"B"` |

Result: `{"vfo": "<vfo>"}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `vfo_swap`

Swap VFO A and VFO B frequencies and modes.

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `vfo_equalize`

Copy VFO A to VFO B (M→S equalize).

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_rit_status`

Enable or disable RIT (Receiver Incremental Tuning).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = RIT on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `rit`

---

### `set_rit_tx_status`

Enable or disable delta-TX RIT mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = delta-TX on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `rit`

---

### `set_rit_frequency`

Set RIT offset frequency in Hz.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `freq` | int (Hz) | — | `0` | RIT offset in Hz |

Result: `{"freq": <Hz>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `rit`

---

## Power / PTT

### `ptt`

Transmit control (key/unkey).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `state` | bool | ✓ | — | `true` = transmit, `false` = receive |

Result: `{"state": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | **TX command — rejected in read-only mode**

```json
{"id": "tx", "name": "ptt", "params": {"state": true}}
```

---

### `ptt_on`

Assert PTT (transmit).

No params.

Result: `{}`

Queue-backed ✓ | **TX command — rejected in read-only mode**

---

### `ptt_off`

Release PTT (receive).

No params.

Result: `{}`

Queue-backed ✓ | **TX command — rejected in read-only mode**

---

### `set_rf_power` / `set_power`

Set RF output power level. `set_power` is an accepted backward-compatible alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | Unit depends on radio: Icom CI-V = 0–255 (`raw_255`), Yaesu CAT = watts |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `PowerControlCapable` radio

---

### `set_powerstat`

Power the transceiver on or off via CI-V power control.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `true` | `true` = power on, `false` = power off |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `power_control`

---

### `set_drive_gain`

Set drive gain level (0–255).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `drive_gain`

---

## Audio / Levels

### `set_af_level`

Set AF (audio) output level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `af_level`

```json
{"name": "set_af_level", "params": {"level": 80}}
```

---

### `set_af_mute`

Mute or unmute the AF output.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = muted |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_af_mute`

Read the current AF mute state (bypasses queue).

| Param | Type | Required | Default |
|-------|------|----------|---------|
| `receiver` | int | — | `0` |

Result: `{"on": <bool>, "receiver": <int>}`

**Queue-bypass** (read-only) | Requires capability: `af_level`

---

### `set_rf_gain`

Set RF gain level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `rf_gain`

---

### `set_sql` / `set_squelch`

Set squelch level. `set_squelch` is an accepted alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `squelch`

---

### `set_mic_gain`

Set microphone gain level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_monitor`

Enable or disable TX monitor.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = monitor on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `monitor`

---

### `set_monitor_gain`

Set TX monitor gain level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `monitor`

---

### `set_comp` / `set_compressor`

Enable or disable speech compressor. `set_compressor` is an accepted alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `true` | `true` = compressor on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_compressor_level`

Set speech compressor level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `compressor`

---

### `set_ssb_tx_bw`

Set SSB transmit bandwidth.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | Bandwidth code (radio-specific) |

Result: `{"value": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `ssb_tx_bw`

---

### `set_acc1_mod_level`

Set ACC1 modulation input level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate (commonly requires `data_mode` radio)

```json
{"name": "set_acc1_mod_level", "params": {"level": 50}}
```

---

### `get_acc1_mod_level`

Read ACC1 modulation input level (bypasses queue).

No params.

Result: `{"level": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_usb_mod_level`

Set USB modulation input level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

```json
{"name": "set_usb_mod_level", "params": {"level": 72}}
```

---

### `get_usb_mod_level`

Read USB modulation input level (bypasses queue).

No params.

Result: `{"level": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_lan_mod_level`

Set LAN modulation input level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_lan_mod_level`

Read LAN modulation input level (bypasses queue).

No params.

Result: `{"level": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_data_off_mod_input`

Set modulation source for DATA-OFF mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `source` | int | ✓ | — | `0`=MIC, `1`=ACC, `2`=MIC+ACC, `3`=USB, `4`=MIC+USB |

Result: `{"source": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_data_off_mod_input`

Read modulation source for DATA-OFF mode (bypasses queue).

No params.

Result: `{"source": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_data1_mod_input`

Set modulation source for DATA1 mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `source` | int | ✓ | — | `0`=MIC, `1`=ACC, `2`=MIC+ACC, `3`=USB, `4`=MIC+USB |

Result: `{"source": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

```json
{"name": "set_data1_mod_input", "params": {"source": 3}}
```

---

### `get_data1_mod_input`

Read modulation source for DATA1 mode (bypasses queue).

No params.

Result: `{"source": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_data2_mod_input`

Set modulation source for DATA2 mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `source` | int | ✓ | — | `0`=MIC, `1`=ACC, `2`=MIC+ACC, `3`=USB, `4`=MIC+USB |

Result: `{"source": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_data2_mod_input`

Read modulation source for DATA2 mode (bypasses queue).

No params.

Result: `{"source": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

### `set_data3_mod_input`

Set modulation source for DATA3 mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `source` | int | ✓ | — | `0`=MIC, `1`=ACC, `2`=MIC+ACC, `3`=USB, `4`=MIC+USB |

Result: `{"source": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_data3_mod_input`

Read modulation source for DATA3 mode (bypasses queue).

No params.

Result: `{"source": <int>}`

**Queue-bypass** (read-only) | Requires capability: `data_mode`

---

## DSP: NB / NR / Notch / PBT / AGC / APF / IP+

### `set_nb`

Enable or disable noise blanker.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = NB on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nb`

---

### `set_nb_level`

Set noise blanker level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nb`

---

### `set_nb_depth`

Set noise blanker depth.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nb`

---

### `set_nb_width`

Set noise blanker width.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nb`

---

### `set_nr`

Enable or disable noise reduction.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = NR on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nr`

---

### `set_nr_level`

Set noise reduction level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `nr`

---

### `set_auto_notch`

Enable or disable automatic notch filter.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = auto-notch on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `notch`

---

### `set_manual_notch`

Enable or disable manual notch filter.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = manual notch on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `notch`

---

### `set_notch_filter`

Set manual notch filter frequency position.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 notch position |

Result: `{"value": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `notch`

---

### `set_manual_notch_width`

Set manual notch filter width.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"value": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `notch`

---

### `set_pbt_inner`

Set passband tuning inner limit.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"value": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `pbt`

---

### `set_pbt_outer`

Set passband tuning outer limit.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"value": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `pbt`

---

### `set_agc`

Set AGC mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | `0`=OFF, `1`=FAST, `2`=MID, `3`=SLOW (radio-specific) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"mode": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_agc_time_constant`

Set AGC time constant value.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"value": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_apf`

Set audio peak filter mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | `0`=OFF, `1`=ON, `2`=THROUGH (radio-specific) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"mode": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `apf`

---

### `set_audio_peak_filter`

Enable or disable audio peak filter.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = APF on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `apf`

---

### `set_twin_peak`

Enable or disable twin peak filter.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = twin peak on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `twin_peak`

---

### `set_digisel`

Enable or disable DIGI-SEL (digital IF selectivity filter).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = DIGI-SEL on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `digisel`

---

### `set_digisel_shift`

Set DIGI-SEL shift amount.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `digisel`

---

### `set_ip_plus` / `set_ipplus`

Enable or disable IP+ (roofing filter). `set_ipplus` is an accepted alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = IP+ on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `ip_plus`

---

## Antenna

### `set_att` / `set_attenuator`

Set attenuator level in dB. `set_attenuator` is an accepted alias.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int (dB) | — | `0` | Attenuation dB; also accepted as `db` |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"db": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `attenuator`

---

### `set_preamp`

Set preamplifier level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | `0`=OFF, `1`=P.AMP 1, `2`=P.AMP 2 |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"level": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `preamp`

---

### `set_antenna_1`

Enable antenna 1 for main receiver.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = select ANT1 |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_antenna_2`

Enable antenna 2 for main receiver.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = select ANT2 |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_rx_antenna_ant1`

Enable ANT1 as RX antenna for the sub receiver.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = RX antenna = ANT1 |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_rx_antenna_ant2`

Enable ANT2 as RX antenna for the sub receiver.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = RX antenna = ANT2 |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `set_rx_antenna`

Select numbered RX antenna.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `antenna` | int | ✓ | — | Antenna number (1-based, radio-specific) |
| `on` | bool | — | `false` | `true` = enable |

Result: `{"antenna": <int>, "on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `rx_antenna`

---

### `set_civ_output_ant`

Enable or disable CI-V output on the antenna port.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = CI-V output on antenna port |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_civ_output_ant`

Read CI-V output antenna port state (bypasses queue).

No params.

Result: `{"on": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `antenna`

---

## Scope / Waterfall

All scope commands require capability: `scope`.

### `switch_scope_receiver`

Switch scope to MAIN or SUB receiver.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_during_tx`

Enable or disable scope display during TX.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = scope active during TX |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_center_type`

Set scope center reference type.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `center_type` | int | ✓ | — | `0`=Center, `1`=Fixed (radio-specific) |

Result: `{"center_type": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_edge`

Set scope edge preset (1–4).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `edge` | int | ✓ | — | Edge preset index `1`–`4` |

Result: `{"edge": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_fixed_edge`

Define a fixed edge by start and end frequency.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `edge` | int | ✓ | — | Edge preset index `1`–`4` |
| `start_hz` | int (Hz) | ✓ | — | Start frequency in Hz |
| `end_hz` | int (Hz) | ✓ | — | End frequency in Hz |

Result: `{"edge": <int>, "start_hz": <Hz>, "end_hz": <Hz>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_vbw`

Set scope video bandwidth (narrow/wide).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `narrow` | bool | — | `false` | `true` = narrow VBW |

Result: `{"narrow": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_rbw`

Set scope resolution bandwidth.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `rbw` | int | — | `0` | RBW code (radio-specific) |

Result: `{"rbw": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_dual`

Enable or disable dual scope display.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `dual` | bool | ✓ | — | `true` = dual scope |

Result: `{"dual": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_mode`

Set scope operating mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | `0`=Center, `1`=Fixed (radio-specific) |

Result: `{"mode": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_span`

Set scope span in Hz.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `span` | int (Hz) | ✓ | — | Span in Hz (e.g. `500000` = 500 kHz) |

Result: `{"span": <Hz>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_speed`

Set scope sweep speed.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `speed` | int | ✓ | — | `0`=Fast, `1`=Mid, `2`=Slow (radio-specific) |

Result: `{"speed": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_ref`

Set scope reference level in dBm.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `ref` | int (dBm) | ✓ | — | Reference level (e.g. `-20`) |

Result: `{"ref": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

### `set_scope_hold`

Enable or disable scope hold.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = scope hold on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scope`

---

## CW

### `send_cw_text`

Send CW text to the radio keyer.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `text` | string | ✓ | — | Text to send, max 70 characters |

Result: `{"text": "<text>"}`

**Queue-bypass** | **TX command — rejected in read-only mode** | Requires capability: `cw`

---

### `stop_cw_text`

Stop in-progress CW transmission.

No params.

Result: `{}`

**Queue-bypass** | **TX command — rejected in read-only mode** | Requires capability: `cw`

---

### `set_cw_pitch`

Set CW sidetone pitch in Hz.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int (Hz) | ✓ | — | Pitch in Hz (e.g. `600`) |

Result: `{"value": <Hz>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `cw`

---

### `set_key_speed`

Set CW keying speed in WPM.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `speed` | int (WPM) | ✓ | — | Speed in words per minute |

Result: `{"speed": <WPM>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `cw`

---

### `set_break_in`

Set CW break-in mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | `0`=OFF, `1`=Semi, `2`=Full |

Result: `{"mode": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `break_in`

---

### `set_break_in_delay`

Set CW break-in delay level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `break_in`

---

### `get_break_in_delay`

Read CW break-in delay (bypasses queue).

No params.

Result: `{"level": <int>}`

**Queue-bypass** (read-only) | Requires capability: `break_in`

---

### `set_dash_ratio`

Set CW dash-to-dot weight ratio.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | 0–255 (radio-specific range) |

Result: `{"value": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `cw`

---

### `get_dash_ratio`

Read CW dash ratio (bypasses queue).

No params.

Result: `{"value": <int>}`

**Queue-bypass** (read-only) | Requires capability: `cw`

---

### `cw_auto_tune`

Detect CW tone via FFT and shift VFO to zero-beat. Listens for up to 3 seconds.

No params.

Result: `{"detected": <Hz> | null, "cw_pitch": <Hz>, "delta": <Hz>, "applied": <bool>}`

**Queue-bypass** (async read + optional queue write) | No capability gate

---

## VOX

### `set_vox`

Enable or disable VOX.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = VOX on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `vox`

---

### `set_vox_gain`

Set VOX sensitivity level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `vox`

---

### `set_anti_vox_gain`

Set anti-VOX gain level.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `vox`

---

### `set_vox_delay`

Set VOX hold delay.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `level` | int | ✓ | — | 0–255 |

Result: `{"level": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `vox`

---

## Tone / CTCSS / TSQL

### `set_tone_freq`

Set CTCSS tone frequency code.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `freq` | int | ✓ | — | Tone code (radio-specific index, not Hz) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"freq": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `repeater_tone`

---

### `set_tsql_freq`

Set CTCSS squelch (TSQL) frequency code.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `freq` | int | ✓ | — | TSQL code (radio-specific index) |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"freq": <int>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `tsql`

---

### `set_repeater_tone`

Enable or disable repeater tone encode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = CTCSS tone encode on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `repeater_tone`

---

### `set_repeater_tsql`

Enable or disable repeater CTCSS squelch decode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = TSQL decode on |
| `receiver` | int | — | `0` | `0` = MAIN, `1` = SUB |

Result: `{"on": <bool>, "receiver": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `tsql`

---

## Tuner

### `set_tuner_status`

Set antenna tuner state.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | `0`=OFF, `1`=ON, `2`=TUNING |

Result: `{"value": <int>, "label": "<OFF|ON|TUNING>"}`

**Queue-bypass** (direct or queue depending on radio capability) | `value=2` (TUNING) rejected in read-only mode | Requires capability: `tuner`

---

### `get_tuner_status`

Read antenna tuner state (bypasses queue).

No params.

Result: `{"status": <int>, "label": "<OFF|ON|TUNING>"}`

**Queue-bypass** (read-only) | Requires capability: `tuner`

---

## Dual Watch / Main-Sub Tracking

### `set_dual_watch`

Enable or disable dual-watch (simultaneous MAIN+SUB receive).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = dual watch on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `dual_rx`

---

### `get_dual_watch`

Read dual-watch state (bypasses queue).

No params.

Result: `{"on": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `dual_watch`

---

### `set_main_sub_tracking`

Enable or disable MAIN↔SUB frequency tracking.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = tracking on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `main_sub_tracking`

---

### `quick_dualwatch`

Composite trigger: equalize M→S then enable dual watch (emulates front-panel
long-press). Preferred over `set_quick_dual_watch` / `get_quick_dual_watch`.

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `dual_rx`

---

### `get_quick_dual_watch` / `set_quick_dual_watch`

⚠ Backward-compat aliases. These send the IC-7610 config-flag read frame
(`0x1A 05 00 33`), **not** a dual-watch toggle. Prefer `quick_dualwatch`.

No params.

Result: `{}`

Queue-backed ✓ | No capability gate

---

## Split

### `quick_split`

Composite trigger: equalize M→S then enable split (emulates front-panel
long-press). Preferred over `set_quick_split` / `get_quick_split`.

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `dual_rx`

---

### `get_quick_split` / `set_quick_split`

⚠ Backward-compat aliases. These send the IC-7610 config-flag read frame
(`0x1A 05 00 32`), **not** a split toggle. Prefer `quick_split`.

No params.

Result: `{}`

Queue-backed ✓ | No capability gate

---

## Dial / Lock

### `set_dial_lock`

Enable or disable the dial lock.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | — | `false` | `true` = dial locked |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

## Scan

All scan commands require capability: `scan`.

### `scan_start`

Start a scan sequence.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `type` | int | — | `0x01` | Scan type code (radio-specific) |

Result: `{"type": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scan`

---

### `scan_stop`

Stop the active scan.

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scan`

---

### `scan_set_df_span`

Set the delta-F scan span.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `span` | int | ✓ | — | Span code `0xA1`–`0xA7` |

Result: `{"span": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scan`

---

### `scan_set_resume`

Set the scan resume mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | ✓ | — | Mode code `0xD0`–`0xD3` |

Result: `{"mode": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `scan`

---

## Memory Channels

All memory commands require `MemoryCapable` radio (IC-7610 and other
full-featured Icom transceivers). Check for the `memory` capability in
`/api/v1/capabilities`.

### `set_memory_mode`

Recall a memory channel into the VFO (select memory mode for that channel).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `channel` | int | ✓ | — | Channel number `1`–`101` |

Result: `{"channel": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

### `memory_write`

Write current VFO to the selected memory channel.

No params.

Result: `{}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

### `memory_to_vfo`

Copy a memory channel to VFO (memory→VFO transfer).

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `channel` | int | ✓ | — | Channel number `1`–`101` |

Result: `{"channel": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

### `memory_clear`

Clear (erase) a memory channel.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `channel` | int | ✓ | — | Channel number `1`–`101` |

Result: `{"channel": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

### `set_memory_contents`

Write a fully-specified memory channel record.

Params are fields of the `MemoryChannel` dataclass. All fields are forwarded
directly. At minimum, `channel` (int, `1`–`101`) must be provided.

Result: `{"channel": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

### `set_bsr`

Write a band stack register entry.

Params are fields of the `BandStackRegister` dataclass. `band` and `register`
are required.

Result: `{"band": <int>, "register": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires `MemoryCapable` radio

---

## System / Clock / Config

### `get_system_date`

Read the radio's internal clock date (bypasses queue).

No params.

Result: `{"year": <int>, "month": <int>, "day": <int>}`

**Queue-bypass** (read-only) | Requires capability: `system_settings`

---

### `set_system_date`

Set the radio's internal clock date.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `year` | int | ✓ | — | 4-digit year |
| `month` | int | ✓ | — | `1`–`12` |
| `day` | int | ✓ | — | `1`–`31` |

Result: `{"year": <int>, "month": <int>, "day": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `system_settings`

---

### `get_system_time`

Read the radio's internal clock time (bypasses queue).

No params.

Result: `{"hour": <int>, "minute": <int>}`

**Queue-bypass** (read-only) | Requires capability: `system_settings`

---

### `set_system_time`

Set the radio's internal clock time.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `hour` | int | ✓ | — | `0`–`23` |
| `minute` | int | ✓ | — | `0`–`59` |

Result: `{"hour": <int>, "minute": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | Requires capability: `system_settings`

---

### `get_ref_adjust`

Read reference oscillator adjustment (bypasses queue).

No params.

Result: `{"value": <int>}`

**Queue-bypass** (read-only) | Requires capability: `system_settings`

---

### `set_ref_adjust`

Set reference oscillator adjustment.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `value` | int | ✓ | — | Signed adjustment value (radio-specific range) |

Result: `{"value": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_civ_transceive`

Read CI-V transceive mode (bypasses queue).

No params.

Result: `{"on": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `system_settings`

---

### `set_civ_transceive`

Enable or disable CI-V transceive mode.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = transceive on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_tuning_step`

Read VFO tuning step (bypasses queue).

No params.

Result: `{"step": <int>}`

**Queue-bypass** (read-only) | Requires capability: `tuning_step`

---

### `set_tuning_step`

Set VFO tuning step.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `step` | int | ✓ | — | Step code (radio-specific) |

Result: `{"step": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_utc_offset`

Read UTC offset (bypasses queue).

No params.

Result: `{"hours": <int>, "minutes": <int>, "is_negative": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `system_settings`

---

### `set_utc_offset`

Set UTC offset.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `hours` | int | ✓ | — | `0`–`13` |
| `minutes` | int | ✓ | — | `0` or `30` or `45` |
| `is_negative` | bool | ✓ | — | `true` = negative offset (behind UTC) |

Result: `{"hours": <int>, "minutes": <int>, "is_negative": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

## XFC / TX Frequency Monitor / Band Edge

### `get_band_edge_freq`

Read band edge frequency (bypasses queue).

No params.

Result: `{"freq": <Hz>}`

**Queue-bypass** (read-only) | Requires capability: `band_edge`

---

### `get_xfc_status`

Read XFC (cross-band full-duplex) status (bypasses queue).

No params.

Result: `{"on": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `xfc`

---

### `set_xfc_status`

Enable or disable XFC.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = XFC on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

### `get_tx_freq_monitor`

Read TX frequency monitor state (bypasses queue).

No params.

Result: `{"on": <bool>}`

**Queue-bypass** (read-only) | Requires capability: `tx`

---

### `set_tx_freq_monitor`

Enable or disable TX frequency monitoring.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `on` | bool | ✓ | — | `true` = TX freq monitor on |

Result: `{"on": <bool>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

## Speak

### `speak`

Trigger the radio's voice synthesizer to announce the current settings.

| Param | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `mode` | int | — | `0` | Announcement mode (radio-specific) |

Result: `{"mode": <int>}`

Queue-backed ✓ | Batch-eligible ✓ | No capability gate

---

## Batch Examples

### Frequency / Mode / Audio profile

Switch to 14.074 MHz USB DATA with USB audio input:

```json
{
  "id": "ft8-profile",
  "steps": [
    {"name": "set_freq",           "params": {"freq": 14074000}},
    {"name": "set_mode",           "params": {"mode": "USB"}},
    {"name": "set_data_mode",      "params": {"mode": 1}},
    {"name": "set_data1_mod_input","params": {"source": 3}},
    {"name": "set_usb_mod_level",  "params": {"level": 72}},
    {"name": "set_af_level",       "params": {"level": 80}}
  ]
}
```

### VARA FM on 144.390 MHz

```json
{
  "id": "vara-fm",
  "steps": [
    {"name": "set_freq",            "params": {"freq": 144390000}},
    {"name": "set_mode",            "params": {"mode": "FM"}},
    {"name": "set_data_mode",       "params": {"mode": 1}},
    {"name": "set_data1_mod_input", "params": {"source": 3}},
    {"name": "set_usb_mod_level",   "params": {"level": 72}},
    {"name": "set_af_level",        "params": {"level": 72}}
  ]
}
```

### Memory/channel recall

Recall channel 5, then copy it to VFO for editing:

```json
{
  "id": "recall-ch5",
  "steps": [
    {"name": "memory_to_vfo", "params": {"channel": 5}}
  ]
}
```

### Dual watch enable

Equalize MAIN→SUB then enable dual watch in one step:

```json
{
  "id": "dw-on",
  "steps": [
    {"name": "quick_dualwatch", "params": {}}
  ]
}
```

---

## Regression Check

The set of documented command names is checked against
`ControlHandler._COMMANDS` by `tests/test_command_catalog.py`.
If a command is added to `_COMMANDS` without updating this catalog, the test
will fail. This prevents silent catalog drift.

Run the check with:

```bash
uv run pytest tests/test_command_catalog.py -v
```
