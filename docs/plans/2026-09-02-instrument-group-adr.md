# The instrument group — Phase 2 ADR draft

**Status: Draft for owner decision, 2026-09-02.** Two owner decisions are
already recorded, in §1 under "Decided 2026-09-02"; everything else is an
option set for the owner to pick from, collected in §10.

## 1. Status and provenance

**Revision read:** `origin/main` at `b197b09a` (`git rev-parse origin/main`,
2026-09-02). Every code claim below was established by reading that revision
in this worktree; where a claim came from the Phase 1 input file instead of
from my own read, it says so.

**Inputs:** the coordinator's condensed Phase 1 material (owner rulings, code
map, mechanism audit, two prior-art surveys, in-flight constraints), quoted
here for the rulings and re-derived from source for the code.

**Owner rulings this draft implements** (Russian is the ruling; the English
line under each is a gloss, not a second ruling; times are UTC, 2026-09-02):

1. 19:58 — «Каждый контейнер владеет своим контентом, но не наоборот. …
   layout — может содержать в себе панели. Получает снаружи — разрешение
   экрана (viewport). Может отобразить панель, а может и нет. Может их
   масштабировать как хочет и располагать внутри себя. Панель может содержать
   элементы и/или группы. … Как будут отрисовываться кнопки — в 2 или в 3 ряда
   — решает панель. … Компонент VFO — грубо 8 цифр, S-метр, индикатор, который
   можно и нужно объединять в группы, и показывать/скрывать в зависимости от
   capabilities.»
   *Gloss: containment is one-way; each container sizes and places its own
   children; capability decides what is shown.*
2. 20:09 — «в рамках одного скина? Другой может быть реализован иначе?» …
   «Приборы настоящих радио и любой SDR-фейс живут по первому способу
   [transform]. Веб-панели с кнопками — по второму [reflow]» … «да, скорее
   всего так и будет».
   *Gloss: transform-vs-reflow is a per-container choice; both may appear in one skin.*
3. 20:21 / 20:23 — «Контейнер — считай карточка, которую сделали желтой через
   CSS. С круглыми углами. А в ней компоненты, жестко зафиксированные на
   местах как в настоящих приборах.»
   *Gloss: the fixed-proportion container is a card with a look, its components pinned as on a real instrument.*
4. 20:26 — «Да, это похоже на то, что мы изобретаем конструктор интерфейсов. В
   каком-то смысле — оно так и есть. Но я НЕ ЗНАЮ, как должен выглядеть
   идеальный интерфейс. Поэтому я хочу, чтобы их было легко делать. И пускай
   комьюнити занимается.»
   *Gloss: deliberately a UI construction kit; ease of authoring is the goal, and the community is expected to author.*
5. 20:31 — «не произвольный, а с ограниченным словарём — верно. У радио есть
   VFO, но руля у него нет. По факту, не так много примитивов на радио, и по
   моему они все у нас описаны».
   *Gloss: bounded vocabulary, not free composition; the owner believes it is already fully present in the tree — checked in §8.*
6. 20:31 — non-goal 5 becomes «не копировать фирменный вид производителя один
   в один»; non-goal 4 narrows to no free-form widget tree. Public contract
   for the format: «возможно позже, да». «Проектируется один раз и как
   следует — это ко всему относится. Я ж не зря v3 затеял.»
7. 20:00 — «Svelte 5 — тут тоже свет клином не сошелся. Можно рассмотреть
   варианты, если они будут достойные и облегчат нам жизнь.»
   *Gloss: the framework was open to alternatives on measured evidence — closed
   by ruling 8 below.*
8. 20:46 — «остаёмся на Svelte, лица — данные, запиши как решение».
   *Gloss: recorded as a DECISION, not an option — see "Decided 2026-09-02"
   immediately below.*

### Decided 2026-09-02 (owner, 20:46 UTC, MOR-2249)

