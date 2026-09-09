/**
 * Skin registry — resolves which skin to load based on capabilities and user preference.
 *
 * Each skin is a top-level Svelte component that composes semantic components
 * into a layout. Skins are lazy-loaded to keep the initial bundle small.
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md
 */

import type { Component } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { AppResource } from '$lib/runtime/resource-demand';
import type {
  FiniteControlAppearance,
  FrequencyRenderer,
  HostedFaceComponentV1,
  MeterAppearance,
  ScalarAppearance,
} from '../../component-kit-api/src/index';
import type { InstrumentComposition } from '../components-v2/wiring/instrument-composition';
import {
  normalizeLayoutMode,
  type LayoutMode,
} from '$lib/runtime/adapters/layout-mode-adapter';
import {
  derivePresentationCapabilities,
} from '$lib/runtime/adapters/presentation-capabilities';
import { getLayout, TOPOLOGY_CLASSES } from '../presentation/layouts/contract';
// Side-effect import, the same idiom `App.svelte` uses for the same registry:
// it populates the layout manifests `getLayout` resolves against. Imported
// here as well so `admitsLiveTopology` below cannot depend on the composition
// root having pulled the barrel in first — an unregistered id is admitted, so
// without this the gate would silently disappear wherever the barrel was not
// already loaded.
import '../presentation/layouts/declarations';

export type SkinId =
  | 'desktop-v2' | 'dual-receiver-cockpit' | 'lcd-cockpit' | 'lcd-scope' | 'mobile' | 'peer-split'
  | 'sdr-test' | 'dual-sdr-face' | 'unified-instrument' | 'panadapter-first'
  | 'flagship-probe';

export type PresentationId = string;
export type PresentationHostMode =
  'self-contained' | 'instrument-handles' | 'external-instruments-v1';
export type InstrumentHandlesPresentation = Component<{ instruments: InstrumentComposition }>;
export type SelfContainedPresentation = Component;
export type PresentationComponent =
  InstrumentHandlesPresentation | SelfContainedPresentation | HostedFaceComponentV1;

export interface ResolvedHostedFaceAppearancesV1 {
  readonly scalar: ScalarAppearance;
  readonly frequency: FrequencyRenderer;
  readonly finite: FiniteControlAppearance;
  readonly meter: MeterAppearance;
}

export interface ExternalPresentationRecord {
  readonly id: string;
  readonly kind: 'external-instruments-v1';
  readonly loader: () => Promise<HostedFaceComponentV1>;
  readonly resources: readonly AppResource[];
  readonly layoutId: string;
  readonly appearances: ResolvedHostedFaceAppearancesV1;
}

type BuiltInPresentationRecord<Id extends SkinId> =
  | {
      readonly id: Id;
      readonly kind: 'built-in-instrument-layout';
      readonly loader: () => Promise<{ default: InstrumentHandlesPresentation }>;
      readonly resources: readonly AppResource[];
    }
  | {
      readonly id: Id;
      readonly kind: 'built-in-self-contained';
      readonly loader: () => Promise<{ default: SelfContainedPresentation }>;
      readonly resources: readonly AppResource[];
    };

type BuiltInPresentationCatalog = {
  readonly [Id in SkinId]: BuiltInPresentationRecord<Id>;
};

export type PresentationRecord = BuiltInPresentationRecord<SkinId> | ExternalPresentationRecord;

export interface PreparedExternalPresentationBatch {
  readonly records: readonly ExternalPresentationRecord[];
}

export interface SkinResolutionContext {
  capabilities: Capabilities | null;
  layoutPreference: LayoutMode;
  isMobile: boolean;
  hasAnyScope: boolean;
}

/** Refused layout + live topology class pairs already reported, so a repeated
 *  resolution (every capability revision re-runs `App.svelte`'s `$derived`)
 *  reports once rather than per evaluation. */
const reportedRefusals = new Set<string>();

/** The skin an unresolvable preference lands on (MOR-1097 cutover). */
const DEFAULT_SKIN_ID: SkinId = 'desktop-v2';

