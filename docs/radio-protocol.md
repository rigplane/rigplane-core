---
robots: noindex, follow
---

# Radio Protocol — Multi-Backend Architecture

## Overview

The Radio Protocol defines a vendor-neutral interface for controlling amateur radio transceivers. Any backend that implements the `Radio` protocol can be used with the Web UI, rigctld server, and CLI without modification.

```
┌──────────────────────────────────────────────┐
│          Web UI  /  rigctld  /  CLI           │
├──────────────────────────────────────────────┤
│          Radio Protocol (core)                │
│  ┌──────────────┬─────────────┬────────────┐ │
│  │ AudioCapable │ ScopeCapable│ DualRxCap. │ │
│  └──────────────┴─────────────┴────────────┘ │
├────────┬──────────┬──────────┬───────────────┤
│IcomLAN │IcomSerial│ YaesuCAT │  Future...    │
└────────┴──────────┴──────────┴───────────────┘
```

## Core Protocol: `Radio`

Every backend **must** implement this. It covers the essentials that any transceiver supports.

```python
from rigplane.radio_protocol import Radio

class MyRadio:
    """Implements Radio protocol."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    
    @property
    def connected(self) -> bool: ...

    # Frequency (Hz)
    async def get_frequency(self, receiver: int = 0) -> int: ...
    async def set_frequency(self, freq: int, receiver: int = 0) -> None: ...

    # Mode → ("USB", filter_num_or_None)
    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...
    async def set_mode(self, mode: str, filter_width: int | None = None, receiver: int = 0) -> None: ...

    # DATA mode (USB-D, LSB-D for digital modes)
    async def get_data_mode(self) -> bool: ...
    async def set_data_mode(self, on: bool) -> None: ...

    # TX
    async def set_ptt(self, on: bool) -> None: ...

    # Meters
    async def get_s_meter(self, receiver: int = 0) -> int: ...
    async def get_swr(self) -> float: ...

    # Power (0-255 normalised)
    async def get_power(self) -> int: ...
    async def set_power(self, level: int) -> None: ...

    # Levels (0-255 normalised)
    async def set_af_level(self, level: int) -> None: ...
    async def set_rf_gain(self, level: int) -> None: ...
    async def set_squelch(self, level: int) -> None: ...

    # State
    @property
    def radio_state(self) -> RadioState: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[str]: ...

    # Server integration
    def set_state_change_callback(self, callback) -> None: ...
    def set_reconnect_callback(self, callback) -> None: ...
```

### Standard Mode Names

Cross-vendor mode strings used in `get_mode()` / `set_mode()`:

| Mode | Description |
|------|-------------|
| `USB` | Upper Sideband |
| `LSB` | Lower Sideband |
| `CW` | CW (normal) |
| `CWR` | CW Reverse |
| `AM` | Amplitude Modulation |
| `FM` | Frequency Modulation |
| `RTTY` | RTTY (normal) |
| `RTTYR` | RTTY Reverse |
| `PSK` | PSK |
| `DV` | D-STAR Digital Voice |
| `DD` | D-STAR Data |

### Standard Capability Tags

Returned by `radio.capabilities`:

| Tag | Meaning |
|-----|---------|
| `audio` | Audio streaming (RX/TX) |
| `scope` | Spectrum scope / panadapter |
| `dual_rx` | Dual independent receivers |
| `meters` | S-meter, SWR, power readings |
| `tx` | Transmit capability |
| `cw` | CW keyer |
| `attenuator` | Attenuator control |
| `preamp` | Preamplifier control |
| `rf_gain` | RF gain control |
| `af_level` | AF output level control |
| `squelch` | Squelch control |
| `nb` | Noise blanker |
| `nr` | Noise reduction |

## Optional Protocols

### `AudioCapable`

For radios that support audio streaming — either over LAN (Icom) or via USB audio device (serial-connected radios, Digirig).

```python
from rigplane.radio_protocol import AudioCapable

if isinstance(radio, AudioCapable):
    # Direct callback API
    await radio.start_audio_rx_opus(callback)
    await radio.push_audio_tx_opus(opus_data)
    await radio.stop_audio_rx_opus()

    # AudioBus pub/sub (recommended for multi-consumer)
    bus = radio.audio_bus
    async with bus.subscribe(name="my-consumer") as sub:
        async for packet in sub:
            process(packet.data)
```

