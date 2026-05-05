# Open-core policy

This document codifies the hard constraints that govern **rigplane** as the
open-core half of a planned commercial product. These rules are not advisory:
they shape what may and may not land in this repository.

If a contribution conflicts with anything below, the contribution loses. The
correct response is to redesign — or, if the constraint is genuinely wrong, to
open an issue and change this document first.

---

## 1. Project shape

- **rigplane** — open-core, MIT-licensed Python library + Web UI for Icom
  transceivers over LAN/USB. Target users: hams, hobbyists, headless servers,
  Raspberry Pi setups, anyone who wants a free, scriptable, embeddable radio
  control plane.
- **rigplane-pro** — proprietary commercial layer, planned to ship roughly
  2–3 months out. Architecturally a Tauri 2 single-signed installer that
  spawns a Python sidecar (the rigplane server) as a child process on
  `127.0.0.1:8470`, plus a native integrations layer (USB HID for RC-28,
  OS-level virtual audio routing, global hotkeys, license gating in
  Tauri-Rust). Pro injects UI extensions into the open-core frontend through
  `frontend/src/lib/local-extensions/`.
- **One binary, two tiers.** The free experience is rigplane rendered without
  Pro extensions. Paid unlocks Pro features through the same shell.

This document is about rigplane. It exists so that the two halves can coexist
without the open-core erosion that historically follows commercial spin-offs.

---

## 2. No telemetry, ever

rigplane does not phone home. No exceptions, no opt-in tracking, no anonymous
usage statistics, no product analytics, no crash reporting beacon.

**Forbidden in open-core dependencies and runtime:**

- Sentry, Bugsnag, Rollbar, Honeybadger, or any hosted error reporter.
- PostHog, Mixpanel, Amplitude, Heap, Segment, or any product analytics SDK.
- Datadog RUM, New Relic Browser, or any commercial APM client.
- Google Analytics (`gtag.js`, `analytics.js`), Google Tag Manager, Plausible,
  Fathom, Matomo cloud, or any web-analytics tag.
- Background HTTP calls to first- or third-party telemetry endpoints from the
  Python server, the frontend bundle, or the rigctld bridge.

If telemetry is desirable, it lives in **Pro**, where it is opt-in,
self-hosted, disclosed up-front, and the user pays for the product partly to
get it.

### Caveat — "telemetry" as a domain term

The word `telemetry` already appears in this codebase referring to **local
radio data** that the radio sends back to us: drain voltage, currents, ALC,
SWR, and similar meter readings surfaced in `radio_poller.py` and the
`AmberTelemetryStrip` panel of the LCD skin. That is radio-domain telemetry,
not analytics. It does not leave the user's machine and is not subject to
this rule.

### Carve-out — user-initiated diagnostic support reports

A diagnostic report workflow that lets a user **explicitly send a bundle of
local logs and state** to a maintainer-operated triage endpoint is
permitted. Telemetry is forbidden because it is automatic, ambient, and
invisible; a support report is the opposite of those things — the user
authored it, knows what's in it, and chose to send it.

A diagnostic report mechanism in open-core must satisfy **all** of these:

- **No automatic, background, recurring, first-run, or silent collection or
  submission.** Logs may be written locally on a rotating file basis (see
  `SafeRotatingFileHandler` in `rigplane.diagnostics`), but submission to a
  remote endpoint is never automatic.
- **The user must explicitly start the workflow every time.** No "always send
  reports" toggle, no install-time consent that grants ongoing permission, no
  background queue. Each report = one user gesture (CLI command, Web UI
  button, Pro Tauri click).
- **CLI defaults never upload.** `rigplane diagnose` (no flags) builds and
  saves locally. `--upload` is opt-in. With a TTY, the final consent prompt
  defaults to "save locally" (`[y/N]` for upload), so accidentally hitting
  Enter cannot transmit anything.
- **Headless / non-TTY mode never prompts and never auto-uploads.** Scripted
  use must explicitly pass `--upload` to send.