/**
 * The live receiver topology class, `${structuralCount}/${scheme}` — the same
 * string `lib/runtime/adapters/radio-view-model-adapter.ts` composes for its
 * `topologyId`, over the same `TOPOLOGY_CLASSES` vocabulary a manifest
 * declares against. `null` when nothing establishes one: no capabilities yet,
 * or a `vfoScheme`/`receivers` pair `derivePresentationCapabilities` reports
 * as `invalid-topology`.
 */
function liveTopologyClass(capabilities: Capabilities | null): string | null {
  const topology = capabilities ? derivePresentationCapabilities(capabilities).topology : null;
  return topology === null ? null : `${topology.structuralCount}/${topology.scheme}`;
}

/**
 * T198 — whether the layout manifest registered under `id` accepts the radio
 * `capabilities` describes.
 *
 * `compatibleTopologies` was validated (`presentation/layouts/contract.ts:
 * validateLayoutManifest`) and documented as a restriction, but no runtime
 * path read it: `?layout=flagship-probe` mounted the probe on an IC-7300,
 * whose `1/ab` class that manifest does not declare.
 *
 * Two shapes are admitted without deriving anything: an id with no registered
 * manifest, and a manifest that declares every class in `TOPOLOGY_CLASSES`.
 * Neither can exclude a radio, so neither needs to see one — which is what
 * keeps an unrestricted preference resolving before capabilities arrive. A
 * manifest that DOES exclude a class must see its own: an underivable
 * topology is refused, because nothing establishes that the manifest covers
 * it.
 */
function admitsLiveTopology(id: string, capabilities: Capabilities | null): boolean {
  const manifest = getLayout(id);
  if (manifest === undefined) return true;
  const declared = manifest.compatibleTopologies;
  if (TOPOLOGY_CLASSES.every((topologyClass) => declared.includes(topologyClass))) return true;
  const live = liveTopologyClass(capabilities);
  return live !== null && declared.some((topologyClass) => topologyClass === live);
}

function reportRefusal(id: SkinId, capabilities: Capabilities | null, mounted: SkinId): void {
  const live = liveTopologyClass(capabilities);
  const key = `${id} ${live ?? ''}`;
  if (reportedRefusals.has(key)) return;
  reportedRefusals.add(key);
  const declared = getLayout(id)?.compatibleTopologies ?? [];
  console.warn(
    `[rigplane] layout "${id}" declares compatibleTopologies [${declared.join(', ')}], and the `
    + `live receiver topology is ${live ?? 'not derivable yet'} — mounting "${mounted}" instead.`,
  );
}

/**
 * T198 — the skin actually mounted for a preferred `id`: `id` itself when its
 * manifest admits the live topology, otherwise the first hop down the
 * `fallbackLayoutId` chain that is a built-in skin admitting it, and
 * `DEFAULT_SKIN_ID` when the chain runs out. `LayoutManifest.fallbackLayoutId`
 * was declared and validated but read by no runtime path before this.
 *
 * A hop is taken only when it is both a built-in skin and admitted; any other
 * candidate — an id no manifest is registered under, one registered but not
 * loadable, one whose own manifest also excludes the radio — continues down
 * that candidate's own chain. `visited` bounds the walk: a chain that names an
 * id already seen stops there instead of looping.
 *
 * One report per refused (preference, live class) pair, naming the skin the
 * walk actually mounted — `console.warn`, the channel
 * `lib/stores/qa-cockpit-override.ts` already uses to explain its own no-op.
 */
function resolveWithFallback(id: SkinId, capabilities: Capabilities | null): SkinId {
  if (admitsLiveTopology(id, capabilities)) return id;
  const visited = new Set<string>([id]);
  let mounted: SkinId = DEFAULT_SKIN_ID;
  let candidate = getLayout(id)?.fallbackLayoutId ?? null;
  while (candidate !== null && !visited.has(candidate)) {
    visited.add(candidate);
    if (BUILT_IN_SKIN_IDS.has(candidate) && admitsLiveTopology(candidate, capabilities)) {
      mounted = candidate as SkinId;
      break;
    }
    candidate = getLayout(candidate)?.fallbackLayoutId ?? null;
  }
  reportRefusal(id, capabilities, mounted);
  return mounted;
}

