---
robots: noindex, follow
---

# IcomRadio

LAN-specific radio class. For new code, prefer the backend-neutral **[create_radio](public-api-surface.md)** + **Radio** API so the same code works over LAN or USB serial. Use `IcomRadio` when you need direct LAN control or are migrating from older examples.

::: rigplane.runtime.radio.IcomRadio

## Reference

### Class: `IcomRadio`

```python
from rigplane import IcomRadio
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
    audio_codec: AudioCodec | int = AudioCodec.PCM_2CH_16BIT,
    audio_sample_rate: int | None = None,
    audio_codec_explicit: bool | None = None,
    audio_sample_rate_explicit: bool | None = None,
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

- `audio_codec`, `audio_sample_rate`, `audio_codec_explicit`, `audio_sample_rate_explicit` — audio stream configuration
- `auto_reconnect`, `reconnect_delay`, `reconnect_max_delay`, `watchdog_timeout` — reconnect/watchdog behavior
- `auto_recover_audio`, `on_audio_recovery` — audio recovery behavior
- `cache_ttl_s` — per-field TTL overrides for fallback cache
- `profile`, `model` — runtime profile/model selection for capability/routing behavior. Without `model`, `profile` or a `radio_addr` that matches a loaded profile, the constructor raises `ValueError`.

### Context Manager

`IcomRadio` supports `async with` for automatic connection management:

```python
async with IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610") as radio:
    freq = await radio.get_freq()
# Disconnect happens automatically
```

Equivalent to:

```python
radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
await radio.connect()
try:
    freq = await radio.get_freq()
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

### `get_audio_capabilities()`

```python
from rigplane import get_audio_capabilities

def get_audio_capabilities() -> AudioCapabilities
```

A module-level function, not an `IcomRadio` method. Returns the stable rigplane
audio capability structure:

- `supported_codecs`
- `supported_sample_rates_hz`
- `supported_channels`
- `default_codec`
- `default_sample_rate_hz`
- `default_channels`

Default values are deterministic:

1. Codec: first supported codec in rigplane preference order.
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

### `get_freq()`

```python
async def get_freq(self, receiver: int = RECEIVER_MAIN, *, bypass_cache: bool = False) -> int
```

Get the current operating frequency in **Hz** for `receiver` (0=MAIN, 1=SUB).

**Returns:** `int` — frequency in Hz (e.g., `14074000`)

### `set_freq()`

```python
async def set_freq(self, freq_hz: int, receiver: int = 0) -> None
```

Set the operating frequency.

| Parameter | Type | Description |
|-----------|------|-------------|
| `freq_hz` | `int` | Frequency in Hz |
| `receiver` | `int` | 0=MAIN, 1=SUB |

`get_frequency()` and `set_frequency()` are aliases of `get_freq()` and
`set_freq()`, kept for existing callers.

---

## Mode

### `get_mode()`

```python
async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]
```

Get the current operating mode.

**Returns:** `(mode_name, filter_number_or_None)` such as `("USB", 2)`

### `get_mode_info()`

```python
async def get_mode_info(self, receiver: int = RECEIVER_MAIN) -> tuple[Mode, int | None]
```

Get current mode and filter number (if radio reports filter in response).

### `get_filter()` / `set_filter()`

```python
async def get_filter(self, receiver: int = 0) -> int | None
async def set_filter(self, filter_width: int, receiver: int = 0) -> None
```

Read/set current filter number (1-3) while preserving mode.

### `set_mode()`

```python
async def set_mode(self, mode: Mode | str, filter_width: int | None = None, receiver: int = 0) -> None
```

Set the operating mode.

| Parameter | Type | Description |
|-----------|------|-------------|
| `mode` | `Mode \| str` | Mode enum or name string (`"USB"`, `"CW"`, etc.) |

---

## Power

### `get_rf_power()`

```python
async def get_rf_power(self) -> int
```

Get the RF power level.

**Returns:** `int` — power level (0–255)

### `set_rf_power()`

```python
async def set_rf_power(self, level: int) -> None
```

Set the RF power level.

| Parameter | Type | Description |
|-----------|------|-------------|
| `level` | `int` | Power level 0–255 |

`get_power()` and `set_power()` are aliases of `get_rf_power()` and
`set_rf_power()`, kept for existing callers.

---

## Meters

### `get_s_meter()`

```python
async def get_s_meter(self) -> int
```

Read the S-meter value. **Returns:** `int` (0–255)

### `get_swr()`

```python
async def get_swr(self) -> float
```

Read the SWR as a calibrated ratio (>= 1.0), using the profile's
`[[meters.swr.calibration]]` table (TX only). **Returns:** `float`

### `get_swr_meter()`

```python
async def get_swr_meter(self) -> int
```

Read the raw SWR meter value (TX only). **Returns:** `int` (0–255)

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

### `get_vfo_slot()` / `set_vfo_slot()`

```python
async def get_vfo_slot(self, receiver: int = 0) -> str
async def set_vfo_slot(self, slot: str, receiver: int = 0) -> None
```

Read or select the active VFO slot (`"A"` or `"B"`) on `receiver`.

### `select_receiver()`

