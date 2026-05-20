---
description: CI-V commands supported by RigPlane — frequency, mode, filter, AGC, attenuator, preamp, meters, and per-radio extensions exposed in a Python API.
---

# CI-V Commands

The library supports a comprehensive set of CI-V commands for controlling your Icom transceiver.

## Frequency

```python
# Get current frequency (returns Hz as int)
freq = await radio.get_frequency()
print(f"{freq / 1e6:.3f} MHz")  # 14.074 MHz

# Set frequency
await radio.set_frequency(14_074_000)   # 20m FT8
await radio.set_frequency(7_074_000)    # 40m FT8
await radio.set_frequency(3_573_000)    # 80m FT8
await radio.set_frequency(144_300_000)  # 2m SSB
```

Frequency is always in **Hz** (integer). The radio internally uses BCD encoding (5 bytes, little-endian).

## Mode

```python
from rigplane import Mode

# Get current mode
mode_name, filt = await radio.get_mode()
print(mode_name)  # "USB"

# Get mode + filter number (if reported)
mode, filt = await radio.get_mode_info()

# Set mode (string or enum)
await radio.set_mode("USB")
await radio.set_mode("CW")
await radio.set_mode(Mode.LSB)

# Set/read filter number
await radio.set_filter(2)
f = await radio.get_filter()
```

Available modes:

| Mode | Value | Description |
|------|-------|-------------|
| `LSB` | `0x00` | Lower Sideband |
| `USB` | `0x01` | Upper Sideband |
| `AM` | `0x02` | Amplitude Modulation |
| `CW` | `0x03` | Continuous Wave |
| `RTTY` | `0x04` | Radio Teletype |
| `FM` | `0x05` | Frequency Modulation |
| `WFM` | `0x06` | Wide FM |
| `CW_R` | `0x07` | CW Reverse |
| `RTTY_R` | `0x08` | RTTY Reverse |
| `DV` | `0x17` | D-Star Digital Voice |

## RF Power

```python
# Get RF power level (0–255)
power = await radio.get_power()

# Set RF power level
await radio.set_power(128)  # ~50% power
await radio.set_power(255)  # Maximum power
await radio.set_power(0)    # Minimum power
```

!!! note "Power Mapping"
    The 0–255 value is the radio's internal setting. The actual wattage depends on the radio model and operating mode. For IC-7610: 255 ≈ 100W on HF.

## Meters

```python
# S-meter (0–255, receive signal strength)
s = await radio.get_s_meter()

# SWR meter (0–255, during TX only)
swr = await radio.get_swr()

# ALC meter (0–255, during TX only)
alc = await radio.get_alc_meter()
```

!!! warning "TX-Only Meters"
    `get_swr()` and `get_alc_meter()` will timeout if the radio is not transmitting. Wrap them in try/except:

    ```python
    from rigplane.exceptions import TimeoutError

    try:
        swr = await radio.get_swr()
    except TimeoutError:
        swr = None  # Not transmitting
    ```

## PTT (Push-To-Talk)

```python
# Start transmitting
await radio.set_ptt(True)

# Stop transmitting
await radio.set_ptt(False)
```

!!! danger "Transmit Safety"
    Always ensure:

    - Your antenna is connected
    - You are authorized to transmit on the current frequency
    - PTT is released when done (use try/finally)

    ```python
    try:
        await radio.set_ptt(True)
        # ... do TX work ...
    finally:
        await radio.set_ptt(False)
    ```

## VFO Control

```python
# Select VFO slot within the active receiver (A/B)
await radio.set_vfo_slot("A")     # VFO A
await radio.set_vfo_slot("B")     # VFO B

# Select receiver on dual-RX rigs (IC-7610 / IC-9700)
await radio.select_receiver("MAIN")
await radio.select_receiver("SUB")

# A=B command on the active receiver
await radio.equalize_vfo_ab()

# Swap VFO A and B on the active receiver
await radio.swap_vfo_ab()

# Swap MAIN/SUB on dual-RX rigs
await radio.swap_main_sub()

# Split mode
await radio.set_split(True)   # TX on VFO B, RX on VFO A
await radio.set_split(False)
```

## Attenuator & Preamp

These commands use **Command29 framing** for dual-receiver radios (IC-7610).
By default, they target the MAIN receiver.

```python
from rigplane import RECEIVER_MAIN, RECEIVER_SUB

# --- Attenuator ---
# Get attenuation level in dB
db = await radio.get_attenuator_level()  # Returns 0, 3, 6, ..., 45
print(f"ATT: {db} dB")

# Set attenuation level (0–45 in 3 dB steps)
await radio.set_attenuator_level(18)  # 18 dB
await radio.set_attenuator_level(0)   # Off

# Simple toggle (compat — maps on=18dB, off=0dB)
await radio.set_attenuator(True)
await radio.set_attenuator(False)

# Boolean check
is_on = await radio.get_attenuator()  # True/False

# --- Preamp ---
# Get preamp level
level = await radio.get_preamp()  # Returns 0, 1, or 2
print(f"Preamp: {level}")

# Set preamp level
await radio.set_preamp(0)  # Off
await radio.set_preamp(1)  # PREAMP 1
await radio.set_preamp(2)  # PREAMP 2

# --- Target SUB receiver (IC-7610) ---
await radio.set_preamp(1, receiver=RECEIVER_SUB)
db = await radio.get_attenuator_level(receiver=RECEIVER_SUB)
```

!!! note "Command29 — Dual-Receiver Radios"
    The IC-7610 has two independent receivers (MAIN and SUB). Commands like
    attenuator and preamp are per-receiver. The library automatically wraps
    them with the `0x29 <receiver>` CI-V prefix that tells the radio which
    receiver to target, without changing the selected VFO.

    Single-receiver radios (IC-7300) don't use Command29 — the receiver
    parameter is ignored.

## CW Keying

Send CW text using the radio's built-in keyer:

```python
# Send CW text (auto-chunked to 30 chars)
await radio.send_cw_text("CQ CQ DE KN4KYD K")

# Stop CW in progress
await radio.stop_cw_text()
```

CW text supports A–Z, 0–9, and standard prosigns. Long messages are automatically split into 30-character chunks.

## Power Control

```python
# Remote power on
await radio.power_control(True)

# Remote power off
await radio.power_control(False)
```

!!! note
    Power-on requires the radio to maintain network connectivity in standby mode. This is model-dependent.

## Raw CI-V Commands

For commands not covered by the high-level API, use `send_civ()` directly:

```python
# Send any CI-V command
response = await radio.send_civ(
    command=0x1A,     # CI-V command byte
    sub=0x05,         # Sub-command (optional)
    data=b"\x01\x23", # Payload (optional)
)

print(f"Command: 0x{response.command:02X}")
print(f"Data: {response.data.hex()}")
```

This gives you access to the full CI-V command set — refer to your radio's CI-V reference manual for available commands.
