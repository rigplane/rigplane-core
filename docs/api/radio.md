# IcomRadio

LAN-specific radio class. For new code, prefer the backend-neutral **[create_radio](public-api-surface.md)** + **Radio** API so the same code works over LAN or USB serial. Use `IcomRadio` when you need direct LAN control or are migrating from older examples.

::: icom_lan.runtime.radio.IcomRadio

## Reference

### Class: `IcomRadio`

```python
from icom_lan import IcomRadio
```

### Constructor

```python
IcomRadio(
    host: str,
    port: int = 50001,
    username: str = "",
    password: str = "",
    radio_addr: int | None = None,
    timeout: float = 5.0,
    audio_codec: AudioCodec | int = AudioCodec.PCM_1CH_16BIT,
    audio_sample_rate: int = 48000,
    auto_reconnect: bool = False,
    reconnect_delay: float = 2.0,
    reconnect_max_delay: float = 60.0,
    watchdog_timeout: float = 30.0,
    auto_recover_audio: bool = True,
    on_audio_recovery: Callable[[AudioRecoveryState], None] | None = None,
    cache_ttl_s: dict[str, float] | None = None,
    profile: RadioProfile | str | None = None,
    model: str | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `host` | `str` | *required* | Radio IP address or hostname |
| `port` | `int` | `50001` | Control port number |
| `username` | `str` | `""` | Authentication username |
| `password` | `str` | `""` | Authentication password |
| `radio_addr` | `int \| None` | `None` | Optional CI-V address override (uses profile default when omitted) |
| `timeout` | `float` | `5.0` | Default timeout for all operations (seconds) |

Additional optional parameters:

- `audio_codec`, `audio_sample_rate` — audio stream configuration
- `auto_reconnect`, `reconnect_delay`, `reconnect_max_delay`, `watchdog_timeout` — reconnect/watchdog behavior
- `auto_recover_audio`, `on_audio_recovery` — audio recovery behavior
- `cache_ttl_s` — per-field TTL overrides for fallback cache
- `profile`, `model` — runtime profile/model selection for capability/routing behavior

### Context Manager

`IcomRadio` supports `async with` for automatic connection management:

```python
async with IcomRadio("192.168.1.100", username="u", password="p") as radio:
    freq = await radio.get_frequency()
# Disconnect happens automatically
```

Equivalent to:

```python
radio = IcomRadio("192.168.1.100", username="u", password="p")
await radio.connect()
try:
    freq = await radio.get_frequency()
finally:
    await radio.disconnect()
