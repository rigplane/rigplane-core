---
robots: noindex, follow
---

# Faceplate locale-invariance policy (MOR-1450)

Status: adopted — owner ruling, MOR-1450 guided walkthrough scenario 10,
extended by two follow-up owner rulings during review (see "MOR-1450
audit" below).
Scope: `frontend/src/lib/i18n/locales/*.json` catalog authoring policy.
Companion docs: [Translator guide](./translating.md) (contributor-facing
rules), [Core string inventory](./core-string-inventory.md) (where each
string lives), [Locale preference contract](./locale-contract.md) (how the
active locale is resolved).

## Why this exists

The ru-RU and ja-JP pilot catalogs pre-dated this policy and translated a
handful of instrument-panel readouts into the target language — for example
`core.vfo.split.label` + `core.vfo.state.off` rendering as "Сплит:
выключено" instead of "Split: off". During the MOR-1450 walkthrough the
owner ruled this out of scope for translation, with the rationale (in
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
  (e.g. `BAND`, `SCAN`, `SETUP`, `MOD IN`, `RF POWER`, `TX SETTINGS`)

This list is illustrative, not exhaustive — the test is whether the string
names a control or reading a physical Icom/Yaesu/Kenwood transceiver would
print in English, not whether it happens to already exist in the catalog.

**Scope note:** this policy — and the `FACEPLATE_INVARIANT_KEYS` guard list
below — covers i18n **catalog** keys only. Most of the vocabulary above
(mode names like `USB`/`CW`, scope controls like `SPAN`/`REF`, most DSP
abbreviations) is hardcoded directly in Svelte components rather than
routed through `t()` — see `core-string-inventory.md`'s P0/P1/P2 batches,
most of which have not been extracted into the catalog yet. Hardcoded
English literals need no catalog guard; there is nothing for a translator
to change. The guard only protects the subset that already flows through
the i18n runtime.

### Value words

A faceplate readout is usually a label paired with a value word, and the
value word follows the same rule as the label. `OFF` stays `OFF`, not
`Выключено`; `ON` stays `ON`. Example as shipped today (the owner ruled to
leave the current catalog casing as-is — `core.vfo.split.label` is `Split`,
`core.vfo.state.off` is `off` — rather than force a new casing convention;
a future UI pass may change how this renders visually):

```
"Сплит: выключено"   ->   "Split: off"
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
этой частоте" translates. Same pattern for the quick-action tooltips
`core.vfo.ops.quickSplit` / `core.vfo.ops.quickDualWatch` ("Быстрый Split" /
"Быстрый Dual watch" in ru-RU): the action verb translates, the faceplate
token inside it does not. Note the embedded token matches the exact casing
en-US uses in that specific string (lowercase `split` mid-sentence in
"Quick split", vs. capitalized `Split` for the standalone label) — the
`i18n-check.mjs` substring lint is case-sensitive, so the translation must
reuse whichever casing the English source chose for that string, not the
"canonical" label casing. See `core-string-inventory.md`'s note that
glossary tokens "may appear inside translatable copy" for the general
version of this rule; this document narrows it specifically to the
faceplate domain and adds the value-word corollary above.

Two keys need a specific note because their invariance rationale differs
from "it's a faceplate readout":

- `core.mobile.setupButton` (`"Setup"`) is an **aria-label only** — the
  visible button content is an icon, not text. It is kept English to match
  the visible title of the SETUP sheet it opens (`core.mobile.sheet.setup`),
  which satisfies WCAG 2.5.3 Label in Name: the accessible name must
  contain the visible label text a sighted user (or a voice-control user
  reading the screen) would use to refer to the control. This is an
  accessibility requirement, not a faceplate-recognition one — but it
  produces the same invariant value, so the key is still on
  `FACEPLATE_INVARIANT_KEYS`.
- `core.mobile.sheet.setup` (`"SETUP"`) is **dual-use**: it is both the
  chip/button label that mirrors a physical faceplate `SETUP` control *and*
  a bottom-sheet dialog title. Invariance was chosen because the button
  reading wins today. If the owner later wants the dialog *title* localized
  independently of the button label, the fix is to split this into two
  catalog keys (one for the button, one for the sheet `title=`) rather than
  relaxing the current key — tracked as a follow-up, not done here.

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
`frontend/scripts/i18n-check.mjs`'s existing glossary-token lint
(`GLOSSARY_TOKENS`), which only verifies that a token (e.g. `VFO`, `TX`,
and — as of this policy — `Split`/`split`/`Dual watch`/`dual watch`) still
appears *somewhere* inside a translated string — the designed gate for
faceplate tokens embedded inside otherwise-translatable prose, such as the
quick-action tooltips above. The keys on `FACEPLATE_INVARIANT_KEYS` *are*
the faceplate readout in full, so the entire value must match, not just
contain the token.

## MOR-1450 audit

### Round 1 (initial walkthrough)

Auditing the ru-RU and ja-JP catalogs against this policy found the
following faceplate-domain keys translated out of English, reverted to the
en-US value:

`core.statusbar.power.labelOn`, `core.statusbar.power.labelOff`,
`core.statusbar.nowPlaying.live`, `core.mobile.chip.band`,
`core.mobile.chip.scan`, `core.mobile.sheet.setup`,
`core.mobile.setupButton`, `core.vfo.split.label`,
`core.vfo.dualWatch.label`, `core.vfo.state.on`, `core.vfo.state.off`,
`core.vfo.state.unknown`, `core.vfo.txTarget.label` (ru-RU also needed the
two power labels; ja-JP already had those two correct, which is what
confirmed the intended policy rather than a judgment call).

### Round 2 (independent review + follow-up owner rulings)

Independent review found two more direct violations round 1 missed, plus
one regression round 1's own revert had introduced:

- `core.vfo.ops.quickSplit` / `core.vfo.ops.quickDualWatch` — the faceplate
  token was transliterated *inside* translatable tooltip prose ("Быстрый
  сплит" / "クイックスプリット"). Per the "token inside prose" rule these
  keep translating, but the `split` / `dual watch` token inside them must
  stay literal: fixed to "Быстрый split" / "クイック split" and "Быстрый
  dual watch" / "クイック dual watch" (lowercase, matching the exact casing
  en-US uses in "Quick split" / "Quick dual watch"). Added
  `Split`/`split`/`Dual watch`/`dual watch` to `i18n-check.mjs`'s
  `GLOSSARY_TOKENS` so a future regression on this specific pattern fails
  the substring lint (these four entries are core-local additions, not
  sourced from the strategy glossary — see the comment at their
  definition).
- `core.overlay.poweredOff.hint` (ru-RU) still read "кнопку ВКЛ" after the
  power button was relabeled to `ON` — a regression the round-1 revert
  itself created by changing the button's own label without updating a
  string that names it. Fixed to "кнопку ON".
- `core.statusbar.power.toggleOn` / `toggleOff` (ru-RU) — `ON`/`OFF` were
  translated into adjective forms ("ВКЛЮЧЁН"/"ВЫКЛЮЧЕН") instead of staying
  literal inside the sentence. `core-string-inventory.md` uses this exact
  string as its worked example of keeping `ON`/`OFF` literal inside prose,
  and ja-JP's `"トランシーバーは ON"` was already doing this correctly.
  Fixed ru-RU to match: `"...трансивер ON..."` / `"...трансивер OFF..."`.
- `core.toast.readinessNoRadio` (ru-RU/ja-JP) named the setup section by
  its pre-revert local name ("«Настройка»" / "セットアップで") after
  `core.mobile.sheet.setup` was relabeled to `SETUP`. Fixed both to name
  `SETUP`.

Two follow-up owner rulings landed during this round and extend the
invariant set (superseding the round-1 "token inside prose" reading for
these specific keys, since the owner determined they are full faceplate
readouts rather than descriptive dialog titles):

- Mobile bottom-sheet titles `core.mobile.sheet.rfPower`, `.txSettings`,
  `.dataMode`, `.allModes`, `.filterSettings` are fully invariant (`"RF
  POWER"`, `"TX SETTINGS"`, `"DATA MODE"`, `"ALL MODES"`, `"FILTER
  SETTINGS"`) — reverted from ru-RU/ja-JP translations and added to
  `FACEPLATE_INVARIANT_KEYS`.
- `core.mobile.chip.essentials` (`"ESSENTIALS"`) is invariant — this
  supersedes `core-string-inventory.md`'s P0.6 note calling `ESSENTIALS`
  "plain English, not glossary" (translatable). The owner ruling for this
  chip takes precedence over that inventory note going forward.
- `core.mobile.nav.tab.meters` (`"Meters"`) is invariant per the owner
  ruling. `core.mobile.nav.tab.spectrum` / `.controls` ("Spectrum" /
  "Controls") were **not** ruled on and remain localized — pending a future
  ruling, do not treat their current translated state as settled policy.

All round-2 fixes and additions are on `FACEPLATE_INVARIANT_KEYS`
(29 keys total) except the two quick-action tooltip keys, which stay off
the byte-identical guard by design (they carry legitimate translatable
prose) and are instead covered by the `i18n-check.mjs` substring lint.