/**
 * Determine which skin to use based on context.
 *
 * Rules:
 * - Mobile viewport → mobile skin
 * - QA-only: layoutPreference === 'dual-receiver-cockpit' → dual-receiver-cockpit
 *   (MOR-1257 — only reachable via the exact `?layout=dual-receiver-cockpit`
 *   query param; see `lib/stores/qa-cockpit-override.ts`)
 * - QA-only: layoutPreference === 'flagship-probe' → flagship-probe
 *   (T160 PR-1 — same module, same terms, `?layout=flagship-probe`)
 * - User forced 'sdr-test' → sdr-test
 * - User forced 'lcd' or 'lcd-cockpit' → lcd-cockpit
 * - User forced 'lcd-scope' → lcd-scope
 * - User forced 'standard' → desktop-v2
 * - User forced 'peer-split' → peer-split
 * - Auto: use desktop-v2 (the v3 default); explicit LCD choices are the
 *   recoverable compatibility-window opt-out
 * - T198: a forced preference resolves through `resolveWithFallback`, which
 *   returns the preferred skin only while its manifest admits the live
 *   receiver topology and otherwise walks that manifest's `fallbackLayoutId`
 *   chain, ending at `DEFAULT_SKIN_ID`. The 'standard' branch returns
 *   'desktop-v2' directly — the walk's own endpoint, so routing it through
 *   the walker could only report a refusal and return the same id
 */
export function resolveSkinId(ctx: SkinResolutionContext): SkinId {
  if (ctx.isMobile) return 'mobile';
  // MOR-1257: interim QA reachability. `ctx.layoutPreference` can only be
  // this value when the caller resolved it from the exact
  // `?layout=dual-receiver-cockpit` query param — it is deliberately NOT a
  // `CanonicalLayoutMode` (lib/stores/layout.svelte.ts), so
  // `normalizeLayoutMode` below would fall it straight through to 'auto'.
  // Checked here, before normalization, for that reason.
  if (ctx.layoutPreference === 'dual-receiver-cockpit') return resolveWithFallback('dual-receiver-cockpit', ctx.capabilities);
  // T160 PR-1: the geometry probe is gated the same way and checked here for
  // the same reason — it is not a `CanonicalLayoutMode` either.
  if (ctx.layoutPreference === 'flagship-probe') return resolveWithFallback('flagship-probe', ctx.capabilities);
  const layoutPreference = normalizeLayoutMode(ctx.layoutPreference);
  if (layoutPreference === 'sdr-test') return resolveWithFallback('sdr-test', ctx.capabilities);
  if (layoutPreference === 'lcd-cockpit') return resolveWithFallback('lcd-cockpit', ctx.capabilities);
  if (layoutPreference === 'lcd-scope') return resolveWithFallback('lcd-scope', ctx.capabilities);
  if (layoutPreference === 'standard') return 'desktop-v2';
  if (layoutPreference === 'peer-split') return resolveWithFallback('peer-split', ctx.capabilities);
  if (layoutPreference === 'unified-instrument') return resolveWithFallback('unified-instrument', ctx.capabilities);
  if (layoutPreference === 'panadapter-first') return resolveWithFallback('panadapter-first', ctx.capabilities);
  if (layoutPreference === 'dual-sdr-face') return resolveWithFallback('dual-sdr-face', ctx.capabilities);
  // MOR-1097 cutover: every non-mobile auto start uses the reworked
  // desktop-v2 composition. Scope availability remains presentation data, not
  // default-selection policy; explicit LCD preferences stay selectable.
  return DEFAULT_SKIN_ID;
}

/**
 * One total built-in catalog keeps the lazy loader, host contract and resource
 * bridge plan on the same discriminated record. A new SkinId cannot silently
 * inherit any of those three decisions from a separate table.
 *
 * Resource membership only permits bridging: `App.svelte` bridges a resource
 * solely while it is already demanded, so presentation choice cannot start a
 * service. `rx-audio` is absent because the runtime owns that lease.
 * Loaders remain lazy, so only the selected skin's entry component is loaded.
 */
