# IC-7610 USB Serial Backend Setup (macOS)

This guide shows how to control the IC-7610 via **USB serial CI-V + USB audio devices** instead of the default LAN backend.

## Why Use the Serial Backend?

- **No network required** — direct USB connection
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Field operation** — works without WiFi/Ethernet

## Hardware Requirements

- IC-7610 transceiver
- USB A-to-B cable (typically included with the radio)
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

!!! danger "Critical Setup Step"
    On the IC-7610, navigate to **Menu → Set → Connectors → CI-V → CI-V USB Port** and set it to **`Link to [CI-V]`**, **NOT** `[REMOTE]`.
    
    - `Link to [CI-V]` — serial CI-V commands work (required for rigplane serial backend)
    - `[REMOTE]` — RS-BA1 mode, serial CI-V is blocked
    
    This finding was confirmed with live hardware validation in issue #146.

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Port** | `Link to [CI-V]` | ✅ Required — enables serial CI-V |
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0x98` (default) | Library auto-detects, but confirm if changed |
| **USB Audio TX** | Enabled | Allows browser/WSJT-X TX via USB audio |
| **USB Audio RX** | Enabled | Exports RX audio to computer |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Lower baud rates (19200, 9600) work for basic control (freq, mode, PTT) but scope/waterfall is disabled by a guardrail due to high packet rate
    - CI-V baud rate IS significant on IC-7610 — it must match between radio and library

## macOS Setup

### 1. Install rigplane

```bash
# Core install — includes serial CI-V (pyserial), USB audio RX/TX,
# audio-device listing (sounddevice + numpy + opuslib).
pip install rigplane
```

!!! note
    Since v0.19 the audio-bridge stack (`sounddevice`, `numpy`, `opuslib`)
    is part of the core install. The legacy `[bridge]` and `[audio]` extras
    still resolve but are now no-op aliases.

### 2. Connect the Radio

1. Power on the IC-7610
2. Connect USB A-to-B cable from Mac to IC-7610 **USB (B)** port (rear panel, square connector)
3. Wait ~5 seconds for macOS to enumerate the device

### 3. Find the Serial Device

```bash
# List serial devices
ls -l /dev/cu.usbserial-*

# Example output:
# /dev/cu.usbserial-111120
```

The IC-7610 typically appears as `/dev/cu.usbserial-XXXXXX` where `XXXXXX` is the radio's serial number.

You can also use `rigplane discover --serial-only` to find USB-connected radios automatically:

```bash
rigplane discover --serial-only
```

```
IC-7610:
  • Serial: /dev/cu.usbserial-111120 (19200 baud)
```

### 4. Find USB Audio Devices

```bash
# List available audio devices
rigplane --list-audio-devices
```

Audio-device listing requires `sounddevice`, which ships with the core
install since v0.19 (`pip install rigplane`).

Example output:

```
4 audio device(s):
  [0] IC-7610 USB Audio  (in=2, out=2)
  [1] Built-in Microphone  (in=2, out=0)
  [2] Built-in Output  (in=0, out=2)
  [3] BlackHole 2ch  (in=2, out=2)
```

The IC-7610 USB audio device is typically named `IC-7610 USB Audio` or similar.

!!! tip "JSON Output"
    ```bash
    rigplane --list-audio-devices --json
    ```
    Returns structured JSON for scripting.

!!! tip "Multi-Radio: Automatic USB Audio Resolution"
    When two or more Icom radios are connected via USB simultaneously (e.g. IC-7300 + IC-7610),
    each exposes an identically named "USB Audio CODEC" device. Specifying `--rx-device` by name
    is ambiguous in this situation.

    The serial backend automatically resolves the correct audio device using **USB topology
    matching** (macOS only): it reads each device's USB hub location from IORegistry and matches
    the audio device that shares the same USB hub as the serial CI-V port. No manual index
    specification is required.

    If topology resolution fails (e.g. on Linux), the driver falls back to selecting the
    first matching USB Audio CODEC device by name, which may be incorrect for multi-radio setups.
    In that case, specify explicit device indices with `--rx-device` and `--tx-device`.

### 5. Test the Connection

```bash
# Set environment variables
export ICOM_SERIAL_DEVICE=/dev/cu.usbserial-111120
export ICOM_SERIAL_BAUDRATE=115200

