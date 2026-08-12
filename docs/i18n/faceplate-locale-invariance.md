---
robots: noindex, follow
---

# Faceplate locale-invariance policy (MOR-1450)

Status: adopted — owner ruling, MOR-1450 guided walkthrough scenario 10.
Scope: `frontend/src/lib/i18n/locales/*.json` catalog authoring policy.
Companion docs: [Translator guide](./translating.md) (contributor-facing
rules), [Core string inventory](./core-string-inventory.md) (where each
string lives), [Locale preference contract](./locale-contract.md) (how the
active locale is resolved).

## Why this exists

The ru-RU and ja-JP pilot catalogs pre-dated this policy and translated a
handful of instrument-panel readouts into the target language — for example
`core.vfo.split.label` + `core.vfo.state.off` rendering as
"СПЛИТ: Выключено" instead of "Split: off". During the MOR-1450 walkthrough
the owner ruled this out of scope for translation, with the rationale (in
English translation): *"We are trying to make the faceplate of a premium,
expensive radio."* A radio operator reading SPLIT, RIT, AGC, or a mode name
recognizes the international vocabulary printed on real transceiver
faceplates; substituting a local word breaks that recognition and reads as
a toy, not an instrument.

## The rule

**Instrument/faceplate terminology is locale-invariant.** It renders
identically — same spelling, same casing — in every bundled locale
catalog, matching the en-US source verbatim. **Auxiliary prose localizes
normally**: dialogs, settings screens, help text, tooltips, and
error/status/refusal messages (the MOR-1422/MOR-1448 string families)
continue to translate as before. This document does not change anything
about that half of the catalog.

### Locale-invariant domain

Faceplate/instrument vocabulary, i.e. anything a real transceiver prints on
its front panel or LCD, including but not limited to:

- Mode names: `USB`, `LSB`, `CW`, `FM`, `AM`, `RTTY`, `DATA`, ...
- `SPLIT`, `RIT`/`XIT`
- Front-end/DSP control abbreviations: `PRE`, `ATT`, `AGC`, `NB`, `NR`, `NF`
- `VFO` A/B, `TX`/`RX`
- `COMP`/`VOX`/`MON`
- S-meter units
- Band names
- Step labels
- Scope controls: `SPAN`, `REF`, `SPEED`, `HOLD`
- Other control/section labels that mirror a physical faceplate control
  (e.g. `BAND`, `SCAN`, `SETUP`, `MOD IN`)

This list is illustrative, not exhaustive — the test is whether the string
names a control or reading a physical Icom/Yaesu/Kenwood transceiver would
print in English, not whether it happens to already exist in the catalog.

### Value words

A faceplate readout is usually a label paired with a value word, and the
value word follows the same rule as the label. `OFF` stays `OFF`, not
`Выключено`; `ON` stays `ON`. Example from the walkthrough:

```
"СПЛИТ: Выключено"   ->   "SPLIT: OFF"
```

Both halves of the pair — the label and the state word — are
locale-invariant, not just the label.

### What still localizes

Everything that is not a direct instrument readout: dialog titles and body
copy, settings-modal sections, help text, tooltips (`title=`/`aria-label`
prose that *describes* a control rather than *being* its readout),
error/status messages, and refusal reasons. A faceplate token may appear
*inside* a translated sentence — the token itself stays literal while the
surrounding prose translates, e.g. `core.mobile.tx.notAllowedFreq` reads
"TX запрещена на этой частоте" in ru-RU: `TX` stays English, "запрещена на
этой частоте" translates. See `core-string-inventory.md`'s note that
glossary tokens "may appear inside translatable copy" for the general
version of this rule; this document narrows it specifically to the
faceplate domain and adds the value-word corollary above.

## Enforcement

The invariant is enforced structurally, not by regex:

- `frontend/src/lib/i18n/faceplate-invariant-keys.ts` exports
  `FACEPLATE_INVARIANT_KEYS`, the single source of truth for which catalog
  keys are faceplate vocabulary.
- `frontend/src/lib/i18n/__tests__/faceplate-invariant.test.ts` asserts
  every key on that list resolves to the *same string* in every bundled
  locale catalog (en-US, ja-JP, ru-RU) — an explicit divergent value fails
  the test; omitting the key from a translation is allowed (it falls back
  to en-US, which trivially satisfies the invariant).

Adding a new faceplate-domain catalog key: add it to
`FACEPLATE_INVARIANT_KEYS` and either mirror the en-US value verbatim in
every locale file, or leave it absent from non-English catalogs.

This is a different, stricter check than
`frontend/scripts/i18n-check.mjs`'s existing glossary-token lint, which
only verifies that a token (e.g. `VFO`, `TX`) still appears *somewhere*
inside a translated string — appropriate for prose that merely mentions a
token. The keys on `FACEPLATE_INVARIANT_KEYS` *are* the faceplate readout,
so the entire value must match.

## MOR-1450 audit

Auditing the ru-RU and ja-JP catalogs against this policy found the
following faceplate-domain keys translated out of English. All were
reverted to the en-US value; see the MOR-1450 pull request for the exact
diff:

`core.statusbar.power.labelOn`, `core.statusbar.power.labelOff`,
`core.statusbar.nowPlaying.live`, `core.mobile.chip.band`,
`core.mobile.chip.scan`, `core.mobile.sheet.setup`,
`core.mobile.setupButton`, `core.vfo.split.label`,
`core.vfo.dualWatch.label`, `core.vfo.state.on`, `core.vfo.state.off`,
`core.vfo.state.unknown`, `core.vfo.txTarget.label` (ru-RU also needed the
two power labels; ja-JP already had those two correct, which is why the
policy above rather than a wholesale re-translation was the fix).
