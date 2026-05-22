---
robots: noindex, follow
---

# Icom IC-7610 Backend

LAN and serial implementations for Icom IC-7610.

## Modules

- `core.py` — Shared `CoreRadio` (commander, state, CI-V routing)
- `lan.py` — `Icom7610LanRadio` (UDP control/audio/CI-V)
- `serial.py` — `Icom7610SerialRadio` (USB serial CI-V + audio devices)

## Key Classes

- `CoreRadio` — Shared logic for both backends
- `Icom7610LanRadio` — LAN backend (implements `Radio`, `AudioCapable`, `ScopeCapable`)
- `Icom7610SerialRadio` — Serial backend (implements `Radio`, `AudioCapable`)

## See Also

- [Backends Overview](backends.md)
- [IC-7610 USB Setup Guide](../guide/ic7610-usb-setup.md)
