---
robots: noindex, follow
---

# RigPlane Core — Hardcoded User-Facing String Inventory

Issue: [RP-ML-002 — Inventory Core hardcoded user-facing strings](https://github.com/rigplane/rigplane-core/issues/1520)
Source plan: `rigplane-strategy/docs/roadmap/2026-05-19-core-pro-multilanguage-support.md`
Status: Phase 0 (Inventory and Policy) — research output only. No production
code is modified by this document.

This inventory enumerates the customer-facing English strings that live in
`rigplane-core` today, grouped by UI surface and migration priority. It is the
input to RP-ML-003 (i18n runtime) and RP-ML-005 (P0 surface extraction). It is
intentionally not exhaustive at the per-string level: the goal is durable
structure, priority, representative file/line references, and clear
false-positive boundaries — not a one-off dump that goes stale on the first
PR.

Counts in this document are estimates derived from `rg` scans against the
worktree at `codex/rp-ml-002-inventory` (branch base: `origin/main`). They are
order-of-magnitude figures intended for sizing extraction work, not exact
totals.

## Scope and methodology

In scope:

- `frontend/src` — Svelte 5 / Vite frontend (~100 `.svelte` files, ~26.8k LOC
  Svelte; ~13.8k LOC supporting TS in `frontend/src/lib`).
- `src/rigplane/cli/` — Python CLI user output (argparse help, `print(...)`
  diagnostic output, error messages on stderr).
- `src/rigplane/web/server.py` — server-side broadcast notifications whose
  `message` field renders directly in the Toast UI.
- `docs/` — customer-facing docs surfaces under `docs/guide/`, `docs/api/`,
  `docs/index.md`.

Out of scope (explicitly excluded — see [Exclusions](#exclusions-stable-by-policy)):

- protocol values (CI-V opcodes, websocket message `type`, rigctld command
  names, Hamlib `RPRT` values);
- structured error codes / config keys / env var names;
- public Python identifiers (`AudioCapable`, `IcomRadio`, etc.);
- log strings and exception messages that are diagnostic-correlation surfaces;
- test fixtures, CSS class names, command IDs (`set_scope_ref`, …),
  `data-testid` selectors, `panelId` constants;
- internal/working docs under `docs/plans/`, `docs/internals/`,
  `docs/architecture/`, `docs/contracts/`, `docs/operations/`, `docs/parity/`
  (excluded from the published site via `mkdocs.yml`).

Search heuristics used:

- `rg -tsvelte '(aria-label|title|placeholder)="[A-Z]'` for accessibility
  attributes and tooltips;
- `rg -tsvelte '">[A-Z]'` and `">[A-Z][A-Z]+<` for visible button/label text;
- targeted reads of high-signal files (layout/dialogs/panels/status bar);
- `rg -t py 'help=|print\(|broadcast_notification\('` for Python user output.

Strings were not extracted, rewritten, or moved. The inventory is doc-only.

## Glossary tokens (do not translate)

The following tokens appear repeatedly across surfaces and must remain stable
per the source plan's terminology policy (and as further refined by
RP-ML-001). They are documented here so reviewers can recognise them as
non-translatable in any surface entry below.

Product: `RigPlane`, `RigPlane Core`, `RigPlane Pro`, `Tower`.

Radio / mode: `VFO`, `MAIN`, `SUB`, `PTT`, `TX`, `RX`, `RIT`, `XIT`, `CW`,
`CW-R`, `SSB`, `USB`, `LSB`, `AM`, `FM`, `DATA`, `Dn` (D1..D5 data submodes),
`SPLIT`, `A↔B`, `A=B`, `DW` (dual-watch).

DSP / metering: `AGC`, `DSP`, `NB`, `NR`, `NOTCH`, `A-NOTCH`, `AGC-T`,
`VOX`, `SWR`, `ALC`, `S-meter`, `Po`, `ATU`, `VBW`, `RBW`, `BRT`, `REF`,
`HOLD`, `AVG`, `PEAK`.

RF front-end / antenna: `ATT`, `PRE`, `P1`, `P2`, `ANT1`, `ANT2`,
`RF Gain`, `Mic Gain`, `Drive Gain`, `Comp Level`, `Mon Level`, `RF Power`.

Scope / spectrum: `SCOPE`, `BANDS`, `STEP`, `SPAN`, `SPEED`, `EDGE`,
`CTR` / `FIX` / `S-C` / `S-F` (scope modes), `FST` / `MID` / `SLO`
(sweep speeds), `WIDE` / `MID` / `NAR` (notch widths),
`Wide` / `Mid` / `Narrow` (RBW), `dBm`, `kHz`, `MHz`, `Hz`.

Memory: `M`, `CH`, `CLR` (clear), `VFO->M`, `>>VFO`.

Protocol / ecosystem: `rigctld`, `Hamlib`, `CI-V`, `WSJT-X`, `fldigi`,
`JS8Call`, `WAV`, `LIVE` (audio stream).

UI/skin labels that are also brand-stable tokens: `AUTO`, `Standard`,
`LCD Cockpit`, `LCD Scope`, `SDR Screen (test)`.

These tokens may appear inside translatable copy (e.g. *"Toggle power (radio
is ON — click to power off)"* keeps `ON` and `OFF` literal). Catalog entries
should treat them as inline-stable terms, not as variables to extract.

## P0 — High-value migration batch

Target surfaces for RP-ML-005. These are the first views every user sees
during startup, recovery, support, and core operation. Most contain ordinary
prose where translation has clear UX value; the technical tokens listed in the
glossary stay stable.

### P0.1 — App shell, error retry, install prompt

Files:

- `frontend/src/App.svelte:59–86` — backend retry banner, install error copy.
- `frontend/src/components-v2/controls/InstallPrompt.svelte:50,55` — Install
  button, dismiss aria-label.
- `frontend/src/components-v2/controls/install-prompt-utils.ts:50–54` —
  iOS/Android/desktop install instructions (`getInstruction`).

Estimated string count: ~8.

Representative strings:

- `Backend error: ${err}`
- `Server unreachable after multiple attempts. Check connection and reload manually.`
- `Retry {retryAttempt}/{MAX_RETRIES}, next attempt in {retryDelaySec}s…`
- `Install`
- `Tap 📤 then "Add to Home Screen"`
- `Install via browser menu for the best experience`

Glossary tokens: none.

Notes: error retry copy is the very first thing a user sees on a broken setup;
extracting this is high leverage.

### P0.2 — Status bar (top shell, connectivity, power, settings, report)

File: `frontend/src/components-v2/layout/StatusBar.svelte`.

Estimated string count: ~25.

Representative strings:

- `Control link lost` (line 176)
- `Radio ↔ Server: {radioState}`, `Control WebSocket: {controlState}`,
  `Scope WebSocket: {scopeState}`, `Audio WebSocket: {audioState}`,
  `State HTTP: {httpState}` (status-indicator `title=` attrs, lines 180–200)
- Health reason phrases (lines 79–91): `radio link lost`,
  `radio response delayed`, `radio not responding`,
  `radio appears powered off or unreachable`, `server unreachable`,
  `rig offline`.
- `Toggle power (radio is ON — click to power off)`,
  `Toggle power (radio is OFF — click to power on)`,
  `Toggle power (radio state unknown — click to toggle)` (lines 53–58).
- `Disconnect from radio (control link active)`,
  `Connect to radio (control link inactive)` (lines 94–96).
- `Send diagnostic report`, `Report`, `Show settings`, `Select UI skin`,
  `Skin` (`sr-only`), `Disconnect` / `Connect`, `OFF` / `ON` button labels
  (lines 258–311).
- Skin dropdown labels (lines 36–42): `AUTO`, `Standard`, `LCD Cockpit`,
  `LCD Scope`, `SDR Screen (test)` — glossary-stable per policy.
- `Turn OFF the radio?`, `Turn ON the radio?`, `Disconnect?`, `Connect?`
  `confirm()` prompts (lines 118–135).
- `Failed to turn off radio: ${err}`, `Failed to turn on radio: ${err}`
  `alert()` strings.
- Now-Playing popover (lines 213–245): static labels `Frequency:`,
  `Location:`, `Language:`, `Country:`, `Target:`, `Schedule:`, `Band:`,
  `Details:`, `Source:`, `LIVE`, plus aria-labels `Station details`.

Glossary tokens: `ON`, `OFF`, `MAIN`, `SUB`, `LIVE`, scope/audio/control
labels are protocol-stable.

Notes: many `title=` attrs are template strings that interleave a literal
prefix with a runtime state ("connected"/"disconnected"). For i18n, the
prefix is the catalog message; the state value comes from a separate catalog
keyed on the protocol-stable status string.

### P0.3 — Settings modal

File: `frontend/src/components-v2/layout/RadioLayout.svelte:320–398`.

Estimated string count: ~10 (heading + 6 panel section titles + 3
confirmation/instructions; underlying panels counted in P1).

Representative strings:

- `Radio is powered off`, `Use the ON button in the status bar to power up`
  (power-off overlay, lines 327–328).
- `SETTINGS` heading (340), close `✕` (decorative — exclude).
- `VFO / BAND`, `DSP`, `AGC`, `RF FRONT END`, `RIT / XIT`, `CW`
  `CollapsiblePanel` `title` props (lines 344–392).
- HardwareButton labels `SPLIT`, `A↔B`, `A=B` (351–367) — glossary.
- aria-label `Settings`, `Radio is powered off` (321, 338).

Glossary tokens: `VFO`, `BAND`, `DSP`, `AGC`, `RF FRONT END`, `RIT`,
`XIT`, `CW`, `SPLIT`, `A↔B`, `A=B`, `ON`, `OFF`.

Notes: panel section *titles* (`DSP`, `AGC`, `RF FRONT END`, `RIT / XIT`,
`CW`, `BAND`) are glossary-stable English labels and should not be
translated. The settings-modal *heading* (`SETTINGS`) and the powered-off
copy *are* translatable.

### P0.4 — Send-report dialog

File: `frontend/src/components-v2/dialogs/SendReportDialog.svelte`.

Estimated string count: ~30.

Representative strings:

- Dialog heading: `Send diagnostic report` (line 260).
- Form labels: `Describe the problem`, `Issue URL (optional)`,
  `Email (optional)`, `Callsign (optional)` (280–315).
- Placeholders: `What were you doing when the issue occurred?`,
  `https://github.com/.../issues/123`, `you@example.com`, `N0CALL`.
- Helper text: `Generates a redacted bundle of recent logs and configuration. You will see a full preview before anything is uploaded.` (275–277).
- Preview screen: `Review the bundle below. Nothing leaves this machine until you click Send.` (343–344); meta labels `Endpoint`, `Total size`,
  `Files`, `Redactions` (348–357).
- Consent string: `I understand this bundle will be uploaded to the endpoint above and that my redacted logs may be reviewed by the maintainer.`
  (376–378).
- Action buttons: `Cancel`, `Save locally`, `Send`, `Generating…`,
  `Sending…`, `Generate preview`, `Close`, `Copy`, `Copied` (321–451).
- Result screen: `Report uploaded successfully.`, `Report ID`,
  `Tracking URL`, `Auth class`, `Could not send the report.` (414–442).
- Error map in `formatError()` (lines 207–232): `Rate limited. Retry in {n} seconds.`, `Rate limited. Please wait a few minutes and try again.`,
  `Bundle too large. Try unchecking some categories.`,
  `Server rejected the bundle (forbidden content detected). Review the manifest before submitting again.`,
  `Origin mismatch. Please reload the page and try again.`,
  `Preview expired. Generate a new one and try again.`,
  `Session expired. Close and reopen the dialog to try again.`,
  `Upload failed: ${err.detail || err.code}`,
  `Network error. Check your connection and try again.`,
  `Unexpected error: ${String(err)}`.
- File size formatter: unit suffixes `B`, `KiB`, `MiB` (234–238) —
  arguably technical, but the *prose* around them is translatable.

Glossary tokens: `RigPlane`, `B`/`KiB`/`MiB` (binary unit suffixes).

Notes:

- `DiagnosticsApiError` `code` values (`rate_limited`, `bundle_too_large`,
  `forbidden_content`, `origin_mismatch`, `preview_not_found`,
  `csrf_missing`) are stable API contract values — they index into the
  catalog but are not themselves translated. See
  [Exclusions](#exclusions-stable-by-policy).

### P0.5 — Toast notification surface

Files:

- `frontend/src/components/shared/Toast.svelte:35,42` — aria-labels
  `Notifications`, `Dismiss notification`.
- `src/rigplane/web/server.py:788–791,1052,1059` — `broadcast_notification`
  call sites (server-rendered text that flows verbatim into the Toast).

Estimated string count: ~6 (Toast aria + 4 known server-side messages).

Server-emitted notification messages:

- `Radio connected` (success)
- `Radio disconnected` (warning)
- `Audio bridge started` (success)
- `Audio bridge stopped` (info)

Glossary tokens: `Radio`, `Audio` (ordinary English; not protocol).

Notes: today these messages are computed Python-side and shipped over WS as a
preformatted `message` field. The cleanest i18n boundary is to make the
server emit a *reason code* (`radio_connected`, `radio_disconnected`,
`audio_bridge_started`, `audio_bridge_stopped`) and let the Svelte Toast
catalog look up the user-visible message — same pattern recommended for Pro
in the source plan §4. This is a small contract change to track separately
from the pure-frontend extraction.

### P0.6 — Mobile bottom-sheet titles and mobile chip-bar

Files:

- `frontend/src/components-v2/layout/MobileRadioLayout.svelte` — bottom-sheet
  titles (lines 807, 838, 869, 874, 890), chip labels (138–148), receiver
  selector (577–595), settings button, "Setup" label (603–606), TX-permit
  tooltip (542, 599), step-picker aria-labels.
- `frontend/src/components-v2/layout/MobileNav.svelte:10` — `Radio navigation`
  aria-label.
- `frontend/src/components-v2/layout/mobile-chip-bar.svelte:17` —
  `Mobile sections` aria-label.
- `frontend/src/components-v2/layout/mobile-nav-utils.ts:10–14` — bottom-tab
  labels (`VFO`, `Spectrum`, `Controls`, `TX`, `Meters`).

Estimated string count: ~25.

Representative strings:

- BottomSheet titles: `SETUP`, `ALL MODES`, `FILTER SETTINGS`, `RF POWER`,
  `TX SETTINGS`.
- Chip labels: `ESSENTIALS`, `BAND`, `SCAN`, `RF`, `DSP`, `RIT/XIT`, `TX`
  (mostly glossary, but `ESSENTIALS` is plain English).
- Sub-titles in modals: `DATA MODE` (line 852).
- TX permit messages: `TX not allowed on this frequency`, `Push to talk`,
  `TX allowed`, `TX not allowed (out of band)`.
- Sheet buttons: `OFF`, `D1`..`D5`, `SETUP`.
- Step-picker / receiver selector aria-labels: `Close step picker`,
  `Receiver selector`, `Setup`.
- Nav labels (`mobile-nav-utils`): `VFO`, `Spectrum`, `Controls`, `TX`,
  `Meters`.

Glossary tokens: `VFO`, `TX`, `RX`, `BAND`, `DSP`, `RF`, `RIT`, `XIT`,
`SCAN`, `MODE`, `FILTER`, `MAIN`, `SUB`, `SETUP` (treated as
glossary-stable because it appears on hardware as a button label).

Notes: bottom-sheet titles often *are* glossary-stable English labels (e.g.
`SETUP`, `TX SETTINGS`). Even so they need catalog entries because (a) the
i18n runtime cannot tell a hardcoded literal from a label, and (b) pilot
locale QA must verify uppercase glyph rendering and 35%+ expansion budget.

### P0.7 — Connection / power-off / scope-disconnected overlays

Files:

- `frontend/src/components-v2/layout/RadioLayout.svelte:320–331,338` (already
  covered in P0.3).
- `frontend/src/components-v2/layout/LcdLayout.svelte:104–118` — power-off
  overlay with `Radio is powered off`, `Power ON`, `Failed to power on: ${err}`.
- `frontend/src/components/spectrum/SpectrumPanel.svelte:379` —
  `Scope disconnected — reconnecting…`.
- `frontend/src/components-v2/panels/RxAudioPanel.svelte:53` —
  `Audio link lost — reconnecting…`.

Estimated string count: ~6.

Notes: these are the visible "something is wrong" overlays that need clear
non-English copy on day one of i18n.

**P0 subtotal: ~110 strings across ~10 files.**

## P1 — Operational and accessibility batch

Target surfaces for the second extraction wave. These are panel-level
controls, scope/spectrum chrome, diagnostics-adjacent aria-labels, and
keyboard-help UX. Heavy in glossary-stable tokens, so the extraction surface
is smaller than file count suggests.

### P1.1 — Panel section titles (sidebars)

Files:

- `frontend/src/components-v2/layout/LeftSidebar.svelte:33–121`
- `frontend/src/components-v2/layout/RightSidebar.svelte:33–63`

Estimated string count: ~14 unique panel titles.

Strings (all `CollapsiblePanel title=` props):

`RF FRONT END`, `MODE`, `FILTER`, `AGC`, `RIT / XIT`, `BAND`, `ANTENNA`,
`SCAN`, `RX AUDIO`, `DSP`, `TX`, `CW`, `MEMORY`, `AUDIO SCOPE`.

Glossary tokens: nearly all of these are glossary-stable (per policy). Even
so they need catalog keys for layout/expansion QA and so the i18n runtime can
distinguish "intentionally stable English" from "missed extraction".

### P1.2 — Spectrum panel and toolbar

Files:

- `frontend/src/components/spectrum/SpectrumPanel.svelte:378–391,419`
- `frontend/src/components/spectrum/SpectrumToolbar.svelte:138–351`
- `frontend/src/components/spectrum/ScopeSettingsPopover.svelte:9–84`
- `frontend/src/components/spectrum/spectrum-toolbar-logic.ts:12–22` —
  `SPEED_STATIC_LABEL`, `SPAN_LABELS`, `SPEED_LABELS`, `MODE_BUTTONS`
  constants.

Estimated string count: ~40 (about half are glossary-stable).

Representative strings:

- Aria/title tooltips: `Display settings`, `Display settings (BRT / REF)`,
  `Close display settings`, `Close`, `Decrease brightness`,
  `Increase brightness`, `Reset brightness`, `Decrease reference`,
  `Increase reference`, `Reset reference`, `Close layer menu`,
  `Show/hide band plan overlay`, `Select visible layers`,
  `Decrease tuning step`, `Increase tuning step`,
  `Click to step up, right-click to step down`,
  `Decrease span`, `Increase span`, `Scope span`,
  `Decrease speed`, `Increase speed`, `Scope sweep speed`,
  `Scope hold`, `Dual scope`, `Switch scope receiver`,
  `Scope mode: {label}`, `Scope settings`, `Toggle fullscreen`,
  `Drag to resize filter width`, `Resize filter width`.
- Toolbar labels: `STEP`, `EDGE`, `SPAN`, `SPEED`, `HOLD`, `DUAL`, `REF`,
  `BRT`, `AVG`, `PEAK`, `BANDS`.
- Toolbar select options: `Classic`, `Thermal`, `Gray` (color schemes).
- Layer dropdown sections: `Region`, `Layers`, `📻 EiBi Stations...`.
- Scope-settings popover: `Scope Settings`, `Center Type`, `VBW`, `RBW`,
  `During TX`, `Filter`, `Carrier`, `Abs.Freq`, `Wide`, `Narrow`, `Mid`,
  `Off`, `On`.
- Overlay: `Scope disconnected — reconnecting…`.

Glossary tokens: most uppercase tokens. `Classic`, `Thermal`, `Gray`, `Wide`,
`Narrow`, `Mid`, `Off`, `On`, `Filter`, `Carrier`, `Region`, `Layers` —
translatable ordinary English.

False positives in this surface (do not catalog):

- CSS class names (`toolbar-btn`, `popover-backdrop`, `gear-row`, …).
- Glyph-only buttons (`◀`, `▶`, `▾`, `×`, `−`, `+`, `0`, `⏳`, `🔄`,
  `📻`, `&#9881;`/⚙) — decorative or carry meaning via adjacent aria-label.

### P1.3 — DSP / AGC / RF Front-End / RIT-XIT / CW / Filter panels

Files:

- `frontend/src/components-v2/panels/DspPanel.svelte` (489 LOC) — modal
  aria-labels, button titles, modal titles `Noise reduction`, `Noise blanker`,
  `Notch`, `AGC Time Constant`; ValueControl `label=` props (`NR Level`,
  `NB Level`, `NB Depth`, `NB Width`, `Notch Freq`, `AGC Time`).
- `frontend/src/components-v2/panels/FilterPanel.svelte` (588 LOC) — labels
  `WIDTH`, `IF SHIFT`, `PBT Inner`, `PBT Outer`; aria-labels
  `Open filter settings`, `Close filter settings`; `Shape` heading.
- `frontend/src/components-v2/panels/TxPanel.svelte` (332 LOC) — labels
  `RF Power`, `Mic Gain`, `Comp Level`, `Mon Level`, `Drive Gain`;
  aria-labels `Close TX settings`, `TX level settings`.
- `frontend/src/components-v2/panels/CwPanel.svelte` (152 LOC) — labels
  `CW Pitch`, `Key Speed`, `Break-in Delay`; `BREAK_IN_LABELS` constant
  (`OFF`, `SEMI`, `FULL`).
- `frontend/src/components-v2/panels/RfFrontEnd.svelte` — label `RF Gain`;
  `formatAttDb()` / `formatPreamp()` returning `OFF`/`P1`/`P2`/`{n}dB`/`MORE`.
- `frontend/src/components-v2/panels/RitXitPanel.svelte` — label `Offset`;
  uses `RIT`/`XIT` from glossary.
- `frontend/src/components-v2/panels/MeterPanel.svelte` — labels `Po`, `SWR`,
  `ALC`.
- `frontend/src/components-v2/panels/AntennaPanel.svelte:20,43` — `TX`, `RX`
  labels.
- `frontend/src/components-v2/panels/AudioRoutingControl.svelte:46–93` —
  aria-labels `Audio routing`, `Audio focus`, `MAIN gain in decibels`,
  `SUB gain in decibels`; title
  `Route MAIN to left channel, SUB to right channel`.
- `frontend/src/components-v2/panels/DockMeterPanel.svelte:98` — aria-label
  `Meter source selector`.
- `frontend/src/components-v2/panels/dsp-utils.ts:18–32` — `OFF`/`ON`/`AUTO`/
  `MAN` option labels.
- `frontend/src/components-v2/panels/agc-utils.ts:21` — `OFF` fallback.
- `frontend/src/components-v2/panels/audio-utils.ts:13–35` — monitor option
  labels (`RADIO`, `LIVE`, `MUTE`) and status messages
  (`Radio speaker output`, `Browser audio stream`, `Audio muted`).
- `frontend/src/components-v2/panels/lcd/LcdContrastControl.svelte:46–55`,
  `LcdDisplayModeControl.svelte:36–45` — `LCD contrast preset`,
  `Contrast preset {p}`, `LCD display mode`, `Display mode {label}`.

Estimated string count: ~80 across panels (high glossary density; ~40
non-glossary translatable strings).

Glossary tokens dominate this surface. The translatable strings are mostly
prose tooltips (`NB — click to toggle; long-press for settings`,
`Manual Notch — click to toggle; long-press for settings`,
`AGC Time — click for settings`, `Auto Notch`) and ValueControl friendly
labels (`Noise reduction`, `Noise blanker`, `Notch`, `AGC Time Constant`,
`Radio speaker output`, `Browser audio stream`, `Audio muted`).

False positives in this surface (do not catalog):

- Numeric formatters that return units (`Hz`, `dB`, `s`, `kHz`).
- `panelId` values (`m-dsp`, `desktop-cw`, …) — they are command IDs.
- `data-testid` values.

### P1.4 — Memory panel

File: `frontend/src/components-v2/panels/MemoryPanel.svelte` (524 LOC).

Estimated string count: ~12.

Representative strings:

- Toolbar: `All`, `{populated}/{MAX_CHANNELS}` (number-only, exclude),
  `VFO -> M` (glossary), `Store to CH`, `Store`, `Cancel`.
- Action tooltips: `Click to edit name`, `Recall to VFO`, `Clear channel`,
  `Store VFO to this channel`.
- Row literals: `Yes`, `No`, `CLR`, `>>VFO`, `<<VFO`, `-- empty --`, `---`.
- Empty state: `No stored channels. Use "VFO -> M" to store the current frequency.`.

Notes: `>>VFO` / `<<VFO` / `CLR` / `VFO -> M` are glossary-stable (hardware
mnemonics). `Yes`/`No`/`Cancel`/`Click to edit name`/`No stored channels…`
are ordinary translatable copy.

### P1.5 — VFO header, ops, panel, mobile VFO bar, BandPlan / EiBi overlays

Files:

- `frontend/src/components-v2/layout/VfoHeader.svelte:172,206,212` — labels
  `SCOPE`, `SPLIT`, `RX {rxFrequency} TX {txFrequency}`; title
  `Speak current frequency aloud`.
- `frontend/src/components-v2/vfo/VfoOps.svelte` — 5 user-facing prose
  `title=` tooltips: `Equalize VFOs`, `SPLIT` mode toggle, `DUAL WATCH`,
  `Exchange VFOs`, `Transmit on receive VFO`. Glossary-stable tokens
  (`SPLIT`, `DUAL WATCH`) appear inside ordinary tooltip prose; both
  the token and the surrounding sentence need catalog keys for QA, only
  the surrounding sentence is meaningfully translatable.
- `frontend/src/components-v2/vfo/VfoPanel.svelte`,
  `frontend/src/components-v2/vfo/ActiveReceiverToggle.svelte` —
  acknowledged in scope; the extraction agent in RP-ML-005 should walk
  the entire `components-v2/vfo/` directory rather than spot-check.
- `frontend/src/components/spectrum/BandPlanOverlay.svelte:243–290` —
  aria-labels `Close band info`, `Band segment details`; popup key labels
  `Freq`, `Mode`, `Layer`, `License`, `Station`, `Language`, `Schedule`.
- `frontend/src/components/spectrum/EiBiBrowser.svelte:220–311` — title
  `EiBi Broadcast Stations`; meta strings `Season {x} • {n} stations`;
  empty-state `EiBi database not loaded.`,
  `Click 🔄 Fetch to download broadcast schedules from eibispace.de`,
  `~10,000 stations • Free data • Updates seasonally`; filter labels
  `🟢 On-air`, `⭐ Favourites`, `All bands`, `All languages`, `All countries`;
  table headers `Freq`, `Station`, `Lang`, `Schedule`, `Target`; button text
  `🔄 Fetch`, `⏳ Fetching...`.

Estimated string count: ~30.

### P1.6 — Local Extensions host (PRO embedding surface)

File: `frontend/src/lib/local-extensions/LocalExtensionsHost.svelte:189,222–257`.

Estimated string count: ~6.

Representative strings: aria-label `Local extensions`, button titles `Float`,
`Dock left`, `Dock right`, `Dock bottom`, `Collapse`.

Notes: This component renders Pro-supplied local extensions inside Core. The
copy itself is Core-owned UI. Keep in P1 because the surface is rarely the
first user-facing thing on a fresh install.

### P1.7 — Keyboard help overlay

File: `frontend/src/components-v2/layout/KeyboardHandler.svelte:120–144`.

Estimated string count: ~5 chrome strings; per-binding `label` and
`description` come from `KeyboardConfig` payload (server-supplied today and
may carry English text — flag for follow-up).

Strings:

- aria-label `Close keyboard help`.
- Help title (driven by `keyboardConfig.helpTitle` — server data).
- Help subtitle: `Hold Alt to reveal inline shortcut hints on controls.`.
- Section default name `General` (line 36 in script).
- Close button text: `Close`.

Notes: per-binding `label` / `description` need a separate decision —
whether the catalog lives in the Python config payload (so server-side
keyboard maps stay localizable) or in the frontend catalog keyed by binding
ID. Recommend frontend-keyed for the i18n pass; the Python side then only
emits the ID. Track as a follow-up gap.

### P1.8 — Controls (theme picker, install banner, attenuator, pull-to-refresh, collapsible panel)

Files:

- `frontend/src/components-v2/controls/ThemePicker.svelte:64,71,107,116` —
  title `Choose theme`, header `Theme`, tooltips `Use main theme for VFO`,
  `Apply {vfo.name} style to VFO only`.
- `frontend/src/components-v2/controls/AttenuatorControl.svelte:88,92` —
  aria-labels `Close attenuator menu`, `More attenuator values`.
- `frontend/src/components-v2/controls/PullToRefresh.svelte:92` — aria-label
  `Pull to refresh`.
- `frontend/src/components-v2/controls/CollapsiblePanel.svelte:184` —
  aria-label `Drag to reorder`.
- `frontend/src/components-v2/controls/value-control/DualParamRenderer.svelte`
  — long aria-label `RF gain and squelch (single control)…` and any
  paired descriptive copy. Long accessibility prose is a pseudo-locale
  layout-stress candidate.

Estimated string count: ~8.

### P1.9 — SDR test skin

File: `frontend/src/skins/sdr-test/SdrVfoScreen.svelte:198,336–361`.

Estimated string count: ~4.

Strings: aria-label `VFO display`; button titles `Swap VFOs`, `Copy A to B`,
`Speak frequency aloud`; visible `🔈 SPEAK`.

Notes: `SDR Screen (test)` is currently exposed in the skin switcher and is
clearly an internal/experimental surface. May be deferred to P2 if pilot QA
finds it not relevant for non-English release.

**P1 subtotal: ~200 strings across ~25 files. Most are glossary-stable
tokens that still need catalog keys for QA but no translation work; the
genuinely translatable subset is ~80 strings.**

## P2 — Secondary, developer-facing, and documentation batch

These are surfaces with low day-one user impact, or that are primarily
developer documentation. They are catalog candidates only after P0 and P1
ship.

### P2.1 — Demo / development components

Files:

- `frontend/src/components-v2/controls/ControlButtonDemo.svelte` — internal
  control showcase; gated on `?demo=control-buttons`. Plenty of headings
  (`Modern`, `Hardware`, `…`).
- `frontend/src/components-v2/meters/SMeterDemo.svelte:52–56` — `Full`,
  `Compact` variant labels.

Estimated string count: ~40 in `ControlButtonDemo.svelte` alone.

Notes: developer-only routes. Either skip entirely from translation, or
extract on a "best-effort English-only" basis once the i18n runtime exists.
Recommend defer.

### P2.2 — Python CLI user-facing output

Files:

- `src/rigplane/cli/__init__.py` (3656 LOC, ~158 `help=` strings,
  ~282 `print()` / stderr sites).
- `src/rigplane/cli/_diagnose.py` (433 LOC, ~12 `help=`, ~32 print sites).

Estimated string count: ~470 (help text + diagnostic prose).

Representative strings:

- `argparse` `help=` strings — short technical descriptions
  (`Show version and exit`, `Radio IP (default: $ICOM_HOST, …)`, …). These
  *are* customer-facing for anyone running `rigplane --help`.
- Auto-discovery prose (lines 128–181):
  `No --host specified, scanning LAN for radios ({timeout:.0f}s)...`,
  `Found radio at {host}`, `Found {n} radios — please specify one with --host:`,
  `No --serial-port specified, scanning serial ports...`,
  `Found {model} on {port} ({baudrate} baud)`.
- Error stderr: `Error: ${name} must be an integer (got {val!r})`.

Boundary decisions (per source plan):

- **Translate**: short prose error/help intended for end users.
- **Do not translate**:
  - argparse subcommand names (`status`, `freq`, `mode`, `power`, `meter`,
    `audio capabilities`, `audio capture-rx`, `audio probe`, `audio bridge`,
    `cw`, `ptt`, `power-on`, `power-off`, `att`, `preamp`, `antenna`, …);
  - `metavar` values that map to protocol enums (`USB`, `LSB`, `CW`, …);
  - choice lists that are protocol values (`on`/`off`/`tune`,
    `lan`/`serial`/`yaesu-cat`);
  - log lines (anything via `logger.*`);
  - JSON output (`--json` payloads are an API surface, not a translation
    target).

Notes: `_diagnose.py` exists to write support bundles; its output is read by
maintainers, not by the radio operator UI. Recommend marking the entire
module as exclusion — log/support correlation surface — and re-evaluating
case by case if a specific message is repeatedly user-visible.

Recommended sub-priority: CLI extraction can wait until P0/P1 frontend is
shipped. Mark as P2 to avoid blocking pilot UI release.

### P2.3 — Customer-facing docs (`docs/guide/`, `docs/index.md`, `docs/api/`)

Files (all under `docs/`):

- `docs/guide/cli.md`, `docs/guide/configuration.md`,
  `docs/guide/connection.md`, `docs/guide/diagnostic-reports.md`,
  `docs/guide/audio-recipes.md`, `docs/guide/ic*-usb-setup.md`,
  `docs/guide/commands.md`.
- `docs/api/*.md` (mkdocstrings-rendered Python API docs).
- `docs/index.md`, `docs/radio-protocol.md`, `docs/capabilities-matrix.md`,
  `docs/SECURITY.md`, `docs/PROJECT.md`, `docs/PERFORMANCE.md`.

Estimated string count: not measured at the per-string level; ~5.7k Markdown
lines across `docs/guide/`, plus mkdocstrings auto-generation for `docs/api/`.

Boundary decisions:

- `docs/guide/` is the highest-value translation surface in this batch
  (user-facing setup walkthroughs). Recommend incremental translation
  *only after* core app UI ships in pilot locale, per source plan §6.
- `docs/api/` is auto-generated from Python docstrings and is developer
  surface; it must not be translated in the first pass.
- Internal docs are already excluded by `mkdocs.yml`'s `exclude_docs:` block
  (`architecture/`, `plans/`, `internals/`, `contracts/`, `operations/`,
  `parity/`, `EXTENDED_PROTOCOL_RESEARCH.md`) — confirmed
  out of scope.

Notes: docs translation needs its own workflow (parallel locale tree,
freshness gating) that is not modelled in the message-catalog mechanism for
the Svelte UI. Treat as a separate, later track.

### P2.4 — README, CHANGELOG, top-level repo docs

Files: `README.md`, `CHANGELOG.md`, `ROADMAP.md`, `CONTRIBUTING.md`.

Notes: GitHub-rendered docs read by contributors. English-only is fine
indefinitely; not a translation target. Listed for completeness.

**P2 subtotal: ~510 strings (mostly CLI help) plus large Markdown surfaces
that need a separate doc-translation track.**

## Exclusions (stable by policy)

These are *not* translation targets. They must remain stable English /
symbolic strings because they participate in machine contracts, support
correlation, or developer-readable artifacts. Inventory readers should treat
any string that matches these patterns as exclusions even when they appear
inside otherwise translatable files.

### E.1 — WebSocket message `type` and event names

- `state_update`, `notification`, `dx_spot`, `connection_state`,
  `radio_health`, etc. (search: `"type"` keys in
  `src/rigplane/web/server.py`, `src/rigplane/web/handlers/control.py`).
- Frontend dispatch values (`msg.type === 'notification'` in
  `frontend/src/components/shared/Toast.svelte:27`).
- These are wire-format identifiers and must not be translated.

### E.2 — API `error` codes / `DiagnosticsApiError.code` values

- `rate_limited`, `bundle_too_large`, `forbidden_content`, `origin_mismatch`,
  `preview_not_found`, `csrf_missing` (in
  `frontend/src/lib/api/diagnostics.ts` and the corresponding Python
  endpoint).
- The UI uses these codes to *select* a localized message in
  `SendReportDialog.formatError()`. The code itself is the catalog key.

### E.3 — Command IDs and protocol-level identifiers

- WebSocket command names sent by the frontend
  (`runtime.send('set_scope_dual', …)`, `set_scope_mode`, `set_scope_ref`,
  `switch_scope_receiver`, `set_scope_center_type`, `set_scope_vbw`,
  `set_scope_rbw`, `set_scope_during_tx`, `set_scope_edge`, etc.).
- All `panelId=` props (`desktop-vfo-ops`, `m-band`, `m-rf-quick`,
  `rf-front-end`, …) — these are identifiers used by the drag/reorder store
  and shortcut hints map.
- All `data-testid=` values.
- All shortcut action names in `keyboard-map.ts` (e.g. `toggle_monitor`,
  `adjust_af_level`).
- CSS class names, selectors, and styled element role tokens.

### E.4 — CI-V / Hamlib / rigctld surface

- CI-V opcodes (`0x05`, `0x06`, `0x16/0x40`, `0x1A`, `0x27`, `0x29`, …) in
  `src/rigplane/commands/`, `src/rigplane/civ.py`, command-map.
- rigctld command names (`get_freq`, `set_freq`, `get_mode`, `set_mode`,
  `T`, `t`, `f`, `m`, `\dump_state`, …) in `src/rigplane/rigctld/`.
- Hamlib `RPRT` numeric error values.
- CI-V address bytes (`0x94`, `0x98`, `0xE0`).

### E.5 — Config keys, env vars, public Python identifiers

- TOML config keys in `rigs/*.toml`, `profiles/`.
- Environment variables (`ICOM_HOST`, `ICOM_USER`, `ICOM_PASS`,
  `ICOM_SERIAL_DEVICE`, `ICOM_SERIAL_BAUDRATE`, `ICOM_SERIAL_PTT_MODE`,
  `ICOM_USB_RX_DEVICE`, `ICOM_USB_TX_DEVICE`, `ICOM_AUDIO_SAMPLE_RATE`,
  `ICOM_PORT`).
- Public Python class/protocol names (`Radio`, `IcomRadio`, `AudioCapable`,
  `StatePollable`, `RigctldRoutable`, `UsbAudioCapable`, …) — public API
  surface.
- Profile names, backend names (`lan`, `serial`, `yaesu-cat`).

### E.6 — Logs, exception messages, diagnostic schemas

- All `logger.info / warning / error / debug` strings under `src/rigplane/`.
- Exception messages raised from backends / transport / commands. These flow
  into diagnostic bundles; translating them breaks support workflow.
- Diagnostic bundle file names, paths, manifest JSON keys (under
  `src/rigplane/diagnostics/`).
- Telemetry event names (none exist in `rigplane-core` today — the project
  is no-telemetry per `docs/architecture/open-core-policy.md`, listed for
  completeness in case the Pro repo's plan touches Core through this
  mechanism later).

### E.7 — Test fixtures, mocks, internal demos

- All strings under `tests/`, `frontend/src/components-v2/**/__tests__/`,
  `frontend/src/components/**/__tests__/`.
- `frontend/src/components-v2/controls/ControlButtonDemo.svelte` (gated
  developer demo at `?demo=control-buttons`).

### E.8 — File-format unit suffixes that are protocol-stable

- `B` / `KiB` / `MiB` (binary IEC suffixes) used in
  `SendReportDialog.formatBytes` — these are universal technical units and
  should follow source-plan policy of "do not localize unit symbols". The
  surrounding prose is translatable.
- `Hz`, `kHz`, `MHz`, `dB`, `dBm`, `s`, `ms`, `Hz` similarly.

### E.9 — Glyph-only buttons

Bare-glyph button text (`✕`, `×`, `▾`, `◀`, `▶`, `−`, `+`, `0`, `⚙`, `📻`,
`🔈`, `⏳`, `🔄`, `⚠`, `🟢`, `⭐`) is decorative. The aria-label on the same
element is the translatable surface.

## False-positive notes

Patterns that look like user-visible strings but are not, and must not be
catalog candidates:

- **CSS class names** in `class="..."`/`class:` directives — these are
  styling identifiers, not text.
- **`panelId` props** on `<CollapsiblePanel>` and friends — they index into
  layout-state stores.
- **`data-testid=` attributes** — they index into Vitest/Playwright assertions.
- **HardwareButton `color` props** (`'gray'`, `'cyan'`, `'orange'`, `'green'`,
  `'yellow'`) — design-system tokens.
- **`renderer=` props** on `<ValueControl>` (`'hbar'`, `'discrete'`,
  `'knob'`, `'dual'`) — render-strategy enum.
- **`tickStyle=` props** (`'notch'`, …) — render hint.
- **`variant=` props** (`'hardware-illuminated'`, …) — design token.
- **CSS custom properties referenced in inline styles**
  (`var(--v2-accent-cyan)`) — color tokens.
- **`indicator=` props** (`'edge-left'`, …) — hardware-button hint.
- **`accentColor=` props** containing CSS `var(...)` calls.
- **All-caps tokens that match the glossary** — these are intentionally
  stable English (see "Glossary tokens" section above).
- **Numeric formatters with unit suffixes** — `formatAttDb`,
  `formatPreamp`, `formatBytes`, `formatStep`, `formatPower`,
  `formatFrequencyString` return strings that interleave numeric values
  with stable unit symbols. Translate the *surrounding prose*; leave the
  formatter outputs alone.

## Surface coverage summary

| Batch | Surfaces                                  | Files (approx) | Strings (approx) | Translatable subset |
|-------|-------------------------------------------|----------------|------------------|---------------------|
| P0    | shell, status, settings modal, report dialog, mobile sheets, connection/power overlays | ~10            | ~110             | ~95                 |
| P1    | sidebars, spectrum, panel modals, memory, keyboard help, install/banner controls, BandPlan / EiBi, local extensions, SDR-test skin | ~25            | ~200             | ~80                 |
| P2    | demo components, Python CLI, customer docs | ~10 + docs tree | ~550             | requires per-message review |
| **Total** | All in-scope Core surfaces            | ~45            | ~860             | ~210 user-visible prose strings + glossary-stable labels |

The "translatable subset" column excludes glossary-stable tokens (CW, VFO,
TX, RX, MAIN, SUB, AGC, DSP, etc.) that need catalog keys for QA but no
translation work. It also excludes CLI help text where the boundary
(end-user vs. developer-output) is per-message and decided in P2.

## Surprises and notes for downstream agents

- **Server-rendered notification text** in `src/rigplane/web/server.py:788–
  791,1052,1059`. These flow verbatim into the Svelte Toast component. The
  catalog cannot live entirely in the frontend without either (a) the server
  switching to emit `reason_code` and letting the UI render the catalog
  message, or (b) the server holding its own English-only fallback while
  the UI overlays a localized version. Recommend route (a) — same pattern
  the source plan recommends for Pro's Rust shell — and call it out in
  RP-ML-003.
- **Keyboard help labels** come from a server-supplied `KeyboardConfig`
  payload. RP-ML-005 should either extract labels client-side (by binding
  ID) or extend the config to ship stable IDs that the catalog resolves.
- **`status_page.py` does not exist in this repo** — the local status page
  is a Pro concern (per source plan §"Pro"). Confirmed by repo grep: there
  is no `src/rigplane/companion/local/status_page.py` or equivalent in
  Core. Out of scope here.
- **Mobile chip and bottom-sheet labels** mix glossary tokens (`SETUP`,
  `BAND`, `DSP`, `TX SETTINGS`) with ordinary English (`ESSENTIALS`). Both
  need catalog keys for QA, but only the second is meaningfully
  translatable.
- **`SDR Screen (test)`** is an experimental skin exposed in production via
  the skin switcher (`StatusBar.svelte:41`). It is listed in P1.9 with a
  recommendation to defer if pilot QA needs to drop scope.
- **CLI surface is large** (~470 strings) and contains a mix of true
  end-user copy and developer/support output. A subsequent ticket should
  split the CLI module into "end-user prose" vs "support correlation"
  before any extraction, mirroring the source plan's pattern for Pro
  companion CLI in RP-ML-011.
- **Generated logs / dirty trees**: this worktree (`_wt-ml/core`) is clean
  at the time of inventory. No untracked log files were touched.

## What this inventory does not do

- It does not propose message keys. Key naming is RP-ML-001
  (strategy-side glossary + key convention) and RP-ML-003 (runtime
  scaffolding).
- It does not gate hardcoded strings. CI lint rules for newly-added
  hardcoded strings are RP-ML-013.
- It does not propose the i18n runtime, the language selector UX, or the
  preference store. Those are RP-ML-003 and RP-ML-004.
- It does not translate. No `ja-JP` catalog scaffold work happens here.

## Notes for translators (community contributors)

`rigplane-core` is a public open-core repository, and translations beyond
English and the pilot `ja-JP` scaffold are expected to come from the
community over time. This inventory must remain useful to that audience,
not just to the implementation agent who extracts strings.

Implications for RP-ML-003 and downstream extraction PRs:

- Catalog format must be a single editable file per locale (recommend plain
  JSON keyed by `scope.subscope.verbOrNoun` per the strategy glossary §4).
  A community contributor must be able to fork the repo, copy
  `en-US.json` to `<locale>.json`, translate values, and open a PR
  without running the Svelte/Vite toolchain.
- Each catalog must declare placeholder syntax, fallback locale, and the
  non-translatable glossary tokens inline (or via a sibling README) so
  contributors do not need to read the strategy glossary first.
- A `frontend/src/lib/i18n/CONTRIBUTING.md` (or equivalent path under
  `frontend/`) must exist before any public call for community translators.
  The doc must explain: how to add a locale, how to run pseudo-locale and
  missing-key checks locally, which tokens never translate, and how PR
  review works.
- CI checks added in RP-ML-013A must fail with precise file path and key
  name on: invalid JSON, missing keys, broken interpolation, translated
  glossary tokens.
- No Tower / Crowdin / Weblate dependency. Contribution flow is
  `fork → edit JSON → PR`.
- Server-rendered toast text (see "Surprises and notes" above) is the
  one place where the community-friendly story is awkward: if the message
  text lives in `web/server.py`, a translator cannot edit it in the
  frontend JSON alone. RP-ML-003 should land the reason-code-on-the-wire
  pattern so the frontend catalog is the only place a translator needs
  to touch.

This inventory does not by itself prescribe a community PR template, but
RP-ML-003 should ship a CONTRIBUTING doc that the i18n catalog directory
points to.

## Acceptance check (issue #1520)

- [x] Inventory covers `frontend/src`, Python CLI user output, docs surfaces,
  and protocol/error-code boundaries.
- [x] Strings are grouped into P0/P1/P2 migration batches.
- [x] False positives (CSS, command IDs, tests, protocol codes) are documented
  ([False-positive notes](#false-positive-notes), [Exclusions](#exclusions-stable-by-policy)).
- [x] Strings that must stay stable (protocol/API/log/schema identifiers) are
  clearly marked as exclusions and tagged in each surface entry where they
  appear inline.
