# segmentline / `peer-split` — integration spec

Status: accepted 2026-09-01. Implements the first layout of the externally
designed amber-LCD bundle under MOR-1162, whose preconditions (MOR-1092,
MOR-1097) are both Done.

Children: MOR-2147 … MOR-2153, plus MOR-2155 and MOR-2156.

This document is a decision record — which direction was chosen and why,
what the externally authored handoff got wrong, and what the owner decided —
not a description of how the code currently works. Two review rounds found
sections of the latter kind going stale as fast as they could be corrected;
they have been cut rather than fixed a third time.

## 1. What this is

A design bundle authored outside the repository — design language
`segmentline` plus three layout directions — arrived as a handoff package
written against this repository's real contracts. This spec covers landing
**one** direction, `peer-split`, end to end: registered, selectable from the
skin picker, and rendering on the FTX-1.

The bundle is archived outside git at
`~/Projects/rigplane-archives/segmentline-handoff-2026-09-01/`. It is the
visual reference and the source of the numeric values; it is not vendored.

## 2. Why `peer-split`

The FTX-1 has two receivers, not a VFO A/B swap: `rigs/ftx1.toml` declares
`receiver_count = 2` and `[vfo] scheme = "ab_shared"`, a distinct topology
class from a single-receiver radio's `1/ab`. That MAIN is the HF receiver and
SUB a separate VHF/UHF receiver for 2 m and 70 cm is the repository owner's
own statement about the hardware, made directly in conversation — not
something the profile establishes. `rigs/ftx1.toml` puts no band restriction
on `set_band_sub` (it takes the same `{band:02d}` encoding as `set_band`),
and the FTX-1 CAT manual's `BS` command uses one band table, starting at 1.8
MHz, for both `P1=0` (MAIN-side) and `P1=1` (SUB-side); neither source
confirms or rules out SUB being VHF/UHF-only.

`peer-split` — two equal columns, symmetry first — is the direction that
fits a two-receiver radio whether or not SUB turns out to be VHF-only;
`unified-instrument` would demote the second receiver to a subordinate strip
either way, so the choice below does not rest on the unestablished half.

Three supporting facts, each established by reading or running, not assumed:

- **Topology matches.** `rigs/ftx1.toml` declares `receiver_count = 2` and
  `[vfo] scheme = "ab_shared"`; `derivePresentationCapabilities` resolves
  that to a `structuralCount: 2, scheme: 'ab_shared'` topology. The
  `2/ab_shared` string itself is built by `radio-view-model-adapter.ts`'s
  `topologyId` field (a `structuralCount`/`scheme` template literal) — one of
  `peer-split`'s two declared compatible topologies.
- **It is the only manifest of the three that validates.** See §3.1.
- **`panadapter-first` is out for this radio.** It requires a hardware scope;
  `rigs/ftx1.toml` declares no `[spectrum]` section and no `scope` feature. It
  remains applicable to the IC-7300.

## 3. Defects found in the handoff before writing any code

The package states plainly that its Svelte files were never compiled and its
manifests never executed. Both statements proved load-bearing.

### 3.1 Two of the three manifests fail our validator

Established by running `validateLayoutManifest` from
`presentation/layouts/contract.ts` against the three manifest objects:

```
peer-split          — validates
unified-instrument  — Layout "unified-instrument" requires a semantic surface that no zone mounts.
panadapter-first    — Layout "panadapter-first" requires a semantic surface that no zone mounts.
```

Both failures name `vfo` in `requiredSemanticSurfaces` while declaring no zone
whose `surfaces` contains it.

### 3.2 The package's own prose and its own code disagree about zones

`INTEGRATION.md` §3 gives each direction four or five zones carrying one to
four surfaces each. `segmentline-declarations.ts` in the same package
declares nine or ten, each carrying exactly one, under different ids. They
cannot both be the specification. The `.ts` is the more careful of the two —
it carries the comment explaining which zone ids the wiring owns — and is
taken as the base.

### 3.3 `peer-split` declares zones the dual composition could not mount

`peer-split` declares `offsets` (`ritXitScan`), `dsp-rail`, `front-end-rail`
and `band-rail`. In the design those rails are **inside the glass** — part of
the instrument, not surrounding chrome — so they must render through the
semantic vertical. At the time this was found, `SemanticRadioSurfaces.svelte`'s
dual composition had no path for any of the four.

