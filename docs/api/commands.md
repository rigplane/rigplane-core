---
robots: noindex, follow
---

# Commands Module

Low-level CI-V command encoding and decoding. Most users should use the high-level **Radio** API (via [`create_radio`](public-api-surface.md)).

## `cmd_map` is required

Every command builder takes `cmd_map` as a **required, keyword-only** parameter — there is no
hardcoded fallback path. Checked directly against every non-underscore function defined in
`src/rigplane/commands/*.py` (excluding `_frame.py`): 244 distinct builder functions require
`cmd_map` (256 counting names re-exported twice for backward compatibility, e.g.
`antenna.py: get_antenna` aliasing `get_antenna_1`); the remaining 33 exported functions are
`parse_*` response decoders, which take no `cmd_map` because they decode a frame the radio
already sent, with nothing left to look up.

Calling a builder without a real map — `cmd_map` omitted, or passed explicitly as `None` —
raises `TypeError` rather than silently building a wrong frame. Both shapes are handled by
`commands/_frame.py: require_cmd_map`, applied to every migrated builder: a call missing
`cmd_map` entirely gets Python's own missing-argument `TypeError` with an explanation appended,
and a call passing `cmd_map=None` gets a dedicated `TypeError` before the builder body ever runs.

```python
from rigplane.commands import get_af_level

get_af_level(to_addr=0x98)  # TypeError: missing keyword-only argument 'cmd_map'
get_af_level(to_addr=0x98, cmd_map=None)  # TypeError: cmd_map is None -- ...
```

### Calling a builder directly

```python
from pathlib import Path
from rigplane.rig_loader import load_rig
from rigplane.commands import get_af_level

cfg = load_rig(Path("rigs/ic7300.toml"))
cmd_map = cfg.to_command_map()

frame = get_af_level(to_addr=0x94, cmd_map=cmd_map)
```

### Recommended: `BoundCommands`

`commands/bound.py: BoundCommands` binds a radio's `CommandMap` once, at construction, so call
sites never pass `cmd_map` themselves:

```python
from rigplane.commands.bound import BoundCommands

bound = BoundCommands(cmd_map)
frame = bound.get_af_level(to_addr=0x94)
```

`runtime/radio.py: CoreRadio` constructs one `BoundCommands` per radio and uses it for every
migrated builder.

### The undeclared-command policy (three states, not two)

A command a profile's `CommandMap` does not declare is not silently ignored. `BoundCommands`
classifies every miss into one of three states:

1. **Declared** — the profile has an entry; the builder sends its bytes.
2. **Declared absent** — the profile records the radio as confirmed not to have this command,
   naming a source; calling it raises `core.exceptions.CommandError` quoting that source.
3. **Unknown** — neither declared nor recorded absent. This state is not expected to exist in a
   released profile (a coverage test enumerates every builder against every profile), but if
   reached, `BoundCommands` refuses the same way as state 2 and, once, invokes the optional
   `on_undeclared` hook before raising.

Neither state 2 nor state 3 logs and continues: both raise `CommandError`, so a caller cannot
observe a command that silently did nothing.

See [`docs/api/rig-loader.md`](rig-loader.md) for the `CommandMap` class reference.

::: rigplane.commands

## CI-V Frame Format

```
FE FE <to> <from> <cmd> [<sub>] [<data>...] FD
```

- `FE FE` — preamble (2 bytes)
- `<to>` — destination CI-V address (1 byte)
- `<from>` — source CI-V address (1 byte)
- `<cmd>` — command byte (1 byte)
- `<sub>` — optional sub-command (1 byte)
- `<data>` — optional payload (variable length)
- `FD` — terminator (1 byte)

## Constants

```python
from rigplane import IC_7610_ADDR, CONTROLLER_ADDR

IC_7610_ADDR   # 0x98 — IC-7610's default CI-V address
CONTROLLER_ADDR  # 0xE0 — Controller address (us)
```

## Frame Building

### `build_civ_frame()`

```python
def build_civ_frame(
    to_addr: int,
    from_addr: int,
    command: int,
    sub: int | None = None,
    data: bytes | None = None,
) -> bytes
```

Build a raw CI-V frame.

### `parse_civ_frame()`

```python
def parse_civ_frame(data: bytes) -> CivFrame
```

Parse raw bytes into a `CivFrame` dataclass.

## Command Builders

Each function returns raw CI-V frame bytes ready to send. `cmd_map` is required and
keyword-only on every one of these; omitted here for brevity — see the sections above for the
full contract.

