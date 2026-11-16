---
description: Radios supported by RigPlane — native Icom and Yaesu providers today, plus Xiegu, Lab599, and other serial CAT candidates through Hamlib-backed assisted discovery.
---

# Supported Radios

Radio support in rigplane is provider-based. Native providers use **TOML rig
profiles** in `rigs/` to describe capabilities and protocol details. Long-tail
serial CAT radios can use the [Hamlib / external rigctld provider](hamlib-rigctld-provider.md),
which emits structured discovery candidates and maps Hamlib control into
RigPlane capabilities through an external `rigctld` process.

## Native Radios

### IC-7610

- **CI-V Address:** `0x98`
- **LAN Ports:** 50001 (control), 50002 (CI-V), 50003 (audio)
- **USB:** Serial CI-V + USB audio devices ([setup guide](ic7610-usb-setup.md))
- **Features:** frequency, mode, power, S-meter, SWR, ALC, PTT, CW keying, VFO select, split, attenuator, preamp, power on/off, discovery (LAN only), scope/waterfall
- **Dual receiver:** use `select_receiver("MAIN")` / `select_receiver("SUB")`

#### Backend Comparison

| Feature | LAN Backend | Serial Backend |
|---------|-------------|----------------|
| **Control (freq/mode/PTT)** | ✅ Full | ✅ Full |
| **Meters (S/SWR/ALC)** | ✅ Full | ✅ Full |
| **Audio RX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Audio TX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Scope/Waterfall** | ✅ Full (~225 pkt/s) | ⚠️ Requires ≥115200 baud |
| **Dual Receiver** | ✅ Command29 | ✅ Command29 |
| **Remote Access** | ✅ Over LAN/VPN | ❌ USB only |
| **Discovery** | ✅ UDP broadcast | ❌ N/A |
| **Setup** | IP, username, password | USB cable + device path |

