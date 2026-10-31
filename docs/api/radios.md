---
robots: noindex, follow
---

# Radio Models

Presets for known native radio models with CI-V addresses and capabilities.

::: rigplane.runtime.radios.RadioModel

::: rigplane.runtime.radios.get_civ_addr

## Supported Models

| Model | CI-V Address | Receivers | LAN | WiFi |
|-------|-------------|-----------|-----|------|
| IC-7610 | 0x98 | 2 | ✅ | ❌ |
| IC-7300 | 0x94 | 1 | ✅ | ❌ |
| IC-705 | 0xA4 | 1 | ✅ | ✅ |
| IC-9700 | 0xA2 | 2 | ✅ | ❌ |
| IC-R8600 | 0x96 | 1 | ✅ | ❌ |
| IC-7851 | 0x8E | 2 | ✅ | ❌ |
| X6200 | 0xA4 | 1 | ❌ | ✅ |

!!! note "X6200 and IC-705 share address 0xA4"
    `get_civ_addr("X6200")` returns the Xiegu factory CI-V address, but
    discovery still performs model disambiguation because IC-705 uses the same
    default address. For automatic serial discovery, trust the resolved model
    and profile rather than address alone.

## Usage

```python
from rigplane import create_radio, LanBackendConfig, get_civ_addr

# Look up CI-V address by model name
addr = get_civ_addr("IC-705")  # returns 0xA4
x6200_addr = get_civ_addr("X6200")  # also returns 0xA4

# Use with create_radio (recommended)
config = LanBackendConfig(host="192.168.1.100", username="u", password="p", radio_addr=addr)
async with create_radio(config) as radio:
    ...
```

For LAN-only code you can still use `IcomRadio(host, radio_addr=addr)` — see [IcomRadio](radio.md).