### Frequency

```python
get_frequency(to_addr=0x98, cmd_map=cmd_map) -> bytes
set_frequency(freq_hz: int, to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### Mode

```python
get_mode(to_addr=0x98, cmd_map=cmd_map) -> bytes
set_mode(mode: Mode, filter_width: int | None = None, *, to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### RF Power

```python
get_rf_power(to_addr=0x98, cmd_map=cmd_map) -> bytes
set_rf_power(level: int, to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### Meters

```python
get_s_meter(to_addr=0x98, cmd_map=cmd_map) -> bytes
get_swr(to_addr=0x98, cmd_map=cmd_map) -> bytes
get_alc(to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### PTT

```python
ptt_on(to_addr=0x98, cmd_map=cmd_map) -> bytes
ptt_off(to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### VFO

```python
# ``code`` is the rig's selector byte, from the profile's
# ``[vfo] main_select`` / ``sub_select`` — this builder holds no
# name-to-byte table. ``radio.py: CoreRadio._set_vfo_wire`` resolves it.
select_vfo(code: int, *, to_addr=0x98, cmd_map=cmd_map) -> bytes
set_split(on: bool, to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### RF Controls (Command29-aware)

All RF control commands use `build_cmd29_frame()` for dual-receiver compatibility.

```python
# Frame builder for Command29-wrapped commands
build_cmd29_frame(to_addr, from_addr, command, sub=None, data=None, receiver=RECEIVER_MAIN) -> bytes

# Attenuator
get_attenuator(to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes
set_attenuator_level(db: int, to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes
# No set_attenuator(bool) builder at this layer (MOR-2086): a command
# builder cannot see the profile, so it cannot resolve on/off to a valid
# dB value. Use runtime/radio.py: CoreRadio.set_attenuator instead, which
# resolves against the connected profile's declared values.

# Preamp
get_preamp(to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes
set_preamp(level: int = 1, *, to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes

# DIGI-SEL
get_digisel(to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes
set_digisel(on: bool, to_addr=0x98, receiver=RECEIVER_MAIN, cmd_map=cmd_map) -> bytes
```

Constants: `RECEIVER_MAIN = 0x00`, `RECEIVER_SUB = 0x01`

### CW

```python
send_cw(text: str, to_addr=0x98, cmd_map=cmd_map) -> list[bytes]  # Returns multiple frames
stop_cw(to_addr=0x98, cmd_map=cmd_map) -> bytes
```

### Power Control

```python
power_on(to_addr=0x98, cmd_map=cmd_map) -> bytes
power_off(to_addr=0x98, cmd_map=cmd_map) -> bytes
```

## Response Parsers

### `parse_frequency_response()`

```python
def parse_frequency_response(frame: CivFrame) -> int
```

Parse a frequency response to Hz. **Raises** `ValueError` if not a frequency response.

### `parse_mode_response()`

```python
def parse_mode_response(frame: CivFrame) -> tuple[Mode, int | None]
```

Parse a mode response. Returns `(mode, filter_width)`.

### `parse_meter_response()`

```python
def parse_meter_response(frame: CivFrame) -> int
```

Parse a meter response to 0–255 int.

### `parse_ack_nak()`

```python
def parse_ack_nak(frame: CivFrame) -> bool | None
```

Check if frame is ACK (`True`), NAK (`False`), or neither (`None`).

## CI-V Command Codes

Values below are confirmed either as surviving constants in `commands/_frame.py` (frequency,
mode, levels, meters, PTT, attenuator, preamp, power, scope, ACK/NAK) or, for the three now
resolved entirely from a profile's `CommandMap` (VFO select, split, CW keying), as the value
`rigs/ic7300.toml` declares — a profile is free to declare a different byte for its own radio.

| Code | Command |
|------|---------|
| `0x03` | Read frequency |
| `0x04` | Read mode |
| `0x05` | Set frequency |
| `0x06` | Set mode |
| `0x07` | VFO select / equalize / swap |
| `0x0F` | Split on/off |
| `0x11` | Attenuator |
| `0x14` | Levels (RF power, etc.) |
| `0x15` | Meter readings |
| `0x16` | Preamp |
| `0x17` | CW keying |
| `0x18` | Power on/off |
| `0x27` | Scope/waterfall |
| `0x1C` | PTT / transceiver status |
| `0xFB` | ACK (command accepted) |
| `0xFA` | NAK (command rejected) |
