# The component-kit SDK

A component kit is a plain data object shaped by `ComponentKitDeclaration`
(`frontend/component-kit-api/src/index.ts: ComponentKitDeclaration`), built
with `defineComponentKit`, that registers renderer appearances and,
optionally, whole hosted "faces" for the frontend to load. This document
covers the public contract, its activation, verification and known limits.
Everything below points at real, current source under `frontend/`.

## 1. What a component kit is and is not

A kit takes one or both of two shapes:

- **Appearance kit** — registers one or more renderer sets (scalar,
  frequency, finite-control, meter appearances) that get swapped into the
  host's own built-in semantic surfaces. The kit supplies rendering only; the
  host still owns state, layout and commands.
- **Hosted-face kit** — additionally ships a `LayoutManifest` and a
  `HostedFacePresentationV1`, whose `loader` resolves a whole Svelte
  component (a "face") that the host mounts in place of its own semantic
  surfaces for the families that face's layout admits (§4).

A hosted face is not a way to reach new radio state or bypass the host: it
receives only opaque Svelte `Snippet` handles through `HostedFacePropsV1`
(§4) — never the runtime, a store, transport, or a command object
(`frontend/src/component-kits/HostedFaceInstrumentBridge.svelte` passes only
these snippets to `<Face>`).

Component kits are part of the open-core frontend surface this repository
ships; see `docs/architecture/open-core-policy.md` for the general
open-core/Pro boundary rules the rest of the frontend follows (that document
does not name component kits specifically).

## 2. Contract surface

The package is `@rigplane/component-kit-api`, currently version `0.6.0`,
declared `"private": true` (`frontend/component-kit-api/package.json`; see
§5 on what that means for installation). `COMPONENT_KIT_API_VERSION` is the
literal `1` (`frontend/component-kit-api/src/index.ts`); a declaration whose
`apiVersion` does not match it is rejected at activation with:

```
Component kit "<id>" requires unsupported API version <version>.
```

A declaration's accepted top-level keys are exactly `apiVersion`, `id`,
`scalarAppearances`, `frequencyReadouts`, `finiteControlAppearances`,
`meterAppearances`, `designLanguages`, `layouts`, `instrumentGroups`,
`presentations` (`frontend/src/component-kits/activation.ts: KIT_KEYS`) — any
other key throws (`ownDataEntries`, `"<owner> has unknown property "<key>"."`).
Of those, two are recognized only to be rejected outright,
`UNSUPPORTED_SECTIONS` (`activation.ts`):

```
Component kit "<id>" declares unsupported property "<designLanguages|instrumentGroups>".
```

So `designLanguages` and `instrumentGroups` are parseable by the type system
(`ComponentKitDeclaration` still types them) but never reach the running app.

## 3. Appearances

Four appearance categories, each a record of `id -> appearance` merged
across activated kits, with duplicate ids across kits rejected
(`activation.ts: prepareActivation`, `"Duplicate <kind> "<id>" conflicts
with <owner>."`). Scalar appearances additionally seed from the host's own
built-in scalar appearances before any kit is merged in
(`activation.ts: prepareActivation`, iterating
`builtInScalarAppearances`) — the other three categories have no built-in
seed, only what kits themselves register:

| Category | Type | Required members |
|---|---|---|
| Scalar | `ScalarAppearance` (`component-kit-api/src/index.ts`, aliasing `Skin` in `frontend/src/components-v2/controls/value-control/skin.ts`) | `name: string`; optional renderer components `hbar`, `knob`, `bipolar`, `discrete`, one `Component` per continuous-control form |
| Frequency | `FrequencyRenderer = Component<FrequencyRendererProps>` (`component-kit-api/src/index.ts: FrequencyRenderer`, `FrequencyRendererProps`) | a single renderer component, not a per-form map |
| Finite control | `FiniteControlAppearance` (`component-kit-api/src/index.ts`, aliasing `frontend/src/primitives/control-instruments/control-instrument-renderer.svelte.ts: FiniteControlAppearance`) | `action`, `toggle`, `choice` — all three required `Component`s (`activation.ts: FINITE_APPEARANCE_KEYS`) |
| Meter | `MeterAppearance` (`component-kit-api/src/index.ts`) | `signal`, `level` — both required `Component`s (`activation.ts: METER_APPEARANCE_KEYS`) |

Unlike the scalar category, finite-control and meter appearances have no
optional members: activation requires every one of `action`/`toggle`/`choice`
and `signal`/`level` to be a function, or it throws
(`copyFiniteAppearance`/`copyMeterAppearance` in `activation.ts`).

## 4. Hosted faces

