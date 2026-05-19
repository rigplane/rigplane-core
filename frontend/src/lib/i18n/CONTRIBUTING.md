# Contributing translations to RigPlane Core

> **The canonical translator guide lives at <https://rigplane.dev/i18n/translating/>
> (source: [`docs/i18n/translating.md`](../../../../docs/i18n/translating.md)).**
>
> This stub stays here so contributors who discover the catalogs via the
> `locales/` folder have a pointer back to the full guide.

## Quick TL;DR

1. Fork `rigplane/rigplane-core` on GitHub.
2. Copy `frontend/src/lib/i18n/locales/en-US.json` to a new file named after
   your locale (BCP-47), e.g. `de-DE.json`, `fr-FR.json`, `pt-BR.json`.
3. Edit the values in the new file. Keep the keys exactly the same.
4. Leave the `$schema` documentation wrapper at the top.
5. Open a pull request: `i18n: add de-DE translation`.

No `npm install`, no Node, no Svelte/Vite tooling required — CI validates
your file. See the canonical guide for placeholder syntax, plural rules,
non-translatable tokens (brand names, radio abbreviations, units), the
glossary namespace, lessons from the ru-RU pilot, and the review process.

Companion docs:

- [Translator guide](../../../../docs/i18n/translating.md) — the full document.
- [Core string inventory](../../../../docs/i18n/core-string-inventory.md) —
  where every translatable string lives across the codebase.
- [Locale preference contract](../../../../docs/i18n/locale-contract.md) —
  how Core and embedded Pro coordinate the active locale.

Open an issue at <https://github.com/rigplane/rigplane-core/issues> with the
`i18n` label if anything is unclear.