!!! tip "USB Serial Setup"
    See the **[IC-7610 USB Serial Backend Setup Guide](ic7610-usb-setup.md)** for step-by-step instructions on using the serial backend (macOS-first).
    For IC-7610 USB operation, set **Menu → Set → Connectors → CI-V → CI-V USB Port**
    to `Unlink from [REMOTE]` (the radio's default), not `Link to [REMOTE]`.

### IC-7300

- **CI-V Address:** `0x94`
- **Connectivity:** USB serial (CI-V) — no built-in LAN
- **VFO scheme:** VFO A/B (not Main/Sub)
- **Rig profile:** `rigs/ic7300.toml`
- **Features:** frequency, mode, power, S-meter, SWR, ALC, PTT, CW keying,
  VFO A/B select, attenuator, preamp, NB, NR, scope/waterfall, audio RX/TX
- **Not available:** DIGI-SEL, LAN, dual receiver

!!! tip "Setup Guide"
    **[IC-7300 USB Serial Backend Setup](ic7300-usb-setup.md)** — Complete USB configuration guide
    for IC-7300 with audio integration and WSJT-X bridging.

#### IC-7300 vs IC-7610

| Feature | IC-7610 | IC-7300 |
|---------|---------|---------|
| Receivers | 2 (MAIN/SUB) | 1 (VFO A/B) |
| VFO labels | MAIN / SUB | VFO A / VFO B |
| DIGI-SEL | ✅ | ❌ |
| IP+ | ✅ | ✅ (supported) |
| LAN | ✅ | ❌ |
| Scope | ✅ | ✅ |
| USB Serial | ✅ | ✅ |

The Web UI automatically hides the DIGI-SEL control when connected to an IC-7300
(capability-based UI guards), while the documented IP+ control is supported and
visible on both radios. VFO labels switch to "VFO A" / "VFO B" automatically.

### Yaesu FTX-1

- **Protocol:** Yaesu CAT (text)
- **Connectivity:** USB serial
- **Rig profile:** `rigs/ftx1.toml`
- **Features:** 17 modes (incl. C4FM), dual RX, ATT on/off, 2m/70cm/HF
- **VFO scheme:** `ab_shared` (2 receivers, 1 VFO)
- **Backends:** Serial (Yaesu CAT) — full working backend
- **Web UI:** Full spectrum/waterfall via Audio FFT Scope, controls, audio RX/TX
- **Audio:** USB audio RX/TX supported; Audio FFT Scope provides real-time IF waterfall

!!! tip "Setup Guide"
    **[FTX-1 USB Setup](ftx1-usb-setup.md)** — the CAT port and rate, radio
    settings for USB audio, and the CLI, Web UI and Python entry points.

!!! tip "Yaesu CAT Backend"
    The FTX-1 uses the Yaesu CAT text protocol over USB serial.
    Full frequency, mode, PTT, and audio control is working.
    The Web UI uses the Audio FFT Scope for spectrum display (no hardware panadapter on FTX-1).

### Yaesu FT-991A

- **Protocol:** Yaesu CAT (text)
- **Connectivity:** USB serial (38400 bps)
- **Rig profile:** `rigs/ft991a.toml`
- **Features:** HF/VHF/UHF all-mode, single receiver, VFO A/B, split, IF shift, narrow/wide filter widths, TX meters (ALC/COMP/power/SWR)
- **VFO scheme:** `ab` (1 receiver, 1 transceiver)
- **Audio:** USB audio RX/TX
- **Status:** new in this version; built from the CAT manual, hardware verification in progress
- **Not yet verified on hardware:** transmit (PTT and digital modes)
- **Not available:** sending CW text, antenna tuner, NB/NR switches, clarifier, memory channels, squelch, contour

!!! tip "Setup Guide"
    **[FT-991A USB Setup](ft991a-usb-setup.md)** — the CAT port and rate, radio
    settings for USB audio, and the CLI, Web UI and Python entry points.

## Long-tail CAT / Profile Candidates

These radios have profiles or known CAT surfaces. The intended direction is assisted discovery and
Hamlib-backed control through an external `rigctld` process where that gives
broader coverage than maintaining a bespoke backend for each dialect.

### Xiegu X6100

- **Protocol:** CI-V (IC-705 compatible subset) / Hamlib candidate
- **CI-V Address:** `0x70`
- **Rig profile:** `rigs/x6100.toml`
- **Features:** HF + 6m, QRP 8W, built-in ATU, WiFi
- **VFO scheme:** `ab`
- **Status:** Profile only; the serial backend refuses it. Also a Hamlib assisted-discovery candidate.

### Xiegu X6200

- **Protocol:** CI-V (Xiegu/Icom-compatible subset)
- **CI-V Address:** `0xA4` by factory default, shared with IC-705
- **Connectivity:** USB serial CAT on `SERIAL-B`; default baud `19200`
- **Rig profile:** `rigs/x6200.toml`
- **Features:** HF + 6m, QRP, single receiver, VFO A/B, audio controls, PTT,
  split, VOX, compressor, CW, RIT/XIT, tuner, meters, tones, data mode, scan,
  dial lock, AGC
- **Not advertised:** remote power on/off and spectrum scope; the documented
  X6200 CI-V command envelope does not include those IC-705-style commands.
  RF power level control is advertised.
- **Status:** A native profile is present. Discovery includes
  X6200-vs-IC-705 disambiguation because both radios can report CI-V address
  `0xA4`. Hardware reports are welcome.

### Xiegu G7250

- **Protocol:** CI-V (Xiegu/Icom-compatible subset), per the Xiegu G7250 CI-V
  Reference Rev 1.2
- **Connectivity:** USB (CI-V serial + USB audio); network control in the
  radio's "wfview" mode, firmware V1.0.1 or later
- **Rig profile:** `rigs/g7250.toml`
- **Setup guide:** [G7250 USB and network setup](g7250-setup.md)

!!! note "Firmware"
    Xiegu G7250 support was verified on firmware V1.0.1, the first firmware
    with network control. Some functions of this firmware are incomplete or
    behave differently over USB and over the network; RigPlane can only
    control what the radio itself implements. For the radio's own functions
    and settings, refer to the manufacturer's documentation and firmware
    release notes. RigPlane's support will be extended as the firmware
    matures.

**Verified on firmware V1.0.1:**

- **USB:** frequency, mode, levels including volume, meters, and RX audio
  through the radio's USB audio device.
- **Network (wfview mode):** signing in with the radio's own network login,
  and polling of frequency, mode and S-meter.

**Not verified, not claimed:** TX, PTT and TX audio, over either connection.

**Panorama.** A USB-connected G7250 gets the audio-derived panorama only; the
radio does not answer the CI-V scope commands over serial. Scope and waterfall
over the network are not supported yet: the shipped profile declares no scope.

**Not exposed:** the 1D 17, 1D 18 and 1D 20 controls and the firmware readout.

**Network modes.** The radio's network menu has two exclusive modes:
**wfview** (Icom LAN control) and **HDSDR** (an IQ stream). In HDSDR mode Icom
LAN control is refused. The radio can end up in HDSDR mode after a restart or
on its own, so select **wfview** on the radio before connecting. Do not use USB
and network control of the same radio at the same time.

**Connecting.** Over USB the radio shows a serial port for CI-V (115200 baud)
and a USB audio device; pass `--serial-port` on the command line. Without
`--serial-port` or `--host`, `--model G7250` searches the network. For network
control, set the radio's network mode to **wfview** and use the network login
set in its network menu. Pass `--model G7250`; with several radios on the
network, give the radio's address with `--host`.

### Lab599 TX-500

- **Protocol:** Kenwood CAT (text) / Hamlib candidate
- **Rig profile:** `rigs/tx500.toml`
- **Features:** HF + 6m, QRP 10W, built-in ATU, minimal CAT (ID FA FB MD FR FT PA RA)
- **VFO scheme:** `ab`
- **Status:** Profile only. Hamlib-backed provider plus assisted discovery is the preferred first path, not a bespoke Kenwood CAT backend.

## More Native Radios

These radios are also supported by native backends.

### IC-705

- **CI-V Address:** `0xA4`
- **Connectivity:** LAN (WiFi) + USB serial (CI-V)
- **VFO scheme:** Single receiver (portable transceiver)
- **Rig profile:** `rigs/ic705.toml`
- **Features:** LAN connect/disconnect, reconnect, frequency, mode, PTT, and
  audio on the WiFi backend
- **Status:** Supported over LAN/WiFi and USB.
- **Connecting over WiFi:** pass `--model IC-705`. LAN discovery finds the
  radio's IP address but not its model.

!!! tip "Setup Guides"
    - **[IC-705 USB Serial Backend Setup](ic705-usb-setup.md)** — Step-by-step USB configuration

### IC-9700

- **CI-V Address:** `0xA2`
- **Connectivity:** LAN (Ethernet) + USB serial (CI-V)
- **VFO scheme:** Dual independent receivers (MAIN/SUB)
- **Rig profile:** `rigs/ic9700.toml`
- **Features:** frequency, mode, power, S-meter, scope/waterfall, audio RX/TX,
  independent MAIN/SUB control. RX audio is one mono stream; separate MAIN/SUB
  audio is not supported yet.
- **Status:** Supported over LAN and USB. An FM/AM waterfall passband that
  rendered asymmetric
  ([#1719](https://github.com/rigplane/rigplane-core/issues/1719)) was fixed in
  [#1792](https://github.com/rigplane/rigplane-core/pull/1792).

!!! tip "Setup Guide"
    **[IC-9700 USB Serial & LAN Setup](ic9700-usb-setup.md)** — Setup guide covering
    serial USB and LAN Ethernet configuration.

## Using Presets

Instead of remembering CI-V addresses, use the built-in presets:

```python
from rigplane import create_radio, LanBackendConfig, get_civ_addr

# Look up by model name and pass to config
addr = get_civ_addr("IC-705")
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", radio_addr=addr)
async with create_radio(config) as radio:
    ...
```

For LAN-only scripts you can still use `IcomRadio(host, radio_addr=get_civ_addr("IC-705"), ...)` — see [API Reference](../api/radio.md).

## Custom CI-V Address

If you've changed your radio's CI-V address in the menu, specify it explicitly in the backend config:

```python
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", model="IC-7610", radio_addr=0x42)
async with create_radio(config) as radio:
    ...
```

## Adding Support for New Radios

See **[Adding a New Radio (Rig Profiles)](rig-profiles.md)** for the complete guide.
In brief:

1. Decide whether the radio belongs on an existing native provider path or the
   Hamlib-backed provider path. For Hamlib, start with the
   [external rigctld provider guide](hamlib-rigctld-provider.md).
2. For native provider work, copy the closest reference rig file as a template:
   - Icom CI-V → `rigs/ic7610.toml`
   - Kenwood CAT → `rigs/tx500.toml`
   - Yaesu CAT → `rigs/ftx1.toml`
3. Update `[radio]` and `[protocol]` sections.
4. Update `[capabilities]`, `[controls]`, `[meters]`, `[[rules]]` as needed.
5. For CI-V radios: update `[commands]` section.
6. Run `uv run pytest tests/test_rig_loader.py tests/test_rig_multi_vendor.py -v` to validate.

A radio needs a rig profile: without a profile, `model` or a `radio_addr` that
matches a loaded profile, RigPlane raises `ValueError` rather than drive it as
another rig. [Open an issue](https://github.com/rigplane/rigplane-core/issues)
or PR with your rig file.

### Finding Your Radio's CI-V Address

- Check your radio's **Menu → Set → CI-V** settings
- Look it up in the Icom CI-V reference manual
- The default is usually printed in the radio's specification sheet