#### AudioBus

The `audio_bus` property provides a pub/sub distribution system for sharing audio RX streams across multiple consumers. First subscriber triggers RX start, last unsubscribe stops it.

```python
# Multiple consumers sharing the same stream
web_sub = radio.audio_bus.subscribe(name="web-audio")
bridge_sub = radio.audio_bus.subscribe(name="audio-bridge")

await web_sub.start()
await bridge_sub.start()

# Both receive the same opus packets independently
async for packet in web_sub:
    send_to_browser(packet)
```

### `ScopeCapable`

For radios with spectrum/panadapter output.

```python
from rigplane.radio_protocol import ScopeCapable

if isinstance(radio, ScopeCapable):
    await radio.enable_scope(span=100_000)
    await radio.disable_scope()
```

### `DualReceiverCapable`

For radios with two independent receivers (e.g. IC-7610 Main/Sub).

```python
from rigplane.radio_protocol import DualReceiverCapable

if isinstance(radio, DualReceiverCapable):
    await radio.vfo_exchange()   # swap Main ↔ Sub
    await radio.vfo_equalize()   # Sub = Main
```

## Implementing a New Backend

1. Create a class that implements `Radio` (and optional protocols as needed)
2. Register it in the radio factory
3. The Web UI, rigctld, and CLI will work automatically

```python
from rigplane.radio_protocol import Radio
from rigplane.radio_state import RadioState, ReceiverState

class YaesuRadio:
    """Yaesu CAT protocol backend."""

    def __init__(self, port: str, model: str = "FTX-1"):
        self._port = port
        self._model = model
        self._state = RadioState()
        self._connected = False

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[str]:
        return {"meters", "tx"}  # no audio/scope over CAT

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def radio_state(self) -> RadioState:
        return self._state

    async def connect(self) -> None:
        # Open serial port, configure baud rate...
        self._connected = True

    async def disconnect(self) -> None:
        # Close serial port...
        self._connected = False

    async def get_frequency(self, receiver: int = 0) -> int:
        # Send "FA;" CAT command, parse response
        ...

    async def set_frequency(self, freq: int, receiver: int = 0) -> None:
        # Send "FA{freq:011d};" CAT command
        ...

    # ... implement remaining Radio methods ...

# Protocol compliance check:
assert isinstance(YaesuRadio("/dev/ttyUSB0"), Radio)
```

## Backend Comparison

### IC-7610: LAN vs Serial

| Feature | LAN Backend | Serial Backend |
|---------|-------------|----------------|
| **Transport** | UDP (ports 50001/2/3) | USB CI-V serial + USB audio devices |
| **Protocol** | CI-V over UDP | CI-V over serial + USB audio |
| **Control (freq/mode/PTT)** | ✅ Full | ✅ Full |
| **Meters (S/SWR/ALC)** | ✅ Full | ✅ Full |
| **Audio RX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Audio TX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Scope/Waterfall** | ✅ Full (~225 pkt/s) | ⚠️ Requires ≥115200 baud* |
| **Dual Receiver (Command29)** | ✅ Full | ✅ Full |
| **Remote Access** | ✅ Over LAN/VPN | ❌ USB only (local) |
| **Discovery** | ✅ UDP broadcast | ❌ N/A |
| **Setup** | IP, username, password | USB cable + device path |
| **Tested Models** | IC-7610, IC-7851 | IC-7610 |

\* **Scope guardrail**: Serial backend enforces minimum 115200 baud for scope/waterfall due to high CI-V packet rate. Lower baud rates risk command timeout/starvation. Override via `allow_low_baud_scope=True` or `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1` (use with caution).

### Cross-Vendor Comparison

| Feature | Icom LAN | Icom Serial | Yaesu CAT | Digirig |
|---------|----------|-------------|-----------|---------|
| **Transport** | UDP | USB Serial | USB Serial | USB Serial |
| **Protocol** | CI-V | CI-V | CAT | CAT/CI-V |
| **Audio** | LAN (Opus/PCM) | USB Audio Device | USB Audio Device | USB Audio Device |
| **Scope** | ✅ (IC-7610/7851) | ⚠️ (IC-7610, ≥115200 baud) | ❌ | ❌ |
| **Dual RX** | ✅ (IC-7610) | ✅ (IC-7610) | ❌ | ❌ |
| **Radios** | IC-7610, IC-7851 | IC-7610 | FTX-1, FT-710, etc. | Any + 3.5mm |