- **Destination endpoint is visible before upload.** The full URL (including
  any `ICOM_LAN_REPORT_ENDPOINT` override) appears in the preview shown to
  the user.
- **Bundle is always available locally.** The user can always choose "save
  locally" instead of uploading; the ZIP file is the same.
- **Contact fields are opt-in only.** Email and callsign are never
  auto-populated from environment variables, system identity, prior reports,
  or any ambient source — only typed by the user at submission time.
- **The endpoint behaviour is documented as a public contract.** The shape
  of the anonymous request lives in `docs/contracts/diagnostic-bundle-v1.md`,
  so the open-core upload client builds against a non-proprietary spec.

This carve-out does **not** allow:
- Crash reporters that send automatically on uncaught exceptions.
- "Send anonymous usage stats" toggles.
- Rate-limited periodic uploads.
- First-run consent dialogs that authorise future automatic sends.
- Pre-filling of contact fields from any source.

If a future feature does not fit cleanly inside this carve-out, the default
is the broader §2 rule: no telemetry, no exceptions.

---

## 3. Headless mode is sacred

rigplane must run on a headless Linux box — including a Raspberry Pi
acting as a remote radio gateway — without a display server, browser, or
audio output device.

- **Canonical entry point:** `--preset headless` in `cli.py`. This preset
  brings up rigctld (and only rigctld); it does not assume a Web UI consumer,
  a graphical environment, or local audio playback.
- **No startup check** in the open-core may require `$DISPLAY`, a working
  audio backend, a browser, or any GUI library being importable.
- **No feature gate** may depend on a graphical environment. Any feature that
  would degrade silently or fail loudly when run headless either belongs in
  Pro, or must be made graceful (skip, log, continue) in open-core.
- New CLI presets, daemons, and entry points must be tested against a
  no-display environment before they are accepted.

Headless is the deployment mode that distinguishes rigplane from the dozens
of GUI-only Icom utilities. Breaking it would be breaking the product.

---

## 4. No hollowing out

We do not remove an existing open-core feature so that it can be re-sold in
Pro. This is the canonical bait-and-switch of open-core projects, and it is
forbidden here.

The bar for moving (or keeping) something Pro-only is narrow:

> **Pro-only test:** the feature requires desktop-only system integration
> that fundamentally cannot live in a server.

Examples that pass the test (acceptable Pro features):

- RC-28 USB HID controller integration — needs a desktop OS USB stack.
- OS-level virtual audio routing (BlackHole, VB-Cable, loopback devices) —
  needs OS audio driver access.
- Tauri global hotkeys, system tray, OS notifications — needs a desktop
  shell.
- Signed installer, code-signing, license activation flow — needs a native
  binary boundary.

Examples that fail the test (must stay open):

- Radio control over LAN/USB (the entire CI-V command surface).
- Audio streaming over the network (PCM and Opus paths).
- Scope rendering and waterfall display.
- Web UI, skins, panels, and frontend runtime.
- Memory channels, presets, profiles, configuration.
- rigctld bridge.

Generic radio functionality that any consumer (script, hamlib client, web
browser, mobile app) might want — stays open.

---

## 5. Radio Protocol and capability protocols are the primary boundary

The Pro layer consumes rigplane through one stable surface: the
`Radio` protocol in `src/rigplane/radio_protocol.py` plus the capability
protocols (`LevelsCapable`, `MetersCapable`, `ScopeCapable`,
`AudioCapable`, etc.).

This surface is **tier-1 stable** under the public API stability commitment
documented in `docs/api/public-api-surface.md`.

- Pro builds against this contract. Breaking it breaks Pro.
- Any change to a capability protocol — adding a method, renaming a method,
  altering a return type, narrowing the accepted argument range — is a
  breaking change.
- Such changes are negotiated in an issue **before** a PR opens, not after.
  The discussion answers: who depends on this, what is the migration
  story, do we need a deprecation window.
- Internal helpers under `radios/`, `commands/`, `commander.py`, and
  transport are **not** part of the boundary. They may change freely as
  long as the protocol surface is preserved.

When in doubt about whether a refactor crosses the boundary, ask in an
issue.

