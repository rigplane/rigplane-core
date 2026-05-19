# i18n visual smoke (RP-ML-006)

Localization layout-regression smoke for Core. Drives the built static
frontend with a stubbed backend (no live radio, no Tower) under three
locales and two viewports, captures one screenshot per surface.

## What this catches

- A localized string is wider than its container — button labels clipped,
  status bar text overflowing, modal headers wrapping into icons.
- A locale catalog is missing a key extracted by RP-ML-005 (renders
  `[missing:<key>]`).
- A reason-coded toast resolves to a raw template instead of the
  translated message (renders `${var}`).
- A glossary token (MAIN/SUB/USB/LSB/CW/VFO/RTTY, brand names, radio
  model identifiers) is accidentally translated away under `qps-ploc`.

## What this does NOT do

- Diff screenshot pixels against committed baselines automatically. The
  baseline PNGs are reference artifacts the reviewer inspects after a
  layout change, not gates. RP-ML-006 keeps the assertion floor on text
  content; a future ticket may layer in `toHaveScreenshot()` comparison
  with a strict tolerance once the layout stabilizes.
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

## Updating baselines

Baselines live in `__screenshots__/i18n/<locale>/<surface>-<viewport>.png`.
After a deliberate layout or copy change:

```bash
cd frontend
npm run test:e2e:i18n
git status __screenshots__/i18n/
# Visually inspect the diff before committing.
git add tests/e2e/i18n/__screenshots__/i18n/
```

PRs that change a Core string MUST include the regenerated baselines.

## Scope split with RP-ML-013A

| Ticket     | Tooling          | Cost  | Catches                                         |
|------------|------------------|-------|-------------------------------------------------|
| RP-ML-013A | vitest + jsdom   | <2 s  | runtime transform, pseudo expansion ratio       |
| RP-ML-006  | Playwright       | ~60 s | layout overflow, missing-key paint, glossary    |

The unit test is the cheap CI floor; the Playwright pack is the visual
backstop for full-page layout regressions a string-level test cannot see.
