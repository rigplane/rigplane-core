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
- **CI-V Address:** the radio answers any address over USB. Over the network
  only `0xA0` answers. <!-- PENDING: L3 (profile moves to 0xA0 and has_lan) -->
- **Connectivity:** USB (CI-V serial + USB audio); network control in the
  radio's "wfview" mode (firmware V1.0.1 or later) <!-- PENDING: L1+L2+L3 -->
- **Rig profile:** `rigs/g7250.toml`
- **Features:** HF + 6m, single receiver, VFO A/B, frequency, mode, levels,
  switches and meters
- **Setup guide:** [G7250 USB and network setup](g7250-setup.md)

**USB.** The radio's USB port shows a serial port for CI-V (115200 baud;
the radio did not answer at 19200) and a separate USB audio device. RX audio
uses the audio device. A USB-connected G7250 gets the audio-derived panorama
only: the radio does not answer the CI-V scope commands over serial, so there
is no hardware scope. With `--model G7250`, pass `--serial-port` for USB;
without `--serial-port` or `--host`, RigPlane searches the network instead.

**Network.** The radio's network port has two modes, chosen in the radio's
network menu: **HDSDR** and **wfview**. The menu offers one at a time, so the HDSDR IQ stream is not available while
the radio is in wfview mode.

| Mode | What the radio sends | RigPlane |
|------|----------------------|----------|
| HDSDR | Raw IQ stream over UDP | Not used for control; a G7250 IQ panorama is not shipped ([Radio LAN IQ](sdr-panadapter.md#radio-lan-iq)) |
| wfview | Icom-style network control: CI-V, audio, scope | Network control <!-- PENDING: L1+L2+L3 --> |

Network control needs the **wfview** mode and firmware **V1.0.1 or later**.
In wfview mode the radio uses the Icom network handshake on UDP ports 50001
(control), 50002 (CI-V) and 50003 (audio), and asks for the user name and
password set in its network menu.

What works, per connection:

| | USB | Network (wfview mode) |
|---|---|---|
| Frequency, mode, levels, switches | Yes | Yes <!-- PENDING: L2+L3 --> |
| Meters (S, power, SWR) | Yes | Yes <!-- PENDING: L3 --> |
| RX audio | Yes (USB audio device) | Yes <!-- PENDING: L1+L3 (audio sub-stream) --> |
| Panorama | Audio-derived only | The radio sends scope data (475 points per sweep, spans 48, 96, 192 and 384 kHz); the hardware panorama ships in a later release than network control <!-- PENDING: scope-PR (alt: "Yes, spans 48 to 384 kHz") --> |
| TX and TX audio | Not verified | Not verified <!-- PENDING: TX-check (alt: replace with the verified result per connection) --> |

TX, PTT and TX audio have not been verified on this radio, over either
connection. They are not claimed. The radio has a 10 s PTT timer
(CI-V Reference Rev 1.2). <!-- PENDING: TX-check -->

**Settings on the radio.** For network control: network mode `wfview`, and a
user name and password in the network menu. The radio does not report its
network mode over CI-V; if RigPlane cannot connect, check the mode first.
Whether a mode change needs a power cycle is not verified. Network discovery finds the
radio, but it does not name the model, so pass `--model G7250`. With several
radios on the network, give the radio's address with `--host`.
<!-- PENDING: L1+L3 (discovery and model choice) -->

**Known limits:**

- AGC (firmware V1.0.1): the radio's panel has Auto, Slow, Fast and Off. Over
  CI-V only Off, Fast and Slow can be selected. Auto cannot be selected by
  command and reads back the same as Off, so RigPlane shows "AGC off" while
  the radio is in Auto. <!-- PENDING: L3 (profile limits AGC choices to Off, Fast, Slow) -->
- Controls the radio only stores without acting on (the reference marks them
  as compatibility values) are not offered. Examples: the CW-R and RTTY modes.
- Band edges, filter widths and the RIT/XIT, VOX, notch and dial-lock
  controls follow the reference and are being checked on the radio; treat
  them as unverified. <!-- PENDING: scope-PR (profile data PR; cut if merged and verified) -->
- Writing a frequency over the network works through the radio's second
  frequency command; the plain one is echoed but not applied. RigPlane does
  this itself. <!-- PENDING: L2+L3 -->
- Hardware reports are welcome.

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
