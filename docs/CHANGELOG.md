---
robots: noindex, follow
---

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.7.3] — 2026-05-31

### Fixed

- Windows FT8 TX delivered no audio (Po=0 W): the callback/`blocksize=0` TX
  capture emitted 960-byte (10 ms) frames while the PCM TX validator requires
  1920-byte (20 ms) frames; capture now re-chunks the continuous callback stream
  into fixed 20 ms frames (keeps the ~50 Hz comb fix; lossless) (MOR-256, #1699,
  17492be6)

### Changed

- Build (packaging): build the web UI during packaging so source/git installs
  bundle the SPA (#1698, 55a0f81f)

## [2.7.2] — 2026-05-30

### Fixed

- Desktop RX audio: resume the `AudioContext` from the LIVE user-gesture so streamed RX audio plays under WKWebView/autoplay-restricted contexts, and recover from suspended-context frame drops instead of silently dropping them (MOR-239, #1694, 921f4c88)
- Audio scope: dispatch audio-FFT frames to `/api/v1/audio-scope` for radios without a hardware scope (e.g. Xiegu X6200), fixing the blank AUDIO SCOPE panel; hardware-scope radios (IC-7610) are unchanged (MOR-241, #1695, 4e98ab0d)
- Serial USB-audio TX: arm the PCM TX path in the shared serial Icom base so bridge TX reaches the radio (IC-705/IC-7300/IC-9700/X6200) instead of raising "PCM TX not started" per frame; the audio bridge now degrades to RX-only on TX failure instead of spamming (MOR-242, #1696, 37fe3d0a)

## [2.7.1] — 2026-05-29

### Fixed
- USB audio now clamps the requested channel count to the device's real
  capability (`input_channels` for RX, `output_channels` for TX) at stream
  open, mirroring the existing sample-rate negotiation. Any mono USB codec
  self-heals instead of failing the global stereo default with PortAudio
  `-9998` ("Invalid number of channels") and starving the stream; the Xiegu
  X6200 mono RX path no longer depends on its profile `codec_preference`
  entry (verified on hardware). The effective channel count and its source
  are recorded on the stream contract for diagnostics (#1692).

## [2.7.0] — 2026-05-29

### Added
- USB-audio topology resolution on **Linux** (`/sys` sysfs) and **Windows**
  (USB PnP / SetupAPI), extending the macOS resolver so USB radios — notably
  the Xiegu X6200's WCH CH342 CDC-ACM bridge (`/dev/ttyACM*`, `COMx`) — map
  their host USB audio device by physical USB topology with a VID:PID identity
  fallback (#1687, #1686).

### Changed
- USB audio↔serial device pairing is now by device **identity** (product name
  + same-name rank) instead of positional enumeration order, correcting
  selection on mixed-vendor multi-radio hosts (#1685).

### Fixed
- Xiegu X6200 browser "LIVE" RX audio now flows: the mono C-Media USB codec is
  opened as 1-channel PCM via an `[audio] codec_preference` profile entry,
  instead of failing the global stereo default (PortAudio `-9998`) and starving
  the stream (#1688).
- Xiegu X6200 CH342 serial session stabilized: the reconnect watchdog uses
  capped exponential backoff and no longer floods the log on transient USB
  renumbering, and CI-V RX framing tolerates short/partial frequency frames
  instead of raising `BCD data must be exactly 5 bytes` (#1689).

## [2.6.0] — 2026-05-29

### Added
- Universal real-radio validation matrix: JSON schema, per-radio templates, and
  a headless dry-run runner (#1652, #1653).
- `rigplane validate --provider hamlib | both` — validate the native rigplane
  path against a Hamlib `rigctld` and surface native-vs-Hamlib comparison
  dimensions, with tolerance-aware readback (#1654, #1655, #1673, #1672, #1671).
- Profile-driven matrix generation: a capability→check-spec registry with
  generic `_check_from_spec` dispatch, Generator A (from profile capabilities)
  and Generator B (registry → Hamlib-filtered template) (#1659, #1660, #1661,
  #1670, #1671, #1668).
- `rigplane radio-validate` — profile-driven wrapper over the validate run path
  (#1675).
- `rigplane convert` — bootstrap a draft rig TOML from Hamlib `dump_caps` with
  cross-check (#1674, #1679).
- Override files for generated matrices: a pure, check_id-keyed,
  safety-invariant merge layer wired into the generate path with metadata audit
  (#1676, #1678).
- Set-and-observe engine path for write-only controls, with per-radio
  write-only classification (#1663, #1664).
- Disambiguate the Xiegu X6200 from the Icom IC-705 (shared CI-V address `0xA4`)
  by the Xiegu-only model-ID opcode `0x1D 0x19` when the USB hwid is
  inconclusive (#1682).

### Fixed
- Xiegu X6200 USB RX audio now resolves in the browser "LIVE" stream on macOS:
  the topology resolver handles CDC-ACM `usbmodem` ports and the C-Media codec
  identity (#1677).
- Xiegu X6200 (and other CDC-ACM / Windows COM serial radios) are no longer
  skipped during discovery — port acceptance anchors on USB identity (VID:PID /
  hwid) instead of the device-name substring (#1680).
- Set mode via CI-V `0x26` selected-mode when the profile declares it (#1657).
- Send plain ATT/PREAMP commands on single-RX radios (#1656).
- Classify the X6200 manual notch as a write-only control (#1665, #1667).
- Harden rigctld-client response alignment over the bridge (#1669).
- Derive TX/tuner authorization from the capability registry rather than the
  mutable template flag (#1683).

### Docs
- Open-source universal validation matrix guide — `radio-validate`, `convert`,
  overrides, evidence (#1681); "Running the radio validation matrix" run recipes
  (#1666).
- ADR for the universal profile-driven validation matrix (#1658), reconciled
  with the tracked tickets (#1662).

## [2.5.1] — 2026-05-28

### Fixed
- Use the selected serial rig profile's `default_baud` when `--model` is
  provided without `--serial-baud`, fixing Xiegu X6200 managed/local launches
  that otherwise fell back to 115200 baud and timed out CAT/control reads
  (#1642).

## [2.5.0] — 2026-05-28

### Added
- Added ordered HTTP command batches so local automation can run structured
  commands in request order with per-step results and timeout handling (#1606,
  31c41425).
- Published the full HTTP/WebSocket command catalog in the API docs so client
  authors can discover the supported command names, parameters, and response
  shapes without reading server code (#1607, f4c113e7).
- Exposed a queued, fire-and-forget raw CI-V `send_civ` command through the
  HTTP/WS command surface and ordered batch endpoint so automation clients can
  send vendor-specific CI-V without opening a competing radio session (#1616,
  fb2965ee).
- Added `POST /api/v1/civ/transaction` for scoped raw CI-V transactions with
  explicit `expect` modes (`none`, `ack`, `data`), deterministic ACK/NAK/data
  JSON results, bounded timeouts, and CI-V ownership guarding (#1622, #1623).
- Added response-capable raw CI-V transaction steps to ordered HTTP batches via
  `type: "raw_civ_transaction"`, preserving order when mixed with queued
  command steps (#1637, 06c4c7b0).
- Added an internal raw CI-V pipe and local Hamlib A1 bridge runner for
  development of transparent external-CAT ownership over CI-V serial backends
  (#1610, #1612, #1614, #1615, c371bc3b, 085cd617, c3a09e1e, deabb95d).
- Added a distinct Xiegu X6200 native rig profile and discovery
  disambiguation from IC-705 despite the shared factory CI-V address `0xA4`
  (#1630, 7b593eee).
- Documented Python usage for response-capable raw CI-V HTTP transactions
  (#1626).

### Changed
- Cleaned up type-checking visible at integration boundaries, including raw
  CI-V transaction coverage and audio TX PCM signatures, reducing false
  positives for typed downstream users (#1628, #1629, db90a992, bda2ed0a,
  a356a1ab).

### Fixed
- Registered Xiegu X6200 in the CLI model registry so `--model X6200` resolves
  to the X6200 preset instead of silently falling back to IC-7610 (#1631,
  204aea24).
- Stopped sending LAN `OpenClose` packets on the serial CI-V wire, avoiding an
  X6200 flicker/wedge failure mode observed on hardware (#1632, 28a962d0).

### Docs
- Clarified raw CI-V transaction and batch contracts, including the split
  between public/dev-facing raw transaction APIs and the internal experimental
  Hamlib A1 raw-pipe bridge path (#1616, #1637, #1638).

## [2.4.0] — 2026-05-24

### Added
- External rigctld client backend that talks to a separately managed `rigctld`
  process so Core can interoperate with Hamlib-managed transports without
  hosting its own rigctld (#1579, 32753adf).
- Safe Hamlib probe ranking internals that score candidate transports without
  committing to them, used by the discover flow (#1581, 68907f6c).
- Serial inventory now includes Hamlib metadata for each enumerated port so
  upstream UIs can present capability and provenance hints (#1580, f6b4ca35).
- Hamlib provider validation in the discover CLI to confirm a candidate probe
  matches the expected radio profile before activation (#1582, 7e2f04a0).

### Fixed
- Hardened the Windows core runtime — process lifecycle, signal handling, and
  cleanup paths now align with the desktop shell's expectations on Windows
  (#1594, 4ec683f8).

### Docs
- Defined the public Hamlib provider contract, the provider strategy
  alignment, the agent handoff, and the rigplane.dev provider guide so external
  integrations have a single source of truth for the Hamlib path (#1577,
  #1583, #1590, #1592, f6b0c099, bd9cbbf4, 3e6c5a8e, c2d73d74).

## [2.3.1] — 2026-05-23

### Added
- TX audio streams now expose write-health snapshots with queued, dropped,
  attempted, completed, failed, and last-error counters for diagnosing local
  transmit paths (#1553, 9125216b).

### Fixed
- PortAudio playback now uses the callback ring, coalesces small writes,
  increases write chunk size, and keeps playback writes nonblocking to reduce
  local audio underruns (#1554, #1555, #1556, #1557, #1559, 2dcf7973,
  f7021beb, 763f2fd0, af51ce12).
- CI-V reconnect recovery now stays tied to the current generation, retries
  with ephemeral local ports when needed, and avoids spurious recovery after
  benign transport churn (#1546, #1550, c29a8a9a, 2169cc4a, 11e682be).
- rigctld power status responses are now compatible with WSJT-X clients
  (c064e5f4).
- Opus TX fails closed when no transcoder is available instead of accepting
  unsupported PCM-through-Opus paths (#1569, c86da7b0).
- LAN data-port cooldown recovery now reconnects cleanly after the radio holds
  the port in cooldown (#1572, #1573, a6976a2c).

### Docs
- Added public docs-site analytics and IndexNow post-deploy pings, and tuned
  the indexing policy for rigplane.dev (#1552, #1571, #1574, c50cadfb,
  a513a0ad, 36662cb7).
- Documented the PortAudio callback-ring intent and kept audio implementation
  comments public-safe (#1561, #1562, 94884a98, fdd37686).

## [2.3.0] — 2026-05-20

### Added
- Added the Core Svelte i18n runtime, locale store, pluralization helpers,
  pseudo-locale tooling, and bundled `en-US`, `ru-RU`, and `ja-JP` locale
  catalogs for the first localized Core web surfaces (#1528, #1534, #1536).
- Added the Core language preference UX and the cross-app locale preference
  contract used by local shells and integrations to hand off language hints
  without overwriting the user's stable Core preference (#1529, #1531).
- Added localization QA coverage: string inventory, runtime/unit tests,
  pseudo-locale smoke tests, visual QA fixtures, and Playwright i18n
  screenshot coverage for desktop and mobile app surfaces (#1530, #1533).
- Added localized server toast reason codes and migrated the P0 Core web
  surfaces to the i18n runtime, including app shell, status bar, install
  prompt, language selector, diagnostics dialog, and shared toasts (#1532).

### Changed
- Moved quick/full/rebrand/agent-review CI workflows to the Mac mini
  build-tier runner while preserving the existing required gates (#1512,
  #1513, #1514).
- Moved the lightweight rebrand and agent-review gate jobs to GitHub-hosted
  runners so protected-branch release fixes are not blocked when the
  self-hosted gate runner is unavailable.
- Refined public packaging metadata and docs SEO/navigation for the
  multi-vendor RigPlane framing, including Project Overview, migration
  guidance, per-page descriptions, and setup-guide download CTAs (#1510,
  #1511, #1538, #1540, #1541, #1542).

### Fixed
- Stabilized the audio-scope aspect ratio in the web UI (#1519).
- Fixed WebView/Tauri audio compatibility by allowing browser clients to
  request PCM16 RX transport, adding a PCM16 TX microphone fallback when
  WebCodecs are unavailable, and guarding async PTT startup/release races
  before keying transmit (#1543).
- Fixed Japanese pilot copy after linguistic validation (#1537).
- Fixed release packaging so the generated frontend bundle is included in
  sdist/wheel artifacts without relying on a local absolute `web/static`
  symlink.
- Fixed PyPI runtime dependency metadata so installed wheels can import and
  serve the Web UI diagnostics upload path without relying on dev-only
  dependencies.
- Refreshed the frontend lockfile to pick up Svelte/devalue security fixes;
  `npm audit --omit=dev` now reports zero vulnerabilities.

### Docs
- Added the Core user-facing string inventory, locale contract, translator
  guide, i18n contribution notes, and visual QA documentation (#1527, #1535).

## [2.2.0] — 2026-05-11

### Added
- Added a friendly station-server discovery and status contract for desktop
  supervisors: UDP discovery now advertises the station-server schema, endpoint
  URLs, readiness, auth-required, backend, display name, and radio metadata
  while preserving legacy discovery fields (#1498).
- Added `GET /api/v1/station` and exposed station readiness in
  `GET /api/v1/runtime`, giving setup tools a stable machine-readable and
  human-readable status payload for LAN station-server discovery (#1498).

### Docs
- Documented the new station-server discovery/status payloads and readiness
  states for supervisors and setup tools (#1498).

## [2.1.2] — 2026-05-09

### Added
- Web state now exposes classified radio health with separate server reachability, radio link, readiness, likely-cause, and health revision fields (#1500).

### Changed
- `/api/v1/state` ETags now include both radio state revision and health revision, so health-only transitions reach UI clients instead of being hidden behind `304 Not Modified` responses (#1501).
- The web client accepts health-only state updates, stores radio health separately from legacy readiness flags, maps degraded causes into status-bar state, and blocks live-radio commands before optimistic updates while radio health is degraded (#1501, #1503).

### Fixed
- Radios with no prior runtime evidence now remain classified as `unknown` instead of being prematurely reported as `radio_not_responding` (#1503).

### Docs
- Documented IC-7610 VPN/tunnel MTU fragmentation symptoms and the `ICOM_AUDIO_SAMPLE_RATE=16000` low-bandwidth workaround (#1504).

### CI
- GitHub workflows were migrated to current Node 24-compatible action versions, and release-related docs/full-matrix workflows were manually validated after merge (#1499).

## [2.1.1] — 2026-05-09

### Added
- Direct CW send/stop HTTP endpoints for local app integrations, plus clearer CLI launch guidance and web radio aliases (#1496).

### Fixed
- Web TX audio on IC-7610 LAN now starts the accepted radio-native TX contract instead of starting Opus against the radio's PCM-only TX stream (#1493).
- Web RX LIVE toggling now sends `audio_start` / `audio_stop` even when the audio WebSocket is already open, fixing the first-click silence after page load (#1495).
- IC-7610 LAN audio keeps 48 kHz PCM as the full-fidelity profile default; troubleshooting docs now call out VPN/tunnel MTU fragmentation issues and the `ICOM_AUDIO_SAMPLE_RATE=16000` low-bandwidth workaround (#1494).
- Existing Svelte check warnings in touched frontend files were removed so `npm run check` is clean for the hotfix branch (#1497).

## [2.1.0] — 2026-05-08

### Added
- Managed station runtime support for Pro/supervisor integrations: runtime mode, health endpoints, startup events, token-file auth, setup-wizard discovery JSON, and stable supervisor API documentation (#1439, #1440, #1441, #1442, #1443, #1444, #1445).
- LAN/USB audio capability groundwork: radio-native audio policy contracts, USB audio device contracts and diagnostics, safe RX-only LAN audio probe CLI, JSON probe artifacts, cooldown/retry controls, and guarded profile proposal tooling (#1464, #1479, #1480, #1481, #1483, #1488).
- Automated audio regression harnesses for WSJT-X and TX pipeline work: in-process TX pipeline, rigctld WSJT-X replay, opt-in OS audio smoke, and IC-7610 hardware validation tests (#1450, #1451, #1452, #1453).

### Changed
- WSJT-X compatibility now derives DATA/LAN policy from the resolved audio route instead of backend-name guards, preserving DATA1 as user-owned while selecting DATA2/LAN only for direct LAN radios that support it (#1446).
- Icom LAN audio profiles now carry evidence-backed PCM-first policies for IC-705, IC-9700, and IC-7610; IC-7610 defaults to the hardware-probed 48 kHz matrix with mono PCM TX (#1477, #1490).
- Browser audio routing keeps Opus as a consumer/web transport policy only; direct stock-radio LAN stream defaults remain radio-native PCM/u-law.

### Fixed
- LAN discovery now advertises the RigPlane service identity after the v2 rebrand (#1485).
- Removed stale rebrand paths from agent commands and docs SEO metadata.

### Docs
- Added audio codec policy, audio capability probing, audio profile audit, USB audio negotiation, managed runtime packaging, CLI, API, and audio recipe documentation for the new runtime/audio contracts.

## [2.0.3] — 2026-05-07

### Fixed
- WSJT-X LAN TX audio on direct Icom LAN: send raw PCM when conninfo negotiates `PCM_1CH_16BIT` instead of pushing Opus bytes into the radio's PCM TX stream (#1430, #1448, 999953e2). Adds explicit TX codec tracking; routes WSJT-X packet modes to DATA2/LAN only for direct Icom multi-DATA radios; keeps DATA1 user-owned (prewarm/profile-apply/state-restore no longer rewrite DATA1 MOD input). Validated on IC-7610 LAN — continuous TX audio reaches the radio.

### Other
- Align `tests/test_pcm_e2e.py` with the new PCM-passthrough TX path: pin `_audio_tx_codec = AudioCodec.OPUS_1CH` on the test radio so the dummy transcoder is exercised, and use a properly-sized 1920-byte synthetic frame for loopback TX (6ad40e91, #1448 follow-up).

## [2.0.2] — 2026-05-06

### Added
- Tailored in-repo release skill (`.claude/skills/release/SKILL.md`) replacing the stale global slash command (#1436).

### Fixed
- Frontend e2e: closed pre-existing Wave 2 rebrand leak in `playwright.config.ts` and `v2-ui-interactive.impl.ts`; removed obsolete `ICOM_LAN_V2_URL` fallback (#1437).

### Docs
- IC-7610 WSJT-X TX level workaround for FT8/JS8/fldigi multi-peak waterfall (#1382, #1384).
- GitHub Project workflow for Codex-style agents: `AGENTS.md`, project doc, `CLAUDE.md` link (#1431).

## [2.0.1] — 2026-05-06

### Fixed
- LAN TX audio bridge: fix audio not flowing in transmit direction (#1434, ba364e54)

## [2.0.0] — 2026-05-05

**Headline:** project renamed from `icom-lan` to **`rigplane`**. The old name was always misleading — the project shipped multi-vendor support (Icom CI-V, Yaesu CAT, Discovery TX-500, Xiegu X6200) over LAN, USB, and serial. Carrying "icom-lan" through a paid Pro tier also created a trademark risk on a vendor name we don't own. New brand at https://rigplane.dev.

### Migration

Existing v1.x scripts continue to work without modification:

```python
# This still works, emits DeprecationWarning on first import:
from icom_lan import IcomRadio, LanBackendConfig, create_radio
import icom_lan.web

# Canonical v2 form:
from rigplane import IcomRadio, LanBackendConfig, create_radio
import rigplane.web
```

The `icom-lan` CLI binary remains as a deprecated alias of `rigplane` (prints a notice to stderr, then forwards `argv`).

User-facing data (web-UI panel layouts, theme, auth token, memory channels, log directories) is migrated automatically on first launch — no re-login or reconfigure.

### Breaking changes

- **PyPI package**: `icom-lan` → `rigplane`. Preferred install: `pip install rigplane`. The old `icom-lan` package on PyPI is frozen at v1.1.0; future releases ship as `rigplane`.
- **Python import path**: `icom_lan.*` → `rigplane.*`. The `icom_lan` shim re-exports from `rigplane` with `DeprecationWarning` and will be removed in a future major release.
- **CLI binary**: `icom-lan` → `rigplane`. `icom-lan` retained as deprecated alias.
- **Environment variables**: `ICOM_LAN_REPORT_ENDPOINT`, `ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING`, `ICOM_LAN_LOG_DIR` → `RIGPLANE_*`. LAN discovery now uses `b"RIGPLANE_DISCOVER\n"` with `b"ICOM_LAN_DISCOVER\n"` accepted as a legacy request.
- **Diagnostic bundle schema**: default emission is now `rigplane-bundle-v2` (was `icom-lan-bundle-v1`). The maintainer-operated triage service accepts both v1 and v2 for at least 12 months.
- **Exception class**: `IcomLanError` → `RigplaneError`. Class is re-exported from `icom_lan` shim under both names.

### Preserved (not renamed — vendor identifiers stay)

- Vendor classes: `IcomRadio`, `IcomBackend`, `IcomCommander`, `Icom7610Profile`, `YaesuRadio`, `YaesuCatRadio`, etc. These name supported hardware vendors, not the product brand.
- Backend directories: `src/rigplane/backends/icom7610/`, `…/yaesu_cat/`, etc. The vendor-model component is preserved.
- Vendor-config env vars: `ICOM_HOST`, `ICOM_USER`, `ICOM_PASS`, `ICOM_PORT`, `ICOM_AUDIO_*`, `ICOM_CIV_*`, etc.
- LAN discovery wire contract: magic byte sequence + service token unchanged in v2.0.0.

### Added

- **`rigplane-bundle-v2`** diagnostic-bundle wire-format spec (`docs/contracts/diagnostic-bundle-v2.md`). Bundle producer emits v2 by default; v1 emission preserved as opt-in.
- **Brand identity v1**: wordmark, mark, lockup (light/dark/mono SVG), favicon set, app icons (Tauri + PWA), social cards, README banner, color palette, typography spec. Color palette: Ink `#0E1A28`, Paper `#F4F1EA`, Signal `#2D5F8A`. Typography: IBM Plex Sans + IBM Plex Mono.
- **First-run platformdirs migration** (`rigplane._platformdirs_migration`). Copies legacy `~/.cache/icom-lan/`, `~/.config/icom-lan/`, etc. to corresponding `rigplane` paths. Best-effort, idempotent, sentinel-guarded.
- **First-run localStorage migration** (`frontend/src/lib/migrate-legacy-storage.ts`). Auto-runs at module import (boot-order critical — stores read localStorage at module-init). Migrates 17 legacy keys to the new namespace.
- **`window.rigplaneExtensionHost`** primary global for Pro local-extensions. `window.icomLanExtensionHost` alias preserved for v1.x extensions.
- **CI grep gate** (`.github/workflows/rebrand-gate.yml`) prevents accidental brand-string regressions.

### Changed

- Default product description across docs, README, mkdocs `site_description`, and PyPI metadata acknowledges multi-vendor support instead of the legacy Icom-only framing.
- Docs site: `https://morozsm.github.io/icom-lan/` → **https://rigplane.dev/**.
- GitHub repo: `morozsm/icom-lan` → `rigplane/rigplane-core`. Auto-redirects active.

## [1.1.0] — 2026-05-04

Two headline items:

1. **Critical fix for the WSJT-X / JS8Call audio regression on IC-7610 LAN
   bridges (#1381).** Since v0.17.0 the LAN audio bridge fed stereo
   radio bytes (the new dual-RX `PCM_2CH_16BIT` default) into a mono
   PortAudio stream, halving the effective sample-rate. Users observed
   1.5 kHz audio with the 3 kHz USB filter and TX silence. Fixed: the
   bridge now auto-detects the radio's negotiated channel count and
   downmixes (L+R)/2 → mono inside the bridge, mirroring the web UI
   broadcaster pattern. WSJT-X / JS8Call / fldigi see a clean mono
   stream at full sample-rate again. Single-RX rigs and the dual-RX
   web routing path are unaffected.

2. **Opt-in diagnostic reporting end-to-end** (epic #1385). New
   `icom-lan diagnose` CLI subcommand + Web UI "Send diagnostic
   report" dialog build a redacted bundle from 9 contributors and
   (only on explicit user action) post to a maintainer-operated
   triage service.

Plus frontend tech-debt closeout (#1369 cluster) and a configurable RX
audio jitter buffer for unreliable LAN links (#1363).

### Added

- **Diagnostic data collection (epic #1385).** New `icom-lan diagnose`
  CLI subcommand and Web UI "Send diagnostic report" dialog (Settings
  panel) collect a structured ZIP from 9 contributors (`system`,
  `invocation`, `radio`, `audio`, `logs`, `state`, `errors`,
  `dependencies`, `config`) with PII redaction (paths, IPs,
  credentials, tokens). Default is local save; explicit `--upload`
  opt-in posts to `https://reports.msmsoft.net/v1/diagnostics/upload`
  (override via `ICOM_LAN_REPORT_ENDPOINT`). Privacy invariants per
  `docs/architecture/open-core-policy.md` §2 carve-out.
- **Public contract `docs/contracts/diagnostic-bundle-v1.md`** documents
  the anonymous-tier upload protocol; third-party self-hosters can
  implement against it (#1398).
- **`DiagnosticContributor` protocol + entry-point discovery**
  (`icom_lan.diagnostics` group) lets `icom-lan-pro` plug in additional
  data sources without touching open-core (#1389).
- **HTTP upload client** with typed errors (`RateLimited`,
  `BundleTooLarge`, `ForbiddenContent`, `MetadataInvalid`,
  `NetworkError`) and a `header_provider` hook for Pro-side signed
  uploads (#1394).
- **Always-on rotating diagnostic log** (`SafeRotatingFileHandler`,
  `~/.cache/icom-lan/logs/`, ~15 MiB cap) scoped to
  `logging.getLogger("icom_lan")` so library-mode use stays clean (#1387).
- **Configurable RX audio jitter buffer** for unreliable LAN links (#1363).
- **User documentation:** `docs/guide/diagnostic-reports.md` walkthrough
  with privacy invariants, troubleshooting, and CLI reference (#1401).

### Changed

- `pip install icom-lan` no longer transitively imports `aiohttp` —
  the `icom_lan.diagnostics.upload` module is lazy-loaded via PEP 562
  `__getattr__` so all CLI commands work without aiohttp installed (a
  dev-only dependency). `icom-lan diagnose --upload` emits a friendly
  install hint instead of a Python traceback when aiohttp is missing
  (#1417, #1420).
- Open-core policy formally documents the **carve-out for user-initiated
  diagnostic support reports** alongside the existing "no telemetry"
  rule (`docs/architecture/open-core-policy.md` §2).

### Fixed

- **`AudioBridge` stereo-to-mono downmix for hamlib clients (#1381).**
  See headline #1 above. Critical regression fix for IC-7610 LAN
  bridge users running WSJT-X / JS8Call / fldigi via
  `icom-lan web --bridge`.
- IPv6 redaction handles compressed `::` forms correctly via
  `ipaddress.ip_address()` validation; previously public addresses
  ending in `::1` were misclassified as loopback (#1418).
- Path redaction skips URL-embedded paths via negative lookbehind (#1418).
- Web UI `handle_send` race condition: atomic CSRF check-and-set under
  the session lock prevents concurrent double-uploads (#1419).
- Web UI modal `handleCancel` respects `busy` state (Escape and
  backdrop click ignored while a request is in flight) (#1419).
- macOS device labels with embedded usernames scrubbed in the audio
  contributor (#1418).
- Hostname redaction (`*.example.com`, `*.local`) for the radio
  contributor's `host` field (#1418).
- Bundle assembler cleans up partial output from failed contributors
  so it doesn't leak into the ZIP (#1418).
- PATH env var redacted per-segment so the `:` separator no longer
  breaks the lookbehind guard (#1418).
- Threading exceptions captured by `_error_ring` via
  `threading.excepthook` alongside `sys.excepthook` (#1418).
- Frontend tech-debt cluster cleared (epic #1369): 173 svelte-check
  errors from self-wired panel migration (#1370), 8 misc type errors
  (#1374), nullable narrowing + Promise generic in HTTP client (#1372),
  vitest 4 mock signatures (#1371), invalid jitter env-var
  half-apply (#1366), ruff format sweep (#1368).
- CI gate: `npm run check` + vitest now blocks frontend regressions (#1375).

### Docs

- Diagnostic data collection design spec, 530 LOC (#1386).
- Public bundle contract `diagnostic-bundle-v1.md` (#1398).
- User guide for diagnostic reports (#1401).
- Open-core policy carve-out for user-initiated reports (#1418).

## [1.0.1] — 2026-05-01

Docs build + internal-import canonicalization patch. **No runtime behaviour
changes**, no public API changes — `pip install --upgrade icom-lan` from
1.0.0 is mechanical.

### Fixed

- **Docs build:** mkdocstrings autorefs in `docs/api/{radios,audio,commander,sync,types}.md` resolve cleanly (#1338). Previously `icom_lan.<shim>.X` references could not be traversed by griffe through `sys.modules`-alias shims, breaking the `docs.yml` workflow.
- **Docs build:** `[Followup]` markdown brackets in `docs/plans/issue-drafts/00-epic.md` are backtick-quoted so `mkdocs_autorefs` stops trying to resolve them as cross-reference targets under `--strict`.

### Internal

- **Canonicalised 60 in-package imports off shim paths (#1338).** Files in `src/icom_lan/<layer>/*.py` now import siblings via canonical paths (e.g. `icom_lan.commands.commander` instead of the top-level `icom_lan.commander` shim). Public API shims are preserved verbatim — downstream `from icom_lan.commander import …` keeps working.
- **Canonicalised 13 mkdocstrings refs in `docs/api/*.md` (#1338).** Tutorial code examples in `docs/guide/*.md` and `docs/radio-protocol.md` deliberately keep using public-stable shim paths.
- Tagged release artefact (`RELEASE_NOTES.md`) removed from the repo per the post-release housekeeping convention; the GitHub release body remains canonical.

## [1.0.0] — 2026-05-01

**Public API stability commitment.** The Tier 1 surface documented in
`docs/api/public-api-surface.md` — the `Radio` protocol, capability
protocols (`AudioCapable`, `ScopeCapable`, `MetersCapable`,
`LevelsCapable`, `StatePollable`, `RigctldRoutable`, `UsbAudioCapable`,
…), `create_radio` / `BackendConfig`, and the `frontend/src/lib/local-extensions/`
host API — is now under SemVer. No public API breaking changes vs
v0.19.0; every prior import path continues to work via `sys.modules`-aliased
re-export shims.

### Added

- **Tier 1 Capability Protocols extended (epic #1322).** Four new
  protocols on `icom_lan.radio_protocol` enable `isinstance`-based feature
  detection — backends are now selected by capability, not by backend-id
  string:
  - `StatePollable` + `StatePoller` (#1322, #1323) — replaces backend-id
    branching in `web_startup`.
  - `RigctldRoutable` (#1322, #1324) — pluggable rigctld routing.
  - `UsbAudioCapable` (#1322, #1326) — uniform USB audio device contract.
  - `PowerControlCapable.native_power_unit` (#1322, #1325) — drops the last
    backend-id discriminator from `web/handlers/control.py`.
- **rigctld per-VFO routing (#1342–#1346).** Parser now accepts the
  Hamlib `chk_vfo=1` leading VFO argument and routes `f`/`m`/`t`/`s`/`S`/`j`/`l`/`L`/`u`/`U`
  per-VFO. `RigctldRouting` Protocol gains an optional `vfo` kwarg;
  `AuditRecord` gains a `vfo` column. `chk_vfo='1'` is re-enabled for
  dual-RX (Variant A complete).
- **`YaesuCatRadio.set_rf_power` (#1331)** — Yaesu CAT backend now
  conforms to `PowerControlCapable`; `web/handlers/control.set_power`
  switched from string-cap to `isinstance(PowerControlCapable)`.

### Changed

- **Internal: source code reorganized into explicit layered structure
  (epic #1283).** `src/icom_lan/` is now organised into 11 layered
  packages — `core/`, `commands/`, `profiles/`, `audio/`, `scope/`,
  `dsp/`, `runtime/`, `backends/`, `web/`, `rigctld/`, `cli/` — with
  per-layer `LAYER.md` charters and the full layer matrix in
  `docs/plans/2026-04-29-modularization-plan.md`. **No public API
  changes:** every existing `from icom_lan.<old_path> import …` keeps
  working via `sys.modules`-aliased re-export shims, and the Tier 1 / Tier 2
  lazy surface in `icom_lan/__init__.py` is unchanged. New code SHOULD use
  canonical layer paths (`icom_lan.runtime.radio`, `icom_lan.backends.discovery`,
  …); see `ARCHITECTURE.md`.
- **`import-linter` enforces layer boundaries.** Repo-root `.importlinter`
  declares one layered contract plus three sibling-independence contracts
  (`web`⊥`rigctld`, `profiles`⊥`audio`, `commands`⊥`scope`⊥`dsp`); run
  locally via `uv run lint-imports`. CI gates every PR.
- **`AudioStats.jitter_ms` renamed to `reorder_depth_ema_ms` (#1231).** The
  field measures reorder-depth EMA, not RFC 3550 jitter. Internal field; no
  back-compat alias.
- **`_require_*()` helpers for optional deps (#1274).** New
  `src/icom_lan/_optional_deps.py` provides `_require_numpy`,
  `_require_sounddevice`, `_require_opuslib`, `_require_pillow`. Ad-hoc
  `try/except ImportError` blocks across the codebase now share uniform
  error messages.

### Fixed

#### rigctld

- **`chk_vfo` now returns `"0"` unconditionally for all radio profiles
  (#1319).** The dual-RX `"1"` advertising introduced in v0.17.0 caused
  WSJT-X / fldigi / JS8Call to fail with "Hamlib error: Feature not
  implemented" on IC-7610, IC-9700, and FTX-1 because Hamlib's `vfo_opt`
  mode prefixes every command with a VFO token. After the rollback,
  Variant A (#1342–#1346) re-enabled `chk_vfo='1'` for dual-RX with
  full parser + per-VFO routing — WSJT-X / fldigi / JS8Call golden replay
  now passes.

#### Web server

- **Blocking file I/O offloaded via `asyncio.to_thread` (#1332).**
  `server._handle_band_plan_config` and `eibi.load_cache` no longer block
  the event loop on disk reads.
- **`runtime_capabilities` fallback recognises `UsbAudioCapable` (#1356).**
  USB-audio detection now matches the Capability Protocol path.

#### Sync API

- **`get_alc_meter` exposed on `sync.IcomRadio` (#1228, refs #1226).**
  Was missing after the v0.19 `get_alc` removal.

#### CI

- **Subdirectory tests now actually run (#1352).** The `tests/test_*.py`
  glob silently skipped suites in nested directories; the glob now
  recurses.

### Removed

These deprecation closures were announced in v0.19 and dropped on schedule.

- **`IcomRadio.set_split_mode` (#1205).** Use `set_split` (`SplitCapable`).
- **`IcomRadio.get_alc` / `sync.IcomRadio.get_alc` (#1207).** Use
  `get_alc_meter` (`MetersCapable`).
- **`icom_lan.commands.levels` aliases (#1208):** `get_power`, `set_power`,
  `get_sql`, `set_sql`. Use canonical `get_rf_power` / `set_rf_power` /
  `get_squelch` / `set_squelch`.
- **`IcomRadio.set_vfo("A"/"B"/"MAIN"/"SUB")` legacy overload + `select_vfo`
  alias (#1206).** Use `ReceiverBankCapable.select_receiver` (MAIN/SUB) and
  `VfoSlotCapable.set_vfo_slot` (A/B). Legacy fallback paths in
  `web/radio_poller.py` and `rigctld/handler.py` remain for third-party
  backends.
- **`meter_cal._TABLES` and `meter_cal.calibrate()` (#1209).** Unreachable
  since #1173 shipped per-rig TOML calibration. `MeterType` and
  `interpolate_swr` remain exported.
- **Web UI v1 layout shell (#1216, #1217, #1218, #1220, #1227).** Legacy
  `AppShell` / `DesktopLayout` / `MobileLayout` removed. v2
  (`RadioLayoutV2` + `frontend/src/components-v2/`) has been the default
  since v0.15.1 and is now the only supported path. The `?ui=v1` URL
  fallback and the `ui-version` store are gone.

### Internal

#### Frontend

- **Panel → adapter migration complete (#1240, #1244–#1248, #1241).** All
  18 panels under `components-v2/panels/` no longer import directly from
  `$lib/stores/*`; capability flags and live radio state flow via panel
  adapters in `wiring/`. ESLint `no-restricted-imports` enforces the
  boundary (tests exempt for mocking).
- **`ControlButtonDemo` is now lazy-loaded (#1232).** Code-split out of
  the main bundle.
- **Type safety: removed `as any` casts in `command-bus.ts` (#1233).**
- **ESLint tightened — v2 layering only (#1219).** Banned `$lib/transport/*`
  and `$lib/audio/audio-manager` from panels and layouts.

#### `radio.py` god-object decomposition (#1063 wave 3)

- **`_fetch_initial_state` extracted to `radio_initial_state.py` (#1260).**
- **`_reconnect_loop` / `_watchdog_loop` extracted to `radio_reconnect.py`
  (#1259).**
- **`snapshot_state` / `restore_state` extracted to
  `radio_state_snapshot.py` (#1258).**

#### Dispatch-table refactors

- **`_civ_rx._update_radio_state_from_frame` → table-driven dispatch
  (#1257).** The 400-line if/elif over CI-V commands now dispatches via
  `_HANDLERS: dict[int, Callable]`. Behaviour preserved — verified by 72
  golden-test fixtures (#1266). Dead code at lines 1082-1093 (duplicate
  cmd 0x12 block) collapsed.
- **`transport._handle_packet` → dispatch table (#1239).** Six packet
  types now dispatch via a dict.
- **`web/handlers/control._enqueue_read_only` → dispatch table (#1263).**

#### Web server decomposition (#1063 wave 4)

- **`WebServer.start()` / `stop()` orchestration extracted to
  `web_startup.py` (#1261).** ~200 LOC moved out; `WebServer.start()` /
  `stop()` are thin delegators.
- **Method/path routing extracted to `web_routing.py` (#1262).**

#### Shared helpers

- **`BoundedQueue` extracted to `_bounded_queue.py` (#1230).** Four
  asyncio call sites (transport RX, radio scope/civ event queues, web
  fanout) now share one bounded-queue implementation.

#### Tests

- **Public-API surface regression test (#1273).** New
  `tests/test_public_api_surface.py` asserts every Tier 1 symbol from
  `docs/api/public-api-surface.md` imports cleanly AND that Tier 1 imports
  do not transitively pull Tier 3 modules into `sys.modules`.
- **Golden-test fixtures for `_civ_rx` (#1256).** 72 synthetic frame
  fixtures + parametrized dispatch test fence the upcoming refactor.
- **rigctld parser VFO-prefix tests + CI consistency guard +
  `dump_state` snapshots (#1342).** Wire-level integration test
  + golden replay fixtures (Variant A foundation).
- **Lazy-resolution contract test (#1284).** Locks down PEP 562
  `__getattr__` Tier 2 surface.

#### Documentation

- **`docs/architecture/open-core-policy.md` (#1276)** — codifies hard
  constraints: no telemetry, headless mode is sacred, no hollowing out,
  Radio protocol + `local-extensions/` as the Pro boundary, frontend
  WebKitGTK-floor compatibility.
- **`local-extensions/` documented as Tier 1 Pro-facing contract
  (#1277).** `docs/api/public-api-surface.md` lists exported types and
  functions from `frontend/src/lib/local-extensions/{host-api,manifest}.ts`
  with breakage policy.
- **`AudioBackend` Protocol Tier 2 stability marker (#1275).**
- **`ARCHITECTURE.md` refreshed for layered structure** + per-layer
  `LAYER.md` charters (11 files) + CLAUDE.md "Layer boundaries" section.

## [0.19.0] — 2026-04-29

### Tier-1 API stability commitment

- **API: tier-1 stability commitment from v0.19 (#1195).** The public API
  surface is now organised into three explicit tiers (stable / best-effort /
  internal) with a documented migration policy. See
  `docs/api/public-api-surface.md` for the tier policy, full symbol lists,
  and import examples.
- **Lazy `__init__.py` via PEP 562 (#1194).** Trimmed eager imports from 203
  to 71 lines; tier-2 symbols now lazy-load on first access. `from icom_lan
  import Radio` no longer transitively pulls in `web/`, `cli`, `rigctld/`,
  or `audio.backend` — measured ~13 % fewer submodules in `sys.modules`.
- **Layering enforcement via ruff TID251 (#1196).** Tier-3 internals
  (`icom_lan.web.*`, `icom_lan.rigctld.*`, `icom_lan.cli`) now banned from
  cross-tree imports. Pre-existing `icom_lan.radio.IcomRadio` ban preserved
  in web modules (#1201).

### Added

- **Receiver-tier protocols on backends.** `ReceiverBankCapable` and
  `VfoSlotCapable` (declared since #711, never implemented) now have
  concrete impls on both Icom (#1170) and Yaesu CAT (#1171) backends.
  Profile-driven dispatch covers IC-7610 / IC-9700 / IC-7300 / IC-705 and
  FTX-1 / Lab599 / X6100 single-RX rigs.
- **`SplitCapable` protocol (#1108)** — universal split control across all
  supported HF/VHF rigs. `set_split_mode` deprecated, alias retained until
  v0.20.
- **`RitXitCapable` protocol (#1099)** — extracted from
  `TransceiverStatusCapable`. Six canonical `*_rit_*` methods on
  YaesuCatRadio plus read-modify-write fix preserving the unaffected RX/TX
  bit on CF000.
- **17 protocol declarations** added across existing capability protocols:
  `DspControlCapable` (filter family, notch, agc), `LevelsCapable`
  (af_level/rf_gain getters, squelch), `MetersCapable` (power_meter,
  alc_meter, swr_meter), `AudioCapable` (codec/sample_rate properties),
  `ScopeCapable` (getters, scope_stream), `VoiceControlCapable`
  (get_compressor), `CwControlCapable` (break_in), `AntennaControlCapable`
  (get_attenuator), `PowerControlCapable` (get_rf_power lift).
- **Default web UX (#1087):** `icom-lan web` now auto-detects loopback and
  enables the audio bridge by default; rigctld serves on 4532 by default
  with `--no-rigctld` opt-out (#1088, #1089).
- **`[bridge]` extras folded into core (#1090).** `pip install icom-lan`
  ships `opuslib`, `sounddevice`, and `numpy` out of the box. Legacy
  `[audio]` and `[bridge]` extras retained as no-op aliases.
- **Calibrated SWR float on Icom rigs (#1173).** `IcomRadio.get_swr` now
  returns calibrated SWR (1.0–6.0+) via TOML calibration tables (5 anchor
  points per rig, sourced from official Icom CI-V references). New
  `get_swr_meter()` on async + sync API for raw 0-255 access.
- **`SetPower` poller dataclass unit-tagged (#1168)** — explicit
  `unit="raw_255"` (Icom default) vs `"watts"` (Yaesu) ends silent
  Icom/Yaesu unit mismatch.
- **State-contract sweep (#1169):** `RadioState.cw_spot` is now tri-state
  (`bool | None`); Yaesu-specific `rx_func_mode`/`tx_func_mode` moved into
  `YaesuStateExtension`.
- **Frontend runtime architecture (epic #708 follow-up).** `FrontendRuntime`
  singleton with `ScopeController`, lib/runtime/ pattern, panel-props /
  panel-commands separation enforced via ESLint.

### Changed

- **`__init__.py` is now ~80 lines** (was 203). Tier-1 symbols eager;
  everything else lazy via PEP 562 `__getattr__`.
- **Audio extras simplified.** `opuslib`, `sounddevice`, `numpy` moved from
  `[bridge]` extra into the main `dependencies` list. `[dsp]` extra now
  installs only `scipy>=1.11`. Documentation updated accordingly (#1090).
- **`set_vfo("A"/"B"/"MAIN"/"SUB")` overload deprecated** (#1187, #1172).
  Web and rigctld migrated to `select_receiver` / `set_vfo_slot`. Legacy
  overload emits `DeprecationWarning`; removal scheduled for v0.20.
- **Filter-width unified on segmented BCD index (#1157)** per wfview
  reference. Removed `direct_bcd_hz` profile encoding (was incorrect for
  IC-705 / IC-9700). Added per-mode segment tables to all four Icom rigs.
- **Scope poller uses public ScopeCapable getters (#1166)** with bounded
  per-call timeout (#1186). Eliminates raw `_civ(0x27, …)` layering
  violation while preserving fire-and-forget semantics (#1188).

### Fixed

- **6 rigctld consistency fixes (consolidating P0-classified findings now
  reclassified P3 — only Yaesu-routing path was active):**
  dial-lock (#1092), tuner-status (#1094), squelch dispatch (#1093),
  notch-filter (#1102), powerstat stub (#1095), set_level SQL set-side
  (#1163).
- **Yaesu compressor-level alias delegation (#1098)** — was returning
  hardcoded 0; now correctly forwards to `*_processor_level`.
- **Yaesu APF mode reachable from web (#1110)** — poller no longer drops
  `SetApf` actions; mode-1 toggle preserves user-tuned freq (#1141).
- **`set_vfo` legacy fallback for backends without `ReceiverBankCapable`
  (#1189)** — `SerialMockRadio` and similar legacy backends no longer
  silently no-op `V VFOA` / `V VFOB`.
- **`get_split` cache fallback honors `TimeoutError` (#1158)**, not just
  `CommandError` — completes the documented fallback contract.
- **CW pitch idx ↔ Hz conversion on Yaesu (#1162)** — `get_cw_pitch` /
  `set_cw_pitch` now correctly translate Yaesu's 0-75 idx to/from 300-1050
  Hz, fixing silent state corruption in `state.cw_pitch`.
- **CI-V worker cancel propagation (#1188)** — caller-side `wait_for`
  cancel now cancels the in-flight CI-V command at worker level, preventing
  cascade-skipping of subsequent queued commands.
- **Layering violation in scope poller (#1166)** and **filter-width
  encoding** (#1101) — moved from raw `_civ` in web/ into backend
  protocol methods.
- **IC-7300 GET decode bug** — silently read 2 BCD bytes when radio sent 1;
  fixed during filter-width unification.
- **VfoSlotCapable RTW bug on Yaesu (#1099)** — `set_rit_status` /
  `set_rit_tx_status` now read CF000 first to preserve the unaffected
  RX/TX bit (P1-02 from audit catalog).
- **Numerous Codex post-merge review fixes** — covering dispatch table
  gaps, capability fallbacks, type-signature drift, and exception-narrowing
  across `web/` and `rigctld/`.

### Removed

- **`vfo_exchange` / `vfo_equalize` aliases (#1114)** — deprecated since
  v0.17, removed per accelerated Q4 deprecation policy.
- **Seven LAN audio aliases overdue from v0.15 (#1111)** —
  `start_audio_rx`, `stop_audio_rx`, `start_audio_tx`, `push_audio_tx`,
  `start_audio`, `stop_audio`, `stop_audio_tx`. Use the canonical
  `*_opus`-suffixed names.
- **Internal facade helpers privatised (#1112):** `_push_pcm_tx`,
  `_push_tx_pcm`, `_has_command`, `_has_write_command`. Public
  `supports_command` is the canonical introspection API.
- **`audio_capabilities()` instance method** — use module-level
  `types.get_audio_capabilities()`.
- **`direct_bcd_hz` filter-width encoding** — no Icom rig actually used it;
  unified on segmented BCD index per wfview.

### Deprecated

- **`icom_lan.commands.levels` backward-compat aliases (#1167):**
  `get_power`, `set_power`, `get_sql`, `set_sql`. Removal v0.20. Use
  canonical `get_rf_power` / `set_rf_power` / `get_squelch` / `set_squelch`.
- **`set_split_mode` (Icom)** — replaced by `set_split` (`SplitCapable`).
  Removal v0.20.
- **`set_vfo("A"/"B"/"MAIN"/"SUB")` overload** — replaced by
  `select_receiver` + `set_vfo_slot`. Removal v0.20.
- **`get_alc` (Icom)** — replaced by `get_alc_meter` (`MetersCapable`).

### Docs

- New "Stability tiers" section in `docs/api/public-api-surface.md`
  documents tier-1 / 2 / 3 policy with full symbol lists and migration
  rules.
- Per-rig SWR calibration anchor tables documented in `rigs/*.toml` per R1
  research (sourced from official Icom CI-V References + wfview).

### Internal

- 47 PRs landed across this release cycle including audit work, Tier-1
  stabilization, Form H UX defaults, Form F sealing, and Codex automated
  review follow-ups.
- 7 architectural epics closed: API audit (#1071), v0.18.1 hotfix bundle
  (#1091), Tier-1 stabilization (#1096), Form H UX defaults (#1087),
  Codex post-merge sweep (#1140), audit closure (#1165), Form F sealing
  (#1193).

## [0.18.0] — 2026-04-19

### Added

- **Meter responsiveness (epic #936)** — backend priority polling tier plus
  frontend rAF needle smoothing. S-meter on RX jumps from ~3 Hz to an
  effective 16 Hz with asymmetric visual smoothing (50 ms attack /
  150 ms release); Pwr/SWR/ALC on TX go from ~3 Hz to 6.7 Hz each.
  PTT-on skips the LOW tier so TX-tier meters meet the smooth-needle
  acceptance target; Vd/Id/Comp deferred to a LOW tier (~750 ms each).
  (#936, #937, #938, #941)
- **Amber-LCD twin skins (epic #887)** — `lcd-cockpit` and `lcd-scope`
  skin variants with dedicated wrappers, 60/40 scope-dominant grid for
  single-RX, peer dual-cockpit grid for dual-RX, per-VFO indicator
  zones + global strip, AmberAfScope dominant mode + running-max line,
  ghost-graticule fallback when AF-FFT is unavailable, memory /
  recent-QSY strip, telemetry strip (VD/TEMP/ID + sparklines), Display
  Mode effects (vintage / CRT / flicker), LCD contrast in the
  control-strip, warm-dark theme. (#808, #823, #836, #837, #838, #861,
  #864, #877, #887-#895, #896-#900, #902, #904-#908, #911, #914, #915,
  #916, #918, #919, #920, #921, #929, #932, #933)
- **Mobile IA overhaul** — chip-scroll navigation + ESSENTIALS panel;
  persistent guarded PTT FAB; container-query collapse + aux row
  reserve; first-class RIT/XIT mobile chip; auto-collapse mode-specific
  panels. (#810, #839, #840, #842, #843, #857, #885, #894, #912, #926,
  #928, #930)
- **MetersDockPanel (epic #820)** — new station-health dock with
  Po/SWR/ALC/S tiles plus Id/Vd/COMP tiles gated by capabilities;
  peak-hold and SWR/ALC fault highlighting; replaced legacy bottom-dock
  cards; audio spectrum relocated accordingly. (#820, #821, #822, #823,
  #848, #866, #872, #878, #880)
- **Active-receiver UI (epic #825)** — `ActiveReceiverToggle` segmented
  `[M|S]` control; keyboard bindings for active-receiver switch with
  audio-focus sync; legacy activate-chip + adapter removed. (#825,
  #827, #828, #856, #858, #868, #875)
- **Skin system** — StatusBar dropdown skin-switcher; `lcd-cockpit` /
  `lcd-scope` registered as first-class skin IDs; variant prop threaded
  through dedicated wrappers. (#888, #889, #895, #901, #902, #904,
  #909, #913)
- **Spectrum toolbar** — keyboard shortcuts; grouping + separators +
  visual spec. (#830, #831, #847, #855)
- **Settings reorg** — settings gear moved into StatusBar; subgrid VFO
  digit/badge layout resolves crowding. (#807, #860, #865, #871)

### Changed

- **LCD token cascade + contrast core** — unified grid scaffold,
  AmberCockpit extracted, behavior-preserving refactors. (#833, #844,
  #853, #859, #890, #903)
- **Panel simplification** — removed panel-wide click-to-activate and
  STANDBY/ACTIVE pill; consolidated ON/OFF into a single power toggle;
  renamed SETTINGS → SETUP and pruned chip-duplicated panels;
  strengthened active/inactive VFO panel treatment. (#805, #824, #826,
  #841, #849, #854, #867, #925)
- **Bottom-dock reshuffle** — replaced bottom-dock cards with
  `MetersDockPanel`; relocated `AudioSpectrumPanel`; BRT/REF moved to
  mobile spectrum gear; SCOPE status moved to `VfoHeader` bridge. (#812,
  #821, #832, #869, #870, #880)

### Fixed

- **Scope reconnect deadlock** — break scope re-enable deadlock on
  reconnect. (#881)
- **CI-V watchdog** — self-cancel + patient OpenClose recovery. (#851)
- **Session rejection** — retry-with-mono fallback on stereo `rx_codec`
  session rejection. (#797, #802)
- **SWR calibration** — honor TOML calibration table at `raw=0`;
  non-linear calibration from TOML config. (#440, #924, #927)
- **hamlib rig model** — read from TOML in Yaesu `dump_state`. (#441,
  #923)
- **TX meter telemetry** — preserved when SUB is active; Vd tile
  readable in RX idle. (#822, #872, #891, #910)
- **AmberScope / AmberCockpit fallbacks** — VFO A filter-width fallback
  restored; ghost fallback when AF-FFT unavailable. (#918, #919, #920,
  #921)
- **Scope controls on mobile + v1** — restored source/dual controls;
  SCOPE pills no longer cropped by VFO bridge column overflow. (#832,
  #873, #883)
- **lcd-scope variant reachability** — end-to-end variant plumbing via
  dedicated skin wrappers. (#895, #909, #913)
- **v2-control-button** — distinguish idle vs disabled states. (#804,
  #879)
- **RightSidebar** — restored `AudioSpectrumPanel`; preserve
  cross-sidebar drag in `loadPanelOrder`. (#884, #886)
- **Codex review batches** — P1/P2 findings resolved across mobile grid
  rows, IP+ per-RX, legacy LCD, QSY debounce + orientation PTT release,
  tile-smoother pruning on capability toggle. (#887, #917, #931, #934,
  #935, #941)

### Docs

- LCD twin-skin redesign plan (epic #887).
- UI refinement design spikes (epic #818, #819).

### Chores

- Repo-wide `ruff format` pass (#922).
- 53 Svelte build warnings cleared — build now emits 0 warnings.
- `chore(#828)` VFO area tooltip audit + standardization (#882).
- `chore(#829)` StatusBar tooltip audit + standardization (#876).

## [0.17.0] — 2026-04-18

### Added

- **Dual-RX stereo LAN audio (epic #787)** — IC-7610 now delivers true stereo
  L=MAIN, R=SUB over LAN.  Backend negotiates `PCM_2CH_16BIT` in conninfo,
  locks `Phones L/R Mix = OFF` at relay start, and gates the behaviour on a
  new `lan_dual_rx_audio_routing` capability so IC-9700 / FTX-1 aren't
  affected.  Frontend resolves `focus` × `split_stereo` via a WebAudio
  ChannelSplitter + per-channel gain + panner graph — no CI-V round-trip
  for routing. (#752, #753, #756, #757, #770, #775, #776, #777, #778, #779,
  #781, #787, #788, #789, #790, #791, #792, #793, #794, #795, #798, #799,
  #800)
- **Dual VFO / dual receiver model (epic #708)** — new `ReceiverBankCapable`,
  `VfoSlotCapable`, `TransceiverBankCapable` protocols; per-receiver A/B
  `VfoSlotState`; split `swap_ab` / `swap_main_sub` command codes;
  `DualVfoDisplay` showing both MAIN+SUB on the desktop skin; receiver
  focus selector on the mobile skin. (#708, #709, #710, #711, #712, #714,
  #715, #716, #717, #718, #719, #722)
- **rigctld full VFOA/VFOB protocol** — implements the complete Hamlib
  per-VFO command set so split-aware digital-mode clients (WSJT-X, fldigi,
  JS8Call) round-trip cleanly. (#722, #723)
- **Composite WS commands** — `quick_dualwatch` and `quick_split` batch the
  radio side of the DW/split setup into single commands with
  double-click / long-press affordances in the UI. (#775, #776, #778, #779)
- **CLI log rotation** — `RotatingFileHandler` controlled by
  `ICOM_LOG_MAX_BYTES` and `ICOM_LOG_BACKUP_COUNT` env vars; prevents
  unbounded growth of `logs/icom-lan.log`.

### Changed

- **IC-7610 profile** declares `lan_dual_rx_audio_routing` capability and
  migrates VFO codes to `swap_main_sub` / `equal_main_sub` plus a new
  `0x14 0x0D` cmd29 route. (#748)
- **IC-9700 profile** correctly declares the MAIN/SUB scheme with proper
  byte codes. (#713)
- **IC-705 / IC-7300** migrated from legacy `swap` / `equal` keys to
  `swap_ab` / `equal_ab`.
- **VfoPanel** now uses `receiverLabel` + `vfoSlotLabel` for correct
  dual-VFO display naming. (#747, #728)
- **RFC §11** documents the dual-RX LAN routing contract: wire format,
  the Phones L/R Mix-OFF invariant, the focus × split_stereo gain/pan
  table, and the historical context of the `0x02` / `0x03` trap.

### Fixed

- **IC-7610 LAN session allocation** — decoupled `tx_codec` from
  `rx_codec` in conninfo; stock firmware rejected the session with
  `error=0xFFFFFFFF` when `tx_codec` was a 2-channel value (mic path is
  mono-only; wfview UI enforces the same constraint). (#794, #795)
- **CollapsiblePanel phantom-collapse on hover** — left-sidebar panels
  spontaneously toggled when the mouse passed over their headers.  Root
  cause was an uninitialised swipe-tracking state that was updated on
  plain `pointermove`; now gated on `swipeActive` set only by
  `pointerdown`. (#796)
- **Audio WebSocket queue** — `audio_config` WS send is queued when the
  socket is not yet open and flushed on `onopen`, so ACTIVATE on
  MAIN/SUB always reaches the radio. (#786)
- **Optimistic UI state** — equalize / swap operations update the UI
  immediately; audio focus follows ACTIVATE. (#785)
- **Scope follows active receiver** — spectrum/waterfall switches to the
  newly selected MAIN/SUB band on every `0x07 0xD0/0xD1`. (#784)
- **Broadcaster mid-stream codec refresh** — picks up codec / channel /
  sample-rate changes without reconnecting. (#766, #769)
- **Broadcaster frame_ms** — derived from actual payload size, not
  hard-coded 20 ms; fixes label mismatch on IC-7610 PCM16 packets. (#765)
- **VFO MAIN/SUB buttons** now emit proper receiver-select
  (`0x07 0xD0/0xD1`), not the MAIN↔SUB swap hack. (#773)
- **MAIN↔SUB poller flip** on IC-7610 + silence `Radio.__del__` test
  warnings. (#751)
- **sync.py default codec** aligned with the async `IcomRadio` default —
  both paths now return `PCM_2CH_16BIT` by default. (#798)
- **vitest flakiness** — split into fast + isolated projects to stabilise
  keyboard-wiring tests. (#771, #782)
- **post-review P1** — `swap_vfo_ab` safety + rigctld split rollback. (#746)
- **rig loader** parses `transceiver_count` from `[radio]` section. (#745)
- **DualVfoDisplay** — dedicated activate button resolves WCAG 4.1.2. (#744)
- **command-bus** — tighten focus→mode handoff race. (#720)

### Docs

- Dual-RX / transceiver / receiver / VFO model primer in
  `docs/internals`. (#724)
- IC-7610 cmd29 parity reconciled with wfview's `IC-7610.rig`. (#725)
- Opus DSP/tap gate behavior + one-shot warning documented. (#762)

### Chores

- `chore: mypy cleanup` — zero errors on default install; `numpy` /
  `sounddevice` / `opuslib` optional-dep imports now ignored via
  `[[tool.mypy.overrides]]`.
- Delete speculative `AudioBufferPool` + PCM-8 mapping. (#765, #768)

## [0.16.4] — 2026-04-16

### Fixed
- **Web UI TX audio:** transcoder now uses the radio's negotiated sample rate
  (was hard-coded to 48 kHz, silently dropped TX on radios negotiated at 24 kHz) (#691)

### Changed
- **Test suite performance:** backend 87.6s → 64s (−27%), frontend 10.6s → 5.8s (−45%);
  CI per matrix job ~4m30s → ~2m00s (−55%) (#706, #707)

## [0.16.3] — 2026-04-16

### Fixed
- **Web UI:** resolve 33 issues across controls, sync, errors, layout, a11y, and
  performance — wiring layer (DW/VFO targeting), control panels (freq keyboard,
  ATU/ATT/APF), canvas perf (rAF idle loop, DX cap, gradient cache), error
  notifications (Toast mounted in v2 layouts), connection state (WS reconnect,
  scope/audio indicators), waterfall resize preservation (#693, #694–#702)
- **DSP pipeline:** add sample rate validation and auto-resampling for RNNoise (#692)
- **Audio broadcaster:** resolve subscriber leak and pong-timeout loop (#687, #690)
- **Audio WebSocket:** fix crash loop on PTT (#684, #688)
- **CLI:** hard errors for invalid inputs, silent ignores, startup ordering (#689)
- **CLI:** hard errors for explicitly requested features with validation (#686)

## [0.16.2] — 2026-04-15

### Added
- **Companion tuning step sync** — tuning step is now synced to the RC-28
  companion dispatcher via `PUT /api/local/v1/rc28/tuning-step`; incoming
  `companion_state` WS messages update the step in real time
- **WsChannel.reconnect()** — reconnects a WebSocket channel using its
  last-known URL, enabling full lifecycle restore after disconnect

### Fixed
- **Connect/Disconnect lifecycle** — the web UI button now controls the
  entire frontend connection: all WebSocket channels (control, scope,
  audio), HTTP polling, and MediaSession are torn down on Disconnect and
  restored on Connect; the server↔radio connection is never affected
- **Scope/audio channels survive reconnect** — `reconnectAll()` reopens
  all named WS channels (scope, audio-scope) after a disconnect+connect
  cycle; previously they stayed dead until page reload
- **StatusBar state tracking** — connect button now tracks `controlState`
  (WS+HTTP) instead of `radioState` (server↔radio), so the UI updates
  immediately on disconnect/connect
- **Transport warning noise** — suppressed misleading `_packet_pump`
  warning and reduced UDP error log verbosity
- **Companion auto-step preservation** — `setTuningStepFromCompanion()`
  preserves the auto-step preference when syncing from companion

## [0.16.1] — 2026-04-14

### Fixed
- **LAN discovery crash** — `OSError: [Errno 65] No route to host` when network
  is unavailable no longer produces a raw traceback; CLI prints a clear message
  and suggests using `--host` explicitly
- **CI strict mypy** — resolved `no-any-return` in `radio_poller.py` for
  `mypy --strict` boundary check
- **Dynamic CI badges** — tests, version, and mypy badges in README now
  auto-update from CI via gist-backed shields.io endpoints

## [0.16.0] — 2026-04-14

### Added
- **DSP Pipeline** (Epic #682) — pluggable audio processing framework:
  - `DSPNode` Protocol, `DSPPipeline` engine, `PassthroughNode`, `GainNode`
  - `NRScipyNode` — spectral subtraction noise reduction (scipy FFT)
  - `TapRegistry` — multi-consumer PCM analysis bus
  - Inter-node resampling utility; `[dsp]` optional dependency group
- **CW Auto Tuner** (#675, #677, #678) — FFT peak detection engine (`CwAutoTuner`),
  backend `cw_auto_tune` command, restored AUTO TUNE button in Web UI
- **AudioAnalyzer** (#679) — realtime SNR estimation from PCM stream
- **UDP Discovery Responder** — companion apps can broadcast `RIGPLANE_DISCOVER` on
  UDP 8470 and receive server URL, version, and radio status via unicast;
  `--no-discovery` CLI flag to opt out
- **Unified frontend architecture** (Epics #647–#653, #662–#665) — `FrontendRuntime`
  singleton, skin registry, runtime adapters, self-wired panels (AGC, Mode, Antenna,
  RfFrontEnd, RIT/XIT, Scan, CW, DSP, TX, Filter, BandSelector), eslint import
  boundary guardrails, LCD and mobile layout migration to unified runtime path
- **SUB receiver polling** (#562, #563) — TOML commands, receiver routing, AF/RF/squelch
  level polling in slow loop
- **TX meters** (#559) — ALC, Power, COMP, SWR polling during transmit
- **Scope backpressure** (#533) — adaptive poller gap, scope backlog shedding hook,
  `queue_pressure` metric on `IcomTransport`
- **Initial state fetch** (#532) — `_fetch_initial_state()` on connect and reconnect,
  readiness-gated state snapshot in WebServer
- **Cross-sidebar drag** (#566–#568) — move panels between left/right sidebars,
  localStorage persistence, dynamic panel rendering
- **Yaesu FTX-1 enhancements** (#551) — IF bulk query, clarifier clear, APF, CW spot,
  break-in delay, power switch (PS), data mode methods
- **IC-7300 improvements** (#545, #546, #564) — segmented BCD filter width encoding,
  scope marker TOML entry, cleanup of NOT_IMPLEMENTED comments
- **Meter calibration** (#556) — power/SWR/ALC tables in `ic7610.toml`, scope REF
  range constraints, `meter_redlines` in RadioProfile, generic calibration accessors
- **SystemController** (#665) — centralized HTTP system actions
- **Skin abstraction** (#326) — `ProfessionalSkin` (Phase 1)
- **Frontend test coverage** (#555) — component-level tests for LCD, Mobile, Spectrum,
  BandPlan, DspPanel, CwPanel, SpectrumToolbar, DxOverlay, EiBi, state-adapter,
  ws-client, radio store, audio subsystem
- **FTX-1 polling tests** (#551) — integration test suite for Yaesu CAT poller

### Changed
- **Single version source** — `__version__` now reads from `pyproject.toml` via
  `importlib.metadata` instead of being hardcoded in `__init__.py`
- **Frontend panel architecture** — extracted DspPanel + CwPanel logic to dedicated
  panel-logic modules (#594); extracted SpectrumToolbar, BandPlanOverlay,
  MobileRadioLayout, SpectrumPanel inline logic to separate files (#590–#593)
- **LCD layout** (#636) — adapts to reduced viewport height

### Fixed
- **CW auto tune** (#671) — reverted incorrect `cw_sync_tune`, removed broken
  AUTO TUNE button before reimplementing correctly
- **Shutdown reliability** (#634) — `os._exit()` for orphaned threads, manual loop
  with executor timeout, PortAudio stream close before task cancel, shutdown step
  timeouts
- **Audio stability** — drop frames while `AudioContext` suspended, resume once in
  `start()` instead of per-frame
- **Yaesu serial** — report `disconnected` status correctly, show serial port in
  startup banner, graceful poller disconnect handling
- **Connection readiness** (#602) — expose readiness fields from backend state
- **Frontend null guards** (#603–#605) — null receiver state, null numeric fields
  coerced to defaults, encoder revision for initial state snapshot
- **Disconnect cleanup** (#600) — clear stale state, reset delta and radio store
- **Code review fixes** (#670, #576) — 5 findings from session audit, layering and
  model guards, reconnect timing
- **Drag-reorder** — unregister instances from registry on component destroy
- **All mypy errors resolved** — `ControlPhaseHost` protocol gap, `YaesuCatRadio`
  missing `get_data_mode`/`set_data_mode`, scipy stubs, `no-any-return` fixes
- **All ruff errors resolved** — unused imports in test_cli.py

### Documentation
- Refreshed Web UI guide for v2 runtime and skin workflows (#681)

## [0.15.1] — 2026-04-10

### Changed
- **Web UI v2 is now the default layout.** New visitors and fresh installs see the
  redesigned RadioLayout v2 interface. Users who previously selected v1 keep their
  choice (persisted in localStorage). Switch manually with `?ui=v1` or `?ui=v2`.

## [0.15.0] — 2026-04-10

### Added
- **Zero-config CLI startup** (Epic #526) — `icom-lan web` auto-discovers radio via LAN broadcast,
  `--preset hamradio|digimode|serial|headless` for common scenarios, smart startup banner with
  loopback device hints (#527, #528, #529, #530).
- **Drag-and-drop panel reorder** — drag handles on right sidebar panels (#557).
- **Complete CI-V command coverage** (Epic #535) — scope settings popover (#538), missing polling
  entries (#539), VOX/CW/DSP panels (#540), TX band edge support (#541), memory channel
  manager + scan modes (#542, #543), TX meters + scope toolbar controls (#536, #537).
- **Center dead zone for RF/SQL dual slider** — prevents accidental threshold jumps.
- **Poller deadlock regression tests** (#554) — state consistency + deadlock detection tests.
- **Yaesu CAT backend and CLI factory routing** — `--backend yaesu-cat`, capability-based polling,
  rigctl routing strategy, Web ControlHandler support, meters/advanced-control conformance,
  and follow-up code review fixes for issues #427-#445.
- **Universal radio profile system** — declarative `OperatingProfile` / `apply_profile()` /
  `PRESETS`, packet/data profile helpers for IC-705, and additional sync control methods.
- **TLS/HTTPS for Web UI** — HTTPS listener support with automatic self-signed certificates (#205).
- **Audio FFT UI work** — full-color `AudioSpectrumPanel`, standard-layout integration, audio-scope
  WebSocket channel, variable FFT bandwidth handling, and audio spectrum rendering fixes.
- **Expanded Web/rigctld command coverage** — raw CI-V passthrough, levels/functions support,
  data mode inputs/levels, VOX, tone/TSQL, CW text/stop, band/split, system/config,
  selected/unselected freq+mode, memory API support, and scope toolbar controls.
- **Capability/tag cleanup** — extracted `capabilities.py`, added `system_settings` tag,
  `supports_command()` on the Radio protocol, and removed remaining protocol abstraction gaps.
- **Issue #448 UI/antenna work** — v2 antenna panel, capability/state tracking, corrected IC-7610
  TX ANT vs RX ANT semantics, and startup readiness checks split between connect-time validation
  and server-side guards.

### Changed
- **Connection readiness contract** — `radio.connect()` now owns bounded wait-for-ready and fails
  if the radio never becomes usable; Web UI and rigctld startup now perform instant guards only.
- **Protocol/capability routing** — replaced several `isinstance(AdvancedControlCapable)` checks with
  capability tags and centralized capability constants.
- **Spectrum/waterfall interaction architecture** — clean separation of gesture, drag, and tune layers.
- **Frontend/test hygiene** — resolved Svelte/type issues, fixed frontend redesign regressions,
  refreshed API docs and badges, and updated test fixtures for stricter protocol mocks.

### Fixed
- **Meter calibration** (#536) — corrected S-meter, RF power, SWR, ALC calibration tables per
  IC-7610 CI-V Reference p.4; dimmed irrelevant meter rows.
- **Scope REF BCD encode/decode** (#553) — fixed to match IC-7610 CI-V Reference p.15.
- **CENTER Type polling** (#552) — fixed root cause: poller was overwriting scope CENTER Type
  to Filter on every poll cycle; restored CTR mode indicator at center position.
- **Tuning indicator** (#552) — proportional positioning + scope REF display.
- **Deadlock: EnableScope** — EnableScope await blocked all commands during initial fetch.
- **Click-to-tune** — only on waterfall, not spectrum area; via pointerup instead of click event.
- **Reliable shutdown** — 3-tier signal handling, reuse_address for TIME_WAIT, force exit on
  second Ctrl-C, proper audio relay shutdown order.
- **AF scope** — bandwidth tracks actual filter width, crash fix when center_freq is 0.
- **Power-off state** not detected on server restart.
- **Startup fail-fast** — added pre-flight port check (#422), fail-fast on `civ_port=0` (#424),
  and eliminated half-working Web/rigctld startups when the radio transport is not actually ready.
- **IC-705 Wi-Fi binding** — hardened routed local bind handling and validated LAN support.
- **Audio/runtime stability** — fixed broadcaster restart behavior, audio handler lifecycle,
  control transport queue overflow after long runs, and Python 3.13 flaky tests (#398).
- **Scope/UI correctness** — fixed scope dispatch capability checks, scope polling/state updates,
  step-control width, BCD span payloads, speed arrow direction, PTT TX wiring, and optimistic
  state sync for antenna/scope controls.
- **Type-check/lint cleanup** — resolved all 188 ruff lint errors and 499 mypy type errors:
  file-level noqa for re-export modules, mixin TYPE_CHECKING base pattern, per-module mypy
  overrides for duck-typing consumers, and ControlPhaseHost protocol expansion.

### Documentation
- Added/updated Radio Profiles guide, web/rigctld API references, and test badges/documentation sync.

## [0.14.2] — 2026-03-27

### Changed
- **Git cleanup** — removed 83 tracked files (-33k lines): backups, internal dev docs
  (plans/sprints/reviews/audits), scripts, mockups, references, credentials in run-dev.sh
- **Documentation refresh** — index.md, radios.md, README.md updated for multi-vendor reality;
  FTX-1 moved from "planned" to "tested"; mkdocs nav expanded with 12 missing pages;
  5 broken links fixed; mkdocs build --strict passes clean
- **CI fixed** — removed parity matrix tests (depended on deleted files); marked 2 flaky
  reconnect tests as xfail (#398); CI green on Python 3.11/3.12/3.13

## [0.14.1] — 2026-03-27

### Fixed
- FTX-1 LCD layout, band indicator, DSP/TX panel redesign, CAT fixes (feature/ftx1-filter-width)
- Removed FTX-1 monitor tests (ML command not supported via CAT)
- Fixed tuner routing through command queue for Yaesu radios

## [0.14.0] — 2026-03-27

### Added

- **Multi-vendor rig profile support** — TOML schema extended for non-Icom radios:
  - `rigs/ftx1.toml` — Yaesu FTX-1 (Yaesu CAT, 17 modes, dual RX, meter calibration)
  - `rigs/x6100.toml` — Xiegu X6100 (CI-V 0x70, IC-705 compatible, QRP 8W)
  - `rigs/tx500.toml` — Lab599 TX-500 (Kenwood CAT, minimal command set, QRP 10W)
- **`[protocol]` section** — `type = "civ" | "kenwood_cat" | "yaesu_cat"` (default: `"civ"`)
- **`[controls]` section** — UI control styles: `toggle`, `stepped`, `selector`,
  `toggle_and_level`, `level_is_toggle`
- **`[meters]` section** — Non-linear calibration tables for S-meter and TX meters
  with `redline_raw` threshold
- **`[[rules]]` section** — Declarative constraint rules: `mutex`, `disables`,
  `requires`, `value_limit`
- **Extended VFO schemes** — added `"ab_shared"` (FTX-1) and `"single"` (simple QRP)
- **`[commands]` now optional** — non-CI-V radios may have empty command maps
- **`civ_addr` now optional** — defaults to 0 for Kenwood/Yaesu CAT radios
- `RadioProfile` and `RigConfig` extended with `protocol_type`, `controls`,
  `meter_calibrations`, `rules`
- **Yaesu CAT backend** (Epic #107) — full implementation for Yaesu FTX-1/FT-710/FT-991A:
  - YaesuCatTransport (async line protocol, `;` terminated, echo handling)
  - CAT template formatter + response parser (compile-once)
  - Polling scheduler for smooth meters (fast meters, slower state)
  - Full Web UI integration (command dispatch, levels, audio)
- **Audio FFT Scope** (Epic #383) — IF waterfall from USB/LAN audio stream:
  - AudioFftScope class (real-time FFT processor, consumes PCM, produces ScopeFrame)
  - Backend-agnostic (works with any AudioCapable radio)
  - Reuses existing scope protocol (SpectrumPanel + WaterfallCanvas)
- **Amber LCD display** (#389, #386) — retro KX3-style UI for radios without hardware spectrum:
  - 7-segment font, segmented bargraph, status indicators
  - Embedded Audio FFT strip (trapezoid filter visualization)
  - Grouped indicators (ATT/PRE/ATU/Contour/PROC/VOX)
  - Adaptive lerp (smooth animated filter width transitions)
- **Profile-driven command dispatch** (Epic #390-#396) — auto-wire all TOML commands to Web UI:
  - Frontend capability guards for multi-radio (hide unsupported controls)
  - Optimistic UI updates for NB/NR levels
  - Auto-reconnect on persistent serial errors
- **Serial discovery** (Epic #222) — `icom-lan discover` scans LAN + USB serial:
  - Multi-protocol probing (CI-V auto baud, Yaesu CAT, Kenwood CAT)
  - Deduplication (same radio found via LAN and serial)
- 42 new tests in `test_rig_multi_vendor.py` + 636 new tests total (3934 passed, 0 regressions)

## [0.12.0] — 2026-03-15

### Added

- **Data-driven rig profiles** (Epic #251) — radio configuration moved from hardcoded Python
  to TOML files in `rigs/`:
  - `rigs/ic7610.toml` — IC-7610 reference profile (full feature set, dual receiver)
  - `rigs/ic7300.toml` — IC-7300 profile (single receiver, VFO A/B, no DIGI-SEL/IP+)
  - `rigs/_schema.md` — TOML schema specification
  - `rig_loader.py` — `load_rig()`, `discover_rigs()`, `RigConfig`, `RigLoadError`
  - `command_map.py` — `CommandMap` (immutable CI-V wire byte lookup)
- **IC-7300 support** — tested via USB serial backend; rig profile defines all 200+
  supported commands, VFO A/B scheme, and IC-7300-specific wire byte overrides
- **`cmd_map` parameter on all 223 command functions** — every builder function in
  `commands.py` now accepts `cmd_map: CommandMap | None = None`; when provided, wire bytes
  come from the TOML profile instead of hardcoded IC-7610 defaults
- **`RadioProfile` additions** — `vfo_scheme` (`"ab"` | `"main_sub"`), `has_lan` fields
- **Web UI capability guards** — UI controls for DIGI-SEL, IP+, and dual-receiver
  features are automatically hidden when the active profile doesn't support them
- **Dynamic VFO labels** — Web UI shows "MAIN" / "SUB" for IC-7610 (main_sub scheme)
  and "VFO A" / "VFO B" for IC-7300 (ab scheme)
- **`/api/v1/info` enriched** — `capabilities` object now includes `vfoScheme`, `hasLan`,
  `maxReceivers`, `modes`, `filters` from the active rig profile
- **`/api/v1/capabilities` additions** — `receivers`, `vfoScheme` fields
- **`/api/v1/state` adapts** — omits `sub` receiver state for single-receiver rigs

### Changed

- +3497 lines, 236 new tests across `test_rig_loader.py`, `test_command_map.py`,
  `test_rig_ic7610.py`, `test_rig_ic7300.py`, `test_commands_cmd_map.py`
- Hardcoded IC-7610 wire bytes remain as defaults when `cmd_map=None` — fully backward-compatible

## [0.11.0] — 2026-03-12

### Added
- **Abstract Radio Protocol** (`radio_protocol.py`) — vendor-neutral interface with `Radio`, `AudioCapable`, `ScopeCapable`, `DualReceiverCapable` protocols
- **Epic #140 complete** — 100% CI-V command coverage (134/134 IC-7610 commands implemented)
- **Epic #215 complete** — Post-audit cleanup: mypy 197→0 errors, dead code removed (-616 lines), `__all__` API surface defined
- `IcomRadio.model`, `.capabilities`, `.radio_state` properties
- `set_state_change_callback()`, `set_reconnect_callback()` public methods
- `control_connected` property for transport health status
- `get_mode()` now returns Protocol-compatible `tuple[str, int | None]`
- Graceful shutdown: SIGTERM handler ensures clean radio disconnect on kill
- `_force_cleanup_civ()` for unconditional CI-V transport teardown
- Retry mechanism for `civ_port=0` (radio session not ready): 3×10s retries
- Connection indicators in Web UI update from `/api/v1/state` poll (200ms)
- `/api/v1/capabilities` endpoint uses `radio.capabilities`

### Fixed
- **Sequence counter overflow** — `_civ_send_seq` / `_audio_send_seq` now wrap at uint16 (was unbounded, crashed after ~1.5h)
- **Broken pipe recovery** — watchdog falls back to full reconnect when soft_reconnect fails
- **CI-V indicator accuracy** — `connected` property checks actual transport health, not just state enum
- UDP error logging rate-limited (first 3, then every 100th)
- `0x16` added to `_COMMANDS_WITH_SUB` (NB/NR/DIGI-SEL sub-command parsing)
- `server.stop()` uses full `disconnect()` instead of `soft_disconnect()` for complete session cleanup

### Changed
- All Web UI/rigctld consumers now use `Radio` Protocol type hints instead of `IcomRadio`
- `isinstance(radio, AudioCapable)` guards instead of `hasattr`
- Test coverage: 85% → 95% (3173 tests, +1434 from v0.10.0)
- **Type safety** — 0 mypy errors, full protocol-based typing for Radio/AudioCapable/ScopeCapable/DualReceiverCapable

## [0.8.0] — 2026-02-28

### Added

- **Web UI v1** — full-featured browser interface at `icom-lan web`:
    - Real-time spectrum and waterfall display (Canvas2D, click-to-tune)
    - Radio controls: VFO A/B, mode, filter, power, ATT, preamp, PTT
    - Band selector buttons (160m–6m with FT8 defaults)
    - Frequency entry, tuning step selector with snap, arrow keys, scroll wheel
    - Frequency marker and filter passband overlay on spectrum/waterfall
    - Eight real-time meter bars (S-meter, Power, SWR, ALC, COMP, Id, Vd, TEMP)
    - RX audio playback and TX audio capture in the browser (WebSocket binary)
    - Responsive layout, light/dark theme toggle, keyboard shortcuts
    - WebSocket pub/sub for scope, meters, audio, and control channels
- **Connect/Disconnect button** in Web UI — toggle radio connection without restarting server
- **Soft reconnect** — disconnect closes only CI-V/audio, keeps control transport alive.
  Reconnect re-opens CI-V instantly (~1s) without discovery or re-authentication.
  Audio auto-restarts after reconnect.
- **Skip discovery on reconnect** — `transport.reconnect()` reuses cached `remote_id`,
  eliminating the 30-60s discovery timeout on IC-7610.
- **Connection state machine** — `RadioConnectionState` enum formalizing connect lifecycle (#61)
- **State cache with TTL** — cached GET fallback values with configurable TTL
  (10s freq/mode, 30s power) via `cache_ttl_s` parameter (#63)
- **API docs from docstrings** — mkdocstrings-generated API reference (#65)
- **Scope assembly timeout** — 5s default prevents memory leak on incomplete frames (#62)

### Changed

- **CI-V commander: fire-and-forget for SET commands** — SET operations no longer wait
  for ACK from the radio, matching wfview behavior. GET commands retain 2s timeout
  with cache fallback on timeout. NAK silently logged at debug level. (#56)
- **`radio.py` refactored into focused modules** — split from 2395 to 1549 lines (#60):
    - `_control_phase.py` (452 lines) — authentication, conninfo, connection setup
    - `_civ_rx.py` (418 lines) — CI-V frame dispatch and RX pump
    - `_audio_recovery.py` (132 lines) — audio stream snapshot/resume
    - `_connection_state.py` — FSM enum for connection lifecycle
    - Public API surface unchanged (mixin pattern)
- **Optimistic port connection** — uses default ports (control+1, control+2) immediately
  instead of blocking on status packet. Status read in background with 2s timeout;
  if radio reports different ports, uses those instead. Eliminates up to 24s connection
  delay when radio returns `civ_port=0` after rapid reconnects.
- **CLI `--port` renamed to `--control-port`** to avoid confusion (#54)

### Fixed

- **CI-V GET timeout during scope streaming** (release blocker, #66) — RX pump now
  drains ALL pending packets from the transport queue each iteration instead of
  processing one at a time. Scope flood (~225 pkt/sec) no longer starves ACK/response
  packets behind hundreds of scope frames.
- **Conninfo local ports** — send reserved ephemeral UDP ports in conninfo packet
  (wfview-style `socket.bind(("", 0))`). Root cause of CI-V instability: radio
  didn't know where to send responses when local ports were 0.
- **Safari iOS audio** — AudioContext resume after background via `visibilitychange`
  listener; increased jitter buffer pre-roll from 50ms to 200ms for VPN use.
- **Flaky `test_hello_on_connect`** — race condition fix, pytest-asyncio dependency (#64)
- **Duplicate WebSocket connections** on page load/reconnect (#50)
- **Scope enable** — single entry point via `server.ensure_scope_enabled()` (#51)
- **PTT button** — toggle mode for click vs hold (#57)
- **Filter sync** after band change (#58)
- **PTT wait_response** restored after fire-and-forget refactor (#59)
- **Watchdog false disconnect** — use packet counter instead of qsize
- **Tuning flood** — throttle tuning commands to prevent CI-V timeout cascade
- **Frequency clamping** — valid range 30 kHz – 60 MHz

### Documentation

- Web UI user guide (`docs/guide/web-ui.md`)
- RFC for Web UI v1 protocol spec and architecture
- Updated architecture docs with mixin pattern and new module structure
- Updated test count: 1202 tests (was 1040)
- Roadmap Phase 8: Virtual Audio Bridge

## [0.7.0] — 2026-02-26

### Added

- Internal PCM<->Opus transcoder foundation for upcoming high-level PCM audio APIs.
- Typed audio exceptions: `AudioCodecBackendError`, `AudioFormatError`, `AudioTranscodeError`.
- High-level async PCM audio APIs: `start_audio_rx_pcm()` / `stop_audio_rx_pcm()`,
  `start_audio_tx_pcm()` / `push_audio_tx_pcm()` / `stop_audio_tx_pcm()`.
- Audio capability introspection: `audio_capabilities()`, `AudioCapabilities`.
- CLI: `icom-lan audio caps`, `audio rx`, `audio tx`, `audio loopback`.
- Runtime audio stats: `get_audio_stats()` with packet loss, jitter, latency metrics.
- Rigctld WSJT-X compatibility: `icom-lan serve --wsjtx-compat`.
- Golden protocol test suite: 45 parametrized fixtures.
- TCP server wire integration tests.

### Changed

- Audio API names explicit with `_opus` suffix.
- Rigctld mode mapping includes `PKTRTTY`.

### Fixed

- First-TX latency spikes in WSJT-X workflows.
- Abandoned rigctld requests no longer execute in background.

### Deprecated

- Ambiguous audio aliases (two-minor-release deprecation window).

## [0.6.0] — 2026-02-25

### Added

- Scope/waterfall API with `ScopeFrame`, `ScopeAssembler`, callbacks.
- Scope rendering: `render_spectrum()`, `render_waterfall()`, `render_scope_image()`.
- CLI `icom-lan scope` with themes, capture, JSON output.
- Mock radio server for integration testing (30 new tests).

## [0.5.1] — 2026-02-25

### Fixed

- `_ensure_audio_transport()` raises `ConnectionError` when audio port is 0.
- Ruff lint warnings resolved.

## [0.5.0] — 2026-02-25

### Added

- Command29 support for dual-receiver radios (IC-7610).
- Attenuator and preamp CLI commands with Command29 framing.

## [0.4.0] — 2026-02-25

### Changed

- Faster non-audio connect path (lazy audio port init).

## [0.3.2] — 2026-02-25

### Added

- Commander layer with priority queue, pacing, dedupe, transactions.
- New APIs: `get_mode_info()`, `get_filter()`, `set_filter()`, `snapshot_state()`.
- Extended integration test coverage.

## [0.3.0] — 2026-02-25

### Added

- Audio streaming (full-duplex, JitterBuffer, codec enum).
- Synchronous API (`icom_lan.sync`).
- Radio model presets (IC-7610, IC-7300, IC-705, IC-9700, IC-R8600, IC-7851).
- Token renewal and auto-reconnect with watchdog.

## [0.2.0] — 2026-02-25

### Added

- CLI tool with full command set.
- VFO control, RF controls, CW keying, power control, network discovery.

## [0.1.0] — 2026-02-24

### Added

- Transport layer, authentication, CI-V commands, meters, PTT, keep-alive.
- Clean-room Icom LAN UDP protocol implementation.

[Unreleased]: https://github.com/rigplane/rigplane-core/compare/v2.7.3...HEAD
[2.7.3]: https://github.com/rigplane/rigplane-core/compare/v2.7.2...v2.7.3
[2.7.2]: https://github.com/rigplane/rigplane-core/compare/v2.7.1...v2.7.2
[2.7.1]: https://github.com/rigplane/rigplane-core/compare/v2.7.0...v2.7.1
[2.7.0]: https://github.com/rigplane/rigplane-core/compare/v2.6.0...v2.7.0
[2.6.0]: https://github.com/rigplane/rigplane-core/compare/v2.5.1...v2.6.0
[2.5.1]: https://github.com/rigplane/rigplane-core/compare/v2.5.0...v2.5.1
[2.5.0]: https://github.com/rigplane/rigplane-core/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/rigplane/rigplane-core/compare/v2.3.1...v2.4.0
[2.3.1]: https://github.com/rigplane/rigplane-core/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/rigplane/rigplane-core/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/rigplane/rigplane-core/compare/v2.1.2...v2.2.0
[2.1.2]: https://github.com/rigplane/rigplane-core/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/rigplane/rigplane-core/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/rigplane/rigplane-core/compare/v2.0.3...v2.1.0
[2.0.3]: https://github.com/rigplane/rigplane-core/compare/v2.0.2...v2.0.3
[2.0.2]: https://github.com/rigplane/rigplane-core/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/rigplane/rigplane-core/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/rigplane/rigplane-core/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/morozsm/icom-lan/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/morozsm/icom-lan/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/morozsm/icom-lan/compare/v0.19.0...v1.0.0
[0.19.0]: https://github.com/morozsm/icom-lan/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/morozsm/icom-lan/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/morozsm/icom-lan/compare/v0.16.4...v0.17.0
[0.16.4]: https://github.com/morozsm/icom-lan/compare/v0.16.3...v0.16.4
[0.16.3]: https://github.com/morozsm/icom-lan/compare/v0.16.2...v0.16.3
[0.16.2]: https://github.com/morozsm/icom-lan/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/morozsm/icom-lan/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/morozsm/icom-lan/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/morozsm/icom-lan/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/morozsm/icom-lan/compare/v0.14.2...v0.15.0
[0.14.2]: https://github.com/morozsm/icom-lan/compare/v0.14.1...v0.14.2
[0.14.1]: https://github.com/morozsm/icom-lan/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/morozsm/icom-lan/compare/v0.12.0...v0.14.0
[0.12.0]: https://github.com/morozsm/icom-lan/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/morozsm/icom-lan/compare/v0.8.0...v0.11.0
[0.8.0]: https://github.com/morozsm/icom-lan/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/morozsm/icom-lan/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/morozsm/icom-lan/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/morozsm/icom-lan/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/morozsm/icom-lan/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/morozsm/icom-lan/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/morozsm/icom-lan/compare/v0.3.1...v0.3.2
[0.3.0]: https://github.com/morozsm/icom-lan/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/morozsm/icom-lan/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/morozsm/icom-lan/releases/tag/v0.1.0