const SKIN_LOADERS = {
  'desktop-v2': {
    id: 'desktop-v2',
    kind: 'built-in-instrument-layout',
    loader: () => import('./desktop-v2/DesktopSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  // MOR-1068 (F8): the cockpit's layout manifest registers under this exact
  // id, so it needs the matching loadable SkinId — it was the only registered
  // manifest the App had no way to load. MOR-1257 later added the QA branch
  // in `resolveSkinId` above that returns it.
  'dual-receiver-cockpit': {
    id: 'dual-receiver-cockpit',
    kind: 'built-in-self-contained',
    loader: () => import('./dual-receiver-cockpit/DualReceiverCockpit.svelte'),
    resources: [],
  },
  'lcd-cockpit': {
    id: 'lcd-cockpit',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-cockpit/LcdCockpitSkin.svelte'),
    resources: ['audio-fft'],
  },
  'lcd-scope': {
    id: 'lcd-scope',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-scope/LcdScopeSkin.svelte'),
    resources: ['audio-fft'],
  },
  'mobile': {
    id: 'mobile',
    kind: 'built-in-self-contained',
    loader: () => import('./mobile/MobileSkin.svelte'),
    resources: ['hardware-scope'],
  },
  // MOR-2155 made `peer-split` addressable and loadable; MOR-2152 (see the
  // `resolveSkinId` branch below) is what makes a forced 'peer-split'
  // preference actually resolve to it. MOR-2153 PR-1 retargeted the loader
  // from the bare glass (`segmentline/PeerSplitLayout.svelte`) to the LCD
  // shell wrapper: `lcd-peer-split/LcdPeerSplitSkin.svelte` mounts
  // `LcdLayout` with `variant="peer-split"`, which renders the glass inside
  // the shell rather than loading it standalone.
  'peer-split': {
    id: 'peer-split',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-peer-split/LcdPeerSplitSkin.svelte'),
    resources: ['audio-fft'],
  },
  'unified-instrument': {
    id: 'unified-instrument',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-unified-instrument/LcdUnifiedInstrumentSkin.svelte'),
    resources: ['audio-fft'],
  },
  'panadapter-first': {
    id: 'panadapter-first',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-panadapter-first/LcdPanadapterFirstSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  'sdr-test': {
    id: 'sdr-test',
    kind: 'built-in-instrument-layout',
    loader: () => import('./sdr-test/SdrTestSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  // T160 PR-1 — the flagship geometry probe. Self-contained, like the
  // cockpit: it mounts `SemanticRadioSurfaces` itself and owns its own grid.
  // Reached only through the QA branch in `resolveSkinId` above; `StatusBar`'s
  // picker cannot list it, because that array is typed `CanonicalLayoutMode`.
  //
  // `hardware-scope` alone: the shell mounts one `SpectrumPanel` and no
  // audio-FFT surface — the same plan, for the same reason, that `mobile`
  // and `dual-sdr-face` carry.
  'flagship-probe': {
    id: 'flagship-probe',
    kind: 'built-in-self-contained',
    loader: () => import('./flagship-probe/FlagshipProbeSkin.svelte'),
    resources: ['hardware-scope'],
  },
  'dual-sdr-face': {
    id: 'dual-sdr-face',
    kind: 'built-in-self-contained',
    loader: () => import('./dual-sdr-face/DualSdrFaceSkin.svelte'),
    resources: ['hardware-scope'],
  },
} satisfies BuiltInPresentationCatalog;

/** The catalog's built-in ids, captured before `commitExternalPresentationBatch`
 *  can add an externally authored presentation to the same object: a fallback
 *  hop must land on a `SkinId`, which an external presentation id is not. */
const BUILT_IN_SKIN_IDS: ReadonlySet<string> = new Set(Object.keys(SKIN_LOADERS));

const hasOwn = (value: object, key: PropertyKey): boolean =>
  Object.prototype.hasOwnProperty.call(value, key);

/**
 * Built-in ids, previously committed external ids and Object.prototype names
 * are all unavailable. The latter matters because SKIN_LOADERS intentionally
 * remains the one normal-prototype literal catalog parsed by existing guards.
 */
export function isPresentationIdReserved(id: string): boolean {
  return id in SKIN_LOADERS;
}

export function getPresentationRecord(id: PresentationId): PresentationRecord | undefined {
  if (!hasOwn(SKIN_LOADERS, id)) return undefined;
  return (SKIN_LOADERS as unknown as Record<string, PresentationRecord>)[id];
}

function requirePresentationRecord(id: PresentationId): PresentationRecord {
  const record = getPresentationRecord(id);
  if (record === undefined) throw new Error(`Presentation "${id}" is not registered.`);
  return record;
}

export function prepareExternalPresentationBatch(
  records: readonly ExternalPresentationRecord[],
): PreparedExternalPresentationBatch {
  const batchIds = new Set<string>();
  const prepared: ExternalPresentationRecord[] = [];
  for (const record of records) {
    if (isPresentationIdReserved(record.id)) {
      throw new Error(`Presentation id "${record.id}" is registered or reserved.`);
    }
    if (batchIds.has(record.id)) {
      throw new Error(`Presentation id "${record.id}" appears more than once in the batch.`);
    }
    batchIds.add(record.id);
    prepared.push(Object.freeze({
      ...record,
      resources: Object.freeze([...record.resources]),
      appearances: Object.freeze({ ...record.appearances }),
    }));
  }
  return Object.freeze({ records: Object.freeze(prepared) });
}

/** Commit only a synchronously prepared batch; all fallible work is upstream. */
export function commitExternalPresentationBatch(batch: PreparedExternalPresentationBatch): void {
  const catalog = SKIN_LOADERS as unknown as Record<string, PresentationRecord>;
  for (const record of batch.records) catalog[record.id] = record;
}

export function presentationHostMode(id: SkinId): Exclude<PresentationHostMode, 'external-instruments-v1'>;
export function presentationHostMode(id: PresentationId): PresentationHostMode;
export function presentationHostMode(id: PresentationId): PresentationHostMode {
  const record = requirePresentationRecord(id);
  if (record.kind === 'built-in-instrument-layout') return 'instrument-handles';
  if (record.kind === 'external-instruments-v1') return 'external-instruments-v1';
  return 'self-contained';
}

type LoadedPresentation<Id extends SkinId> =
  Awaited<ReturnType<(typeof SKIN_LOADERS)[Id]['loader']>>['default'];

export function loadSkin<Id extends SkinId>(id: Id): Promise<LoadedPresentation<Id>>;
export function loadSkin(record: ExternalPresentationRecord): Promise<HostedFaceComponentV1>;
export function loadSkin(id: PresentationId): Promise<PresentationComponent>;
export async function loadSkin(
  source: PresentationId | ExternalPresentationRecord,
): Promise<PresentationComponent> {
  const record = typeof source === 'string' ? requirePresentationRecord(source) : source;
  if (record.kind === 'external-instruments-v1') return record.loader();
  return (await record.loader()).default;
}

/**
 * Which App-session resources a presentation's subtree can demand (MOR-1060).
 *
 * This is a private composition-root detail, NOT a public workspace or
 * design-language API: it exists so `App.svelte` can hold demand across a
 * presentation swap and stop the outgoing subtree's release from bouncing a
 * stream the incoming subtree is about to ask for again.
 *
 * Read off the actual component trees:
 * - `hardware-scope` — `SpectrumPanel`, mounted by the desktop/sdr-test and
 *   mobile layouts, plus the selected display in `panadapter-first`.
 * - `audio-fft` — `AudioSpectrumPanel` (right sidebar, desktop/sdr-test/LCD,
 *   `RightSidebar.svelte` behind `hasAudioFft()`) and `AmberCockpit` /
 *   `AmberScope` (LCD `cockpit`/`scope`); `peer-split` demands it purely
 *   through the same `RightSidebar` it now shares with `cockpit`/`scope` —
 *   `unified-instrument` selects that source for its display. The
 *   `panadapter-first` LCD shell still contains the existing AF inset in its
 *   right sidebar even though the selected display uses hardware scope. The
 *   mobile layout has none.
 *
 * `rx-audio` is deliberately absent: its lease is held by the runtime
 * (`setRxLive`), not by a presentation subtree, so it already survives a swap.
 *
 * Membership here only permits bridging — `App.svelte` bridges a resource
 * solely while it is already demanded, so a presentation choice can never
 * manufacture a live service (v3 ADR invariant 12).
 */
export function presentationResourcePlan(id: PresentationId): readonly AppResource[] {
  return requirePresentationRecord(id).resources;
}