# Test basic control
rigplane --backend serial status
rigplane --backend serial freq
rigplane --backend serial mode
```

Expected output:

```
Frequency:    14,074,000 Hz  (14.074000 MHz)
Mode:         USB
S-meter:      42
Power:        50
```

### 6. Test Audio (Optional)

```bash
# Capture 10 seconds of RX audio to WAV
rigplane --backend serial \
    --rx-device "IC-7610 USB Audio" \
    audio rx --out test_rx.wav --seconds 10

# Audio devices are auto-detected if not specified
```

## Usage Examples

### CLI

```bash
# Status check
rigplane --backend serial status

# Set frequency
rigplane --backend serial freq 7.074m

# Set mode
rigplane --backend serial mode USB

# PTT on/off
rigplane --backend serial ptt on
rigplane --backend serial ptt off

# CW keying
rigplane --backend serial cw "CQ CQ DE KN4KYD K"

# Attenuator (uses Command29 for IC-7610)
rigplane --backend serial att 18
rigplane --backend serial att 0

# Preamp
rigplane --backend serial preamp 1
rigplane --backend serial preamp 0
```

### Python API

```python
import asyncio
from rigplane.backends.factory import create_radio
from rigplane.backends.config import SerialBackendConfig

async def main():
    config = SerialBackendConfig(
        device="/dev/cu.usbserial-111120",
        baudrate=115200,
        radio_addr=0x98,
        rx_device="IC-7610 USB Audio",  # or None for auto-detect
        tx_device="IC-7610 USB Audio",  # or None for auto-detect
    )
    
    radio = create_radio(config)
    
    async with radio:
        # Control
        freq = await radio.get_frequency()
        print(f"Frequency: {freq/1e6:.3f} MHz")
        
        await radio.set_frequency(14_074_000)
        await radio.set_mode("USB")
        
        # Meters
        s = await radio.get_s_meter()
        print(f"S-meter: {s}")
        
        # Audio (if audio_capable)
        from rigplane.radio_protocol import AudioCapable
        if isinstance(radio, AudioCapable):
            def on_audio(packet):
                print(f"RX audio: {len(packet.data)} bytes")
            
            await radio.start_audio_rx_opus(on_audio)
            await asyncio.sleep(10)
            await radio.stop_audio_rx_opus()

asyncio.run(main())
```

### Web UI with Serial Backend

```bash
# Start web UI on serial backend
rigplane --backend serial \
    --rx-device "IC-7610 USB Audio" \
    --tx-device "IC-7610 USB Audio" \
    web

# Then open http://localhost:8080
```

The web UI will show:
- Frequency/mode control
- Meters (S-meter, power, SWR during TX)
- RX audio streaming to browser
- TX audio from browser microphone (if USB audio TX is enabled)

### rigctld with Serial Backend

```bash
# Start rigctld server on serial backend
rigplane --backend serial serve

# Then configure WSJT-X:
# Radio: Hamlib NET rigctl
# Network Server: localhost:4532
```

## Capability Differences: LAN vs Serial

| Feature | LAN Backend | Serial Backend |
|---------|-------------|----------------|
| **Control (freq/mode/PTT)** | ✅ Full | ✅ Full |
| **Meters (S/SWR/ALC)** | ✅ Full | ✅ Full |
| **Audio RX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Audio TX** | ✅ Opus/PCM over UDP | ✅ USB audio device |
| **Scope/Waterfall** | ✅ Full (~225 pkt/s) | ⚠️ Requires ≥115200 baud* |
| **Dual Receiver** | ✅ Command29 | ✅ Command29 |
| **Remote Access** | ✅ Over LAN/VPN | ❌ USB only |
| **Discovery** | ✅ UDP broadcast | ✅ CI-V auto-probe |

\* **Scope guardrail**: Serial backend enforces a minimum 115200 baud for scope/waterfall operations due to high CI-V packet rate. Lower baud rates risk command timeout/starvation. Override is possible via `allow_low_baud_scope=True` or `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1` (use with caution).

## Troubleshooting

### "No such file or directory: /dev/cu.usbserial-..."

**Cause**: USB cable not connected, or radio not powered on.

**Fix**:
1. Check USB cable connection (rear panel **USB (B)** port on IC-7610)
2. Power-cycle the radio
3. Wait 5-10 seconds after power-on
4. Run `ls -l /dev/cu.usbserial-*` again

### "Permission denied: /dev/cu.usbserial-..."

**Cause**: User lacks permissions to access serial device.

**Fix** (macOS typically doesn't need this, but if it happens):
```bash
# Add your user to the dialout group (Linux)
sudo usermod -a -G dialout $USER

