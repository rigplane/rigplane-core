---
description: Get your first RigPlane connection to an Icom radio over LAN in under five minutes — credentials, discovery, and a basic frequency read in Python.
---

# Quick Start

Get your first connection in under 5 minutes.

## 1. Set Credentials

Use environment variables to avoid putting credentials in code:

```bash
export ICOM_HOST=192.168.1.100   # Your radio's IP
export ICOM_USER=myuser           # Network username
export ICOM_PASS=mypass           # Network password
```

## 2. Try the CLI

```bash
# Check radio status
rigplane status
```

Expected output:

```
Frequency:    14,074,000 Hz  (14.074000 MHz)
Mode:         USB
S-meter:      42
Power:        50
```

```bash
# Change frequency
rigplane freq 14.074m

# Change mode
rigplane mode USB

# Read meters as JSON
rigplane meter --json
```

## 3. Python API

Use **`create_radio`** with a backend config to get a **`Radio`** instance (works for both LAN and USB serial backends):

```python
import asyncio
from rigplane import create_radio, LanBackendConfig

async def main():
    config = LanBackendConfig(
        host="192.168.1.100",
        username="myuser",
        password="mypass",
    )
    async with create_radio(config) as radio:
        # Read current state
        freq = await radio.get_frequency()
        mode, _ = await radio.get_mode()
        s_meter = await radio.get_s_meter()
        print(f"{freq/1e6:.3f} MHz  {mode}  S={s_meter}")

        # Tune to 20m FT8
        await radio.set_frequency(14_074_000)
        await radio.set_mode("USB")

asyncio.run(main())
```

For LAN-only scripts you can still use **`IcomRadio(host, username=..., password=...)`** — see [API Reference](../api/radio.md).

## 4. Discover Radios

Don't know your radio's IP — or want to find USB-connected radios too? Use unified discovery:

```bash
rigplane discover
```

```
Scanning for Icom radios (3s LAN + serial)...

Found 1 radio with 2 connection methods:

IC-7610:
  • LAN: 192.168.55.40
  • Serial: /dev/cu.usbserial-11320 (19200 baud)
```

The command scans both LAN (UDP broadcast) and USB serial ports in parallel. Use filters for targeted scans:

```bash
rigplane discover --lan-only      # UDP broadcast only
rigplane discover --serial-only   # USB serial ports only
rigplane discover --timeout 5     # Longer LAN listen window
```

## What's Next?

- **[CLI Reference](cli.md)** — full list of CLI commands
- **[CI-V Commands](commands.md)** — frequency, mode, meters, PTT, CW, VFO
- **[Public API Surface](../api/public-api-surface.md)** — supported vs advanced exports
- **[API Reference](../api/radio.md)** — complete Radio API and legacy IcomRadio reference
- **[Connection Lifecycle](connection.md)** — understand the handshake and keep-alive