```python
async def select_receiver(self, which: int | str) -> None
```

Make `which` (MAIN or SUB) the active receiver for subsequent commands.

### `swap_vfo_ab()` / `equalize_vfo_ab()`

```python
async def swap_vfo_ab(self, receiver: int = 0) -> None
async def equalize_vfo_ab(self, receiver: int = 0) -> None
```

Swap VFO A and VFO B, or copy the active VFO to the inactive one, on
`receiver`. `equalize_vfo_ab()` raises `CommandError` when the profile
declares no A=B command.

### `swap_main_sub()` / `equalize_main_sub()`

```python
async def swap_main_sub(self) -> None
async def equalize_main_sub(self) -> None
```

Swap MAIN and SUB, or copy MAIN to SUB. Both require a dual-receiver profile.

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
async def get_attenuator(self, receiver: int = RECEIVER_MAIN) -> bool
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

Toggle attenuator (compatibility wrapper). Resolves `on` against the
connected profile's declared `[attenuator] values` (MOR-2086) rather than
a fixed dB constant: `off` always resolves to 0; `on` resolves to the
profile's single non-zero value.

**Raises:** `CommandError` if the profile declares more than one non-zero
attenuator value (a stepped attenuator, e.g. IC-7610) — the boolean form
has no single defined answer there; call `set_attenuator_level()` directly
with the desired dB value instead. Also raised if the profile declares no
attenuator values at all.

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
from rigplane.scope import ScopeFrame

def handle_scope(frame: ScopeFrame):
    print(f"Receiver {frame.receiver}: "
          f"{frame.start_freq_hz/1e6:.3f}–{frame.end_freq_hz/1e6:.3f} MHz, "
          f"{len(frame.pixels)} pixels, mode={frame.mode}")

radio.on_scope_data(handle_scope)
```

### `enable_scope()`

```python
async def enable_scope(
    self,
    *,
    output: bool = True,
    policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.VERIFY,
    timeout: float = 5.0,
) -> None
```

Enable scope display and data output on the radio. Sends CI-V `0x27 0x10 0x01` (scope on) and `0x27 0x11 0x01` (data output on).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output` | `bool` | `True` | Also enable wave data output |
| `policy` | `ScopeCompletionPolicy \| str` | `VERIFY` | Completion policy (strict, fast, verify) |
| `timeout` | `float` | `5.0` | Verification timeout in seconds |

**Raises:** `CommandError` if the radio rejects the command (strict policy);
`TimeoutError` if verification times out (verify policy).

### `disable_scope()`

```python
async def disable_scope(
    self, *, policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
) -> None
```

Disable scope data output. Sends CI-V `0x27 0x11 0x00`.

**Raises:** `CommandError` if the radio rejects the command.

### `ScopeFrame`

```python
from rigplane.scope import ScopeFrame
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
    *,
    wait_response: bool = True,
    priority: Priority = Priority.NORMAL,
    wait_dispatch: bool = True,
) -> CivFrame | None
```

Send an arbitrary CI-V command through the active backend transport.

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `int` | CI-V command byte |
| `sub` | `int \| None` | Optional sub-command byte |
| `data` | `bytes \| None` | Optional payload data |
| `wait_response` | `bool` | When `True`, wait for and return the parsed response frame. When `False`, send the frame without registering a response waiter. |

**Returns:** `CivFrame` with the radio's response when `wait_response=True`;
`None` when `wait_response=False`.

Use `wait_response=False` for fire-and-forget writes where a later response is
not needed. Use `send_civ_transaction()` when the caller needs explicit
ACK/NAK/data semantics and bounded response handling.

### `send_civ_transaction()`

```python
async def send_civ_transaction(
    self,
    command: int,
    sub: int | None = None,
    data: bytes | None = None,
    *,
    expect: Literal["none", "ack", "data"] = "data",
    timeout: float | None = None,
) -> RawCivTransactionResult
```

Send one raw CI-V command with explicit response semantics.

| Parameter | Type | Description |
|-----------|------|-------------|
| `command` | `int` | CI-V command byte, `0` through `255` |
| `sub` | `int \| None` | Optional CI-V sub-command byte, `0` through `255` |
| `data` | `bytes \| None` | Optional payload bytes |
| `expect` | `"none" \| "ack" \| "data"` | Response contract for this transaction |
| `timeout` | `float \| None` | Optional timeout in seconds |

`expect="none"` sends one frame and returns a sent result without waiting.
`expect="ack"` waits for a fresh CI-V ACK or NAK. `expect="data"` waits for
the keyed data response, while still treating a fresh NAK as a deterministic
failure result.

While the transaction is active, cooperating pollers pause via the external
CAT-session ownership guard so background CI-V traffic cannot consume or
pollute the caller's response.

**Returns:** `RawCivTransactionResult` with `status`, optional parsed frame
fields, and explicit ACK/NAK/data/sent outcome metadata.

!!! note "Public surface"
    `send_civ_transaction()` is the Python counterpart to the public/dev-facing
    raw CI-V HTTP transaction APIs. The lower-level raw CI-V pipe used by the
    local Hamlib A1 bridge runner is internal experimental infrastructure and
    should not be treated as a stable application API.
