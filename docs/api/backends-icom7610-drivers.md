---
robots: noindex, follow
---

# Icom IC-7610 Drivers

Low-level transport drivers for IC-7610 serial backend.

## Modules

- `serial_civ_link.py` — Serial CI-V transport
- `serial_session.py` — USB serial session management
- `usb_audio.py` — re-export of `rigplane.audio.usb_driver` (USB audio device handling), kept for old imports
- `contracts.py` — Driver contracts and types

## See Also

- [Transport](transport.md)
- [Serial Backend](backends-icom7610.md)