**Svelte 5 stays.** The framework question is closed for this program. Ruling
8 above is the whole of the owner's words on it; the revisit condition that
follows is this document's proposal, not a quotation. It is reopened only if a
concrete workspace need appears (tabs, edge docking — §6 records that the
target screen shows neither) AND a framework-agnostic library
spike fails on DOM ownership under Svelte 5 — and then only with the rewrite
cost measured first. The scale of that cost, established here by
`find frontend/src -name "*.svelte" | wc -l` and the same for `*.ts` at
`b197b09a`: 122 `.svelte` and 580 `.ts` source files. The suite size at
`origin/main`, 8342 tests, comes from the coordinator's message, not from a run
of mine. The 2026-03-07 scaffold PR #157 that chose Svelte recorded no
rationale (coordinator's statement; not verified here), so there is no earlier
argument to weigh against.

**Faces are data.** The owner's words are «лица — данные»; the operational
reading that follows is this document's gloss, for the owner to confirm — a
face author writes declarations, and possibly CSS, without touching `src/` and
never meets Svelte, while behaviour and the instruments stay code. §3's
boundary sentence and §5's weighting of shape option (c) rest on that gloss and
fall with it if the owner reads «данные» more narrowly.

## 2. Vocabulary

Five things need names. Collision counts below are files under `frontend/src`
matching the word case-insensitively, from `git grep -lni "\bWORD\b" --
frontend/src | wc -l` at `b197b09a`. Every count in both tables is the output
of exactly that command, re-run word by word on 2026-09-02: `face` 14, `panel`
277, `rack` 0, `glass` 19, `group` 154, `card` 38, `instrument` 24, and
`layout` 191 for the comparison the `panel` row makes.

| Role | Set A ("keep the words we have") | Set B ("fewest collisions") |
|---|---|---|
| Shell that receives the viewport | `layout` | `face` |
| Reflow container of controls | `panel` | `rack` |
| Fixed-place card | `glass` | `group` |
| Member pinned on the card | `instrument` | `instrument` |
| Look | `design language` | `design language` |

Costs, per candidate (files = the collision count above):

| Candidate | Files | Cost of using it |
|---|---|---|
| `layout` | — | Already the manifest (`presentation/layouts/contract.ts: LayoutManifest`), a Svelte shell (`components-v2/layout/RadioLayout.svelte`) and a workspace field (`presentation/workspace/contract.ts: WorkspaceV1.layout`). Zero new vocabulary; "layout" keeps meaning both the manifest and the component, as it already does |
| `face` | 14 | The owner's own word in MOR-2231/MOR-2232 ("SDR face", "LCD face", from the input file). Cost: a fourth word beside skin / layout / manifest, which already name overlapping things |
| `panel` | 277 | The whole legacy tree `components-v2/panels/*`, plus `CollapsiblePanel.svelte`, `lib/runtime/props/panel-props.ts`, `lib/runtime/adapters/panel-adapters.ts`. Highest collision of any candidate — 277 against the 191 of `layout`, the next highest, both from the command above — and it points at exactly the code the 2026-04-12 addendum says is to be removed: the word would mean "legacy widget box" and "declared reflow container" at once for the whole migration |
| `rack` | 0 | No collision at all; the cost is a word with no existing reader |
| `glass` | 19 | `peer-split-glass` in `skins/segmentline/PeerSplitLayout.svelte`, the amber glass in `components-v2/layout/LcdLayout.svelte`. Already means the fixed-proportion instrument surface — the thing being named. Cost: it names a hand-written CSS class today, so class and concept would share a name |
| `group` | 154 | Almost all unrelated senses (`role="group"`, "fact group", "choice group"). Closest to the ruling's own «группы». Cost: grepping the concept returns mostly noise, making future audits expensive |
| `card` | 38 | In neither set. The owner's own analogy (ruling 3), but dominated by the CSS token `--v2-bg-card` — a collision inside the theme layer a group would style through — and it carries no radio meaning |
| `instrument` | 24 | The 2026-04-12 addendum already fixes its meaning: the finite component set (button/toggle, slider/knob, S-meter and gauges, frequency display, state feedback) whose single home going forward is `frontend/src/primitives/`. Both sets take it |
| `design language` | — | Already a contract (`presentation/languages/contract.ts: DesignLanguageManifest`) with one frozen activation mechanism (`[data-design-language]`, MOR-1278). Both sets take it; no rename proposed |

**Owner picks the set.** Inference, not established: the sets can be mixed
(say `face` / `panel` / `group` / `instrument`), but each borrowed word brings
its own row's cost.

## 3. Boundary sentence and guards

**Boundary sentence** (owner decision, 2026-09-02, as glossed in §1 — the
owner's own words are «лица — данные»): a face author writes
declarations and possibly CSS — which instruments appear, where each sits on a
group's canvas, how that group scales, which design-language tokens paint it,
which zone it mounts in — without touching `src/` and never meeting Svelte; and
may not declare or touch capability facts, TX authority, radio behaviour,
component module paths, or any transport, audio, store or runtime import,
because behaviour and the instruments themselves stay code.

| Clause | Enforced today by | New guard needed? |
|---|---|---|
| No capability facts in a declaration | `presentation/layouts/contract.ts: findCapabilityLikeKey` (its `FORBIDDEN_MANIFEST_KEY_MARKERS` list), run first inside `validateLayoutManifest` | Yes if the group is a separate node: the scan runs over a `LayoutManifest` only |
| No module paths in a declaration | `presentation/layouts/contract.ts: findModulePathLikeValue` (`MODULE_PATH_VALUE_PATTERN`) | Same as above |
| Members come from a bounded vocabulary | Nothing. `SEMANTIC_SURFACE_NAMES` and `validateZones` bound *surfaces*, not instruments; there is no instrument registry at `b197b09a` | **Yes** — this is the ruling-5 guarantee and nothing checks it |
| A skin imports no transport/audio/stores | `frontend/eslint.config.js: FORBIDDEN_SKINS_IMPORTS`, exercised in CI only through `frontend/src/__tests__/architecture-boundaries.test.ts` (`npm run lint` is not called by `quick.yml`'s frontend block) | No, if groups live under `skins/` or `presentation/`; `FORBIDDEN_PRESENTATION_IMPORTS` and `FORBIDDEN_PRIMITIVES_IMPORTS` cover those directories |
| Python layer boundaries | `.importlinter`'s `root_packages = rigplane` — it does not see `frontend/` at all (read at `b197b09a`) | Not applicable; naming it as a frontend guard would be false |
| Exactly one TX key authority | Construction, not a test of the declaration: `components-v2/wiring/SemanticRadioSurfaces.svelte` keeps exactly one `<RxTxSurface>` tag and one TX *source* id per mounted instance — `sourceId`, built from the module-scope `surfaceSeq`, which is the identity the controller keys lease ownership by; the *lease* id passed to `tx.start` is `${sourceId}-${++leaseSeq}` and is fresh per key request — against the App-owned controller (`lib/runtime/tx-controller/app-host: getAppTxController`); v3 invariant 11 | **Yes** if a group may name an `rxTx`-class instrument: a declaration could otherwise ask for two |
| Capability gating decides visibility | `lib/runtime/adapters/radio-view-model-adapter.ts: toRadioViewModel` plus the per-surface `{#if view?.…}` gates and `zoned(…, allowBare=false)` in the wiring (MOR-1069: a control-bearing surface never renders bare) | Yes for a group's own `requires` field — it must resolve through the same view model, never a second capability schema (v3 "Capability integration") |
| Native size stays declaration-only outside `presentation/layouts/` | `presentation/layouts/__tests__/stage-sizing-boundary.test.ts` (`GUARDED_NAMES` = `stageSizing`, `fitsViewport`), a textual scan | No new guard, but see §4: a group carrying its own canvas field never names either identifier, which is why `skins/segmentline/PeerSplitLayout.svelte` passes today while duplicating the numbers |

### Proposed replacement text for the v3 plan's "Goals and non-goals"

Exact replacements for the fourth and fifth non-goal bullets (2026-09-02,
MOR-2249, pending owner decision):

- fourth bullet, currently "making an arbitrary JSON UI-tree editor", becomes:
  *"a free-form widget tree: a face composes only the declared instrument
  vocabulary, never arbitrary components"*;
- fifth bullet, currently "faithfully cloning physical radio faceplates",
  becomes: *"cloning a manufacturer's trade dress one-for-one"* — the owner's
  own wording is «не копировать фирменный вид производителя один в один».

## 4. The group declaration: schema draft

Illustrative shape only. This block is a sketch in a document; it is not code
and compiles into nothing.

```ts
type CanvasUnit = number; // same units as `canvas.w`/`canvas.h` below

interface InstrumentPlacement {
  readonly instrument: InstrumentName;   // from a bounded registry (§3, row 3)
  readonly x: CanvasUnit;
  readonly y: CanvasUnit;
  readonly w: CanvasUnit;
  readonly h: CanvasUnit;
  /** Hidden when the capability is absent; resolved through the existing
   *  view model, never a second capability schema. */
  readonly requires?: CapabilitySelectorName;
}

interface InstrumentGroup {
  readonly schemaVersion: 1;
  readonly id: string;                    // kebab-case, naming policy as layouts
  readonly canvas: { readonly w: CanvasUnit; readonly h: CanvasUnit };
  readonly members: readonly InstrumentPlacement[];
  readonly scaling:
    | { readonly mode: 'fixed-native'; readonly minScale: number }
    | { readonly mode: 'reflow' };
  readonly look: {
    readonly fill: DesignTokenName;       // token name, not a colour value
    readonly radius: DesignTokenName;
    readonly bezel: DesignTokenName;
    readonly backgroundAsset?: AssetId;   // optional, ruling 3
  };
  readonly zone: string;                  // a zone id the layout manifest declares
}
```

What each field replaces or feeds:

- `canvas` — absorbs **F2**, the native size declared twice: `SEGMENTLINE_GLASS_STAGE`
  in `presentation/layouts/segmentline-declarations.ts` and the `NATIVE_W`/`NATIVE_H`
  constants in `skins/segmentline/PeerSplitLayout.svelte` (both 1280×540, read at
  `b197b09a`). One declaration, read by the shell.
- `members` — absorbs **F5**, placement's three descriptions with no owner:
  manifest zones, the literal DOM zone ids inside `components-v2/wiring/
  SemanticRadioSurfaces.svelte`, and the explicit CSS grid placement in
  `PeerSplitLayout.svelte`'s `<style>`.
- `scaling` — feeds `primitives/stage/ScaledStage.svelte` in `fixed-native`
  mode; `reflow` means no stage at all, the container's own CSS decides
  (ruling 2). `minScale` gives **D3**'s thrice-declared, `fitsViewport`-only
  field a real reader (letterbox fallback) instead of deleting it.
- `look` — token names bound by the active design language; activation stays
  the single `[data-design-language]` attribute (MOR-1278). Nothing here is a
  colour value.
- `zone` — where the group mounts. `SKIN_RESOURCE_PLAN` in `skins/registry.ts`
  stays hand-maintained and is not derived from this: membership there only
  permits App-owned resource bridging (v3 invariant 12), which is a fact about
  what a subtree can consume, not about what a face declares — deriving it
  from a declaration would let an author manufacture a live service.
- `layoutCompatibility` (`presentation/languages/contract.ts`) is unchanged: a
  group is not a layout and gets no entry, so
  `presentation/languages/__tests__/layout-compatibility-inventory.test.ts`
  keeps working on layouts only.

**Native size as props.** The shell resolves the group declaration and passes
`canvas.w`/`canvas.h` (and `minScale`) down as component props, exactly as
`PeerSplitLayout.svelte` does today with its own constants. Because the group
field is named `canvas` and not `stageSizing`, no file outside
`presentation/layouts/` names a guarded identifier, and
`stage-sizing-boundary.test.ts`'s textual scan stays green untouched.

**The MOR-1068 cycle stays open.** `SemanticRadioSurfaces.svelte` must not
import the group declarations barrel — that would close manifest → loader →
skin → wiring and let a wiring change register a layout. The wiring's own
header states the rule; the shell (the skin component) resolves the
declaration and hands the wiring only values.

## 5. Shape options with costs

No recommendation. Expressiveness is tested against three targets: the
peer-split glass; MOR-2231's SDR deck (side columns plus a meters strip);
MOR-2232's LCD face.

| Shape | Files that change | Tests that change | Cannot express | One-line falsifier |
|---|---|---|---|---|
| **(a)** `LayoutZone` gains a stage/placement descriptor | `presentation/layouts/contract.ts`; every manifest file declaring a zone; `presentation/workspace/contract.ts` (`WORKSPACE_ZONE_IDS`) | The twelve `*-declarability.test.ts` files, the registration suites' manifest-shape assertions, `stage-sizing-boundary.test.ts` | Two scaling modes in one zone — the SDR deck's fluid spectrum beside a fixed meters strip needs two zones per visual area, or a per-member override | If a manifest can carry a grid template as data without becoming behaviour, the sentence "A manifest is a DECLARATION, never behaviour" is wrong — it is the module header of `presentation/layouts/lcd-declarations.ts` and of `mobile-declarations.ts`, not of `contract.ts`, whose own header states the narrower "never executable radio behavior, capability objects, or component module paths" |
| **(b)** separate `InstrumentGroup` node + registry, referenced by zones | A new `presentation/groups/` module (contract + declarations); one reference line per manifest; the shells that mount groups | A new registry/validator suite mirroring `presentation/layouts/__tests__/registry.test.ts`; existing layout suites untouched | Nothing among the three targets that I can name — all three are a canvas with placed members. The limitation is duplication, not expressiveness, and it is this row's own file list: a second registry with its own id policy, validator and fallback semantics, and an author who learns two vocabularies and keeps a member name in step across both | If the two validators end up sharing more than the marker lists, (b) has become (a) with extra files — the mechanism-duplication shape the Phase 1 audit exists to catch |
| **(c)** hand-written Svelte groups registered by id | One `.svelte` per group plus a `Record<GroupId, loader>` beside `skins/registry.ts`'s `SKIN_LOADERS` | One component test per group, the shape `skins/segmentline/__tests__/PeerSplitLayout.component.test.ts` already has | Nothing a Svelte file cannot do — but nothing is *declared*, so ruling 5's bounded vocabulary is unenforceable and ruling 4's "easy for the community" means writing Svelte | If the third hand-written group repeats the second's structure, the loader table has become (b)'s registry without (b)'s validation |

**What the owner decision constrains.** «Лица — данные» (§1), read through
that section's gloss, requires a group declaration a community author can
hand-write and a validator can check. Option (c) does not produce one — a
Svelte component is not a declaration, so an author writes code and no
validator can bound the vocabulary. That weighs against (c) *for the group
itself*; the costs of (a) and (b) above are unchanged, and (c) remains a
legitimate shape for a bespoke skin that declares no group. This whole
paragraph rests on §1's gloss and not on the owner's five words: a narrower
reading of «данные» lifts it and leaves the three shapes' own costs as the
table states them.

**What the Phase 1 audit's constraints exclude:** nothing outright. The audit's
closing (input file §3) requires a group node to absorb F2 and F5, decide
transform-vs-reflow per group, delete nothing but D2 first, and leave
`SKIN_RESOURCE_PLAN`, the LCD/segmentline constant pair and `SKIN_LOADERS`
alone. All three satisfy that; (c) satisfies F5 only by making the CSS the
declaration, which is the state the audit calls "three descriptions and no
owner".

## 6. Workspace-level library

Three first-class options. The workspace level is the *outer* problem — how
panels are arranged, collapsed and moved around the shell — not how a group's
members are placed. The framework question is closed (§1): all three options
below are evaluated as things a Svelte 5 app would adopt.

**What the workspace already does at `b197b09a`.** Persisted per-user state is
`WorkspaceV1` in `presentation/workspace/contract.ts` — the whole interface is
`version`, `layout`, `designLanguage`, `theme`, `density`, `visibleSurfaces`,
`zoneOrder` (the last two per-zone) and `pinnedCommands` — written
through `presentation/workspace/store.svelte.ts`'s `setZoneVisibleSurfaces` /
`setZoneOrder`, surfaced to the operator by
`components-v2/controls/WorkspaceSettingsPanel.svelte` (layout, language,
theme, density, reset) and `WorkspaceImportExport.svelte`. Separately and
outside the workspace document, `components-v2/controls/CollapsiblePanel.svelte`
persists per-panel collapse under the `rigplane:panel-collapsed` localStorage
key, and `lib/drag-reorder.svelte.ts`'s `createDragReorder` implements
drag-to-reorder *including cross-sidebar transfer* (its own module header says
so), used by `components-v2/layout/LeftSidebar.svelte` and `RightSidebar.svelte`.
Note the split: the workspace document can only ever subtract or reorder
declared surfaces, while collapse and sidebar order live in their own
localStorage keys.

**What the target screen needs.** Reading `docs/screenshots/hero.png` at
1280×672: a chevron and a drag handle on each side-column panel (RF FRONT END,
MODE, FILTER on the left; RX AUDIO, AUDIO SCOPE, DSP, TX on the right), a fixed
centre column, a bottom meters strip, and a CW console pull-out at bottom
right. There are **no** tabs, no edge-docking affordances, no floating windows
and no resize handles visible in that image. So the workspace gestures the
target needs are: collapse per panel, and drag within and across the two side
columns.

**The three options on the same five cells.** Figures marked *(survey)* come
from the Phase 1 input file and were not re-verified here.

| Option | What it provides | Glue / integration cost | Deterministic DOM for baselines | Accessibility | Maintenance risk |
|---|---|---|---|---|---|
| **(1) Dockview core** — MIT, v8.2.0 (2026-08-19), framework-agnostic, ~81 kB gzip *(survey)* | Docking, tabs, floating windows, JSON serialization. Of these, the screen reading above names none | No Svelte binding: a glue component constructing the instance in `onMount`, tearing it down in `onDestroy` and mounting Svelte components into library-owned panes — roughly 80–150 lines for a first working shell, inferred from the API's shape, not measured | Unmeasured; the spike's first row below is what decides it | Documented keyboard support *(survey)* | DOM ownership — the library moves the nodes our Svelte components live in while Svelte 5 holds its own references to them (the spike's second row) — plus a release cadence we do not set |
| **(2) Gridstack.js** — MIT, v13.2.0 (2026-08-20), vanilla *(survey)* | Grid drag and resize with JSON persistence. Of these, the screen reading above names drag only | The same "no Svelte binding" glue shell as (1) | Generates random stylesheet ids, to be neutralised by configuration before any visual baseline *(survey)* | Declared wontfix *(survey)* | The same DOM-ownership risk as (1), and the same third-party cadence |
| **(3) No library** — extend `lib/drag-reorder.svelte.ts: createDragReorder` and `components-v2/controls/CollapsiblePanel.svelte` | Exactly the two gestures the screen reading names — per-panel collapse, and drag within and across the two side columns, cross-sidebar transfer included per that module's own header. Nothing beyond them | None; both files are already in the tree and already pass this repo's own boundaries | No library-generated ids, because there is no library: the DOM is the one we write | Ours to write — pointer capture, keyboard equivalents and touch. Nothing checks any of the three for these two files today; the keyboard-traversal row of the spike protocol below, which option (3) is measured against as well, is what would | Every gesture is ours to write, review and keep working; a missing one is an implementation, not a configuration flag |

If a gesture is missing under (3), one Svelte-native candidate is
`svelte-dnd-action` — MIT, latest 0.9.79, 312,698 downloads in the week
2026-08-23…29 (npm registry and
`api.npmjs.org/downloads/point/last-week`, fetched 2026-09-02), and its README
documents Svelte 5 event syntax (`onconsider`/`onfinalize`). Its most recent
commit or release date is **unverified** — I could not read a date from the
GitHub page — so no maintenance claim is made here. `frontend/package.json` at
`b197b09a` has exactly one runtime dependency, `lucide-svelte`; no drag, grid
or docking library is installed today.

**Spike protocol** (scratch branch, never merged; delete after measuring):
build the two side columns plus one centre pane under the candidate, mount one
real semantic surface inside a library-owned pane, and measure four things.

| Measure | Pass | Fail |
|---|---|---|
| Deterministic DOM across two renders of the same state | Byte-identical class names and attribute order in both renders (the visual comparator needs this) | Any generated id or class differs between renders and cannot be neutralised by configuration |
| Svelte 5 mount/unmount inside a library-owned pane | A surface mounts, updates from live state, and unmounts with no "node not found" or double-teardown error, after two full drag cycles | Any teardown error, orphaned node, or need to patch the library |
| `[data-design-language]` scoping still applies inside the pane | The language stylesheet's rules match elements inside the pane (the attribute is on the semantic-vertical root, so a pane reparented outside that root breaks it) | Rules stop matching, or the fix requires a second activation mechanism — which MOR-1278 forbids |
| Keyboard traversal | Every panel reachable and reorderable from the keyboard alone | Mouse-only, or focus is lost after a move |

Option (3) is measured against the same four rows on the same branch, using
the existing `createDragReorder` — otherwise the comparison measures "new code"
against "no code" rather than the three options against each other.

## 7. Guards for declared groups

- **Registry-derived inventory test.** Enumerate a group's members from its
  declaration and compare against the rendered DOM, so a member lost in a
  refactor reddens. This is the discipline
  `presentation/languages/__tests__/layout-compatibility-inventory.test.ts`
  already uses on the layouts barrel (its own header explains why the list is
  derived structurally rather than hand-listed) and
  `presentation/layouts/__tests__/loader-identity-inventory.test.ts` uses for
  loader identity.
- **One visual baseline per group.** `frontend/fixtures/catalog.ts`'s
  `PEER_SPLIT_FIXTURES` carries one fixture, `peer-split-chassis`, and its own
  comment says its `expect` field is deliberately absent — the fixture is "for
  LOOKING at the chassis, not for pinning its behavior" (read at `b197b09a`).
  Proposed crop rectangles, in pixels of
  `docs/screenshots/hero.png` (1280×672), each verified by cropping with
  `sips -c <h> <w> --cropOffset <top> <left>` and viewing the result:
  MAIN VFO glass `x 180–570, y 30–240` (390×210); SUB VFO glass
  `x 710–1105, y 30–240` (395×210); station-meters strip `x 6–1276, y 568–668`
  (1270×100).
- **Picker-escape property.** MOR-2237's property — a face selected from the
  picker can always be left again — generalises to every selectable face,
  including any face composed of groups; it is a property over
  `skins/registry.ts: resolveSkinId` and the picker array in
  `components-v2/layout/StatusBar.svelte`, not over a single skin.
- **What `PeerSplitLayout.component.test.ts` pins, and what it cannot.** It
  pins: the glass element exists and carries `peer-split-glass`; the stage
  element's inline `width`/`height` are `1280px`/`540px`; the dual
  composition's real zones (`channel-strips`, `cockpit-global-row`,
  `rx-tx-zone`, `tx-aux-surface`, `meters-surface`, `scope-display-surface`)
  are inside the glass; the wall clock renders; and nine CSS declarations read
  as text out of the component's own `<style>` block across three cases —
  seven for the bezel (`position`, `overflow`, `box-sizing`, `padding`,
  `border`, `border-radius`, `background`), the `minmax(72px, 1fr)` row, and
  `grid-template-columns: subgrid`. It cannot pin the grid itself — the file's own
  comments say a `getComputedStyle(...).display === 'grid'` assertion does not
  hold in jsdom — so nothing in CI reddens if the placement is wrong while the
  elements are all present.

## 8. Vocabulary check against the target

The claim under test is ruling 5's «они все у нас описаны». **Counting rule:**
one row per element I can name in `docs/screenshots/hero.png` at 1280×672 that
is a distinct visible control or readout group — a titled panel, a glass, a
toolbar, a strip, the top bar, or a labelled cluster inside one of those. A row
counts as *found* when a file in the tree at `b197b09a` renders that element's
function today, whether or not it looks the same as it did at 1.0.0; a
difference of appearance is recorded under the table, never in the
found/not-found column. Three things sit deliberately outside the enumeration:
single buttons and chips inside a cluster, carried by their container's row
(the glass header's `TX`/`BW3.2K` chips, `TUNE`, `SPLIT TX`, `AGC SLOW`, and
each glass's `ATT`/`P.AMP`/`IP+`/`NB`/`NR`/`NOTCH`/`DIGI-SEL`/`RFG` strip);
per-panel chrome — the chevron and the drag handle — which §6 treats as a
workspace gesture rather than an instrument; and the spectrum's own axis
labels. Two rows sit finer than that level, `DUAL-W` and `CW CONSOLE`, and are
kept at it because an earlier draft of this section scored both as absent.

Rows enumerated this way: 22 — **22 found, 0 not found.** That number is a
property of this enumeration, not of the screen: a finer reading of the same
image, one row per labelled button, yields more rows, and I did not enumerate
at that level.

| Element in `hero.png` | Renders today |
|---|---|
| Top bar, DISCONNECT/OFF | `components-v2/layout/StatusBar.svelte` |
| Connection-status indicator cluster, top left | `StatusBar.svelte`'s `status-indicators` block — `role="status"` spans for radio, control, scope, audio and HTTP. How many render is capability-gated, not fixed: the scope span is behind `hasAnyScope()` and an undeclared `scopeDisplay`, the audio span behind `hasAudio()`, so the five in the image are what this capability set yields |
| Skin picker "SDR SCREEN (TEST)" | `StatusBar.svelte`'s `skinOptions` |
| RF FRONT END panel | `components-v2/panels/RfFrontEnd.svelte`; semantic twin `semantic/RfFrontEndSurface.svelte` |
| MODE panel | `components-v2/panels/ModePanel.svelte` — no own semantic surface; `SEMANTIC_SURFACE_NAMES` has no `mode`, and mode facts reach `semantic/FilterSurface.svelte` through the view model's `modeFilter` group |
| FILTER panel | `components-v2/panels/FilterPanel.svelte`; `semantic/FilterSurface.svelte` |
| MAIN VFO glass | `components-v2/layout/VfoHeader.svelte` → `components-v2/panels/vfo/DualVfoDisplay.svelte` → `components-v2/vfo/VfoPanel.svelte`; semantic twin `semantic/VfoSurface.svelte` |
| SUB VFO glass | same components, `sub` tile |
| S-meter inside each glass | `components-v2/meters/LinearSMeter.svelte`, mounted by `VfoPanel.svelte` |
| `ANT1` chip in each glass | `components-v2/panels/AntennaPanel.svelte` (its `ANT1`/`ANT2` buttons); semantic twin `semantic/AntennaSurface.svelte`, which renders `ANT {port}` over `ANTENNA_PORTS` |
| `RIT` / `XIT` rows in each glass (`+0.00 kHz`) | readout in `components-v2/vfo/VfoPanel.svelte` (its `rit-label` span); controls in `components-v2/panels/RitXitPanel.svelte`, semantic twin `semantic/RitXitScanSurface.svelte` |
| Dual column (MAIN/SUB, A=B, A≠B, SPLIT, SPEAK) | `VfoHeader.svelte`'s bridge block plus `components-v2/vfo/VfoOps.svelte` and `components-v2/vfo/ActiveReceiverToggle.svelte` |
| DUAL-W button in that column | **found** — `components-v2/vfo/VfoOps.svelte` renders it as the `data-op="dw"` bridge button labelled `DW`, pinned by `components-v2/vfo/__tests__/VfoOps.isolated.test.ts`, whose two double-click cases select the button by that trimmed text. The literal string `DUAL-W` is absent from `frontend/`: `git grep -n "DUAL-W" b197b09a` returns three hits — two in `docs/plans/`, one in `src/rigplane/runtime/_poller_types.py` — and none of them renders anything |
| Scope toolbar | `components/spectrum/SpectrumToolbar.svelte`; semantic twin `semantic/ScopeControlsSurface.svelte` |
| `BANDS` control in that toolbar | `SpectrumToolbar.svelte`'s band-plan toggle (`showBandPlan`, in its `bands-group`). No semantic twin, by that file's own header: `BANDS`/layers is one of the client-side view options with no wire field and no field-status entry, category (b) of the S10 boundary ruling, so `ScopeControlsSurface.svelte` contains no band vocabulary at all (`grep -cni band` over it returns 0) |
| Spectrum + waterfall | `components/spectrum/SpectrumCanvas.svelte` and `WaterfallCanvas.svelte`, under `SpectrumPanel.svelte` |
| RX AUDIO panel | `components-v2/panels/RxAudioPanel.svelte`; `semantic/RxAudioSurface.svelte` |
| AUDIO SCOPE panel | `components-v2/panels/audio-scope/AudioSpectrumPanel.svelte` — no semantic surface name for it |
| DSP panel | `components-v2/panels/DspPanel.svelte`; `semantic/DspSurface.svelte` |
| TX panel (PTT, ATU, TUNE) | `components-v2/panels/TxPanel.svelte`; `semantic/RxTxSurface.svelte` plus `semantic/TxAuxSurface.svelte` |
| STATION METERS strip | `components-v2/panels/MetersDockPanel.svelte`; `semantic/MetersSurface.svelte` over `LinearSMeter.svelte` / `BarGauge.svelte` |
| CW CONSOLE pull-out | **found** on function — `components-v2/panels/CwPanel.svelte` carries the CW controls and is mounted by `LeftSidebar.svelte`, `RightSidebar.svelte`, `RadioLayout.svelte` and `MobileRadioLayout.svelte`; `semantic/CwKeyerSurface.svelte` is its semantic twin, wired by `SemanticRadioSurfaces.svelte` and declared in `presentation/layouts/desktop-declarations.ts`. Neither is a pull-out — a difference of form, recorded below. The literal name is absent at that revision: `git grep -i "cw console" b197b09a` returns nothing, while at this PR's own head it matches this document |

So the owner's claim holds for every row as counted above. Two rows are found
on function while differing in form from the 1.0.0 image, and neither
difference is a missing instrument: the dual-watch toggle is labelled `DW`
today rather than `DUAL-W`, and the CW console is a docked sidebar panel rather
than a pull-out from the meters strip. A pull-out is a workspace gesture, which
is §6's question, not a member the vocabulary would have to name. Not
established: whether the 1.0.0 screenshot shows any mockup element —
`docs/screenshots/hero.png` was committed at `24cb260c` (2026-05-01) per the
input file, and I did not read that revision's source.

## 9. Migration order and deletions

1. **Peer-split glass first**, after MOR-2243 lands (it moves the glass into
   `LcdLayout` as `variant="peer-split"` and renames the id — input file §5).
   No instrument changes: the members are the surfaces the dual composition
   already mounts, so this slice tests the declaration and nothing else.
2. **The SDR deck** (MOR-2231). Its decomposition items 1–2 become, under a
   group: item 1 the group declaration for the two VFO glasses and the meters
   strip; item 2 the shell that resolves it and passes `canvas` down as props.
   Everything about placement moves out of skin CSS into the declaration.
3. **The LCD face** (MOR-2232) last: it is the one target with no live
   consumer of its declared native size today
   (`presentation/layouts/lcd-declarations.ts`'s `LCD_NATIVE_STAGE` is declared
   and nothing reads it back), so it is the cleanest test that the shell path
   works from a cold start.

**D2 as an immediate separate PR**, independent of every decision here.
`frontend/src/primitives/stage/ScaledStage.svelte` exists and
`skins/segmentline/PeerSplitLayout.svelte` is its one production importer
(`git grep -n "import ScaledStage"` at `b197b09a` returns that file and the
primitive's own isolated test), yet five source sites plus the v3 plan still
say otherwise. Per the delete-don't-narrow rule the false clauses come out
rather than being rewritten:

| Site | False clause |
|---|---|
| `presentation/layouts/contract.ts` | "the future `ScaledStage` primitive"; "until that primitive exists" |
| `presentation/layouts/__tests__/stage-sizing-boundary.test.ts` | "until the ScaledStage primitive … exists" |
| `presentation/layouts/dual-receiver-cockpit.ts` | "banned while the ScaledStage primitive does not exist" |
| `presentation/layouts/lcd-declarations.ts` and `mobile-declarations.ts` | MOR-1160 "froze without implementing it" — stale; the same sentence's claim that the primitive owns measurement and the transform is now true and stays |
| `docs/plans/2026-07-25-ui-composition-architecture-v3.md` | "the future `ScaledStage` primitive, which will own …"; "until `ScaledStage` exists to enforce it" |

**D1, D3, D4 are options, not decisions.** D1: the viewport/topology half of
`contract.ts` — `fitsViewport`, `resolveLayoutForViewport`,
`resolveLayoutForTopology`, `supportsTopology`, `resolveFallback`,
`listLayoutIds` — has no caller outside `presentation/layouts/` and its own
tests (`git grep` for the four exported names over `frontend/src` excluding
`__tests__` at `b197b09a` returns only their definitions and their internal
uses inside `contract.ts`). Keep it awaiting its consumer, or delete and re-add
when a group needs it. D3 (`minScale`): make the group declaration its reader,
or delete it with D1. D4 (`LayoutManifest.loader` invoked only in one test,
while `SKIN_LOADERS` is the real load path — from the input file's audit, not
re-derived here): pick one loader mechanism, or leave both until the group work
says which one a group uses.

## 10. Decisions reserved for the owner

1. **Vocabulary set** — Set A (`layout`/`panel`/`glass`/`instrument`): no new
   words, but `panel` collides with 277 files including the tree being
   removed. Set B (`face`/`rack`/`group`/`instrument`): near-zero collisions,
   but three new words to teach.
2. **Shape** — (a) zone extension: fewest new files, most existing tests
   touched, grid-as-data strains "a manifest is a declaration". (b) separate
   group node: expresses all three targets, costs a second registry and
   validator. (c) hand-written components: cheapest now, but "faces are data"
   (§1) rules it out for the group itself — see §5.
3. **Workspace library** — Dockview: docking and keyboard for free, ~81 kB
   plus glue and a DOM-ownership risk. Gridstack: grid resize for free, random
   ids to neutralise and accessibility declared wontfix. No library: no glue
   and no new risk, but every gesture is ours to write and maintain.
4. **Non-goal amendments** — adopt §3's two replacement bullets as written,
   amend the wording, or leave the v3 plan's list unchanged.
5. **Public contract for the declaration format** — the owner said «возможно
   позже, да»: publish the schema now (community can author, and the format
   becomes expensive to change), or keep it internal until the third face
   exists.
6. **Scaling default for a new group** — `fixed-native` (matches real
   instruments and any SDR face) or `reflow` (matches web panels with
   buttons), per ruling 2. A default decides what an author gets when silent.
7. **D1 / D3 / D4** — keep the dead viewport half, `minScale`, and the manifest
   `loader` awaiting consumers, or delete each. Keeping costs nothing today but
   keeps five unused exports; deleting costs a re-add if the group work needs
   them.
8. **Migration entry point** — start on peer-split (smallest, but its file is
   in flight under MOR-2243) or on the SDR deck (the default face, larger, and
   what the owner actually looks at).
