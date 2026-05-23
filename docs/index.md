---
title: RigPlane — Ham Radio Control Library and Web UI for Icom, Yaesu, Xiegu
description: RigPlane is a Python library and browser Web UI for controlling ham radios through native providers and external rigctld-backed Hamlib CAT coverage while keeping one capability-driven station model.
---

# rigplane

**Python library for controlling Icom, Yaesu, and other transceivers through native providers and external `rigctld`-backed Hamlib CAT coverage.**

RigPlane keeps the browser UI, audio path, diagnostics, and `rigctld`-compatible endpoint consistent across provider paths.

---

<div class="grid cards" markdown>

- :material-rocket-launch:{ .lg .middle } **Quick Start**

    ---

    Get up and running in under 5 minutes.

    [:octicons-arrow-right-24: Getting Started](guide/quickstart.md)

- :material-console:{ .lg .middle } **CLI Tool**

    ---

    Control your radio from the terminal.

    [:octicons-arrow-right-24: CLI Reference](guide/cli.md)

- :material-radio-tower:{ .lg .middle } **Supported Radios**

    ---

    Pick the radio you operate and follow the matching setup path.

    [:octicons-arrow-right-24: Radios](guide/radios.md) · [:octicons-arrow-right-24: IC-7610 USB](guide/ic7610-usb-setup.md) · [:octicons-arrow-right-24: WSJT-X](guide/wsjtx-setup.md)

- :material-api:{ .lg .middle } **API Reference**

    ---

    Supported public API and full async Python documentation.

    [:octicons-arrow-right-24: Public API Surface](api/public-api-surface.md) · [:octicons-arrow-right-24: IcomRadio](api/radio.md)

- :material-radio-tower:{ .lg .middle } **Protocol Internals**

    ---

    Deep dive into the Icom LAN UDP protocol.

    [:octicons-arrow-right-24: Protocol](internals/protocol.md)

- :material-folder-cog:{ .lg .middle } **Rig Profiles**

    ---

    Add native radio profiles and understand where Hamlib-backed provider work fits.

    [:octicons-arrow-right-24: Adding a New Radio](guide/rig-profiles.md) · [:octicons-arrow-right-24: Hamlib Guide](guide/hamlib-rigctld-provider.md)

- :material-monitor:{ .lg .middle } **Web UI**

    ---

    Spectrum, waterfall, controls, and audio in your browser.

    [:octicons-arrow-right-24: Web UI Guide](guide/web-ui.md)

</div>

---

## Features

- :white_check_mark: **Multi-vendor support** — native Icom CI-V and Yaesu CAT, plus external `rigctld`-backed Hamlib long-tail CAT coverage
- :white_check_mark: **Direct UDP connection** — no intermediate software for native LAN operation
- :white_check_mark: **USB serial backend** — CI-V over USB for IC-7610, IC-7300; USB audio devices
- :white_check_mark: **Full CI-V command set** — frequency, mode/filter, power, meters, PTT, CW keying, VFO, split, ATT/PREAMP
- :white_check_mark: **Yaesu CAT backend** — full working native backend for Yaesu FTX-1 (USB serial)
- :white_check_mark: **Hamlib provider** — broad serial CAT coverage through an external `rigctld` process, assisted discovery, and normalized RigPlane capabilities
- :white_check_mark: **Audio streaming** — RX/TX with jitter buffer and full-duplex support
- :white_check_mark: **Audio FFT Scope** — real-time FFT on USB/LAN audio for radios without hardware spectrum
- :white_check_mark: **Discovery** — find supported LAN radios automatically; assisted serial CAT discovery can suggest and validate Hamlib candidates
- :white_check_mark: **CLI tool** — `rigplane status`, `rigplane freq 14.074m`
- :white_check_mark: **Built-in Web UI** — spectrum, waterfall, controls, meters, audio in browser; LCD layout for non-scope radios
- :white_check_mark: **Async + Sync API** — async by default, blocking wrapper available
- :white_check_mark: **Auto-reconnect** — watchdog + exponential backoff (opt-in)
- :white_check_mark: **Minimal dependencies** — core requires only `pyserial`; no web frameworks or heavy libraries
- :white_check_mark: **Type-annotated** — full `py.typed` support for IDE autocompletion
- :white_check_mark: **4492 tests** — high coverage with golden protocol fixtures, UDP wire tests, and real-radio integration suite

## Supported Radios

| Radio | Protocol | Status |
|-------|----------|--------|
| IC-7610 | CI-V `0x98` | :white_check_mark: Tested (LAN + USB) |
| IC-7300 | CI-V `0x94` | :white_check_mark: Tested (USB) |
| Yaesu FTX-1 | Yaesu CAT | :white_check_mark: Tested (USB) |
| IC-705 | CI-V `0xA4` | :white_check_mark: Validated (LAN/WiFi community-tested) |
| IC-9700 | CI-V `0xA2` | :material-help-circle: Profile — not yet tested |
| Xiegu X6100 | CI-V `0x70` / Hamlib candidate | :material-help-circle: Profile only; assisted discovery candidate |
| Lab599 TX-500 | Kenwood CAT / Hamlib candidate | :material-help-circle: Profile only; assisted discovery candidate |
| IC-7851 | CI-V `0x8E` | :material-help-circle: Should work |
| IC-R8600 | CI-V `0x96` | :material-help-circle: Should work |

See [Supported Radios](guide/radios.md) and the [Hamlib provider guide](guide/hamlib-rigctld-provider.md) for full details. Any Icom radio with LAN/WiFi control should work — the CI-V address is configurable. If you're choosing the commercial desktop app first, start from the matching landing page on [rigplane.com](https://rigplane.com/): [IC-7610](https://rigplane.com/ic-7610/), [IC-7300](https://rigplane.com/ic-7300/), [IC-705](https://rigplane.com/ic-705/), [IC-9700](https://rigplane.com/ic-9700/), or the platform pages for [Mac](https://rigplane.com/ham-radio-software/mac/) and [Linux](https://rigplane.com/ham-radio-software/linux/).

## Indexing policy

This docs site intentionally indexes operator-facing guides, setup pages, and the public API overview. Low-level generated API pages and internal engineering notes are marked `noindex, follow`: crawlers can follow their links, but search results should prefer the pages that help an operator install, configure, and use RigPlane.

## Minimal Example

```python
import asyncio
from rigplane import create_radio, LanBackendConfig

async def main():
    config = LanBackendConfig(host="192.168.1.100", username="user", password="pass")
    async with create_radio(config) as radio:
        freq = await radio.get_frequency()
        print(f"{freq / 1e6:.3f} MHz")

asyncio.run(main())
```

## License

MIT — see [LICENSE](https://github.com/rigplane/rigplane-core/blob/main/LICENSE).

Protocol knowledge derived from the [wfview](https://wfview.org/) project's reverse engineering work. This is an independent clean-room implementation.

!!! note "Trademark Notice"
    Icom™ and the Icom logo are registered trademarks of [Icom Incorporated](https://www.icomjapan.com/). This project is not affiliated with, endorsed by, or sponsored by Icom. Product names are used solely for identification and compatibility purposes (nominative fair use).