---

## 6. `local-extensions/` is a stable Pro-facing contract

`frontend/src/lib/local-extensions/` exposes a versioned host API used by
Pro to inject UI extensions (panels, dock items, keyboard scopes, manifest
entries) into the open-core frontend.

- The current version constant is `LOCAL_EXTENSION_HOST_API_VERSION = 1` in
  `host-api.ts`.
- Pro consumers call `installLocalExtensionHostApi()` and import types from
  `host-api.ts` and `manifest.ts`.
- **Additive changes only** between major versions. Adding a new method,
  callback, or manifest field is fine. Removing one, renaming one, or
  changing an existing signature requires bumping
  `LOCAL_EXTENSION_HOST_API_VERSION`.
- The full type list is documented in `docs/api/public-api-surface.md`
  under "Frontend extension host API (Pro-facing)".

Treat `local-extensions/` with the same discipline as the Python `Radio`
protocol: it has external consumers, the changes ship to people who paid
money, and silent breakage is not an option.

---

## 7. The frontend renders in four environments

The rigplane frontend ships into more than just a developer's Chrome tab.
It must render correctly in:

1. **Browsers** — Chrome, Safari, Firefox.
2. **Tauri WebViews** —
   - macOS: WKWebView (close to Safari).
   - Windows: WebView2 (close to Edge / Chromium).
   - Linux: WebKitGTK (closer to old Safari, the most restrictive of the
     four).

WebKitGTK is the floor. If a CSS feature, a Web API, or a font-rendering
trick works in Chrome and breaks in WebKitGTK, the open-core frontend
breaks for every Linux Tauri user — including the eventual Pro user on
Linux.

**Practical consequences:**

- Verify exotic CSS (`backdrop-filter`, modern `color-mix()` functions,
  newer `@property` registrations, container queries with deep nesting)
  against WebKitGTK before merging — especially in the LCD-skin
  typography panels (`AmberCockpit`, `AmberScope`, `AmberTelemetryStrip`),
  which lean heavily on font and shadow effects.
- Check new Web APIs (`AudioWorklet`, `WebCodecs`, `WebTransport`,
  `OffscreenCanvas`) for WebKitGTK availability before adopting.
- **Use relative paths only.** No hardcoded `localhost`, `127.0.0.1`, or
  absolute URLs in the production frontend. The UI may be served through
  Pro's reverse proxy at `127.0.0.1:8470`, behind a tunnel, or from a
  remote host; absolute URLs break all of these.
- Stay proxy-friendly. WebSocket and HTTP routes resolve relative to the
  current origin.

---

## 8. Form B (lib + app two-package split) — deferred

Per the architecture review in
`research/2026-04-27-architecture/05-recommendations.md`, the option of
splitting rigplane into separate `rigplane-lib` and `rigplane-app` packages
(Form B) remains deferred.

**Today.** Pro uses rigplane as a separate Python server process via
HTTP/WebSocket on `127.0.0.1:8470`, plus a narrow library import surface
(`audio.backend`, `audio.dsp`, `dsp.*`) for the small handful of helpers
that don't make sense as RPC. That surface is narrow enough that the
single-package form (Form F) remains sufficient.

**Triggers that activate Form B.** Either of:

- Pro embeds rigplane in-process for the radio loop (skipping the
  server-process indirection), and the lib/app boundary becomes load-bearing
  for cold-start and packaging size.
- A second downstream Python consumer of rigplane emerges (a third-party
  application, a research tool, a different commercial product) that needs
  the library without the Web UI / rigctld application code.

Until one of those happens, Form F holds. Revisit when triggers activate.

---

## See also

- `docs/api/public-api-surface.md` — the canonical list of tier-1 stable
  Python and frontend surfaces.
- `docs/PROJECT.md` — overall project context.
- `docs/plans/2026-04-12-target-frontend-architecture.md` — frontend
  layering ADR referenced by `CLAUDE.md`.
- `research/2026-04-27-architecture/05-recommendations.md` — the form-F
  vs form-B analysis backing section 8.