### 3.4 The fallback points at a layout that will not exist

`peer-split`'s `fallbackLayoutId` names `unified-instrument`. Retarget it to
`lcd-cockpit`, where the persisted amber preference already routes.

## 5. The second receiver before MOR-2144

`rigs/ftx1.toml` declares `dual_rx`; the web layer discards it because
`YaesuCatRadio` does not satisfy `DualReceiverCapable`. Tracked as MOR-2144,
not fixed here.

The consequence is **not** that `peer-split` cannot render.
`derivePresentationCapabilities` keeps both receivers structurally present and
sets `operationalReceivers = ['MAIN']` with a `dual-rx-unavailable`
diagnostic; `toRadioViewModel` emits a `receiver.SUB` /
`capability-unavailable` disabled reason, and `isOperationalStrip` reads it
back onto the strip. `radio-view-model-adapter.ts` records this as the
intended behaviour: a structurally dual radio without the tag keeps SUB in
`vfos`, "correct: MOR-977 renders it PRESENT".

**Owner decision, 2026-09-01:** ship it that way. The SUB column renders
present and marked not-operational, and becomes live when MOR-2144 lands with
no change in this layout.

## 6. Architecture placement

Two of v3's independent knobs, not one:

| Designed thing | v3 dimension | Lands at |
|---|---|---|
| Amber glass, ink ramp, outlined cells, seven-segment readout, segmented meters, TX perimeter | design language `segmentline` | `frontend/src/presentation/languages/segmentline/` |
| Which surfaces appear and where | layout `peer-split` | `frontend/src/presentation/layouts/` + `frontend/src/skins/segmentline/` |

Constraints inherited, not renegotiated:

- **A design language annotates; it does not draw.** The stylesheet draws
  from the annotations.
- **Input field names are the caller's.**
- **No per-frame work in a renderer.**
- **Unobserved is not zero.** An unread meter renders `?`, never a gauge
  resting at zero.
- **Activation is one attribute** — `[data-design-language="segmentline"]`,
  set by the shipped workspace selection, not by the bundle's shim.
- **Instruments scale, chrome does not** (MOR-1160).

## 7. Delivery

**MOR-2156 is a record of a mistake, not work.** An earlier version of this
line described it as "a false CSS-specificity claim in the two shipped
stylesheets". That was wrong, and the wrongness was mine: the claim in
`studioline.css` and `fieldline.css` is **correct**. Specificity is scored over
the whole selector, and the shipped rules are compound —
`[data-design-language='x'][data-design-language] .vfo-surface` is two
attributes plus a class, (0,3,0), which outranks Svelte's compiled
`.vfo-surface.s-<hash>` at (0,2,0). A census of both sheets puts 41 of 42 and
53 of 54 selectors at (0,3,0) or higher; the single exception in each is the
bare prefix on the custom-property block, which competes with nothing.

Three readers scored the PREFIX named in the sentence's parenthetical instead
of the RULE the sentence is about, and each confirmed the others. The only
thing that stopped it was measuring. Nothing in those two files needs changing;
the false version briefly existed only in `segmentline.css`, introduced by this
plan's own coordinator, and is deleted under MOR-2148.

### Why this order, and three earlier orders that did not work

The dependency that drives everything: `segmentline` must name a layout it is
compatible with, because `layout-compatibility-inventory.test.ts` fails any
shipped design-language manifest that declares no `compatible: true` entry.
And `designLanguageActivation` matches that entry against the resolved
**`SkinId`**, not against a layout manifest id — read the call site in
`App.svelte`, which passes `skinId`; the parameter is merely *named* `layoutId`.

So the SkinId must exist before the language. The layout manifest need not.

Three orders were tried and abandoned, each for a reason worth keeping:

1. **Language first.** Its only `compatible: true` entry then named something
   that did not exist. Nothing catches that —
   `declaresNoLayoutCompatibility` asks only whether a `true` entry is
   present, and nothing asserts that a `layoutCompatibility` entry names a
   layout the barrel exports; `layout-compatibility-inventory.test.ts`'s
   MOR-2070 block checks only the opposite direction (every barrel-exported
   layout is mentioned by a language, or explicitly exempt).
2. **Language plus the whole layout manifest in one change.** Established by
   building it and running the suite, not by reading: registering a manifest
   with the archived draft's full zone set fails 9 test files and needs ~14
   files — `skins/registry.ts` and its test, plus hand-listed completeness
   literals in three inventory suites and six `*-declarability.test.ts` files.
   With the language's own 10, that is 24 files against a ceiling of 10.
