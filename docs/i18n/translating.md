# Contributing translations to RigPlane Core

Thank you for helping localize RigPlane Core. This document is the
self-contained guide for community translators. You do not need to read
the strategy glossary or run any frontend tooling to add or update a
translation.

If anything here is unclear, please open an issue at
<https://github.com/rigplane/rigplane-core/issues> with the `i18n` label.

## TL;DR

1. Fork `rigplane/rigplane-core` on GitHub.
2. Copy `frontend/src/lib/i18n/locales/en-US.json` to a new file named
   after your locale, for example `de-DE.json`, `fr-FR.json`, `pt-BR.json`.
   Use a BCP-47 tag (`language-REGION`).
3. Edit the values in the new file. Keep the keys exactly the same.
4. Leave the `$schema` documentation wrapper at the top — it is ignored at
   runtime and reminds future translators of the rules.
5. Open a pull request. Mention the locale in the title:
   `i18n: add de-DE translation`.

No `npm install`, no Node.js, no Svelte/Vite tooling required to
translate. The repository's CI will validate your file.

## File format

Each locale file is plain JSON. Keys are dot-separated identifiers
(e.g. `core.statusbar.connection.connected`); values are translated
strings. The English source catalog is the single source of truth — only
keys that exist in `en-US.json` are valid.

Example (excerpt):

```json
{
  "core.statusbar.connection.connected": "Connected",
  "core.statusbar.connection.connectingTo": "Connecting to {radio} on {transport}…"
}
```

Same key, translated to Japanese:

```json
{
  "core.statusbar.connection.connected": "接続済み",
  "core.statusbar.connection.connectingTo": "{radio} に {transport} で接続中…"
}
```

You may omit keys you have not translated yet. Missing keys fall back to
English silently.

## Placeholder syntax

Variables inside translations use **named placeholders** wrapped in
curly braces:

```
"Connecting to {radio} on {transport}…"
```

Rules:

- Every placeholder you receive in English must appear in your
  translation. If `{radio}` is in the English string, your translation
  must include `{radio}` somewhere.
- You may reorder placeholders freely. That is the whole point.
- A literal `{` is written as `'{'` (single quote, opening brace, single
  quote — three characters). This is unusual; you will almost never need
  it.
- Do not introduce new placeholders. Translators cannot invent variables;
  if you need a placeholder the source does not have, open an issue.

## Plural forms

