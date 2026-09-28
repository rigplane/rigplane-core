# MOR-2215 / MOR-2425 acceptance census — origin/main @ 3c19f5c9 (2026-09-28)

Read-only census. Evidence is file:symbol / test name / commit or PR from
`origin/main`. No tests were run; no Linear ticket besides the provided text
was opened (MOR-2425 description intentionally not read).

---

## MOR-2425 — supported v3 host boundary for external component kits

### 1. Versioned public interfaces + shared mechanism, no private paths — **MET**

- Public versioned package: `frontend/component-kit-api/` (`@rigplane/component-kit-api`
  0.6.0), `COMPONENT_KIT_API_VERSION = 1`, `defineComponentKit`
  (`frontend/component-kit-api/src/index.ts`); mismatched `apiVersion` rejected at
  activation (`frontend/src/component-kits/activation.ts`).
- No deep private paths: `verify-package.mjs: assertFixturePublicImports` (fixture may
  import only `@rigplane/component-kit-api` / `svelte`) and `assertClosedDeclarations`
  (emitted `.d.ts` may not import `$lib` or absolute frontend paths); consumer-side
  `@ts-expect-error` battery pins that host authority/factories/subpaths stay
  unexported (`frontend/component-kit-api/verify-package.mjs`, lines ~632–726).
- Shared mechanism, no duplicate runtime: kits receive host-owned seats/leases
  (`ScalarRendererSeat`/`ScalarRendererLease`, `MeterRendererSeat.svelte`,
  `HostedFaceInstrumentBridge.svelte` passes only `Snippet` handles — never runtime,
  store or transport; `docs/architecture/component-kit-sdk.md` §1/§4); registrations
  merge into the host registries with duplicate ids rejected
  (`activation.ts: prepareActivation`; tests `activation.test.ts:414-418`).
