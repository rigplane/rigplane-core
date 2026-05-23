---
description: Radios supported by RigPlane — native Icom and Yaesu providers today, plus Xiegu, Lab599, and other serial CAT candidates through Hamlib-backed assisted discovery.
---

# Supported Radios

Radio support in rigplane is provider-based. Native providers use **TOML rig
profiles** in `rigs/` to describe capabilities and protocol details. Long-tail
serial CAT radios can use the [Hamlib / external rigctld provider](hamlib-rigctld-provider.md),
which emits structured discovery candidates and maps Hamlib control into
RigPlane capabilities through an external `rigctld` process.

## Tested

### IC-7610

- **CI-V Address:** `0x98`
- **LAN Ports:** 50001 (control), 50002 (CI-V), 50003 (audio)
- **USB:** Serial CI-V + USB audio devices ([setup guide](ic7610-usb-setup.md))
- **Features verified:** frequency, mode, power, S-meter, SWR, ALC, PTT, CW keying, VFO select, split, attenuator, preamp, power on/off, discovery (LAN only), scope/waterfall
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
    to the CI-V option (`Link to [CI-V]`), not `[REMOTE]`.

### IC-7300

- **CI-V Address:** `0x94`
- **Connectivity:** USB serial (CI-V) — no built-in LAN
- **VFO scheme:** VFO A/B (not Main/Sub)
- **Rig profile:** `rigs/ic7300.toml`
- **Features verified:** frequency, mode, power, S-meter, SWR, ALC, PTT, CW keying,
  VFO A/B select, attenuator, preamp, NB, NR, scope/waterfall, audio RX/TX
- **Not available:** DIGI-SEL, IP+, LAN, dual receiver

!!! tip "Setup Guide"
    **[IC-7300 USB Serial Backend Setup](ic7300-usb-setup.md)** — Complete USB configuration guide
    for IC-7300 with audio integration and WSJT-X bridging.

#### IC-7300 vs IC-7610

| Feature | IC-7610 | IC-7300 |
|---------|---------|---------|
| Receivers | 2 (MAIN/SUB) | 1 (VFO A/B) |
| VFO labels | MAIN / SUB | VFO A / VFO B |
| DIGI-SEL | ✅ | ❌ |
| IP+ | ✅ | ❌ |
| LAN | ✅ | ❌ |
| Scope | ✅ | ✅ |
| USB Serial | ✅ | ✅ |

The Web UI automatically hides DIGI-SEL and IP+ controls when connected to an IC-7300
(capability-based UI guards). VFO labels switch to "VFO A" / "VFO B" automatically.

### Yaesu FTX-1

- **Protocol:** Yaesu CAT (text)
- **Connectivity:** USB serial
- **Rig profile:** `rigs/ftx1.toml`
- **Features:** 17 modes (incl. C4FM), dual RX, ATT 4 levels, 2m/70cm/HF
- **VFO scheme:** `ab_shared` (2 receivers, 1 VFO)
- **Backends:** Serial (Yaesu CAT) — full working backend
- **Web UI:** Full spectrum/waterfall via Audio FFT Scope, controls, audio RX/TX
- **Audio:** USB audio RX/TX supported; Audio FFT Scope provides real-time IF waterfall

!!! tip "Yaesu CAT Backend"
    The FTX-1 uses the Yaesu CAT text protocol over USB serial.
    Full frequency, mode, PTT, and audio control is working.
    The Web UI uses the Audio FFT Scope for spectrum display (no hardware panadapter on FTX-1).

## Long-tail CAT / Profile Candidates

These radios have profiles or known CAT surfaces, but they are not yet
first-party hardware-validated. The intended direction is assisted discovery and
Hamlib-backed control through an external `rigctld` process where that gives
broader coverage than maintaining a bespoke backend for each dialect.

### Xiegu X6100

- **Protocol:** CI-V (IC-705 compatible subset) / Hamlib candidate
- **CI-V Address:** `0x70`
- **Rig profile:** `rigs/x6100.toml`
- **Features:** HF + 6m, QRP 8W, built-in ATU, WiFi
- **VFO scheme:** `ab`
- **Status:** Profile only. May work with CI-V backend (untested); also a Hamlib assisted-discovery candidate.

### Lab599 TX-500

- **Protocol:** Kenwood CAT (text) / Hamlib candidate
- **Rig profile:** `rigs/tx500.toml`
- **Features:** HF + 6m, QRP 10W, built-in ATU, minimal CAT (ID FA FB MD FR FT PA RA)
- **VFO scheme:** `ab`
- **Status:** Profile only. Hamlib-backed provider plus assisted discovery is the preferred first path, not a bespoke Kenwood CAT backend.

## Community-Validated / Maintainer Hardware Pending

These radios have working field reports and validated user integrations, but the
maintainer has not yet completed first-party hardware validation in this repo.

### IC-705

- **CI-V Address:** `0xA4`
- **Connectivity:** LAN (WiFi/Ethernet) + USB serial (CI-V)
- **VFO scheme:** Single receiver (portable transceiver)
- **Rig profile:** `rigs/ic705.toml`
- **Validated features:** LAN connect/disconnect, reconnect, frequency, mode,
  PTT, and audio path integrations on the WiFi backend
- **Status:** Community-validated on LAN/WiFi. First-party maintainer hardware
  validation is still pending.

!!! tip "Setup Guides"
    - **[IC-705 USB Serial Backend Setup](ic705-usb-setup.md)** — Step-by-step USB configuration
    - Use **Menu → Set → Connectors → CI-V → CI-V USB Port** = `Link to [CI-V]`

## Has Rig Profile (Not Yet Backend-Tested)

These radios have complete rig profiles and the CI-V backend should support them, but they
have not been tested by the maintainers. Community testing and reports welcome!

### IC-9700

- **CI-V Address:** `0xA2`
- **Connectivity:** LAN (Ethernet) + USB serial (CI-V)
- **VFO scheme:** Dual independent receivers (MAIN/SUB)
- **Rig profile:** `rigs/ic9700.toml`
- **Expected features:** frequency, mode, power, S-meter, scope/waterfall, audio RX/TX,
  independent MAIN/SUB control, dual audio streaming
- **Status:** Profile complete. Backend untested — reports welcome.

!!! tip "Setup Guide"
    **[IC-9700 USB Serial & LAN Setup](ic9700-usb-setup.md)** — Setup guide covering
    serial USB and LAN Ethernet configuration.

## Should Work (Untested)

These radios use the same Icom LAN protocol and should work out of the box. Community testing and reports welcome!

### IC-7851

- **CI-V Address:** `0x8E`
- **Connectivity:** Ethernet (built-in)

### IC-R8600

- **CI-V Address:** `0x96`
- **Connectivity:** Ethernet (built-in)
- **Notes:** Receiver only — PTT/TX commands will be rejected.

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
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", radio_addr=0x42)
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

The library is CI-V address agnostic — any radio that speaks the Icom LAN protocol should
work. If you test with a new model:

1. Connect with the model's default CI-V address
2. Verify basic operations (frequency, mode, meters)
3. [Open an issue](https://github.com/rigplane/rigplane-core/issues) or PR with your rig file

### Finding Your Radio's CI-V Address

- Check your radio's **Menu → Set → CI-V** settings
- Look it up in the Icom CI-V reference manual
- The default is usually printed in the radio's specification sheet