```

---

## Properties

### `connected`

```python
@property
def connected(self) -> bool
```

Whether the radio session is connected at transport level.

`connected` does **not** guarantee CI-V stream freshness. Use `radio_ready` for
operation readiness.

### `radio_ready`

```python
@property
def radio_ready(self) -> bool
```

Backend source of truth for CI-V readiness.

`radio_ready` is `True` only when:

- `connected` is `True`
- CI-V stream is marked ready
- backend is not in a recovery phase
- last CI-V data timestamp is within the readiness idle timeout

This is a backend-managed readiness contract: clients should treat
`connected` vs `radio_ready` as distinct signals.

---

## Audio Capabilities

### `audio_capabilities()`

```python
@staticmethod
def audio_capabilities() -> AudioCapabilities
```

Return the stable icom-lan audio capability structure:

- `supported_codecs`
- `supported_sample_rates_hz`
- `supported_channels`
- `default_codec`
- `default_sample_rate_hz`
- `default_channels`

Default values are deterministic:

1. Codec: first supported codec in icom-lan preference order.
2. Sample rate: highest supported sample rate.
3. Channels: implied by default codec (fallback to minimum supported channels).

### `get_audio_stats()`

```python
def get_audio_stats(self) -> dict[str, bool | int | float | str]
```

Return runtime audio quality stats for the active audio stream as a JSON-friendly
dictionary. If no audio stream is active, returns a zeroed idle snapshot.

---

## Connection Methods

### `connect()`

```python
async def connect(self) -> None
```

Open connection to the radio and authenticate. Performs the full handshake:

1. Discovery (Are You There → I Am Here)
2. Login with credentials
3. Token acknowledgement
4. Conninfo exchange
5. CI-V data stream open

**Raises:**

| Exception | When |
|-----------|------|
| `ConnectionError` | UDP connection failed |
| `AuthenticationError` | Login rejected |
| `TimeoutError` | Radio didn't respond |

### `disconnect()`

```python
async def disconnect(self) -> None
```

Cleanly disconnect from the radio. Closes the CI-V data stream and both UDP connections.

---

## Frequency

### `get_frequency()`

```python
async def get_frequency(self) -> int
```

Get the current operating frequency in **Hz**.

**Returns:** `int` — frequency in Hz (e.g., `14074000`)

### `set_frequency()`

```python
async def set_frequency(self, freq_hz: int) -> None
```

Set the operating frequency.

| Parameter | Type | Description |
|-----------|------|-------------|
| `freq_hz` | `int` | Frequency in Hz |

**Raises:** `CommandError` if the radio rejects the frequency.

---

## Mode

### `get_mode()`

```python
async def get_mode(self) -> tuple[str, int | None]
```

Get the current operating mode.

**Returns:** `(mode_name, filter_number_or_None)` such as `("USB", 2)`

### `get_mode_info()`

```python
async def get_mode_info(self) -> tuple[Mode, int | None]
```

Get current mode and filter number (if radio reports filter in response).

### `get_filter()` / `set_filter()`

```python
async def get_filter(self) -> int | None
async def set_filter(self, filter_width: int) -> None
```

Read/set current filter number (1-3) while preserving mode.

### `set_mode()`

```python
async def set_mode(self, mode: Mode | str, filter_width: int | None = None) -> None
```

Set the operating mode.

| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `Mode \| str` | Mode enum or name string (`"USB"`, `"CW"`, etc.) |

**Raises:** `CommandError` if the radio rejects the mode.

---

## Power

### `get_power()`

```python
async def get_power(self) -> int
```

Get the RF power level.

**Returns:** `int` — power level (0–255)

### `set_power()`

```python
async def set_power(self, level: int) -> None
```

Set the RF power level.

| Parameter | Type | Description |
|-----------|------|-------------|
| `level` | `int` | Power level 0–255 |

---

## Meters

### `get_s_meter()`

```python
async def get_s_meter(self) -> int
```

Read the S-meter value. **Returns:** `int` (0–255)

### `get_swr()`

```python
async def get_swr(self) -> int
```

Read the SWR meter value (TX only). **Returns:** `int` (0–255)

**Raises:** `TimeoutError` if not transmitting.

### `get_alc_meter()`

```python
async def get_alc_meter(self) -> int
```

Read the ALC meter value (TX only). **Returns:** `int` (0–255)

**Raises:** `TimeoutError` if not transmitting.

---

## PTT

### `set_ptt()`

```python
async def set_ptt(self, on: bool) -> None
```

Toggle Push-To-Talk.

| Parameter | Type | Description |
|-----------|------|-------------|
| `on` | `bool` | `True` = TX, `False` = RX |

---

## VFO & Split

### `select_vfo()`

```python
async def select_vfo(self, vfo: str = "A") -> None
```

Select the active VFO.

| Value | Description |
|-------|-------------|
| `"A"` | VFO A |
| `"B"` | VFO B |
| `"MAIN"` | Main receiver (IC-7610) |
| `"SUB"` | Sub receiver (IC-7610) |

### `vfo_equalize()`

```python
async def vfo_equalize(self) -> None
```

Send the CI-V A=B command. On MAIN/SUB radios (e.g. IC-7610), practical semantics can differ from a literal MAIN→SUB copy depending on rig state.

### `vfo_exchange()`

```python
async def vfo_exchange(self) -> None
```

Swap VFO A and VFO B.

### `set_split()`

```python
async def set_split(self, on: bool) -> None
```

Enable or disable split mode (TX on VFO B, RX on VFO A).

---

## Attenuator & Preamp

All attenuator and preamp methods use **Command29 framing** for dual-receiver
radio compatibility (IC-7610). The `receiver` parameter defaults to `RECEIVER_MAIN` (0).

### `get_attenuator_level()`

```python
async def get_attenuator_level(self, receiver: int = RECEIVER_MAIN) -> int
```

Read attenuator level in dB (0, 3, 6, ..., 45).

### `get_attenuator()`

```python
async def get_attenuator(self) -> bool
```

Read attenuator state as boolean (compatibility wrapper).

### `set_attenuator_level()`

```python
async def set_attenuator_level(self, db: int, receiver: int = RECEIVER_MAIN) -> None
```

Set attenuator level in dB. IC-7610 supports 0–45 in 3 dB steps.

**Raises:** `ValueError` if `db` is not a valid step.

### `set_attenuator()`

```python
async def set_attenuator(self, on: bool, receiver: int = RECEIVER_MAIN) -> None
```

Toggle attenuator (compatibility wrapper: on=18 dB, off=0 dB).

### `get_preamp()`

```python
async def get_preamp(self, receiver: int = RECEIVER_MAIN) -> int
```

Read preamp level.

### `set_preamp()`

```python
async def set_preamp(self, level: int = 1, receiver: int = RECEIVER_MAIN) -> None
```

Set the preamp level.

| Level | Description |
|-------|-------------|
| `0` | Off |
| `1` | PREAMP 1 |
| `2` | PREAMP 2 |

| Receiver | Constant | Value |
|----------|----------|-------|
| Main | `RECEIVER_MAIN` | `0x00` |
| Sub | `RECEIVER_SUB` | `0x01` |

---

## CW

### `send_cw_text()`

```python
async def send_cw_text(self, text: str) -> None
```

Send CW text via the radio's built-in keyer. Long messages are automatically split into 30-character chunks.

### `stop_cw_text()`

```python
async def stop_cw_text(self) -> None
```

Stop CW sending in progress.

---

## Power Control

### `power_control()`

```python
async def power_control(self, on: bool) -> None
```

Remote power on/off. Requires the radio to maintain network connectivity in standby.

---

## State Guardrails

### `snapshot_state()` / `restore_state()`

```python
async def snapshot_state(self) -> dict[str, object]
async def restore_state(self, state: dict[str, object]) -> None
```

Best-effort helpers for preserving and restoring rig state in integration workflows.

### `run_state_transaction()`

```python
async def run_state_transaction(self, body: Callable[[], Awaitable[None]]) -> None
```

Run an operation with automatic snapshot/restore guard.

## Scope / Waterfall

### `on_scope_data()`

```python
def on_scope_data(self, callback: Callable[[ScopeFrame], Any] | None) -> None
```

Register a callback for scope/waterfall data. The callback receives a complete `ScopeFrame` each time the radio delivers a full spectrum burst (all sequences assembled).

Pass `None` to unregister.

```python
from icom_lan.scope import ScopeFrame

