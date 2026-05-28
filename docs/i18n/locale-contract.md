---
robots: noindex, follow
---

# Cross-app locale preference contract (RP-ML-012)

Status: Phase 3 wave 2 — public-core.
Scope: protocol-level propagation contract only. No commercial framing, no
Tower involvement. See strategy `docs/i18n/glossary-and-policy.md` §5.4 for
the precedence policy this contract implements (sanitized per §7 row 5.4 —
"scope to the propagation contract; do not surface Tower or commercial
framing").

## Why this contract exists

RigPlane Pro embeds the Core web UI either via a Tauri WebView, an
external browser window, or a managed-station kiosk surface. When Pro
holds a user-explicit locale preference (set in the Pro Settings UI),
the embedded Core UI must honor that preference instead of falling back
to the browser locale. Conversely, when Core is launched standalone or
without any Pro hint, its existing per-installation explicit choice
(`localStorage["rigplane.i18n.locale"]`) and detection logic must keep
working unchanged.

The contract intentionally avoids any network dependency. Locale
selection is local-first and works offline. Tower is never consulted.

## Envelope shape

A single small JSON envelope is the unit of propagation:

```json
{
  "locale": "ja-JP",
  "source": "pro-shell",
  "updatedAt": "2026-05-19T18:00:00Z",
  "supportedLocales": ["en-US", "ja-JP", "qps-ploc"]
}
```

Fields:

- `locale` — BCP-47 tag. Must be one of `supportedLocales`. Only the
  locales bundled with Core's runtime are accepted; anything else is
  rejected and Core falls back to its standalone resolution.
- `source` — one of:
  - `"pro-shell"` — written by Pro from its own Settings UI;
  - `"core-explicit"` — informational; for symmetry when Core writes to
    its own contract surface (Core already uses
    `rigplane.i18n.locale`, so this value is reserved for future use);
  - `"browser"` — informational fallback marker;
  - `"fallback"` — informational marker for the en-US fallback.
- `updatedAt` — ISO-8601 UTC instant. The reader does not enforce
  freshness today; the field exists so a future revision can ignore
  stale envelopes (for example, a Pro reinstall that left a stale
  `localStorage` key behind).
- `supportedLocales` — the locales the writer believes are supported.
  Informational; the Core reader validates `locale` against its own
  bundled list, not the writer's list, because Core is the runtime
  authority for what it can render.

The envelope is JSON-encoded as a single value at every transport.

## Transport surfaces

Core accepts the contract from Pro through three surfaces. A writer
(Pro) MAY use any subset; a reader (Core) checks them in fixed order.

### Surface A — URL query parameter `?locale=<bcp47>` (required)

The smallest, most testable surface. Pro launches embedded or external
Core URLs with `?locale=<tag>`, for example:

```
http://127.0.0.1:8080/?locale=ja-JP
```

Core reads `URLSearchParams` exactly once at module init. Subsequent
navigations do not re-read this parameter — it is a boot signal, not a
session state. This is intentional so a user who later picks a
different locale via the Core LanguageSelector is not stomped on every
route change.

The query parameter form carries only `locale`. The `source`,
`updatedAt`, and `supportedLocales` fields are synthesized by the
reader (source `"pro-shell"`, `updatedAt` = read time).

### Surface B — `localStorage` key `rigplane.i18n.proLocale.v1`

Pro MAY write the full envelope as JSON to:

```
localStorage["rigplane.i18n.proLocale.v1"]
```

This surface is useful when Pro and Core share the same WebView storage
partition. The Core reader parses the envelope and validates each
field. Malformed JSON or a missing/invalid `locale` is silently
ignored. This surface is read once at boot, like surface A.

### Surface C — `postMessage` from a Tauri host (deferred)

Reserved for a future revision. The shape MUST be the same envelope
above wrapped in `{ type: "rigplane.i18n.localePreference", payload: <envelope> }`.
Not implemented in RP-ML-012A. Pro's RP-ML-012B is not required to
implement this surface either; it is documented to keep the contract
extensible.

## Read precedence (Core side)

When Core boots, it computes the active locale using:

1. Surface A: `?locale=<bcp47>` in the current URL.
2. Surface B: `rigplane.i18n.proLocale.v1` in `localStorage`.
3. Explicit Core setting: existing `rigplane.i18n.locale` in
   `localStorage` (owned by the Core LanguageSelector).
4. Browser locale: `navigator.languages` / `navigator.language`,
   narrowed to a supported locale.
5. `en-US` fallback.

Rungs 1 and 2 are the "Pro setting" rung from strategy §5.4. Rungs 3–5
are the existing Core behavior; they are not changed by this contract.

An external source (rungs 1 or 2) is accepted only if its `locale`
parses as a member of Core's bundled `SUPPORTED_LOCALES`. An invalid
tag (typo, unsupported region, removed locale) is ignored entirely and
Core falls through to rung 3.

The pseudo-locale `qps-ploc` is accepted from a Pro hint only as a
deliberate developer opt-in. The reader does not special-case it; if a
Pro build is configured to forward `qps-ploc`, the Core side honors it.

## Standalone behavior is preserved

When neither surface A nor surface B yields a valid locale, the boot
path is a no-op against the existing store and Core behaves exactly as
before RP-ML-012A. The contract module never writes to the explicit
Core key `rigplane.i18n.locale`; that key remains owned by the
LanguageSelector and is the user's stable preference across launches.

## Dev-mode diagnostics

In development builds (`import.meta.env.PROD === false`) the contract
module logs the resolved source on boot via `console.info`, prefixed
with `[i18n]`. Production builds are silent. This is to support manual
QA of precedence without shipping a visible UI affordance.

## Cross-side coordination

The Pro side (RP-ML-012B / `rigplane-pro` #882) implements the writer.
Both sides reference this document as the single source of truth for
the envelope shape and transport surfaces. Any change to envelope
field names or transport keys is a coordinated change across both
repos.
