---
description: Configure RigPlane backends, credentials, and runtime options — LAN versus USB serial, environment variables, and per-radio configuration tips.
---

# Configuration

## Backend Selection

rigplane supports two backends selected via `--backend`:

| Backend | Description |
|---------|-------------|
| `lan` (default) | Connects over UDP to the radio's LAN interface |
| `serial` | Connects via USB CI-V serial port + USB audio devices |

## LAN Backend Parameters

| Parameter | Python API | CLI Flag | Env Var | Default | Description |
|-----------|-----------|----------|---------|---------|-------------|
| Host | `host` | `--host` | `ICOM_HOST` | `192.168.1.100` | Radio IP address |
| Port | `port` | `--control-port` | `ICOM_PORT` | `50001` | Control port |
| Username | `username` | `--user` | `ICOM_USER` | `""` | Auth username |
| Password | `password` | `--pass` | `ICOM_PASS` | `""` | Auth password |
| CI-V Address | `radio_addr` | — | — | `0x98` (IC-7610) | Radio's CI-V address |
| Timeout | `timeout` | `--timeout` | — | `5.0` | Operation timeout (seconds) |

## Serial Backend Parameters

| Parameter | Python API | CLI Flag | Env Var | Default | Description |
|-----------|-----------|----------|---------|---------|-------------|
| Device | `device` | `--serial-port` | `ICOM_SERIAL_DEVICE` | — | Serial device path (required) |
| Baud rate | `baudrate` | `--serial-baud` | `ICOM_SERIAL_BAUDRATE` | `115200` | CI-V baud rate |
| PTT mode | `ptt_mode` | `--serial-ptt-mode` | `ICOM_SERIAL_PTT_MODE` | `civ` | Serial PTT control mode (`civ` supported) |
| RX device | `rx_device` | `--rx-device` | `ICOM_USB_RX_DEVICE` | auto | USB audio RX device name |
| TX device | `tx_device` | `--tx-device` | `ICOM_USB_TX_DEVICE` | auto | USB audio TX device name |
| CI-V Address | `radio_addr` | — | — | `0x98` (IC-7610) | Radio's CI-V address |
| Timeout | `timeout` | `--timeout` | — | `5.0` | Operation timeout (seconds) |

```bash
# Serial backend quick start
export ICOM_SERIAL_DEVICE=/dev/tty.usbmodem-IC7610
rigplane --backend serial status
rigplane --backend serial freq 14.074m

# List available USB audio devices
rigplane --list-audio-devices
```

## Connection Parameters (LAN, reference)

## CI-V Addresses

Each Icom radio model has a default CI-V address. You can also configure a custom address in the radio's menu.

| Radio | Default CI-V Address |
|-------|---------------------|
| IC-7610 | `0x98` |
| IC-7300 | `0x94` |
| IC-705 | `0xA4` |
| IC-9700 | `0xA2` |
| IC-7851 | `0x8E` |
| IC-R8600 | `0x96` |

```python
from rigplane import create_radio, LanBackendConfig

# IC-7610 (default radio_addr 0x98)
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    ...

# IC-705
config = LanBackendConfig(host="192.168.1.101", username="u", password="p", radio_addr=0xA4)
async with create_radio(config) as radio:
    ...

# Custom CI-V address
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", radio_addr=0x42)
async with create_radio(config) as radio:
    ...
```

## Port Architecture

The Icom LAN protocol uses three UDP ports:

| Port | Default | Purpose |
|------|---------|---------|
| Control | 50001 | Authentication, session management, keep-alive |
| CI-V | 50002 | CI-V command exchange (frequency, mode, etc.) |
| Audio | 50003 | RX/TX audio streaming |

The CI-V and audio ports are **negotiated during the handshake** — the library discovers them automatically from the radio's status packet. You only need to specify the control port.

## Integration/Test Environment Flags

| Env Var | Purpose |
|---------|---------|
| `ICOM_ALLOW_POWER_CONTROL=1` | Enable guarded power off/on integration test |
| `ICOM_SOAK_SECONDS=<N>` | Run soak integration for N seconds |
| `ICOM_CIV_MIN_INTERVAL_MS=<ms>` | Tune CI-V pacing interval in commander |
| `ICOM_SERIAL_CIV_MIN_INTERVAL_MS=<ms>` | Serial-backend CI-V pacing override (default 50 ms) |
| `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1` | Override serial scope low-baud guardrail (use with caution) |
| `ICOM_STRICT_FRONTEND=1` | Enable strict ATT/PREAMP integration profile (fail instead of skip) |

## Environment Variable Setup

### Bash / Zsh

Add to `~/.bashrc`, `~/.zshrc`, or `~/.profile`:

```bash
export ICOM_HOST=192.168.1.100
export ICOM_USER=myuser
export ICOM_PASS=mypass
```

### Fish

```fish
set -Ux ICOM_HOST 192.168.1.100
set -Ux ICOM_USER myuser
set -Ux ICOM_PASS mypass
```

### `.env` File (for scripts)

```bash
# .env
ICOM_HOST=192.168.1.100
ICOM_USER=myuser
ICOM_PASS=mypass
```

!!! warning "Security"
    Never commit `.env` files or credentials to version control. See [Security](../SECURITY.md) for best practices.

## Timeout Tuning

The default 5-second timeout works well for local networks. Adjust if needed:

```python
from rigplane import create_radio, LanBackendConfig

# Fast local network
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", timeout=2.0)
async with create_radio(config) as radio:
    ...

# Over VPN or high-latency link
config = LanBackendConfig(host="10.0.0.100", username="u", password="p", timeout=15.0)
async with create_radio(config) as radio:
    ...
```

The timeout applies to:

- Discovery handshake
- Login/authentication
- Each individual CI-V command
- Status packet reception

## Logging

The library uses Python's standard `logging` module. Enable debug output to troubleshoot connection issues:

```python
import logging

# See all rigplane internal messages
logging.basicConfig(level=logging.DEBUG)

# Or target specific modules
logging.getLogger("rigplane.transport").setLevel(logging.DEBUG)
logging.getLogger("rigplane.radio").setLevel(logging.DEBUG)
```

Log levels:

| Level | What you'll see |
|-------|----------------|
| `ERROR` | Connection failures, protocol errors |
| `WARNING` | Retransmits, missing packets, fallbacks |
| `INFO` | Connection lifecycle events (connect, auth, disconnect) |
| `DEBUG` | Every packet sent/received, sequence numbers, raw data |
