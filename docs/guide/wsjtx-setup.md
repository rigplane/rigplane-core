---
description: Configure WSJT-X, JTDX, and JS8Call against RigPlane's rigctld-compatible endpoint for FT8, FT4, and JS8.
---

# WSJT-X / JTDX / JS8Call Setup Guide

This guide covers configuring WSJT-X (and compatible apps like JTDX, JS8Call)
to work with RigPlane's client-facing `rigctld`-compatible endpoint. This is
separate from the provider layer underneath RigPlane, which may be native or
Hamlib-backed depending on the radio path. If RigPlane itself is using Hamlib,
see the [Hamlib / external rigctld provider guide](hamlib-rigctld-provider.md)
for the provider-facing setup.

## Quick Start

### 1. Start the server

**Recommended: all-in-one** (Web UI + audio bridge + rigctld):

```bash
# Install rigplane (audio-bridge deps ship with the core install since v0.19)
pip install rigplane

# Install BlackHole virtual audio devices (macOS)
# Two devices required: one for RX, one for TX (single device creates feedback loop)
brew install blackhole-2ch
brew install blackhole-16ch

# IMPORTANT: Reboot after installing BlackHole to activate the audio drivers!

# Start all-in-one server with separate RX/TX devices
rigplane --host <RADIO_IP> --user <USER> --pass <PASS> web \
  --bridge "BlackHole 2ch" --bridge-tx-device "BlackHole 16ch"
```

This starts:
- **Web UI** on `:8080`
- **Audio bridge** routing radio RX → BlackHole 2ch, BlackHole 16ch → radio TX
- **Rigctld** on `:4532` (enabled by default)

**Alternative: rigctld only** (no Web UI or audio bridge):

```bash
rigplane --host <RADIO_IP> --user <USER> --pass <PASS> serve --wsjtx-compat
```

### 2. Configure WSJT-X

In **Settings → Radio**:

| Setting | Value |
|---------|-------|
| **Rig** | `Hamlib NET rigctl` |
| **Network Server** | `127.0.0.1:4532` |
| **PTT Method** | `CAT` |
| **Mode** | `Data/Pkt` |
| **Split Operation** | `Fake It` |

Press **Test CAT** — the button should turn green.
Press **Test PTT** — the radio should key up briefly.

!!! note "Which port?"
    Point WSJT-X at RigPlane's client-facing `rigctld` endpoint. If RigPlane is
    also consuming an external Hamlib `rigctld` provider underneath, keep the two
    TCP ports distinct so WSJT-X talks to RigPlane, not around it.

### 3. Configure WSJT-X Audio (with BlackHole bridge)

Configure WSJT-X audio to use the BlackHole devices:

In **Settings → Audio**:

| Setting | Value | Why |
|---------|-------|-----|
| **Input** | `BlackHole 2ch` | Receives RX audio from radio |
| **Output** | `BlackHole 16ch` | Sends TX audio to radio |

> ⚠️ **Why two devices?** BlackHole is a unidirectional loopback — if you use the
> same device for both input and output, the bridge reads its own RX output as TX
> input, creating a feedback loop. Two separate devices isolate the paths.

The audio bridge routes:
- **Radio RX → rigplane → BlackHole 2ch → WSJT-X Input** (decode FT8/FT4)
- **WSJT-X Output → BlackHole 16ch → rigplane → Radio TX** (transmit FT8/FT4)

`--bridge` only describes the local audio loopback between WSJT-X and
rigplane. Radio DATA policy is derived from the resolved audio route:
direct Icom LAN audio uses the LAN audio route, while USB/serial audio
remains a USB route even when a local loopback bridge is enabled.

### Radio Settings (for TX audio)

On the IC-7610 front panel, set:
- **Menu → Connectors → MOD Input → LAN** (select `Data` or `USB`)
- This tells the radio to accept modulation input from the LAN connection

### Verifying Audio

After starting, check the bridge status:
```bash
curl http://localhost:8080/api/v1/bridge
# Should show: {"active": true, "rx_frames": ..., "rx_drops": 0, ...}
```

Zero `rx_drops` = healthy bridge. If you see drops, check CPU load.

## The `--wsjtx-compat` Flag

### What it does

When a CAT client connects for the first time:

- If the radio is in USB, LSB, or RTTY **with DATA mode OFF**,
  the server automatically enables DATA mode.
- This eliminates a known first-TX latency when WSJT-X switches
  from plain SSB to packet mode (PKTUSB).

When `--wsjtx-compat` is used with a direct Icom LAN radio connection on a
radio profile with multiple DATA sub-modes such as the IC-7610, rigplane treats
WSJT-X packet-mode requests as LAN audio operation:

- `PKTUSB`, `PKTLSB`, and `PKTRTTY` are mapped to DATA2 instead of DATA1.
- DATA2 modulation input is set to LAN.
- DATA1 modulation input is not changed.

For USB/serial radio connections, including cases where rigplane bridges local
USB audio devices for remote use, the compatibility mapping remains the legacy
DATA1 behavior.

### When to use it

- **Use** `--wsjtx-compat` if you run WSJT-X, JTDX, or JS8Call
  and want instant TX on the first transmission.
- **Don't use** if you need the radio to stay in the exact mode
  you set manually (e.g., SSB voice operation alongside CAT control).

### What it changes on the radio

In USB/serial CAT operation, only the DATA mode flag (CI-V `0x1A 0x06`) is
changed. In direct Icom LAN operation on multi-DATA radios, rigplane may also
set the selected LAN DATA sub-mode's modulation input to LAN. DATA1 is treated
as user-owned and is not rewritten by prewarm, profile apply, or state restore.

## Known Behavior

### USB → PKTUSB first-TX delay

When WSJT-X connects to a radio in plain USB (DATA off), it sends
`M PKTUSB -1` to switch to packet mode. This involves two radio changes:

1. Verify/set USB mode
2. Enable DATA mode

Some CAT client stacks (including WSJT-X and wfview's rigctld) introduce
a ~15–20 second delay before the first PTT after this transition.
**This is not an rigplane bug** — the same behavior occurs with wfview's
built-in rigctld emulation.

**Workarounds:**

- Use `--wsjtx-compat` (recommended) — pre-warms DATA on connect.
- Manually set the radio to USB-D1 before starting WSJT-X.
- Once DATA mode is active, subsequent TX cycles work without delay.

### After closing WSJT-X

The server gracefully handles client disconnect:

- Abandoned commands are cancelled (no background CI-V spam).
- The poller stops when the last client disconnects.
- Reconnecting a new WSJT-X session works immediately.

## Troubleshooting

### Test CAT fails (button stays red)

1. Verify the server is running: `rigctl -m 2 -r localhost:4532 f`
2. Check the server log for connection/auth errors.
3. Ensure no other application is using port 4532.

### Test PTT fails or has long delay

1. Confirm PTT Method is set to **CAT** (not VOX or hardware).
2. If starting from plain USB, use `--wsjtx-compat` or pre-set DATA mode.
3. Check server logs for CI-V timeout patterns.

### Radio becomes unresponsive after disconnect

1. The circuit breaker may have tripped. Wait ~6 seconds for auto-recovery.
2. Restart the server if needed: Ctrl-C and re-launch.
3. Check if another application (wfview, flrig) is also controlling the radio.

### Mode shows USB instead of PKTUSB

- Ensure WSJT-X Mode is set to **Data/Pkt** (not None or USB).
- With `--wsjtx-compat`, DATA mode should already be active on connect.

## Compatible Applications

Tested with:

- **WSJT-X** 2.7+ (FT8, FT4, JT65, etc.)
- **JTDX** (FT8/FT4 variant)
- **JS8Call** (JS8 mode)
- **fldigi** (various digital modes)
- **Log4OM 2** (logging + CAT)
- **MacLoggerDX** (macOS logging)

Any application that supports `Hamlib NET rigctl` should work.

## Get the Packaged Desktop App

!!! tip "Prefer a packaged desktop app?"
    This guide covers the open-source `rigplane` Python library. If you want
    a polished desktop application with a GUI that handles WSJT-X / JTDX /
    JS8Call integration on macOS and Linux, check out RigPlane Pro.

    [Download RigPlane Pro for Mac and Linux →](https://rigplane.com/downloads/?utm_source=docs.rigplane.dev&utm_medium=cta&utm_campaign=wsjtx-setup)