- Commits: 2f621d55 (#3283), 91c275bb (#3332), 07ff1d3d (#3338), 4191b431 (#3344).

### 2. Documented single activation/configuration boundary — **MET**

- Boundary: `frontend/component-kits.config.ts` (`ComponentKitHostConfig`:
  `{ kits: [...loaders], selection? }`), activated exactly once before App mount in
  `frontend/src/main.ts: startApp` (`activateComponentKits(config)`); second call
  throws. Documented in `docs/architecture/component-kit-sdk.md` §5.
- Another kit needs no host source edits beyond adding its loader to that config;
  selection ids validated at activation (`Selected <kind> ... is not registered.`).
- Package install does not rewrite host sources: pack shape asserted to be exactly
  `package.json` + `dist/` (`verify-package.mjs: assertPackedShape`); no runtime
  "install a kit" API exists (docs §5, §8 "no hot install").
- Commit: 35e2b95a (#3285) "activate configured component kits before App mount".

### 3. Behavior/state preservation, no fabricated values — **MET**

- Status vocabulary carried end-to-end: scalar view has `reading.status`,
  `confirmed/target/requested/phase/error` (`verify-package.mjs` scalarView fixture);
  meter views carry `evidence.state` current/stale/idle/unknown/unsupported plus
  unknown-unit and raw domains (same file, `signals`/`levels` fixtures; browser
  assertions on `data-state`/`data-domain`/`data-value`).
- Policies independent of appearance: behavior lives in host/shared modules
  (`frontend/src/primitives/scalar/committed-scalar.svelte.ts`,
  `frontend/src/primitives/control-instruments/control-instrument-behavior.ts`),
  renderers are pure appearance; "no ACK-as-confirmation" is the committed-presentation
  rule (8becb9a4 #3306 "bind rendered metadata to committed presentation";
  `frontend/src/semantic/pbt-presentation-continuity.test.ts` "does not fabricate a
  value when no prior reading exists").
- Cancellation/replacement: lease `cancel/cancelPointer/dispose` surface; face
  replacement keeps owners (`external-hosted-face.component.test.ts`: "re-creates the
  face on an occurrence change with the same component and keeps its owners",
  "keeps one station owner set across an A-B-A occurrence swap", "disposes the station
  reset leases and its subscription on unmount"); verify-package pins that a retained
  pre-swap DOM handle goes inert (`__retainedEqualize.click()` → 0 invocations).

### 4. Assets/styles resolve; coexistence/cleanup; local-extension & App resources — **MET**

- Styles: fixture faces ship real `<style>` blocks
  (`fixtures/external-kit/src/FaceA.svelte`), compiled and rendered from the packed
  tarball in the Playwright run.
- Coexistence/cleanup: duplicate kit/appearance ids across kits rejected
  (`activation.test.ts:414-418`); renderer detachment/disposal proven
  (`__hideConcreteRenderers` → detached + disposals counted in `verify-package.mjs`).
- Local extension version/transport-result: `frontend/src/lib/local-extensions/`
  (host versioning + canonical transport results) landed and pinned by MOR-2339 —
  commit 2b04c56d (#3185) "version the extension host and preserve canonical
  transport results", tests `host-api.isolated.test.ts`, `manifest.test.ts`,
  `radio-intents.isolated.test.ts`.
- App resources: `SKIN_LOADERS` resource bridging (hardware-scope, audio-fft) in
  `frontend/src/skins/registry.ts` unchanged by kit work; hosted declarations'
  `resources` ride the same bridge (`activation.ts`, docs §4).

### 5. Packed external fixture in isolated validation — **MET**

- `frontend/component-kit-api/verify-package.mjs`: `npm pack`s the API and the fixture
  kit, builds a temp consumer via `file:` tarball deps in `mkdtemp` isolation,
  type-checks, vite-builds, serves over real HTTP and drives Chromium.
- Families exercised: scalar, frequency, finite (action/toggle/choice incl. disabled
  option + reason), meter (signal: 5 evidence shapes; level: 7 states incl. stale,
  idle, unknown, unsupported, ratio) plus all four hosted families — far beyond the
  "two component families" bar.
- Two compositions + swap: FaceA vs FaceB with reversed family order
  (`assertHostedFaceSources` pins the difference); `__setFace('b')`/`'a'` swap asserts
  same seats/I/O and single authority (retained handle inert, live handle counts).
- Wired into required CI: `.github/workflows/quick.yml:281`
  (`npm run verify:component-kit-api`) via `frontend/package.json` script; consumer
  peer-dep fix 5a140f8b (#3600).

### 6. RED/GREEN + review + docs; no registry publication — **MET**

- Docs: `docs/architecture/component-kit-sdk.md` (f8ca5551, #3346) — contract,
  activation, installation, verification, **compatibility matrix** (§7, SDK 0.1.0 →
  0.6.0 with PR numbers) and **remaining explicit limits** (§8), all citing real
  types/symbols; `docs/architecture/building-a-skin.md` updated alongside.
- Review discipline: the PR chain #3283–#3346 carries per-head Agent Review PASS
  evidence (repo convention; e.g. commit messages record "Agent Review PASS at <sha>").
- No publication: `"private": true` in `frontend/component-kit-api/package.json`;
  docs §5 states it is not published to any registry from this repository.

---

## MOR-2215 — v3 acceptance: behavior/render separation + Standard 2.11.1

### 1. Current inventory of every instrument + live consumer — **PARTLY**

- Surface-level mapping exists and is strong: `docs/validation/desktop-v2-v3-parity.md`
  (parity matrix P1–P23 with verdicts, evidence, dispositions, mutation kills).
- Instrument-level enumeration does **not** exist in the repo: the 2026-09-02 addendum
  (`docs/plans/2026-04-12-target-frontend-architecture.md`, commit 189af93c #3025)
  records a name census at `fef3431e` and defers: "The authoritative, current
  enumeration is MOR-2216" — i.e. the stale 2026-09-02 Linear comment. Since then
  large migration/refactor programs landed (MOR-2688 value-rule slices, MOR-2704
  `usable` gate, primitives/ scalar + control-instruments) with no updated inventory;
  `docs/plans/2026-08-29-v3-defect-inventory.md` explicitly declares itself not a
  tracker.
- Smallest closing change: one current inventory doc (e.g.
  `docs/plans/instrument-inventory.md`) enumerating each instrument family
  (button/toggle, scalar, meters, frequency, choice, state readout), its home
  (`primitives/` vs legacy), every live consumer, and its conformance proof
  (checklist item or ticket), generated from the tree. ~1 file, ~200–350 lines;
  refresh the addendum's "Where instruments live today" paragraph to point at it.

### 2. Semantic props/intents, no runtime/store/protocol imports, honest state — **MET**

- Semantic props/intents: `RadioViewModel` + typed callback intents
  (`frontend/src/semantic/radio-view-model.ts`; addendum conformance checklist item 2).
- Import bans enforced and proven: `frontend/eslint.config.js`
  (`FORBIDDEN_SEMANTIC_IMPORTS`, `FORBIDDEN_PRIMITIVES_IMPORTS`,
  `FORBIDDEN_PRESENTATION_IMPORTS`, panels Tier-2 lockdown, skins store ban MOR-2039)
  plus AST-level catch-all 3232dbf3 (#3856, MOR-2719); boundary rules are themselves
  tested in `frontend/src/__tests__/architecture-boundaries.test.ts` (MOR-1061).
  Sanctioned adapter bridge (`lib/runtime/adapters/*`) is the documented exception,
  not a violation.
- Honest unknown/confirmed/requested: `pressed-of.ts` (MOR-1358, P13 closed),
  `primitives/reading-text.ts` + MOR-2688 value rule (a48c1c0f, 685ee822, 5ddc3a29),
  `usable` gate (MOR-2704, cd0515c3 #3785); explicit behavioral options with disabled
  reasons (0aaabbbe #3318).

### 3. Same behavior via distinct visuals and two compositions — **MET**

- Distinct visual implementations: three design languages
  (`frontend/src/presentation/languages/{fieldline,segmentline,studioline}`) over the
  same surfaces; component-kit appearance swap keeps I/O (MOR-2425 evidence above).
- Two+ compositions mounting the same semantic behavior:
  `components-v2/wiring/SemanticRadioSurfaces.svelte` is consumed by desktop-v2
  (via `RadioLayout`), `lcd-cockpit`, `dual-receiver-cockpit`, `dual-sdr-face`,
  `flagship-probe`, segmentline `PeerSplitLayout` (grep across `frontend/src/skins`).
- Equivalent command/confirmation/error handling pinned by property-based continuity:
  `frontend/src/semantic/__tests__/pbt-presentation-continuity.test.ts` and
  `components-v2/wiring/__tests__/semantic-pbt-continuity.component.test.ts`.

### 4. Preserved native/keyboard/touch/accessibility/reconnect/stale/pending behavior — **MET**

- `docs/validation/desktop-v2-v3-parity.md` rows P1–P23 cite discriminating tests per
  family (e.g. `VfoSurface.test.ts` unknown-split tri-state + MUTATION KILL,
  `RxAudioSurface.test.ts` link-lost epistemic pins, `MetersSurface.isolated.test.ts`
  peak/fault channels), each with named mutants killed.
- Keyboard/native/touch paths are part of the pinned surface contracts
  (`FrequencyInteraction` handleWheel/handleKeyDown/handleDigitClick exercised in
  `verify-package.mjs`; scalar native input + key path asserted there).

### 5. Standard reuses the v2.11.1 appearance/components/grid — **MET**

- `standard` preference maps to `desktop-v2`
  (`frontend/src/skins/registry.ts: resolveSkinId`), whose entrypoint is still
  `frontend/src/skins/desktop-v2/DesktopSkin.svelte` mounting the same
  `components-v2/layout/RadioLayout.svelte` grid/sidebars/theme that v2.11.1 shipped
  (compare `git show v2.11.1:...`), now receiving `InstrumentComposition` from the
  App's persistent semantic host (MOR-1266 declarations,
  `presentation/layouts/desktop-declarations.ts`).
- No legacy radio truth/transport ownership reintroduced: legacy twins are suppressed
  by manifest declaration (MOR-1313 — RadioLayout reads `declaredSurfaces`, not skin
  ids); desktop-v2 declares all 16 surfaces, so semantic surfaces render; TX goes
  through the managed controller facade only (`getManagedAppTxController`).

### 6. Migrated production consumers use accepted common contracts — **MET** (migration itself unfinished)

- Every skin-level consumer composes through `SemanticRadioSurfaces` /
  `InstrumentComposition`; migrated instruments live under `frontend/src/primitives/`
  (frequency, scalar, control-instruments, control-feedback, stage) with contract
  tests (`primitives/*/__tests__`, `METER_REGISTRY` meters census gate).
- Not a completion claim: meters (`components-v2/meters/BarGauge|LinearSMeter`),
  buttons (`frontend/src/lib/Button/`) and LCD instruments
  (`components-v2/panels/lcd/Amber*`) still await their one-instrument-per-PR moves,
  and the legacy tree's removal is an owner decision recorded in MOR-2215 — see
  line 1 gap.

### 7. Independent review, required checks, combined candidate — **PARTLY**

- Per-slice process is in place and evidenced: the MOR-2215-linked PR chain (e.g.
  #3025 addendum, #3260, #3232–#3234, #3759; MOR-2688/#3763–#3808, MOR-2704/#3785–#3800)
  merged under exact-head Agent Review + required `quick` checks; commit messages
  record PASS SHAs.
- The umbrella itself is not closed: no final combined-candidate evidence for the
  whole ticket exists on `main`, and the ticket text itself defers operator
  acceptance to MOR-1413. Closing line 7 needs the remaining instrument migrations
  (line 1 gap), one final combined head with its natural `quick`/`visual` and one
  fresh exact-head review — size is the remaining program, not a single edit.

### Required whole-path mechanism audit — **NO**

No audit under `.claude/audits/` covers the whole instrument program. The directory
holds archived, point-in-time reports, each pinned to one revision or one diff
(`.claude/audits/README.md`: "Each report pins the exact revision it audited"); the
closest in scope are `2026-09-26-mechanism-audit-frontend.md` (scoped to one diff,
MOR-2478) and the per-slice MOR-2688/MOR-2704 audits. A whole-program audit remains
to be run by the coordinator.

---

## Summary

| Ticket | Line | Status |
|---|---|---|
| MOR-2425 | 1 interfaces/mechanism | MET |
| MOR-2425 | 2 activation boundary | MET |
| MOR-2425 | 3 behavior preservation | MET |
| MOR-2425 | 4 assets/coexistence/extensions | MET |
| MOR-2425 | 5 packed fixture proof | MET |
| MOR-2425 | 6 review/CI/docs/no publication | MET |
| MOR-2215 | 1 current inventory | PARTLY |
| MOR-2215 | 2 semantic contracts | MET |
| MOR-2215 | 3 behavior across visuals/compositions | MET |
| MOR-2215 | 4 preserved behaviors + tests | MET |
| MOR-2215 | 5 Standard 2.11.1 reuse | MET |
| MOR-2215 | 6 consumers on common contracts | MET |
| MOR-2215 | 7 review/CI/combined candidate | PARTLY |
| MOR-2215 | whole-program mechanism audit | not present |
