# Building a skin

A skin is a top-level Svelte component, addressable by a `SkinId`, that
composes the shared semantic surfaces and/or its own bespoke components into
a renderable UI. For the conceptual picture of how a skin relates to layout
and design-language choice, see "Three independent knobs" in
`docs/plans/2026-04-12-target-frontend-architecture.md` — but that section's
own "Skin implementation (Svelte 5)" and "Skin resolution and lazy loading"
code samples further down do not match current code (compare them to
`SkinId` and `SKIN_LOADERS` in `frontend/src/skins/registry.ts`) and are
tracked stale under MOR-2044 — do not copy them. Everything below points at
real, current source instead. Concretely, and independent of that ADR's
staleness: both worked examples below show that a skin's own code never
special-cases which design language is active — see the
`presentation/languages/*` row below for why.

This doc plus the two worked examples it names is meant to be enough:
building a new skin should not require reading `.svelte` source outside
`frontend/src/skins/sdr-test/`, `frontend/src/skins/lcd-cockpit/`, and the
specific files each of those two entry points imports (named below). Reading
the `.ts` contract and test files this guide points at is expected and
necessary — the restriction is on wandering through other skins' or panels'
`.svelte` implementations for style cues.

Coordinate with MOR-1101 before extending this file — it owns the
skin-guide item this document implements. A case-insensitive repo-wide
search for existing skin-authoring documentation (variants of "skin guide",
"how to build a skin", "skin template"/"skin scaffold") under `docs/` and
`frontend/` found nothing to extend; this is a new file, not a rewrite.

## Read these first

Each is load-bearing; none is restated here beyond its role, because a
restated shape drifts and a pointer does not.

| File | Symbol | Role |
|---|---|---|
| `frontend/src/semantic/radio-view-model.ts` | `RadioViewModel`, `validateRadioViewModel` | The one shape every semantic surface renders. A skin that mounts the semantic vertical never sees raw runtime state — only this. |
| `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts` | `toRadioViewModel` | The one function that produces a `RadioViewModel` from live state + capabilities. `SemanticRadioSurfaces.svelte` (below) runs its output through `validateRadioViewModel` in dev/test (MOR-2040), so a shape mismatch throws immediately instead of rendering garbage. |
| `frontend/src/presentation/languages/contract.ts` | `DesignLanguageManifest`, `DesignLanguageTokens`, `RENDERER_SLOT_NAMES` | What a design language must declare: token groups and renderer slots. Activation is global and orthogonal to which skin is mounted — see `frontend/src/semantic/design-language-renderers.ts`'s header for the `[data-design-language]` mechanism. Neither worked example below registers a new design language. |
| `frontend/src/presentation/languages/declarations.ts` | `studioline`, `fieldline` | The two registered families — read as worked examples of the contract above, not as more contract. |
| `frontend/src/presentation/languages/state-vocabulary.ts` | exported `RF_STATES`, `TX_SESSION_STATES`, `TX_ORIGINS`, `TX_TARGET_STATUSES`, plus the re-exported reason/fault types | The typed `data-*` visual-state vocabulary a design-language stylesheet keys off (landed MOR-2036). Read this before reverse-engineering value sets from `fieldline.css`/`studioline.css` — that module's own header explains why it exists and exactly what it does and does not cover yet. |
| `frontend/src/presentation/layouts/contract.ts` | `LayoutManifest`, `SEMANTIC_SURFACE_NAMES`, `registerLayout`, `getLayout`, `declaredSurfaces` | Which semantic surfaces a layout may mount, its topology/sizing declaration, and the registry every layout goes through. A name in `SEMANTIC_SURFACE_NAMES` is *declarable*, not necessarily *mounted* — the file's own header lists which names nothing renders through a manifest yet. |
| `frontend/src/skins/registry.ts` | `SkinId`, `SKIN_LOADERS` (via `loadSkin`), `SKIN_RESOURCE_PLAN` (via `presentationResourcePlan`), `resolveSkinId` | The single place a skin becomes reachable at runtime. `frontend/src/App.svelte` is the only caller of `loadSkin`/`resolveSkinId`. |
| `frontend/src/lib/runtime/props/panel-props.ts` and `frontend/src/lib/runtime/adapters/*` | e.g. `toVfoProps`, `toMeterProps`, `panel-adapters.ts`'s handlers | Pure state→props mappers and bound callbacks. A skin (or anything it mounts) reaches state and commands through these, never through stores or transport directly — see "What a skin may not import" below. |

