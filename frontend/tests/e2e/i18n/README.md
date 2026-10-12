# i18n visual smoke (RP-ML-006)

Localization smoke for Core. Drives the built static
frontend with a stubbed backend (no live radio, no Tower) under three
locales and two viewports.

## What this catches

- A locale catalog is missing a key extracted by RP-ML-005 (renders
  `[missing:<key>]`).
- A reason-coded toast resolves to a raw template instead of the
  translated message (renders `${var}`).
- A glossary token (MAIN/SUB/USB/LSB/VFO/RTTY, checked on the desktop
  layout) is accidentally translated away under `qps-ploc`.

## What this does NOT do

- Cover Tower-side localization, Pro-side localization, or audio/scope
  panels (those are outside RP-ML-005's extracted P0 set).

## Run locally

```bash
cd frontend
npm run build                # produce dist/ — Playwright serves this
npx playwright install chromium  # one-time
npm run test:e2e:i18n
```

The suite spins up `vite preview` on port 4173 (override with
`RP_I18N_PREVIEW_PORT`). It takes around 60-90 seconds on a warm cache.

## Locale switch path

Two mechanisms, both supported by the RP-ML-012A locale-contract:

1. `localStorage["rigplane.i18n.locale"]` set via `page.addInitScript`
   before the page loads (primary).
2. `?locale=<bcp47>` URL query param (fallback if storage write fails).

We never click through the LanguageSelector for every test because that
introduces UI navigation costs and flakes the moment the selector layout
shifts.

## Backend stub

- `page.route('**/api/v1/state'|'capabilities'|'info')` returns frozen
  fixtures from `fixtures.ts`. Anything else under `/api/v1/` falls back
  to `{}` so no surface 5xxs.
- `window.WebSocket` is replaced before the bundle loads with a polyfill
  that auto-opens, swallows outbound frames, and exposes
  `window.__i18nWsDispatch(msg)` so tests can deliver `notification`
  frames into the Toast surface.

## Scope split with RP-ML-013A

| Ticket     | Tooling          | Cost  | Catches                                         |
|------------|------------------|-------|-------------------------------------------------|
| RP-ML-013A | vitest + jsdom   | <2 s  | runtime transform, pseudo expansion ratio       |
| RP-ML-006  | Playwright       | ~60 s | missing-key paint, glossary                     |