# Logout and login again
```

On macOS, if you see permissions issues, try:
```bash
# Check ownership
ls -l /dev/cu.usbserial-*

# Typically owned by root:wheel with mode 0666 (world read/write)
# If not, contact system admin or check USB security settings
```

### "Audio device 'IC-7610 USB Audio' not found"

**Cause**: USB audio not exported by radio, or wrong device name.

**Fix**:
1. Verify USB audio is enabled in radio settings:
   - Menu → Set → Connectors → USB Audio → **Enabled**
2. List available devices:
   ```bash
   rigplane --list-audio-devices
   ```
3. Use exact device name from the list (case-sensitive)
4. If still not visible, disconnect/reconnect USB cable

### "Scope over serial requires baudrate >= 115200"

**Symptom**: `CommandError` when calling `enable_scope()` or `capture_scope_frame()` with baud rate < 115200.

**Cause**: Scope/waterfall CI-V traffic is high-rate (~225 packets/sec on LAN). Lower serial baud rates cannot sustain this rate without starving command responses.

**Fix**:
1. **Recommended**: Set CI-V USB Baud Rate to **115200** in radio settings
2. **Override** (use with caution):
   ```python
   config = SerialBackendConfig(..., allow_low_baud_scope=True)
   ```
   or
   ```bash
   export ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1
   ```
   Library will log a warning about timeout risk.

### "CI-V response timed out" on serial

**Causes**:
1. **Wrong baud rate** — radio and library must match
2. **Wrong CI-V USB Port setting** — must be `Link to [CI-V]`, not `[REMOTE]`
3. **Serial port busy** — another app (wfview, hamlib, etc.) is using the serial device

**Fix**:
1. Check radio baud rate: Menu → Set → Connectors → CI-V → CI-V USB Baud Rate
2. Check CI-V USB Port setting (see "Critical Setup Step" above)
3. Close other apps using the serial port:
   ```bash
   # Check what's using the port (macOS)
   lsof | grep cu.usbserial
   ```

### Wrong USB audio device selected with two radios connected

**Symptom**: Audio from the wrong radio when IC-7300 and IC-7610 are both connected via USB.

**Cause**: Two identical "USB Audio CODEC" devices appear in the system; name-based selection picks the first one regardless of which radio's serial port is in use.

**Fix**: On macOS, this is resolved **automatically** — the library reads USB hub topology via IORegistry and selects the audio device on the same USB hub as the serial CI-V port. Check the log for a line like:

```
usb-audio-resolve: /dev/cu.usbserial-201410 → prefix 0x2014 → RX device [2], TX device [3]
```

If you see `"topology resolution not supported"` (Linux), specify device indices explicitly:
```bash
rigplane --backend serial --rx-device 2 --tx-device 3 status
```

Use `rigplane --list-audio-devices` to find the correct indices.

### Audio TX not working

**Cause**: Radio USB Audio TX not enabled.

**Fix**:
1. Menu → Set → Connectors → USB Audio → **USB Audio TX** → **Enabled**
2. Check PTT is active before transmitting audio
3. Verify `--tx-device` matches the USB audio device name

### TX appears dirty / multiple peaks on waterfall during the first seconds

**Symptom**: When transmitting from WSJT-X / JS8Call / fldigi via the USB Audio CODEC, the first 5–10 seconds of TX show on the waterfall (or a second receiver) as a "fat" carrier centred ~1500 Hz, or as several spurs spread across the audio passband. The signal then either settles into a clean tone, or never settles if the level is too high.

**Cause**: IC-7610's USB audio input has less internal headroom before ALC compression than IC-7300 / FTX-1. With the WSJT-X power slider (the vertical slider on the right of the main window) at 100%, the FT8/JS8 tone clips inside the radio, producing IM3-style intermodulation products until ALC finds its working point. This is a hardware/firmware property of the IC-7610 USB CODEC — it occurs regardless of which CAT path (LAN, serial, direct rigctl) is used to control the radio, since the audio itself flows OS-level (WSJT-X → CoreAudio → USB Audio CODEC → radio) and never passes through RigPlane.

**Fix**:

1. **WSJT-X → main window → right-side TX power slider** → drop to **~20-25%** as a starting point.
2. Optionally also reduce **Menu → Set → Connectors → MOD Level** on the radio to ~30%.
3. PTT into FT8 / JS8 and verify on the waterfall: a single clean carrier at the FT8 audio centre, no spread peaks during the first seconds.

!!! tip "Precise tuning with an external SWR/power meter"
    A more precise way to find the optimal level — if you have an external SWR/power meter inline — is to dial the WSJT-X slider down while keying TX:

    - Above the ALC threshold the radio's output power stays roughly **flat** as you reduce the slider, because ALC is compressing the overdrive.
    - Continue lowering the slider until the **external meter starts to drop**.
    - That's the point where ALC has stopped engaging. Set the slider at (or just slightly above) that level — that's the optimal clean drive: full rated output, no compression, no IM3 spurs.

    This works because ALC behaves as a hard limiter: once you're below its threshold, output tracks input one-to-one again, so the meter "wakes up" at exactly the right level.

!!! note "IC-7610 specific"
    IC-7300 and FTX-1 tolerate the WSJT-X slider at 100% without this effect because their USB audio paths have more headroom. Don't apply this attenuation to those radios — it just costs you SNR.

!!! tip "Verifying with a second receiver"
    The cleanest way to confirm the fix is to monitor your TX on a second receiver (or a public web SDR — kiwisdr.com, websdr.org). Compare the waterfall during the first 10 seconds of TX at 100% slider vs 25% slider — the difference is unmistakable.

## Environment Variables

For convenience, set these in your shell profile:

```bash
# ~/.bashrc or ~/.zshrc
export ICOM_SERIAL_DEVICE=/dev/cu.usbserial-111120
export ICOM_SERIAL_BAUDRATE=115200
export ICOM_USB_RX_DEVICE="IC-7610 USB Audio"
export ICOM_USB_TX_DEVICE="IC-7610 USB Audio"

