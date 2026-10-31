# rigplane

[![PyPI](https://img.shields.io/pypi/v/rigplane.svg)](https://pypi.org/project/rigplane/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/rigplane/rigplane-core/actions/workflows/quick.yml/badge.svg)](https://github.com/rigplane/rigplane-core/actions/workflows/quick.yml)
[![Docs](https://img.shields.io/badge/docs-rigplane.dev-blue.svg)](https://rigplane.dev)
[![Website](https://img.shields.io/badge/web-rigplane.com-blue.svg)](https://rigplane.com/?utm_source=github&utm_medium=readme&utm_campaign=core-launch&utm_content=readme-home)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rigplane/rigplane-core/blob/main/LICENSE)

> **RigPlane 3.0 is released.** A plain `pip install rigplane` installs it.
> Upgrading from 2.x? 3.0 requires the radio model (`--model`); see
> [What's New in 3.0](https://rigplane.dev/3.0/whats-new/) and the
> [migration guide](https://rigplane.dev/3.0/migrate/). Known gaps are listed in
> [3.0 known limitations](https://github.com/rigplane/rigplane-core/blob/main/docs/release-notes/2026-beta-known-limitations.md).
> Tried it on your radio? [Send a 3.0 report](https://github.com/rigplane/rigplane-core/issues/new?template=beta_report.yml).

**rigplane** is an open-source (MIT) Python asyncio library and web UI for
Icom radios (LAN/USB) and the Yaesu FTX-1 (USB), with a rigctld server. It
has native providers for Icom CI-V and Yaesu CAT paths. The rigctld server
lets Hamlib-aware programs such as WSJT-X, fldigi and JS8Call control the
radio.

<p align="center">
  <img src="https://raw.githubusercontent.com/rigplane/rigplane-core/main/docs/screenshots/hero.png" alt="rigplane Web UI — IC-7610 dual-RX desktop with scope and waterfall" width="100%">
</p>

## Quickstart

```bash
pip install rigplane
rigplane --model IC-7610 web   # discovers the radio's IP on the LAN
# open http://localhost:8080
```

Or as a library:

```python
import asyncio
from rigplane import create_radio, LanBackendConfig

async def main():
    async with create_radio(LanBackendConfig(host="192.168.1.100",
                                             username="user",
                                             password="pass",
                                             model="IC-7610")) as radio:
        await radio.set_frequency(14_074_000)
        await radio.set_mode("USB")
        print(await radio.get_s_meter())

asyncio.run(main())
```

Full guides: [getting started](https://rigplane.dev/3.0/guide/quickstart/),
[CLI](https://rigplane.dev/3.0/guide/cli/),
[public API surface](https://rigplane.dev/3.0/api/public-api-surface/).

## Supported radios

| Radio            | `--model` value | Transport            | Notes                                                                                                                                                                |
|------------------|-----------------|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Icom IC-7610** | `IC-7610`       | LAN, USB CI-V        | Dual receiver; see [known limitations](https://github.com/rigplane/rigplane-core/blob/main/docs/release-notes/2026-beta-known-limitations.md#dual-receiver-topology) |
| **Icom IC-7300** | `IC-7300`       | USB CI-V             | Single receiver, USB-only                                                                                                                                            |
| Icom IC-7300MK2  | `IC-7300MK2`    | LAN, USB CI-V        |                                                                                                                                                                      |
| **Yaesu FTX-1**  | `FTX-1`         | USB CAT              | 17 modes, VHF/UHF, C4FM, audio-derived panadapter (no hardware scope)                                                                                                |
| Icom IC-705      | `IC-705`        | LAN (WiFi), USB CI-V | CI-V `0xA4`, QRP 10 W                                                                                                                                                |
| Icom IC-9700     | `IC-9700`       | LAN, USB CI-V        | VHF/UHF/SHF                                                                                                                                                          |
| Icom IC-905      | `IC-905`        | LAN, USB CI-V        | Documented profile only: 5.6 GHz spectrum and wide spans do not work yet, no 10 GHz/DD/ATV                                                                                  |
| Xiegu X6200      | `X6200`         | USB CI-V             | CI-V `0xA4`, 19200 baud, QRP 8 W                                                                                                                                     |
| Xiegu X6100      | `X6100`         | USB CI-V             | Profile only, no backend yet                                                                                                                                         |
| Lab599 TX-500    | `TX-500`        | USB Kenwood CAT      | Profile only, no backend yet; QRP, minimal CAT                                                                                                                       |

Native radio capabilities are declared in `rigs/*.toml`. For long-tail serial
CAT radios, the intended path is Hamlib-backed control underneath RigPlane's
capability model, with structured discovery candidates and safe read-only
probing where possible. See [adding a new radio](https://rigplane.dev/guide/rig-profiles/).

## Why 1.0

- **Public API stability commitment.** The Tier 1 surface — the `Radio`
  protocol, the capability protocols (`AudioCapable`, `ScopeCapable`,
  `MetersCapable`, `LevelsCapable`, `StatePollable`, `RigctldRoutable`,
  `UsbAudioCapable`, …), `create_radio` / `BackendConfig`, and the
  `local-extensions/` host API — is now under SemVer. See
  [`docs/api/public-api-surface.md`](https://github.com/rigplane/rigplane-core/blob/main/docs/api/public-api-surface.md).
- **Capability-driven multi-radio architecture.** Implement the relevant
  Capability Protocols and your backend slots into the runtime, Web UI,
  and rigctld layers without any of those layers knowing about your
  radio. See [`ARCHITECTURE.md`](https://github.com/rigplane/rigplane-core/blob/main/ARCHITECTURE.md).
- **5,600+ unit tests.** `import-linter` enforces 11-layer package
  boundaries; mypy is clean across the public surface; ruff lints in CI.
- **Verified against the digital-mode ecosystem.** WSJT-X, fldigi, and
  JS8Call golden-replay tests pass over the rigctld bridge with full
  per-VFO routing.

## Web UI

`rigplane web` boots a self-contained HTTP + WebSocket server. The frontend
is a Svelte 5 single-page app served from the same process; no native
shell, no Electron, no Tauri — just a browser tab.

Four user-facing skins resolve from `frontend/src/skins/registry.ts`:

- **Desktop v2** — default skin: dual-RX VFO, scope + waterfall, meters
  dock, control panels.
- **LCD Scope** — alternative dual-RX layout with vintage-LCD typography
  and the same scope + meters dock.
- **LCD Cockpit** — single-RX or dual-cockpit variants with retro LCD
  styling, telemetry strip, AmberScope (also resolves under the legacy
  `amber-lcd` alias).
- **Mobile** — chip-scroll IA, persistent guarded PTT FAB, container-query
  responsive layout.

<p align="center">
  <img src="https://raw.githubusercontent.com/rigplane/rigplane-core/main/docs/screenshots/lcd-scope.png" alt="LCD Scope skin — dual-RX with vintage-LCD typography and scope panel" width="49%">
  <img src="https://raw.githubusercontent.com/rigplane/rigplane-core/main/docs/screenshots/amber-lcd.png" alt="LCD Cockpit (amber) skin with vintage-LCD typography and AmberScope" width="49%">
</p>

<!-- SCREENSHOT: Mobile skin — ESSENTIALS panel + PTT FAB (TBD, pending device) -->

## Architecture

`src/rigplane/` is organised into 11 layered Python packages
(`core/`, `commands/`, `profiles/`, `audio/`, `scope/`, `dsp/`,
`runtime/`, `backends/`, `web/`, `rigctld/`, `cli/`) with explicit
boundaries enforced by `import-linter`. Higher layers depend on lower
ones; siblings are independent. See [`ARCHITECTURE.md`](https://github.com/rigplane/rigplane-core/blob/main/ARCHITECTURE.md)
for the layout and per-layer charters in `src/rigplane/<layer>/LAYER.md`.

Extensibility is centred on **Capability Protocols** in
`rigplane.radio_protocol`. A new backend implements the protocols it
supports; consumers (Web UI, rigctld, CLI, third-party scripts) feature-detect
via `isinstance(radio, ScopeCapable)` and never branch on backend identity.
The `Radio` protocol plus the capability suite is the **stable contract**
between the open core and downstream consumers.

The frontend extension surface lives at
[`frontend/src/lib/local-extensions/`](https://github.com/rigplane/rigplane-core/tree/main/frontend/src/lib/local-extensions) —
a Tier 1 contract for embedders shipping panels, dock items, or keyboard
scopes into the open-core shell.

## Documentation

- [Quickstart](https://rigplane.dev/guide/quickstart/)
- [CLI reference](https://rigplane.dev/guide/cli/)
- [Public API surface (Tier 1 stability)](https://github.com/rigplane/rigplane-core/blob/main/docs/api/public-api-surface.md)
- [Adding a new radio (TOML profiles)](https://rigplane.dev/guide/rig-profiles/)
- [Architecture overview](https://github.com/rigplane/rigplane-core/blob/main/ARCHITECTURE.md)
- [Open-core policy](https://github.com/rigplane/rigplane-core/blob/main/docs/architecture/open-core-policy.md)
- [Diagnostic reports guide](https://github.com/rigplane/rigplane-core/blob/main/docs/guide/diagnostic-reports.md) — how to send a one-click bug report when you hit an issue.
- [Protocol internals](https://rigplane.dev/internals/protocol/)
- [Security](https://github.com/rigplane/rigplane-core/blob/main/docs/SECURITY.md)

## License

MIT — see [LICENSE](https://github.com/rigplane/rigplane-core/blob/main/LICENSE). Protocol knowledge derived from the
[wfview](https://wfview.org/) project's reverse-engineering work; this is
an independent clean-room implementation, not a derivative of wfview's
GPLv3 code. Icom™ and IC-* product names are registered trademarks of
[Icom Incorporated](https://www.icomjapan.com/), used here for nominative
fair-use compatibility identification only — this project is not affiliated
with, endorsed by, or sponsored by Icom.

rigplane is the **open-core** half of a planned product split. RigPlane Pro
(`rigplane-pro`), the commercial desktop app, is in public beta and can be
downloaded from [rigplane.com/downloads](https://rigplane.com/downloads/?utm_source=github&utm_medium=readme&utm_campaign=core-launch&utm_content=readme-download).
It integrates with this library through the public `Radio` protocol
and the `local-extensions/` host API. Open-core constraints — no
telemetry, headless mode is sacred, no hollowing out — are codified in
[`docs/architecture/open-core-policy.md`](https://github.com/rigplane/rigplane-core/blob/main/docs/architecture/open-core-policy.md).

## Status

KN4KYD's personal project. Each supported radio is described by its
`rigs/*.toml` profile and uses the same Capability Protocols.
Issues, profile contributions, and field reports are welcome.

73 de KN4KYD