## Two skins to learn the shape from

### `sdr-test` — the minimal skin

`frontend/src/skins/sdr-test/SdrTestSkin.svelte` is a two-line delegate:
it mounts `frontend/src/components-v2/layout/RadioLayout.svelte` with
`skinId="sdr-test"`. Nothing else lives in this skin's own directory that
is part of the example — see the trap below.

`RadioLayout.svelte` is the shared desktop shell (it also backs
`desktop-v2`). What determines whether it shows the semantic deck instead of
its legacy panels is **the registered layout manifest for the given
`skinId`**, read through `declaredSurfaces(getLayout(skinId))` — not a
hardcoded id string. A manifest whose zones declare a `vfo` surface gets the
semantic deck; `sdr-test`'s manifest declares exactly that
(`frontend/src/presentation/layouts/declarations.ts`'s `sdrTestLayout`,
registered via `registerLayout`). The semantic deck itself is
`frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte` — read its
own header comment; it is the only place the VFO/RX-TX surfaces meet live
state, and it is manifest-blind by construction (its own composition is
fixed, regardless of what a manifest declares beyond `vfo`/`rxTx`).

Two traps in this exact directory, both confirmed against current source
rather than assumed from comments:

- `frontend/src/skins/sdr-test/SdrVfoScreen.svelte` is **not mounted by
  anything**. It is a pre-migration prototype kept as a historical
  reference pending deletion under MOR-1099 (see that file's own header).
  Do not use it as a reference for how sdr-test's live wiring works — use
  `SemanticRadioSurfaces.svelte` instead.
- `SdrTestSkin.svelte`'s own comment says RadioLayout "branches on
  `skinId === 'sdr-test'`". That described the mechanism before MOR-1313.
  `RadioLayout.svelte`'s own header comment documents the replacement
  (the manifest-driven `declaredSurfaces` check described above) — trust
  that comment over the stale one in `SdrTestSkin.svelte`.

### `lcd-cockpit` — the skin with its own instruments

`frontend/src/skins/lcd-cockpit/LcdCockpitSkin.svelte` is the same shape of
delegate, one level down: it mounts
`frontend/src/components-v2/layout/LcdLayout.svelte` with
`variant="cockpit"`. Unlike `RadioLayout`, `LcdLayout` does not consult the
layout-manifest registry to decide what it mounts — the `variant` prop picks
between its own fixed compositions of the bespoke instrument components
under `frontend/src/components-v2/panels/lcd/` (the "Amber" family: its own
frequency display, S-meter, scope, etc., styled by that directory's own
`lcd-vintage.css`, not by `presentation/languages/*`). If you are adding a
third LCD variant, `LcdLayout.svelte` is where the branch lives. If you are
not, do not try to route a new skin through `LcdLayout` — write your own
dedicated layout component instead; `frontend/src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte`
is the existing precedent for that shape (its own registration is checked
by `frontend/src/skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`,
referenced rather than duplicated in the entry-point suite below).

`lcd-cockpit` still registers a `LayoutManifest`
(`frontend/src/presentation/layouts/lcd-declarations.ts`'s `lcdCockpitLayout`)
even though `LcdLayout.svelte` never reads it back — the manifest exists for
topology/compatibility declaration independent of what actually renders.
Register one for your own skin too, whichever shape it takes.

### Is a scaffold skin warranted?

No — decided in-ticket, not left implicit. There are two structurally
different shapes in evidence: delegate into `RadioLayout`'s generic,
manifest-driven mechanism (`sdr-test`, `desktop-v2`), or bring your own
dedicated layout component, whether reused across variants the way
`LcdLayout` is (`lcd-cockpit`, `lcd-scope`) or bespoke to one skin the way
`DualReceiverCockpit.svelte` is. A generic scaffold would have to average
over both shapes or pick one arbitrarily, and with only two shapes to
generalize from, the rule of three doesn't clear: nothing here justifies a
third, synthetic instance nobody has yet needed to copy. `sdr-test` already
serves as the template for the minimal (first) shape; use it directly.

## Wiring a new skin into the app

Most files below are typed `Record<SkinId, ...>`, so skipping one is a
compile error `npm run check` catches. A few instead pin a hand-listed
table against one derived from real source, which only `npx vitest run`
catches — each such step says so. The last step is different again: it
only applies if your skin should be reachable from user-facing layout
preference, and one of its links has no check behind it at all.

1. **`frontend/src/skins/registry.ts`**: add your id to the `SkinId` union;
   add a loader to `SKIN_LOADERS` (reached only through `loadSkin`); add an
   entry to `SKIN_RESOURCE_PLAN` (reached only through
   `presentationResourcePlan`) — `[]` if your skin bridges no App-owned
   resource. Both `SKIN_LOADERS` and `SKIN_RESOURCE_PLAN` are typed
   `Record<SkinId, ...>`, so a missing key fails to compile. Pinned by
   `frontend/src/skins/__tests__/registry.test.ts`.
2. **`frontend/src/__tests__/presentation-switch-resources.component.test.ts`**:
   add your id to `SKIN_PLAN`, a hand-mirrored copy of `SKIN_RESOURCE_PLAN`
   that this file's own "mirrors the production per-skin resource plan"
   test checks against the real one, and to `WIDTH_FOR`, the viewport
   width its fixtures resize to per skin. Both are typed
   `Record<SkinId, ...>`, so a missing key is the same `npm run check`
   failure as step 1, in a second file. Give `WIDTH_FOR` a value none of
   the other entries already use — `widthToSkin()` in that file picks a
   skin by exact width equality, so a duplicate resolves to whichever
   entry `Object.keys` visits first, not necessarily yours.
3. **A `LayoutManifest`**, registered via `registerLayout` from
   `frontend/src/presentation/layouts/contract.ts`, re-exported from
   `frontend/src/presentation/layouts/declarations.ts` (directly, or from
   your own sibling `<name>-declarations.ts` the way `lcd-declarations.ts`
   and `mobile-declarations.ts` are). Pinned by
   `frontend/src/presentation/layouts/__tests__/registry.test.ts` and, by
   convention, a dedicated `<your-skin>-registration.test.ts` alongside
   `sdr-registration.test.ts`/`desktop-v2-registration.test.ts` — plus one
   more that nothing above names:
   `frontend/src/presentation/layouts/__tests__/loader-identity-inventory.test.ts`
   needs a row for your manifest in both `ALL_MANIFESTS` and
   `EXPECTED_LOADER_SPECIFIER` (the latter is your skin component's
   resolved import specifier, e.g. `/src/skins/sdr-test/SdrTestSkin.svelte`
   for `sdr-test`). Both are hand-listed literals, not
   `Record<SkinId, ...>`, checked against the barrel's actual export
   surface by that file's "the manifest table matches every id the barrel
   exports" test — so a missing row fails `npx vitest run`, not
   `npm run check`.

   A different family of files also hand-lists every registered manifest,
   for its own purpose, and does not fail when a new one is missing — it
   just stops covering it:
   `frontend/src/presentation/layouts/__tests__/forward-declaration-inventory.test.ts`
   (`ALL_MANIFESTS`, `DOM_BACKED`) and the sibling `*-declarability.test.ts`
   files in the same directory, one per semantic surface (e.g.
   `meters-declarability.test.ts`'s `ALL` — its own comment reads "extend
   by hand, with a layout review, never silently"). Nothing here is
   required for a minimal skin; know they exist because if your skin
   should be counted in one of them, nothing will tell you that it isn't.
4. **`frontend/src/skins/__tests__/entrypoints.test.ts`**: add your `SkinId`
   to `SKIN_ENTRYPOINT_COVERAGE`, picking the coverage `kind` that matches
   how you actually mount (`radio-layout` / `lcd-layout` / `mobile-layout`
   if you genuinely delegate to one of those three shared shells, or
   `covered-elsewhere` pointing at your own dedicated mount test if you
   don't — see `dual-receiver-cockpit`'s entry for that shape). This table
   is typed `Record<SkinId, ...>`, so an omission is a compile error — but
   only `npm run check` (svelte-check) catches it; the file's own header
   explains why a plain `vitest run` would not.
5. **Only if your skin should be reachable from user-facing layout
   preference** (not just a fixed id, the way `dual-receiver-cockpit` is
   reachable only via an exact query param) — four sites need an entry,
   not just `resolveSkinId`:
   - **`frontend/src/lib/stores/layout.svelte.ts`**: add your id to the
     `LayoutMode` union, and — unless it should stay QA-only the way
     `dual-receiver-cockpit` deliberately is not — to the
     `CANONICAL_LAYOUT_MODES` set. `CanonicalLayoutMode` derives from
     `LayoutMode` automatically; `CANONICAL_LAYOUT_MODES` does not derive
     from anything and is not checked against that type. Miss it and
     `normalizeLayoutMode` silently normalizes your id to `'auto'` before
     `resolveSkinId` (below) ever sees it — no error, the branch you add
     there just never runs.
   - **`frontend/src/presentation/workspace/contract.ts`**: add your id to
     `WORKSPACE_LAYOUT_IDS`, and a matching entry to the map
     `workspaceLayoutManifestId` reads. `WORKSPACE_LAYOUT_IDS` is checked
     for agreement with `CANONICAL_LAYOUT_MODES` above — not for
     completeness on its own — by
     `frontend/src/presentation/workspace/__tests__/contract.test.ts`'s
     "layout ids are exactly the store's CanonicalLayoutMode set" test (a
     `vitest` failure, not a compile one); the manifest-id map is
     `Record<WorkspaceLayoutId, string | null>`, so a missing key is a
     `npm run check` failure.
   - **`frontend/src/components-v2/layout/StatusBar.svelte`**: add your id
     to the `skinOptions` array, or the skin picker never offers it. This
     site has no check at all: `skinOptions` is a plain array, not a
     `Record`, and its own suite (`StatusBar.skin-options.test.ts`) only
     pins that the QA-only id stays excluded and that the array keeps its
     declared type. Skip this step and both commands below stay green
     while your skin is unreachable from the picker.
   - **`frontend/src/skins/registry.ts`**: add a branch to `resolveSkinId`.
     Read its existing doc comment first — the resolution order there is
     deliberate.

   The first of these sits under `$lib/stores/*`, which "What a skin may
   not import" below bans — but that is not a conflict. The ban
   (`frontend/eslint.config.js`'s `FORBIDDEN_SKINS_IMPORTS`) is scoped by
   file location to import statements written inside
   `src/skins/**/*.svelte` and `src/skins/**/*.ts`; it has nothing to say
   about editing `lib/stores/layout.svelte.ts` itself, which sits outside
   that tree. You extend that module as its own owner here, not as a skin
   importing it. The one file in this chain that IS under `src/skins/**`
   (`registry.ts`) needs no new import either: it already reaches
   `normalizeLayoutMode`/`LayoutMode` through
   `frontend/src/lib/runtime/adapters/layout-mode-adapter.ts`, the
   re-export that eslint rule's own comment says was created for this
   exact file when the ban landed (MOR-2039).

## If your skin adds a new meter component

`frontend/src/components-v2/meters/__tests__/meter-contract.ts`
(`METER_REGISTRY`, `MeterValueDomain`) and its suite,
`meter-contract.test.ts`, only engage `.svelte` files added directly under
`frontend/src/components-v2/meters/`. A new one must be registered there
under the correct domain: `'calibrated-db-rel-s9'` (derive S-unit/dBm text
only through `smeter-scale.ts`'s own functions, never a local formula) or
`'preformatted'` (render the given `displayValue` verbatim). Reusing one of
the two existing components (`BarGauge.svelte`, `LinearSMeter.svelte`)
needs no new registration. `lcd-cockpit`'s own instruments live under
`components-v2/panels/lcd/` instead and do not touch this contract at all —
read `meter-contract.ts`'s header before assuming it governs every
meter-shaped widget in the app.

## What a skin may not import

`frontend/eslint.config.js`'s `FORBIDDEN_SKINS_IMPORTS` (MOR-2039) bans
`$lib/stores/*`, `$lib/transport/*`, and `$lib/audio/audio-manager` from
every file under `src/skins/**` — `.svelte` and `.ts` alike. Route state
through `lib/runtime/adapters/*` (see the contracts table above) and
callback props instead.

Nothing in `.github/` runs `eslint` on the frontend as a CI step —
`npm run lint` exists but `.github/workflows/quick.yml`'s frontend block
never calls it. The only thing that actually exercises this boundary in CI
is `frontend/src/__tests__/architecture-boundaries.test.ts`, which
instantiates a real `ESLint` and lints virtual fixture text against the
live flat config, as an ordinary case inside `npx vitest run`. If you need
to confirm your skin's imports are legal — or add a new ban — add or extend
a fixture case there (search that file for `MOR-2039` for the existing
skins fixtures); a rule with no fixture case is not actually checked
anywhere.

## Proving it works

```
cd frontend
npm run check     # svelte-check + tsc — catches every missing Record<SkinId, ...> entry above
npx vitest run     # entrypoints, registry, layout registration, meter-contract, architecture-boundaries, and everything else
```

These are the same two commands `.github/workflows/quick.yml`'s frontend
block runs (minus the i18n check, Playwright visual smoke, and build steps,
which a new skin does not need to touch).
