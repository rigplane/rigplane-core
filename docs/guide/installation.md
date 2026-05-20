---
description: Install RigPlane from PyPI on macOS, Linux, or Windows — Python 3.11+ requirements, optional extras, and verifying the install against a real radio.
---

# Installation

## Requirements

- **Python 3.11+**
- An Icom radio with LAN/WiFi connectivity (IC-7610, IC-705, IC-9700, etc.) or USB serial (IC-7300, IC-7610, etc.)
- Network access to the radio (same LAN/subnet)

## Install from PyPI

```bash
pip install rigplane
```

## Install from Source

```bash
git clone https://github.com/rigplane/rigplane-core.git
cd rigplane
pip install -e .
```

## Development Install

For running tests and contributing:

```bash
git clone https://github.com/rigplane/rigplane-core.git
cd rigplane
pip install -e ".[dev]"
```

## Optional Dependencies

```bash
pip install rigplane[scope]    # Scope PNG rendering (pillow)
pip install rigplane[tls]      # HTTPS with auto-generated certs (cryptography)
pip install rigplane[webrtc]   # WebRTC audio transport (aiortc)
```

!!! note "Audio bridge included by default (since v0.19)"
    `opuslib`, `sounddevice`, and `numpy` are now part of the core install.
    `pip install rigplane` is enough for the Web UI, audio bridge, and Opus
    codec support — no extras needed.

    The legacy `[audio]` and `[bridge]` extras are preserved as no-op
    aliases so existing install commands keep working.

## Verify Installation

```bash
# Check the CLI is available
rigplane --help

# Or run as a module
python -m rigplane --help
```

## Radio Setup

Before connecting, ensure your radio is configured for LAN control:

### IC-7610

1. **Menu → Set → Network** — configure IP address (static recommended)
2. **Menu → Set → Network → Remote Control** — enable "Network Control"
3. **Menu → Set → Network → Network User** — create a username/password
4. Default port: **50001**

### IC-705

1. **Menu → Set → WLAN Set** — connect to your WiFi network
2. **Menu → Set → Network → Remote Control** — enable
3. **Menu → Set → Network → Network User** — create credentials

### IC-7300

The IC-7300 does **not** have LAN/WiFi connectivity. Use the **USB serial backend** instead:

```bash
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-XXXXX status
```

See the [IC-7300 USB Setup guide](ic7300-usb-setup.md) for details.

!!! tip "Static IP Recommended"
    Assign a static IP to your radio to avoid connection issues after DHCP lease changes.

!!! warning "Firewall"
    Ensure UDP ports **50001-50003** are open between your computer and the radio.