3. **MOR-2147 scoped to also "unfreeze `stageSizing`/`fitsViewport`".** It
   does not belong there: `ScaledStage` takes `nativeW`/`nativeH` as props and
   reads no manifest, so it never names either guarded identifier and the
   MOR-1247 tripwire never fires on it — confirmed by running that test
   against the primitive. The unfreeze belongs wherever a layout first feeds a
   declared `stageSizing` into the primitive, which nothing here does; see §9.

What makes the current order fit: the SkinId lands alone (the F8 check in
`cockpit-topology-adaptation.test.ts` is one-directional — every registered
layout *manifest* names a loadable skin, with no reverse requirement), and
the manifest lands with a single `vfo`+`rxTx`
zone. That one zone is what removes six of the fourteen files: the twelve
optional surfaces each have a `*-declarability.test.ts`, and `vfo`/`rxTx`
have none.

## 8. Scope explicitly excluded

- `unified-instrument` and `panadapter-first`. Both need the §3.1 fix, and
  neither can be exercised on the current bench in a way `peer-split` does not
  already cover.
- The package's two shims (`activation.ts`, `scaled-stage.ts`). MOR-2147
  replaces one; the shipped workspace selection replaces the other.
- Porting the bundle's React source. It is the visual reference; values are
  read from it and Svelte is written.
- Fixing MOR-2144. A frontend change does not widen into
  `src/rigplane/web/`.

## 9. Viewport reach, and what actually enforces it

The manifest declares `fixed-native` 1280x540 at `minScale: 0.5`. The
arithmetic that declaration implies, and — since `resolveSkinId`'s `isMobile`
check already runs today, ahead of any layout logic — whether each viewport
actually reaches `peer-split`, are two different questions with two different
answers:

| Viewport | Achievable scale | Declared result | Reaches `peer-split`? |
|---|---|---|---|
| 1920x1080 desktop | 1.50 | renders | yes — not `isMobile` |
| 1024x768 tablet | 0.80 | renders | yes — not `isMobile` |
| 844x390 phone landscape | 0.66 | renders | no — `isMobile` fires first (`min(844,390)=390<640`) |
| 390x844 phone portrait | 0.30 | below `minScale` | no — `isMobile` fires first (`min(390,844)=390<640`) |

**`minScale` enforces none of this today.** `fitsViewport` and
`resolveLayoutForViewport` (`presentation/layouts/contract.ts`) have no
production caller: every reference to either name outside that file is in a
test or a comment (established by grepping the whole of `frontend/src`).
For `fitsViewport` that is deliberate: MOR-1247's tripwire
(`__tests__/stage-sizing-boundary.test.ts`'s `GUARDED_NAMES`) freezes exactly
`stageSizing` and `fitsViewport` declaration-only until a primitive exists to
enforce the policy, and that freeze is still in place. `resolveLayoutForViewport`
is not itself a guarded identifier — its production-caller vacancy today is
not something the tripwire enforces.

What actually keeps a phone off this layout is a different mechanism one layer
up: `resolveSkinId` (`skins/registry.ts`) returns `'mobile'` on its first line
whenever `ctx.isMobile`, before any layout or viewport check runs. So phone
portrait does reach the mobile skin — by device detection, not by the
`minScale` gate.

An earlier version of this section claimed the fallback happened "by
arithmetic, with no device-detection branch". That was exactly backwards, and
is corrected here rather than softened.

This layout does not claim portrait support.

## 10. Verification

Per change: `npm run check` and `npx vitest run` in `frontend/`, plus
`uv run mypy --strict src/rigplane/web` — that gate fires on any change under
`frontend/**` and nothing warns first.

Full-suite comparison runs on the Mac mini against the recorded baseline
(`origin/main` = `1e2981077460d611274c9a74dc73a3a82d6b3c9a`: 373 frontend test
files, 8166 tests passed, eslint clean, svelte-check 0 errors / 0 warnings;
Python PASS, counts not captured on that run).

Live validation is on the FTX-1 over USB CAT. The plan recorded in the
MOR-1162 project document names the IC-7610 and the X6200 — the first retired
2026-08-04, the second destroyed 2026-08-11. That plan is stale and this spec
does not inherit it.