Some keys come in plural variants, identified by suffixes from CLDR
(<https://cldr.unicode.org/index/cldr-spec/plural-rules>):

- `.zero`, `.one`, `.two`, `.few`, `.many`, `.other`

Example:

```json
{
  "core.diagnostics.attachedFiles.one": "1 file attached",
  "core.diagnostics.attachedFiles.other": "{count} files attached"
}
```

For each base key, your locale should provide the forms its grammar
requires. The runtime always falls back to `.other` if a more specific
form is missing, so you can start with just `.other` and add the others
later. Japanese (`ja-JP`) only needs `.other` — that is correct, not a
bug.

## Non-translatable tokens

Some tokens are part of RigPlane's machine-readable contract or its
brand identity. They MUST appear verbatim in every locale, even inside
sentences in your language.

### Brand names

Keep the exact spelling, casing, and Latin letters:

- RigPlane, RigPlane Core, RigPlane Pro
- Tower, RigPlane Pro Companion
- `rigctld` (lowercase, the executable name)
- Hamlib (the library, capital H)
- CI-V (Icom Communications Interface V)

Do not translate, transliterate, or pluralize these terms. In Japanese,
do not wrap them in 「」 brackets.

### Radio abbreviations

These are international amateur/professional radio abbreviations that
operators recognize worldwide. Keep verbatim, in uppercase, without
expansion:

- VFO, MAIN, SUB, A, B, A=B, M-VFO
- PTT, TX, RX, MOX
- RIT, XIT, IF Shift, Notch, BPF, Roofing
- CW, SSB, USB, LSB, AM, FM, NFM, WFM, DV, DD, RTTY, PSK, DATA, DIGITAL
- AGC (FAST/MID/SLOW/OFF), DSP, NB, NR, NF, MN, VOX
- SWR, ALC, COMP, MIC, MON, PWR
- S-meter, squelch (the abbreviation; per-locale forms like スケルチ are
  allowed in body copy — see `glossary.squelch`)
- Q-codes: QSO, QRZ, QRM, QRN, QRP, QSL, QTH, QSY
- Band names: 160m, 80m, 60m, 40m, 30m, 20m, 17m, 15m, 12m, 10m, 6m, 4m,
  2m, 70cm, 23cm
- Units: Hz, kHz, MHz, GHz, dB, dBm, ms, s

Surrounding prose may be translated — only the token itself stays in
English.

### Glossary tokens

Some everyday radio nouns have widely-used local forms (e.g. Japanese
コールサイン for "callsign"). These live in the `glossary.*` namespace:

- `glossary.callsign`
- `glossary.squelch`
- `glossary.bandPlan`

If you add a locale, you may translate these. They are explicitly
intended to be the locale community's word, not a verbatim English copy.

### Things that look like prose but are not

If you ever see a string that contains a `type` field name, an HTTP
status, a JSON key, a CLI flag (`--port`), an environment variable
(`RIGPLANE_*`), or a Python module/class name — it should not be in the
catalog. Please open an issue rather than translating it.

## Testing your translation locally (optional)

You do not need this step to contribute. CI will run all checks on your
PR. If you do want to run the tests yourself:

```bash
cd frontend
npm install
npm test -- src/lib/i18n
```

The pseudo-locale (`qps-ploc`) is a developer/QA tool that wraps every
catalog string in `⟦…⟧` to visualize layout. It is not a real locale and
never ships to users.

## What CI checks

The pull-request lint will report (RP-ML-013A wires the exact CI surface;
the rules below are the contract):

- Invalid JSON.
- Keys present in your file that are not in `en-US.json` (typos).
- Placeholders that do not match the English source (extra or missing
  `{name}`s for the same key).
- Translated glossary tokens that should have stayed verbatim (brand
  names, radio abbreviations).
- File-encoding issues (must be UTF-8 without BOM).

## Review process

A new or improved translation needs a second community speaker of the
locale to review the PR before merge. RigPlane maintainers will:

- Verify the file is valid and complete.
- Check that brand names and radio abbreviations are unchanged.
- Run the pseudo-locale and visual smoke checks.
- Tag a reviewer who reads the locale.

We are not native speakers of every locale we ship; thank you for caring
about the details.

## Where the strings live and what they mean

The English source catalog `en-US.json` is organized by top-level
namespace. Each namespace maps to a surface of the Core web UI a
translator will recognize when they open the app:

- `core.statusbar.*` — the persistent status bar at the bottom of the
  shell (connection state, audio/scope link health, the power and
  Settings/Report buttons).
- `core.settings.*` — the Settings modal (heading, section labels,
  close/save controls, the language card).
- `core.toast.*` — toast notifications (transient pop-ups that announce
  events such as "radio connected" or "audio bridge started").
- `core.diagnostics.*` — the "Send diagnostic report" dialog (form
  labels, preview metadata, consent line, success/error states).
- `core.app.*` — the top-level app shell (backend-error banner, install
  prompt copy, retry messages).
- `core.mobile.*` — mobile bottom-sheet titles and the chip bar that
  switches between mobile sections.
- `core.connection.*` — connection and power overlays ("Radio is
  powered off", "Scope disconnected — reconnecting…", "Audio link lost
  — reconnecting…").
- `common.action.*` — reusable button labels (`Close`, `Retry`,
  `Cancel`) shared across surfaces.
- `glossary.*` — radio terms that have a community-standard rendering
  per locale (`callsign`, `squelch`, `bandPlan` …). See the Glossary
  tokens section above; per-locale conventions are recorded in the
  strategy glossary §2.D.
- `a11y.*` — screen-reader-only labels. Rare in Core today, documented
  for completeness so new keys land in a predictable namespace.

The full per-component map (with file/line references) lives in
`docs/i18n/core-string-inventory.md`. Translators do not need to read
it, but if you are trying to figure out *where in the UI* a key
renders, that is the document to grep.

## How to pick a locale tag

Locale files are named with a [BCP-47](https://www.rfc-editor.org/info/bcp47)
tag of the form `language-REGION`:

- `de-DE` — German (Germany)
- `fr-FR` — French (France)
- `es-ES` — Spanish (Spain) — use `es-MX`, `es-AR`, etc. for regional
  variants
- `pt-BR` — Portuguese (Brazil)
- `zh-CN` — Chinese (Simplified, mainland China) — use `zh-TW` for
  Traditional

The language part is lowercase, the region part is uppercase. If your
locale has CLDR plural rules beyond `.one`/`.other` (Russian, Polish,
Arabic, …) some keys may also need `.few`/`.many` forms; see the
Plural forms section above.

## Workflow step-by-step

1. Fork `rigplane/rigplane-core` on GitHub and create a branch
   (e.g. `i18n/de-DE`).
2. Copy `frontend/src/lib/i18n/locales/en-US.json` to
   `frontend/src/lib/i18n/locales/<your-locale>.json`. Keep the keys
   exactly the same; only translate the values.
3. Validate that your file is still valid JSON. Any editor with JSON
   linting works; you can also paste the contents into
   <https://jsonlint.com> to check.
4. Open a pull request. Title it `i18n: add <locale> translation` and
   add the `i18n` label so locale reviewers see it. Mention in the
   description whether you would like a second native speaker to
   review before merge — the maintainers will tag one.
5. Wait for CI. If anything fails, the lint output points to the
   exact key and token; fix locally and push again.

### What CI failures look like

Two of the most common failures, with example output:

Missing or unknown key:

```
ERROR  frontend/src/lib/i18n/locales/de-DE.json
  Unknown key not present in en-US.json:
    core.statusbar.connection.connectingTo  (line 14)
```

Usually means a typo in the key — copy the exact key from `en-US.json`.

Broken placeholder:

```
ERROR  frontend/src/lib/i18n/locales/de-DE.json
  Placeholder mismatch for key core.statusbar.connection.connectingTo:
    en-US uses {radio}, {transport}
    de-DE uses {radio} only
```

Means your translation dropped `{transport}` (or renamed it). Every
placeholder in the English string must appear in the translation; no
new placeholders may be introduced.

### Asking for review

Add the `i18n` label to your PR. If you are not a maintainer, ask in
the PR description for a second speaker of your locale to review. The
maintainers will tag one if they can. Native-speaker review is
expected for any non-trivial translation before merge.

## Lessons from the ru-RU pilot

A few things that came up during the Russian pilot translation
(rigplane-core#1534) and are worth knowing up front:

- **Mode codes stay English uppercase.** `SSB`, `USB`, `LSB`, `AM`,
  `FM`, `CW`, `DATA`, etc. remain in ASCII even inside translated body
  copy. They are radio operator vocabulary, not generic English nouns.
- **Plural form coverage is bounded by the source.** CI accepts a
  locale's `.few` or `.many` form only when `en-US.json` provides the
  matching key with the same placeholders. If you need a richer plural
  set than the source offers for a given key, the runtime will fall
  back to `.other`; do not invent new plural-form keys to work around
  it (lint will reject them as "unknown key"). For now, phrase
  `.other` so it reads naturally for all non-`.one` counts.
- **Server-rendered toasts use reason codes.** The Python server emits
  a stable code (e.g. `core.toast.radio_connected`); the user-visible
  message is the catalog value for that key. You translate the catalog
  value, not the code.
- **Glossary nouns vs. radio abbreviations.** Some everyday radio
  nouns (`callsign`, `squelch`, `band plan`) have a locale-community
  word that is preferred over the English term in prose — those live
  under `glossary.*` and you should use the locale-community word
  there. The bare radio abbreviation (`squelch` as a control label) is
  still glossary-stable English elsewhere in the catalog.

## What you cannot change without an issue first

The following are *code* changes, not translation contributions. If you
think one of them is needed, please open an issue with the `i18n` label
and a maintainer will pick it up:

- Adding new translatable keys (en-US.json is the single source of
  truth; new keys must come from the code that uses them).
- Renaming existing keys.
- Reordering keys (no semantic effect, but creates needless merge
  conflicts).
- Editing the `$schema` documentation wrapper at the top of each file.
- Anything outside `frontend/src/lib/i18n/locales/`.

## Quick reference: what to translate vs leave alone

| Translate | Leave alone |
| --- | --- |
| Prose, labels, error messages, descriptions | Brand names (RigPlane, RigPlane Core, RigPlane Pro, Tower) |
| Verbs in button labels (Save, Close, Retry, Cancel) | Radio abbreviations (VFO, MAIN, SUB, PTT, TX/RX, CW, SSB, USB, LSB, AM, FM, DATA, AGC, DSP, NB, NR, VOX, SWR, ALC, S-meter) |
| Glossary nouns referenced via `glossary.*` keys | Q-codes (QSO, QRZ, QRM, QRN, QRP, QSL, QTH, QSY) |
| aria-label, title, placeholder attribute values | Units (Hz, kHz, MHz, GHz, dB, dBm, ms, s, B, KiB, MiB) |
| `core.toast.*` reason-code messages (the message text, not the code) | Snake_case identifiers (these are codes/keys, not catalog values) |
| Helper / consent / privacy prose around technical labels | Example placeholders in form fields (`you@example.com`, `N0CALL`, `https://github.com/.../issues/123`) |

When in doubt, leave it alone and ask in the PR description — over-
translating an abbreviation is harder to spot in review than missing
prose.

## Companion docs

These live alongside this guide:

- [Core string inventory](./core-string-inventory.md) — exhaustive map of
  every translatable string by UI surface (status bar, settings, dialogs,
  panels, mobile sheets, accessibility, etc.) with file/line refs. Useful
  when you want to know "where will this catalog key actually show up?"
- [Locale preference contract](./locale-contract.md) — how Core and
  embedded Pro coordinate the active locale (URL query parameter,
  `localStorage` envelope, precedence rules). Translators rarely need
  this, but UI developers integrating new surfaces do.

The strategy-level glossary (which terms are translatable, plural rules,
per-locale §2.D reserved terms for `ja-JP` and `ru-RU`, etc.) lives at
`rigplane-strategy/docs/i18n/glossary-and-policy.md` — a private repo,
not on the published docs site. The relevant rules are mirrored in this
guide; you do not need to read the strategy doc.