def handle_scope(frame: ScopeFrame):
    print(f"Receiver {frame.receiver}: "
          f"{frame.start_freq_hz/1e6:.3f}–{frame.end_freq_hz/1e6:.3f} MHz, "
          f"{len(frame.pixels)} pixels, mode={frame.mode}")

radio.on_scope_data(handle_scope)
```

### `enable_scope()`

```python
async def enable_scope(self, *, output: bool = True) -> None
```

Enable scope display and data output on the radio. Sends CI-V `0x27 0x10 0x01` (scope on) and `0x27 0x11 0x01` (data output on).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output` | `bool` | `True` | Also enable wave data output |

**Raises:** `CommandError` if the radio rejects the command.

### `disable_scope()`

```python
async def disable_scope(self) -> None
```

Disable scope data output. Sends CI-V `0x27 0x11 0x00`.

**Raises:** `CommandError` if the radio rejects the command.

### `ScopeFrame`

```python
from icom_lan.scope import ScopeFrame
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `receiver` | `int` | 0=MAIN, 1=SUB |
| `mode` | `int` | 0=center, 1=fixed, 2=scroll-C, 3=scroll-F |
| `start_freq_hz` | `int` | Lower edge frequency in Hz |
| `end_freq_hz` | `int` | Upper edge frequency in Hz |
| `pixels` | `bytes` | Amplitude values, each 0x00–0xA0 (0–160) |
| `out_of_range` | `bool` | True if scope data is out of range |

**Note:** In center mode, `start_freq_hz` and `end_freq_hz` are already expanded from center ± half-span to actual edge frequencies.

## Raw CI-V

### `send_civ()`

```python
async def send_civ(
    self,
    command: int,
    sub: int | None = None,
    data: bytes | None = None,
) -> CivFrame
```

Send an arbitrary CI-V command and return the response.

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `int` | CI-V command byte |
| `sub` | `int \| None` | Optional sub-command byte |
| `data` | `bytes \| None` | Optional payload data |

**Returns:** `CivFrame` with the radio's response.