## Migration and Backward Compatibility

### Existing Code (LAN Backend)

If you're currently using `IcomRadio` directly, **no changes are required**:

```python
from rigplane import IcomRadio

# This still works (LAN backend, backward compatible)
async with IcomRadio("192.168.1.100", username="user", password="pass") as radio:
    freq = await radio.get_frequency()
```

`IcomRadio` remains the **backward-compatible LAN adapter** built on the shared
IC-7610 core. All existing code, scripts, and integrations continue to work
without modification.

### New Code (Backend Factory)

For new code or when adding serial backend support, use the **typed config factory**:

```python
from rigplane.backends.factory import create_radio
from rigplane.backends.config import LanBackendConfig, SerialBackendConfig

# LAN backend via factory (explicit)
lan_config = LanBackendConfig(
    host="192.168.1.100",
    username="user",
    password="pass",
)
radio = create_radio(lan_config)

# Serial backend via factory
serial_config = SerialBackendConfig(
    device="/dev/cu.usbserial-111120",
    baudrate=115200,
)
radio = create_radio(serial_config)

# Both return a Radio protocol-compliant instance
async with radio:
    freq = await radio.get_frequency()
```

### CLI Backward Compatibility

Default behavior is **unchanged** (LAN):

```bash
# Default: LAN backend (same as before)
rigplane status
rigplane freq 14.074m

# Explicit LAN backend
rigplane --backend lan status

# New: Serial backend
rigplane --backend serial --serial-port /dev/cu.usbserial-111120 status
```

### Web UI and rigctld

Web UI and rigctld now support backend selection via CLI flags. Default is LAN for backward compatibility.

```bash
# Web UI: LAN backend (default)
rigplane web

# Web UI: Serial backend
rigplane --backend serial --serial-port /dev/cu.usbserial-111120 web

# rigctld: LAN backend (default)
rigplane serve

# rigctld: Serial backend
rigplane --backend serial --serial-port /dev/cu.usbserial-111120 serve
```

### Consumer Code (Web/rigctld/CLI)

Consumer runtime paths (`web/`, `rigctld/`, and CLI command execution) are
factory-backed and program against the `Radio` protocol, so no consumer changes
are needed when adding new backends.

**Boundary rule**: `web/` and `rigctld/` must not import concrete radio classes;
they depend only on `radio_protocol.Radio` and capability protocols. The CLI
still keeps narrow `IcomRadio` helper imports, but backend selection at runtime
goes through `create_radio(...)`.

### Capability Detection

Use runtime capability detection for optional features:

```python
from rigplane.radio_protocol import AudioCapable, ScopeCapable

radio = create_radio(config)  # LAN or serial

async with radio:
    # Audio
    if isinstance(radio, AudioCapable):
        await radio.start_audio_rx_opus(on_audio)
    
    # Scope
    if isinstance(radio, ScopeCapable):
        await radio.enable_scope()
```

### Migration Checklist

- [x] **Existing LAN code**: No changes required — `IcomRadio` still works
- [x] **New backend-agnostic code**: Use `create_radio(config)` factory
- [x] **CLI**: Default unchanged (LAN); add `--backend serial` for serial
- [x] **Web/rigctld**: Default unchanged (LAN); add `--backend serial` for serial
- [x] **Capability-specific code**: Use `isinstance(radio, AudioCapable)` checks
- [x] **Tests**: Use `Radio` protocol for mocks, not concrete `IcomRadio`

### IC-7610 USB Hardware Note

For the IC-7610 serial backend, set **Menu → Set → Connectors → CI-V → CI-V USB
Port** to the CI-V option (`Link to [CI-V]`), not `[REMOTE]`. `[REMOTE]` blocks
serial CI-V control and was confirmed on live hardware in issue `#146`.

### Default Backend Selection

| Context | Default Backend | Override |
|---------|-----------------|----------|
| CLI | LAN | `--backend serial` |
| Python API (legacy) | LAN (`IcomRadio` adapter) | Use `create_radio(SerialBackendConfig(...))` |
| Python API (new) | Explicit via config | `LanBackendConfig` or `SerialBackendConfig` |
| Web UI | LAN | `--backend serial` flag |
| rigctld | LAN | `--backend serial` flag |
