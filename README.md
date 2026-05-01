# icom-lan

[![PyPI](https://img.shields.io/pypi/v/icom-lan.svg)](https://pypi.org/project/icom-lan/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/morozsm/icom-lan/actions/workflows/test.yml/badge.svg)](https://github.com/morozsm/icom-lan/actions/workflows/test.yml)
[![Docs](https://img.shields.io/badge/docs-morozsm.github.io%2Ficom--lan-blue.svg)](https://morozsm.github.io/icom-lan)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**icom-lan** is a Python asyncio library and Web UI for controlling Icom
transceivers over LAN (UDP) or USB serial — and now Yaesu CAT radios over
USB. Direct connection to your radio: no wfview, no hamlib daemon, no RS-BA1.
A capability-driven runtime renders the same Web UI and `rigctld`-compatible
network bridge across IC-7610, IC-7300, FTX-1, and any future backend that
honours the public `Radio` protocol. Tested in production against WSJT-X,
fldigi, and JS8Call.

<!-- SCREENSHOT: Web UI hero — IC-7610 dual-RX desktop layout with scope + waterfall -->

## Quickstart

```bash
pip install icom-lan
icom-lan web                # auto-discovers a radio on the LAN
# open http://localhost:8080
```

Or as a library:

```python
import asyncio
from icom_lan import create_radio, LanBackendConfig

async def main():
    async with create_radio(LanBackendConfig(host="192.168.1.100",
                                             username="user",
                                             password="pass")) as radio:
        await radio.set_frequency(14_074_000)
        await radio.set_mode("USB")
        print(await radio.get_s_meter())

asyncio.run(main())
```

Full guides: [getting started](https://morozsm.github.io/icom-lan/guide/quickstart/),
[CLI](https://morozsm.github.io/icom-lan/guide/cli/),
[public API surface](https://morozsm.github.io/icom-lan/api/public-api-surface/).

## Supported radios

| Radio              | Transport          | Status              | Notes                                  |
|--------------------|--------------------|---------------------|----------------------------------------|
| **Icom IC-7610**   | LAN, USB CI-V      | Stable, primary     | Dual receiver MAIN/SUB, full Capability surface |
| **Icom IC-7300**   | USB CI-V           | Stable              | Single receiver, USB-only              |
| **Yaesu FTX-1**    | USB CAT            | Stable              | 17 modes, VHF/UHF, C4FM, audio FFT scope |
| Icom IC-705        | LAN (WiFi)         | Community-validated | CI-V `0xA4`                            |
| Icom IC-9700       | LAN, USB CI-V      | Profile only        | VHF/UHF/SHF                            |
| Icom IC-7851       | LAN                | Profile only        | CI-V `0x8E`                            |
| Icom IC-R8600      | LAN                | Profile only        | RX only                                |
| Xiegu X6100        | USB CI-V           | Profile only        | IC-705 compatible, QRP                 |
| Lab599 TX-500      | USB Kenwood CAT    | Profile only        | QRP, minimal CAT                       |

Radio capabilities are declared in `rigs/*.toml` — adding a new model is
typically a profile change, not Python code. Three protocol families are
supported: CI-V (Icom binary), Kenwood CAT (text), Yaesu CAT (text). See
[adding a new radio](https://morozsm.github.io/icom-lan/guide/rig-profiles/).

## Why 1.0

- **Public API stability commitment.** The Tier 1 surface — the `Radio`
  protocol, the capability protocols (`AudioCapable`, `ScopeCapable`,
  `MetersCapable`, `LevelsCapable`, `StatePollable`, `RigctldRoutable`,
  `UsbAudioCapable`, …), `create_radio` / `BackendConfig`, and the
  `local-extensions/` host API — is now under SemVer. See
  [`docs/api/public-api-surface.md`](docs/api/public-api-surface.md).
- **Capability-driven multi-radio architecture.** Implement the relevant
  Capability Protocols and your backend slots into the runtime, Web UI,
  and rigctld layers without any of those layers knowing about your
  radio. See [`ARCHITECTURE.md`](ARCHITECTURE.md).
- **5,600+ unit tests.** `import-linter` enforces 11-layer package
  boundaries; mypy is clean across the public surface; ruff lints in CI.
- **Verified against the digital-mode ecosystem.** WSJT-X, fldigi, and
  JS8Call golden-replay tests pass over the rigctld bridge with full
  per-VFO routing.

## Web UI

`icom-lan web` boots a self-contained server with three first-class skins:

- **Desktop v2** — primary skin, full dual-RX VFO, scope + waterfall,
  meters dock, control panels.
- **Amber LCD** — single-RX or dual-cockpit variants with vintage-LCD
  typography, telemetry strip, AmberScope.
- **Mobile** — chip-scroll IA, persistent guarded PTT FAB, container-query
  responsive layout.

<!-- SCREENSHOT: Amber LCD skin — single-RX cockpit with AmberScope -->

<!-- SCREENSHOT: Mobile skin — ESSENTIALS panel + PTT FAB -->

The frontend renders identically in browsers, macOS WKWebView, Windows
WebView2, and Linux WebKitGTK — Tauri-friendly by design.

## Architecture

`src/icom_lan/` is organised into 11 layered Python packages
(`core/`, `commands/`, `profiles/`, `audio/`, `scope/`, `dsp/`,
`runtime/`, `backends/`, `web/`, `rigctld/`, `cli/`) with explicit
boundaries enforced by `import-linter`. Higher layers depend on lower
ones; siblings are independent. See [`ARCHITECTURE.md`](ARCHITECTURE.md)
for the layout and per-layer charters in `src/icom_lan/<layer>/LAYER.md`.

Extensibility is centred on **Capability Protocols** in
`icom_lan.radio_protocol`. A new backend implements the protocols it
supports; consumers (Web UI, rigctld, CLI, third-party scripts) feature-detect
via `isinstance(radio, ScopeCapable)` and never branch on backend identity.
The `Radio` protocol plus the capability suite is the **stable contract**
between the open core and downstream consumers.

The frontend extension surface lives at
[`frontend/src/lib/local-extensions/`](frontend/src/lib/local-extensions/) —
a Tier 1 contract for embedders shipping panels, dock items, or keyboard
scopes into the open-core shell.

## Documentation

- [Quickstart](https://morozsm.github.io/icom-lan/guide/quickstart/)
- [CLI reference](https://morozsm.github.io/icom-lan/guide/cli/)
- [Public API surface (Tier 1 stability)](docs/api/public-api-surface.md)
- [Adding a new radio (TOML profiles)](https://morozsm.github.io/icom-lan/guide/rig-profiles/)
- [Architecture overview](ARCHITECTURE.md)
- [Open-core policy](docs/architecture/open-core-policy.md)
- [Protocol internals](https://morozsm.github.io/icom-lan/internals/protocol/)
- [Security](docs/SECURITY.md)

## License

MIT — see [LICENSE](LICENSE). Protocol knowledge based on independent
clean-room study of the Icom proprietary LAN protocol; not affiliated with
or endorsed by Icom Incorporated.

icom-lan is the **open-core** half of a planned product split. A
proprietary commercial layer (`icom-lan-pro`) is under development and
will integrate with this library through the public `Radio` protocol and
the `local-extensions/` host API. Open-core constraints — no telemetry,
headless mode is sacred, no hollowing out — are codified in
[`docs/architecture/open-core-policy.md`](docs/architecture/open-core-policy.md).

## Status

KN4KYD's personal project. Production-grade for IC-7610 (the author's
daily driver) and the Yaesu FTX-1; secondary radios are validated against
the same Capability Protocols but receive less hardware-in-the-loop time.
Issues, profile contributions, and field reports are welcome.

73 de KN4KYD