# Then simply:
rigplane --backend serial status
```

## Migration from LAN to Serial

If you're currently using the LAN backend and want to switch to serial:

1. **No code changes needed** — use `create_radio(config)` factory with typed config:
   ```python
   # Before (LAN)
   from rigplane.backends.config import LanBackendConfig
   config = LanBackendConfig(host="192.168.1.100", ...)
   
   # After (Serial)
   from rigplane.backends.config import SerialBackendConfig
   config = SerialBackendConfig(device="/dev/cu.usbserial-111120", ...)
   
   # Same factory call
   radio = create_radio(config)
   ```

2. **CLI**: add `--backend serial` flag:
   ```bash
   # Before (LAN, default)
   rigplane status
   
   # After (Serial)
   rigplane --backend serial status
   ```

3. **Capability check**: scope/waterfall requires ≥115200 baud (see table above)

4. **Audio device selection**: LAN uses Opus/PCM over UDP, serial uses USB audio devices (explicit device names or auto-detect)

## Next Steps

- **Web UI + Serial**: See [Web UI Guide](web-ui.md)
- **rigctld + Serial**: See [CLI Reference](cli.md)
- **Python API**: See [API Reference](../api/radio.md)
- **Troubleshooting**: See [Troubleshooting Guide](troubleshooting.md)

---

**Hardware validated**: 2026-03-06, IC-7610 over USB on macOS (Darwin arm64), Python 3.11.14, pytest 9.0.2 (issue #146)
