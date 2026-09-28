---
description: Install RigPlane from PyPI on macOS, Linux, or Windows — Python 3.11+ requirements, optional extras, and verifying the install against a real radio.
---

# Installation

## Requirements

- **Python 3.11+**
- A supported radio: see [Supported Radios](radios.md)
- For a LAN radio, network access to it; auto-discovery needs the same subnet

## Install from PyPI

```bash
pip install rigplane
```

While RigPlane 3.0 is in beta, `pip install rigplane` installs the latest 2.x
release. No 3.0 build is on PyPI yet; to run 3.0, [install from
source](#install-from-source).

The prebuilt wheel includes the Web UI and does not require Node.js or npm.
If pip must build from a source distribution instead, follow the source-build
requirements below.

## Install from Source

Building from a Git checkout or source distribution requires Node.js and npm.
The build runs `npm ci` and downloads frontend dependencies from the npm
registry unless they are already cached. The source-distribution install path
has been verified with Node.js 20.20.2 and npm 10.8.2; this is the tested
toolchain, not a claim that other versions are supported.

```bash
git clone https://github.com/rigplane/rigplane-core.git
cd rigplane-core
pip install -e .
```

## Development Install

For running tests and contributing:

```bash
git clone https://github.com/rigplane/rigplane-core.git
cd rigplane-core
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
2. **Menu → Set → Network → Network Control** — set to ON
3. **Menu → Set → Network → Network User1** — set an ID and a password
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