`HostedFacePresentationV1` (`component-kit-api/src/index.ts`) has exactly
six fields: `hostMode` (the literal `'external-instruments-v1'`), `id`,
`layoutId`, `loader: () => Promise<HostedFaceComponentV1>`, `resources`
(`PresentationResources`, §8), and `appearances`
(`HostedFaceAppearanceIdsV1`: `scalar`, `frequency`, `finite`, `meter` ids,
all required — `activation.ts: FACE_APPEARANCE_KEYS`).

**Layout rule.** A hosted-face kit ships its own `LayoutManifest`(s) via
`layouts` (`component-kit-api/src/index.ts: LayoutManifest`, an `Omit` of the
host's manifest type without `loader`). At activation each layout is
structurally validated and then checked for an id collision against both
already-registered layouts and reserved/built-in ids
(`activation.ts: prepareActivation`, `getLayout(layout.id) !== undefined ||
isPresentationIdReserved(layout.id)`), throwing `Layout id "<id>" is
registered or reserved.` on a collision. A presentation's `layoutId` must
name a layout from the *same activation batch* — not merely any registered
layout — or activation throws `Presentation "<id>" layout "<layoutId>" is not
in the activated batch.` (`activation.ts`).

The manifest is a declarative admission ticket, not a runtime grid: its
zones each name which semantic surfaces they admit
(`LayoutZone.surfaces`, `frontend/src/presentation/layouts/contract.ts`).
`frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte` derives each
family's admission from whether some zone in the resolved surface plan
includes the `vfo`, `txAux`, or `meters` surface
(`zoneOwning(surface): string | null`, same file) and
passes four booleans into
`frontend/src/component-kits/HostedFaceInstrumentBridge.svelte`
(`receiverAdmitted`, `vfoOperationsAdmitted`, `txAuxAdmitted`,
`stationMetersAdmitted`) — both `receiver` and `vfoOperations` are gated on
the same `vfo` zone. The face itself never receives the manifest or any
layout geometry, only the boolean-derived family objects below.

**`HostedFacePropsV1`** is `{ instruments: HostedInstrumentFamiliesV1 }`
(`component-kit-api/src/index.ts`). The four families and every handle name:

| Family | Type | Handles |
|---|---|---|
| `receiver` | `ReceiverInstrumentFamilyV1` | `mainFrequency`, `subFrequency`, `mainSMeter`, `subSMeter` (4) |
| `vfoOperations` | `VfoOperationInstrumentFamilyV1` | `split`, `dualWatch`, `activeReceiver`, `equalize`, `swap`, `quickSplit`, `quickDualWatch`, `speak` (8) |
| `txAux` | `TxAuxInstrumentFamilyV1` | `rfPower`, `micGain`, `driveGain`, `voxGain`, `antiVoxGain`, `voxDelay`, `compressorLevel`, `monitorLevel` (8) |
| `stationMeters` | `StationMeterInstrumentFamilyV1` | `signal`, `power`, `swr`, `alc`, `drainCurrent`, `drainVoltage`, `compression` (7) |

(all from `component-kit-api/src/index.ts`; test title covering the first
three: "mounts the accepted faces through the exact 4+8+8 mapping and
retires old occurrence commands",
`frontend/src/__tests__/external-hosted-face.component.test.ts`.)

**Nullability** works at two levels. All four `HostedInstrumentFamiliesV1`
keys are required but individually nullable — `null` exactly when the
corresponding zone is not admitted
(`HostedFaceInstrumentBridge.svelte: receiverAdmitted ? {...} : null`, and
likewise for the other three). Within an admitted family it varies: in
`receiver`, `mainFrequency`/`mainSMeter` are always present but
`subFrequency`/`subSMeter` are independently nullable, driven by whether the
radio has a SUB receiver (`subReceiverAdmitted`, same file); every one of
`vfoOperations`'s eight handles is individually nullable in its own type
(`VfoOperationInstrumentFamilyV1`); none of `txAux`'s eight or
`stationMeters`'s seven handles are nullable — all required once their
family is non-null (`TxAuxInstrumentFamilyV1`, `StationMeterInstrumentFamilyV1`,
all three types in `component-kit-api/src/index.ts`).

**What a face receives, and does not.** Only the snippet handles above — no
owner object, no lifecycle hook, no plan object, and no command or runtime
reference (`HostedFaceInstrumentBridge.svelte` builds each snippet purely by
re-rendering the corresponding host handle; nothing else is passed to
`<Face>`).

**Occurrence replacement.** `SemanticRadioSurfaces.svelte` wraps the bridge
in `{#key externalPresentation}` (the `{#if externalPresentation}` branch
that renders `HostedFaceInstrumentBridge`), so a new presentation load (a
fresh loader generation in `App.svelte: requestPresentation`) tears down and
remounts the face and its bridge. The occurrence's `isCurrent()` predicate
(`frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:
ExternalPresentation.isCurrent`) flows down as the `presentationIsCurrent`
prop to three of the renderer-seat hosts — `ReceiverInstrumentHost.svelte`
(only its `mainFrequency`/`subFrequency` snippets, via
`FrequencyRendererSeat`), `TxAuxScalarHost.svelte` (`ValueControl`), and
`VfoOperationSeatHost.svelte` (`(presentationIsCurrent?.() ?? true) ?
combinedRendererContext : null`) — which withhold their renderer context
once the predicate is false. Test: "re-creates the face on an occurrence
change with the same component and keeps its owners"
(`external-hosted-face.component.test.ts`). This gate does not cover every
handle; see the §8 limit.

**Station meters** render through the presentation's own meter appearance:
`SemanticRadioSurfaces.svelte` passes
`meterAppearance={externalPresentation.record.appearances.meter}` into
`HostedFaceInstrumentBridge.svelte`, which forwards
`levelRenderer={meterAppearance.level}` to `MeterRendererSeat` in its
`stationLevel` snippet and `signalRenderer={meterAppearance.signal}` in its
`stationSignalMeter` snippet — the latter renders `MeterRendererSeat` only
when `signalFrame !== null`. A face gets no separate native DOM meter — the
file's own comment: "An external Face gets no native DOM meter: absent an
installed meter appearance these seats render nothing, the way `mainSMeter`
does." Test: "renders station meters from the record appearance when the
global selection is absent" (`external-hosted-face.component.test.ts`).

## 5. Activation and installation

`frontend/component-kits.config.ts` default-exports an object satisfying
`ComponentKitHostConfig` (`frontend/src/component-kits/activation.ts`):
`{ kits: readonly (() => Promise<{ default: unknown }>)[]; selection?:
ComponentKitSelection }`. As checked into this repository it ships
`{ kits: [] }` — no kit installed. `ComponentKitSelection`'s five optional
string fields (`scalarAppearance`, `frequencyReadout`,
`finiteControlAppearance`, `meterAppearance`, `presentation`) each must name
a registered id, or activation throws `Selected <kind> "<id>" is not
registered.` — for `scalarAppearance` that includes the host's built-in
scalar appearances seeded before any kit is merged (§3), e.g.
`professional` (`components-v2/controls/value-control/skins/index.ts:
skins`); for the other four, only ids the activated batch itself registered.
The presentation field's error uses the kind word `external presentation`:
`Selected external presentation "<id>" is not registered.`.

Activation happens exactly once, before `App.svelte` mounts:
`frontend/src/main.ts: startApp` awaits `activateComponentKits(config)`
before importing and mounting `App.svelte`. A second call throws
`Component kits are already activated.` (`activation.ts`). There is no
runtime "install a kit" API — changing the active kits means editing
`component-kits.config.ts` and rebuilding/reloading the app.

**Installation.** `@rigplane/component-kit-api`'s `"private": true`
(`frontend/component-kit-api/package.json`) means it is not published to any
registry from this repository. I could not establish a tracked path for
adding a kit as an ordinary `dependencies` entry pulled from a package
registry, so this document does not describe one. What is tracked: adding a
kit's default-export loader to `component-kits.config.ts`'s `kits` array
(`() => import('some-kit-module')`), and the exact mechanism
`frontend/component-kit-api/verify-package.mjs` exercises as a consumer
proof — it `npm pack`s the API and the fixture kit and writes a temporary
consumer `package.json` whose `dependencies` point at the two tarballs via
`file:` paths, then builds and runs a real app against them (§6).

## 6. Verification available to kit authors

`frontend/component-kit-api/verify-package.mjs` builds the API and the
fixture kit via `build.mjs`, then:

- checks the fixture's `FaceA.svelte`/`FaceB.svelte` sources arrange every
  `vfoOperations`, `txAux` and `stationMeters` handle exactly once, the four
  `receiver` handles at least once, and never import forbidden internals
  (`assertHostedFaceSources`);
- checks every fixture source file imports only from
  `@rigplane/component-kit-api` or `svelte`, with one exception: `index.ts`
  may also import a sibling `./*.svelte` (`assertFixturePublicImports`);
- `npm pack`s both packages and asserts the packed file list matches the
  built `dist/` output plus `package.json`, nothing else
  (`assertPackedShape`);
- asserts every emitted `.d.ts` file only imports resolvable relative paths
  or an allow-listed bare specifier — never `$lib` or an absolute frontend
  path (`assertClosedDeclarations`);
- builds a real temporary consumer package from the two packed tarballs
  (installed via `file:` dependencies) and type-checks a `.ts`/`.svelte` app
  against it, including a battery of `@ts-expect-error` assertions that
  private root exports stay unavailable and that every required family/handle
  key stays non-optional;
- serves that consumer over a real HTTP server and drives it with Playwright
  /Chromium to assert the packed runtime actually renders and behaves.

The fixture faces
(`frontend/component-kit-api/fixtures/external-kit/src/FaceA.svelte`,
`FaceB.svelte`) are the reference: they exercise every handle in all four
families in deliberately different orders (`assertHostedFaceSources` pins
that `FaceA` places its `receiver` zone before `txAux` and `FaceB` the
reverse). The accepted integration test is
`frontend/src/__tests__/external-hosted-face.component.test.ts`, describe
block `'external hosted face chain'` — see the titles quoted in §4 and §7 for
what it covers.

## 7. Compatibility matrix

Every SDK version to date is a `frontend/component-kit-api` commit on this
branch's history; only version-bumping commits are listed.

| SDK version | Commit / PR | What it added | Known consumers |
|---|---|---|---|
| 0.1.0 | `2f621d553` (#3283) "add portable component kit API" | `ComponentKitDeclaration`, `defineComponentKit`, `scalarAppearances` | fixture kit only |
| 0.2.0 | `31268ef67` (#3290) "publish finite control appearances" | `finiteControlAppearances` / `FiniteControlAppearance` | fixture kit only |
| 0.3.0 | `df8cee747` (#3330) "publish meter appearance contract" | `meterAppearances` / `MeterAppearance` | fixture kit only |
| 0.4.0 | `91c275bba` (#3332) "publish external face SDK contract" | `HostedFacePresentationV1`, `HostedFaceComponentV1`, the `receiver`/`vfoOperations`/`txAux` families | fixture kit only |
| 0.5.0 | `07ff1d3d8` (#3338) "expose scalar renderer seats" | `ScalarRendererSeat`/`ScalarRendererLease` | fixture kit only |
| 0.6.0 | `4191b4318` (#3344) "expose station meters to hosted external faces" | the `stationMeters` family (7 handles) | fixture kit only |

"Known consumers" here means consumers tracked in this repository: the
`frontend/component-kit-api/fixtures/external-kit` fixture, exercised by the
tests named above. No other consumer is tracked in this repository.

## 8. Remaining explicit limits

- **Two declaration sections are rejected outright**: `designLanguages` and
  `instrumentGroups` (§2, `UNSUPPORTED_SECTIONS` in `activation.ts`).
- **Presentation selection is single and static per config.**
  `ComponentKitSelection.presentation` is one optional string id, read once
  during the single `activateComponentKits` call at startup (§5); there is no
  per-session or per-user switch beyond editing `component-kits.config.ts`
  and rebuilding — no hot install (§5).
- **Four families exist today.** Of the declarable `SemanticSurfaceName`s
  (`frontend/src/presentation/layouts/contract.ts: SEMANTIC_SURFACE_NAMES`),
  only `vfo` (→ `receiver` and `vfoOperations`), `txAux`, and `meters`
  (→ `stationMeters`) have a corresponding `HostedInstrumentFamiliesV1`
  family (`frontend/src/component-kits/HostedFaceInstrumentBridge.svelte`'s
  `satisfies HostedInstrumentFamiliesV1` object is the live list). Every
  other name has no family in `HostedInstrumentFamiliesV1` and is
  unavailable to a hosted face.
- **`presentationIsCurrent` gates only some handles** (§4). It reaches
  receiver frequency seats (`FrequencyRendererSeat`, via
  `ReceiverInstrumentHost.svelte`'s `mainFrequency`/`subFrequency` snippets),
  TX-aux scalars (`ValueControl`, via `TxAuxScalarHost.svelte`), and VFO
  operation seats (`VfoOperationSeatHost.svelte: currentRendererContext`) —
  `grep -rl presentationIsCurrent frontend/src` names exactly the six files
  involved in that chain. It does **not** reach `mainSMeter`/`subSMeter`
  (`ReceiverInstrumentHost.svelte`) or any of the seven `stationMeters`
  handles: `frontend/src/component-kits/MeterRendererSeat.svelte` has no
  currency check at all, so a torn-down face's retained meter handle keeps
  rendering through the new occurrence's `MeterRendererSeat`.
- **Resources are limited to a subset of `AppResource`.** The type-level
  union is `AppResource = 'hardware-scope' | 'audio-fft' | 'rx-audio'`
  (`frontend/src/lib/runtime/resource-demand.ts`), but a presentation's
  `resources` may only name `'hardware-scope'` or `'audio-fft'`
  (`activation.ts: PRESENTATION_RESOURCES`) — declaring `'rx-audio'` throws
  `Presentation "<id>" has unsupported resource "rx-audio".`.
