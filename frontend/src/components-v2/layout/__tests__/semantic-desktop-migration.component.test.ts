/**
 * MOR-1065 — one current desktop layout migrated to the semantic surfaces —
 * extended by MOR-1313 into the PER-ZONE suppression matrix.
 *
 * The migrated layouts are `sdr-test` and, since MOR-1313, `desktop-v2`: their
 * VFO and TX presentation is owned by the MOR-1063 / MOR-1064 surfaces. Two
 * things must hold at once:
 *   1. the semantic surfaces render IN PLACE of the legacy twin-VFO block and
 *      the sidebar TX panel — a layout carrying both would ship two PTT
 *      affordances and two VFO truths;
 *   2. everything else in that layout (status bar, sidebars, spectrum, meters
 *      dock) is untouched.
 *
 * What decides (1) is no longer a skin id but the ACTIVE layout manifest's zone
 * declarations, so the last two describes below render the matrix itself: a
 * declared surface is semantic and its legacy twin is gone; a surface no zone
 * declares keeps its legacy presentation. `sdr-test` — one zone declaring both
 * — is the degenerate all-semantic case, and its behavior is unchanged.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'fs';
import type { Capabilities } from '$lib/types/capabilities';
import type { SkinId } from '../../../skins/registry';

const h = vi.hoisted(() => {
  const box = { state: null as unknown, caps: null as unknown };
  return {
    ...box,
    runtime: {
      get state() { return h.state; },
      get caps() { return h.caps; },
      connectionStatus: 'disconnected',
      radioPowerOn: null,
      connection: { status: 'disconnected', radioPowerOn: null },
      audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
      connectionAudio: false,
      // MOR-1312 slice 12B: `SemanticRadioSurfaces` now also reads
      // `runtime.defaultScopeStatus` / `runtime.scope.hardwareScopeConnected`
      // for the scope-display snapshot (the FIFTH adapter argument). No
      // fixture here declares a scope capability, so this stays on its
      // pre-1312 path regardless of these fixed defaults.
      defaultScopeStatus: {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      },
      scope: { hardwareScopeConnected: false },
      bootstrap: async () => () => {},
      setPollingMultiplier: () => {},
      send: () => {},
    },
  };
});

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getRadioPowerOn: vi.fn(() => null),
  getRadioStatus: vi.fn(() => 'disconnected'),
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => false),
  getRigConnected: vi.fn(() => false),
  getRadioReady: vi.fn(() => false),
  getRadioHealth: vi.fn(() => null),
  markScopeFrame: vi.fn(),
}));

vi.mock('../../../lib/runtime/frontend-runtime', () => ({ runtime: h.runtime }));
vi.mock('$lib/runtime', () => ({ runtime: h.runtime }));

// MOR-1011: the App TX controller comes from Svelte context that only
// App.svelte provides; RadioLayout is mounted here without it.
vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  const idle = {
    phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
    mayOwnKey: false, fault: null,
  };
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => idle,
      subscribe: () => () => {},
      start: vi.fn(),
      setIntent: vi.fn(),
      release: vi.fn(),
      resetFault: vi.fn(),
    }),
  };
});

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
  hasDualReceiver: vi.fn(() => true),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => true),
  hasAnyScope: vi.fn(() => false),
  isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false),
  getScopeSource: vi.fn(() => null),
  hasCapability: vi.fn(() => false),
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  setCapabilities: vi.fn(),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]),
  getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]),
  getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getKeyboardConfig: vi.fn(() => null),
  getVfoScheme: vi.fn(() => 'main_sub'),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

import RadioLayout from '../RadioLayout.svelte';
import { getCapabilities, hasAnyScope, hasCapability } from '$lib/stores/capabilities.svelte';
import { topologyFixtures, type TopologyFixtureId } from '../../../semantic/fixtures/topologies';
import {
  registerLayout, type LayoutManifest, type SemanticSurfaceName,
} from '../../../presentation/layouts/contract';
// Barrel, for the same reason `skins/dual-receiver-cockpit/__tests__/
// DualReceiverCockpit.component.test.ts` uses it: a real registered manifest to
// derive the probes below from. RadioLayout.svelte already pulls this module in
// (its side-effect registry import), so this adds no registration of its own.
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
// MOR-1367 (S8): the zone-ELEMENT assertions need the resolved plan in context
// — see `renderWithPlan` in the S8 describe for why `render()` cannot show one.
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../../presentation/workspace/resolution';
import { DEFAULT_WORKSPACE } from '../../../presentation/workspace/contract';

/**
 * MOR-1313 fix round — PARTIALLY DECLARING manifests, the quadrants no shipped
 * layout occupies. All six registered manifests declare both `vfo` and `rxTx`,
 * so the shipped set cannot exercise the rule that decides the R9 key count
 * when the two declarations DISAGREE — and an unpinned R9 gate is one careless
 * slice away from a stranded transmitter.
 *
 * Registered here rather than mocked: this exercises the REAL registry, the
 * REAL `declaredSurfaces`, and the real `RadioLayout` mount. Same precedent as
 * `presentation/layouts/__tests__/mobile-registration.test.ts`'s
 * `mobile-hop-probe`. Safe against the MOR-1272 fast-pool hazard because
 * `*.component.test.ts` runs in the ISOLATED pool (`vite.config.ts`), so these
 * two ids cannot leak into a sibling file's registry.
 *
 * Both shapes are legal: `validateLayoutManifest` requires only that every
 * `requiredSemanticSurfaces` entry is covered by some zone, and the programme's
 * additive subset-declaration pattern (a family declaring one zone first and
 * growing) means a future slice lands in exactly these shapes.
 */
const probeManifest = (
  id: string,
  zones: LayoutManifest['zones'],
  requiredSemanticSurfaces: LayoutManifest['requiredSemanticSurfaces'],
): LayoutManifest => ({
  // Derived from the real desktop-v2 manifest, overriding ONLY identity and the
  // zone declarations under test — so a probe differs from the shipped layout in
  // exactly the dimension this describe is about, and nothing else. (It also
  // keeps the MOR-1247 declaration-only sizing fields out of this file, which
  // `presentation/layouts/__tests__/stage-sizing-boundary.test.ts` scans for.)
  ...desktopV2Layout,
  id,
  displayName: id,
  zones,
  requiredSemanticSurfaces,
});

/** Declares the deck's surface but NOT the TX one. */
const VFO_ONLY = 'vfo-only-probe' as SkinId;
/** Declares the TX surface but NOT the deck's. */
const RX_TX_ONLY = 'rx-tx-only-probe' as SkinId;

registerLayout(probeManifest(VFO_ONLY, [{ id: 'receiver-deck', surfaces: ['vfo'] }], ['vfo']));
registerLayout(probeManifest(RX_TX_ONLY, [{ id: 'rx-tx', surfaces: ['rxTx'] }], ['rxTx']));

/** The verifier's probe shape: every element on screen that can key or unkey. */
const KEY_AUTHORITIES = '[data-testid="rx-tx-surface"], .tx-panel';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });
const receiver = (hz: number) => ({
  ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
});

function liveState(): unknown {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  };
}

/** One representative capability set per canonical topology fixture id. */
function capsFor(id: TopologyFixtureId): Capabilities {
  const scheme = topologyFixtures[id].vfoScheme;
  const dual = scheme === 'ab_shared' || scheme === 'main_sub';
  return {
    model: 'fixture', scope: true, audio: true, tx: true,
    capabilities: dual ? ['scope', 'audio', 'tx', 'dual_rx'] : ['scope', 'audio', 'tx'],
    receivers: dual ? 2 : 1, vfoScheme: scheme, freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: false,
  } as unknown as Capabilities;
}

let mounted: ReturnType<typeof mount>[] = [];

function render(skinId: SkinId): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(RadioLayout, { target, props: { skinId } }));
  flushSync();
  return target;
}

beforeEach(() => {
  mounted = [];
  h.state = liveState();
  h.caps = capsFor('2/main_sub');
  vi.mocked(hasCapability).mockReturnValue(false);
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  mounted.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('the migrated desktop layout owns VFO/TX through the semantic surfaces', () => {
  it('renders both surfaces inside the receiver deck', () => {
    const t = render('sdr-test');
    const deck = t.querySelector('.receiver-deck')!;
    expect(deck.querySelector('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(deck.querySelector('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(deck.querySelector('[data-testid="rx-tx-surface"]')).not.toBeNull();
  });

  // MUTATION KILLED: adding the surfaces alongside the panels they replace.
  // Two PTT affordances and two VFO readouts in one layout is exactly the
  // "duplicate presentation ownership" MOR-1099 exists to retire.
  it('renders none of the legacy VFO/TX presentation it replaces', () => {
    const t = render('sdr-test');
    expect(t.querySelector('.vfo-header')).toBeNull();
    expect(t.querySelector('.sdr-host')).toBeNull();
    expect(t.querySelector('.tx-panel')).toBeNull();
    expect(t.querySelector('[data-testid="tx-strip"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
  });

  it('leaves the rest of the layout intact', () => {
    const t = render('sdr-test');
    expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.center-column .spectrum-slot')).not.toBeNull();
    expect(t.querySelector('.spectrum-panel-stub')).not.toBeNull();
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
    // Non-TX sidebar panels are untouched by the TX suppression.
    expect(t.querySelector('[data-panel-id="rx-audio"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="memory"]')).not.toBeNull();
  });

  // MUTATION KILLED: widening `hideTxPanel` to the sidebar's whole `showTx`
  // branch, which also guards CW. The suppression is TX-panel-scoped by
  // construction, but nothing pinned it — `hasCapability` is mocked false
  // everywhere else in this file, so the CW block never renders to be checked.
  it('keeps the CW panel, which shares the sidebar\'s TX branch', () => {
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    const t = render('sdr-test');
    expect(t.querySelector('[data-panel-id="cw"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
  });

  it.each(['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const)(
    'renders safely on the %s topology', (id) => {
      h.caps = capsFor(id);
      const t = render('sdr-test');
      const tiles = t.querySelectorAll('[data-vfo-tile]');
      expect(tiles.length).toBe(topologyFixtures[id].vfos.length);
      expect(t.querySelector('[data-testid="rx-tx-surface"]')).not.toBeNull();
    },
  );

  it('renders the chrome but no surfaces when capabilities have not loaded', () => {
    h.caps = null;
    const t = render('sdr-test');
    expect(t.querySelector('.receiver-deck')).not.toBeNull();
    expect(t.querySelector('[data-testid="vfo-surface"]')).toBeNull();
    expect(t.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
  });
});

/**
 * MOR-1313 — desktop-v2 resolves through the v3 path.
 *
 * Its manifest splits the pair across TWO zones (`receiver-deck: [vfo]`,
 * `rx-tx: [rxTx]`) where sdr-test uses one, so the same rendered outcome
 * arriving from a different zone shape is the proof that suppression is
 * derived per zone rather than per skin.
 */
describe('desktop-v2 resolves through the v3 path (MOR-1313)', () => {
  // MUTATION KILLED: leaving desktop-v2 on the legacy branch — the state
  // MOR-1266 deliberately shipped and this slice closes.
  it('mounts the semantic surfaces in the receiver deck', () => {
    const t = render('desktop-v2');
    const deck = t.querySelector('.receiver-deck')!;
    expect(deck.querySelector('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(deck.querySelector('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(deck.querySelector('[data-testid="rx-tx-surface"]')).not.toBeNull();
  });

  // MUTATION KILLED: suppressing only ONE of the two zones' twins — the shape
  // a two-zone manifest makes possible and a one-zone one cannot express.
  it('drops the legacy twin of BOTH declared zones', () => {
    const t = render('desktop-v2');
    expect(t.querySelector('.vfo-header')).toBeNull();
    expect(t.querySelector('.tx-panel')).toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
    expect(t.querySelector('[data-testid="tx-strip"]')).toBeNull();
  });

  // MOR-1341 (S5) — the bottom dock joins the matrix. `desktop-v2` now
  // declares a `meters` zone too, so `<MetersDockPanel>` retires the moment
  // the view model actually carries the group. Proven with a state that
  // reports a real meter reading, not the bare `liveState()` fixture every
  // other test in this describe uses — that fixture reports NO meter fields
  // at all, so it would pass this assertion vacuously (both the dock and the
  // semantic surface self-gate away) and prove nothing about suppression.
  it('drops the legacy meters dock in favour of the semantic meters surface', () => {
    const state = liveState() as { main: Record<string, unknown> };
    h.state = { ...state, main: { ...state.main, sMeter: 120 } };
    const t = render('desktop-v2');
    expect(t.querySelector('.bottom-dock')).toBeNull();
    expect(t.querySelector('[data-testid="meters-dock-panel"]')).toBeNull();
    expect(t.querySelector('[data-testid="meters-surface"]')).not.toBeNull();
  });

  // R9, asserted BEHAVIORALLY rather than by import absence: whatever the zone
  // shape, the screen carries exactly ONE key/unkey authority. Counting both
  // the semantic surface and the legacy panel in one query is what makes a
  // "semantic zone mounted alongside its twin" regression fail here — an
  // assertion on either one alone would not.
  it('leaves exactly one key/unkey affordance on screen', () => {
    for (const skinId of ['desktop-v2', 'sdr-test'] as const) {
      const t = render(skinId);
      expect(t.querySelectorAll('[data-testid="rx-tx-surface"]').length).toBe(1);
      expect(t.querySelectorAll('[data-testid="rx-tx-surface"], .tx-panel').length).toBe(1);
    }
  });

  // The rest of the shell is chrome, not a zone: nothing about suppression
  // may reach it. Same claim the sdr-test block above makes, restated for the
  // family that actually ships to every Icom radio. `.bottom-dock` is
  // deliberately NOT asserted here any more (MOR-1341): it is now itself a
  // suppressed twin, not part of "the rest" — its own matrix entry is the
  // dedicated test above. `rx-audio` left this list for the same reason
  // (MOR-1368/S9): it is a suppressed twin now, pinned by its own row in the
  // channel describe below. `memory` stays — it has no semantic twin at all,
  // so it is the panel that proves suppression did not widen into "the rest".
  it('leaves the rest of the layout intact', () => {
    const t = render('desktop-v2');
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.center-column .spectrum-slot')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="memory"]')).not.toBeNull();
  });

  // The manifest declares all four canonical classes (MOR-1266) — desktop-v2
  // is the family every real Icom radio resolves to, so the v3 path has to
  // hold on each of them, not just the dual-receiver bench radio.
  it.each(['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const)(
    'renders the semantic vertical on the %s topology', (id) => {
      h.caps = capsFor(id);
      const t = render('desktop-v2');
      expect(t.querySelectorAll('[data-vfo-tile]').length).toBe(topologyFixtures[id].vfos.length);
      expect(t.querySelectorAll('[data-testid="rx-tx-surface"]').length).toBe(1);
      expect(t.querySelector('.vfo-header')).toBeNull();
    },
  );

  // The presentational class follows the SEMANTIC DECK now (the taller deck
  // row and the wide-viewport promotion were written for it), while `sdr-test`
  // stays an identity hook for the one entrypoint it names.
  it('carries the semantic-deck presentation class, and sdr-test only its own', () => {
    expect(render('desktop-v2').querySelector('.radio-layout.semantic-deck')).not.toBeNull();
    expect(render('desktop-v2').querySelector('.radio-layout.sdr-test')).toBeNull();
    expect(render('sdr-test').querySelector('.radio-layout.semantic-deck.sdr-test')).not.toBeNull();
  });
});

/**
 * The other half of the matrix. Suppression is derived from the manifest, so
 * an id no manifest is registered under declares nothing — and every legacy
 * twin must survive untouched. This is the branch that keeps the shared v2
 * shell honest for any family the v3 build-out has not reached, and it is the
 * fail-safe direction for an unresolvable layout: the shipped panels, never a
 * screen with no VFO and no unkey affordance.
 */
describe('an undeclared layout keeps its legacy presentation (MOR-1313)', () => {
  const UNDECLARED = 'no-such-layout' as SkinId;

  // MUTATION KILLED: making the semantic mount unconditional — "suppression"
  // that never consults the manifest would pass every desktop-v2 assertion
  // above and silently take over every future layout too.
  it('renders the legacy VFO header and TX panel, and no semantic surfaces', () => {
    const t = render(UNDECLARED);
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="semantic-radio-surfaces"]')).toBeNull();
    expect(t.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
  });

  // The legacy branch is still exactly one TX affordance — the R9 count holds
  // on both sides of the matrix, not only where the semantic vertical mounts.
  it('still leaves exactly one key/unkey affordance on screen', () => {
    const t = render(UNDECLARED);
    expect(t.querySelectorAll('[data-testid="rx-tx-surface"], .tx-panel').length).toBe(1);
  });

  // MUTATION KILLED: keying the presentational class off "not sdr-test" (or
  // off any skin id) instead of off the resolved semantic deck.
  it('does not carry the semantic-deck presentation class', () => {
    expect(render(UNDECLARED).querySelector('.radio-layout.semantic-deck')).toBeNull();
  });
});

/**
 * MOR-1313 fix round (F1/F2) — the R9 key count over a PARTIALLY declaring
 * manifest: the quadrants where "does a zone declare `vfo`?" and "does a zone
 * declare `rxTx`?" disagree. Every shipped manifest declares both, so without
 * these two cases the rule that decides `hideTxPanel` is invisible to the whole
 * suite: an independent verifier's mutant removing it outright survived all
 * 4938 tests.
 *
 * The invariant is a COUNT, not a preference for either presentation: exactly
 * one element that can key or unkey the transmitter, in every quadrant. Two is
 * the stranded-transmitter hazard (each affordance holds its own TX lease
 * `sourceId`, so the controller refuses a release from the other one); zero
 * leaves the operator no way to stop transmitting.
 */
describe('exactly one key authority on a partially declaring manifest (R9)', () => {
  // MUTATION KILLED — the one this file was missing. Gating the TX twin on
  // `declared.has('rxTx')` (whether as `semanticDeck && declared.has('rxTx')`
  // or bare) renders the semantic RxTxSurface — `SemanticRadioSurfaces` mounts
  // one unconditionally, being manifest-blind — alongside the legacy TxPanel
  // this manifest never asked to suppress. Measured KEY count under either
  // form: 2.
  it('a vfo-only manifest mounts the semantic deck and still suppresses the legacy TX twin', () => {
    const t = render(VFO_ONLY);
    expect(t.querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
    // ...and it is the semantic one, because the deck it rides in is mounted.
    expect(t.querySelectorAll('[data-testid="rx-tx-surface"]').length).toBe(1);
    expect(t.querySelector('.tx-panel')).toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
    // The deck really is semantic here — otherwise the count above would be
    // trivially satisfied by the legacy branch and prove nothing.
    expect(t.querySelector('.receiver-deck [data-testid="vfo-surface"]')).not.toBeNull();
    expect(t.querySelector('.vfo-header')).toBeNull();
  });

  // The mirror quadrant. `rxTx` declared without `vfo` mounts no semantic deck,
  // so there is no semantic RxTxSurface to own the key — the legacy TxPanel
  // must survive. Kills a "suppress TxPanel whenever rxTx is declared" rule,
  // which would leave this screen with NO unkey affordance at all (count 0).
  it('an rx-tx-only manifest keeps the legacy TX panel as the sole authority', () => {
    const t = render(RX_TX_ONLY);
    expect(t.querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
    expect(t.querySelectorAll('.tx-panel').length).toBe(1);
    expect(t.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
  });

  // The count stated as one law over every quadrant this shell can reach —
  // both partial manifests, both fully-declaring families, and the undeclared
  // fallback. A rule that fixed one quadrant by breaking another fails here.
  it.each([
    ['vfo-only', VFO_ONLY], ['rx-tx-only', RX_TX_ONLY],
    ['desktop-v2', 'desktop-v2' as SkinId], ['sdr-test', 'sdr-test' as SkinId],
    ['undeclared', 'no-such-layout' as SkinId],
  ])('holds the count at exactly one for the %s quadrant', (_label, skinId) => {
    expect(render(skinId).querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });
});

/**
 * MOR-1321 (S3a) — receiver-deck parity for the VFO ops.
 *
 * MOR-1313 put desktop-v2 on the v3 path and, with it, retired the legacy
 * `VfoOps` bridge from the deck: A=B, A↔B and the two composite quick triggers
 * left the flagship skin (A=B/A↔B survived only in the settings modal, which
 * the owner declined as parity). These assert the semantic deck carries them
 * again — end-to-end through the real RadioLayout mount, not just in the
 * surface's own unit tests.
 */
describe('the semantic receiver deck carries the VFO ops again (MOR-1321)', () => {
  const OPS = ['equalize', 'swap', 'quick-split', 'quick-dual-watch'] as const;

  // MUTATION KILLED: the ops landing in the surface but never being wired at
  // the desktop mount site — every VfoSurface unit test would still pass.
  it.each(['desktop-v2', 'sdr-test'] as const)('%s renders all four ops inside the deck', (skinId) => {
    const deck = render(skinId).querySelector('.receiver-deck')!;
    for (const op of OPS) expect(deck.querySelector(`[data-vfo-${op}]`), op).not.toBeNull();
    expect(deck.querySelector('[data-testid="vfo-split-digest"]')).not.toBeNull();
  });

  // The structural gate survives the trip through the real adapter: a
  // single-VFO radio has nothing to swap against, so the deck shows no ops.
  // Kills a wiring that renders them unconditionally at the mount site.
  it('a single-VFO topology renders no ops in the deck', () => {
    h.caps = capsFor('1/single');
    const deck = render('desktop-v2').querySelector('.receiver-deck')!;
    for (const op of OPS) expect(deck.querySelector(`[data-vfo-${op}]`), op).toBeNull();
    expect(deck.querySelector('[data-testid="vfo-split-digest"]')).toBeNull();
  });

  // R9 restated at the deck level: adding four buttons to the deck must not
  // add a second key authority. Same probe shape as the MOR-1313 matrix.
  it('adds no key authority to the deck', () => {
    const t = render('desktop-v2');
    expect(t.querySelectorAll('[data-testid="rx-tx-surface"], .tx-panel').length).toBe(1);
  });
});

/**
 * MOR-1364 (v3-rework S6-pre) — the ONE manifest-driven legacy-twin
 * suppression channel, landed INERT.
 *
 * S2/MOR-1313 built this rule for `vfo`/`rxTx` and S5/MOR-1341 for `meters`;
 * this slice generalises it to every remaining legacy twin the zone slices
 * S6a/S7/S8/S9 will retire — the sidebar panels, the settings modal's third
 * copy of five of them, and the status bar's scope indicator — by handing the
 * same `declaredSurfaces(manifest)` set `RadioLayout` already derives down to
 * `LeftSidebar`, `RightSidebar` and `StatusBar`.
 *
 * Nothing renders differently today (no manifest declares any of these zones),
 * which is the whole point: the risky plumbing lands once, independently
 * pinned, and each zone slice after it is a two-file manifest edit. The ONE
 * exception is the settings modal's SPLIT/A↔B/A=B row, which gates on the
 * already-true `semanticDeck` — a real, deliberate change, pinned by its own
 * named test below rather than folded into the inertness claim.
 *
 * The probes are `desktop-v2`'s REAL manifest plus exactly ONE zone — the
 * literal shape S6a/S7/S8/S9 will land — so a row that fails here is a
 * statement about the channel and not about a hand-built fixture.
 */
describe('the legacy-twin suppression channel (MOR-1364, S6-pre)', () => {
  /** Every panel id either sidebar can host. Without these the pins would pass
   *  vacuously for the boring reason that the drag order never offered the
   *  panel — `rx-audio`/`dsp`/`cw` are exactly the cross-sidebar ones a drag
   *  can move to the other side, so both sidebars are stocked with all of
   *  them. */
  const LEFT_ALL = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band',
    'antenna', 'scan', 'rx-audio', 'dsp', 'tx', 'cw', 'memory'];
  const RIGHT_ALL = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

  /**
   * surface → the zone id its rework slice will declare on `desktop-v2`.
   *
   * ALL GRADUATED. `scopeDisplay` graduated in MOR-1365 (S6a); `filter` and
   * `rfFrontEnd` in MOR-1366 (S7); `band`, `antenna` and `ritXitScan` in
   * MOR-1367 (S8); `rxAudio`, `dsp` and `cwKeyer` in MOR-1368 (S9).
   * `desktopV2Layout` now declares every one of them for real, so spreading
   * `desktopV2Layout.zones` plus a second entry with the same id would
   * duplicate a zone id the real manifest already owns. Each graduate's
   * coverage moved to a describe asserting the REAL registration — a
   * strictly stronger statement. Left as an empty literal rather than
   * deleted: the `it.each` below correctly reports zero cases now, which is
   * the intended terminal state of this synthetic-probe list, not a gap
   * (harness note, same structural blindness ruled on for S6a/S7/S8).
   */
  const ZONES: readonly (readonly [SemanticSurfaceName, string])[] = [];
  type CoveredSurface = SemanticSurfaceName;

  const ZONE_PROBE = Object.fromEntries(ZONES.map(([surface, zoneId]) => {
    const id = `${zoneId}-zone-probe` as SkinId;
    registerLayout(probeManifest(
      id,
      [...desktopV2Layout.zones, { id: zoneId, surfaces: [surface] }],
      desktopV2Layout.requiredSemanticSurfaces,
    ));
    return [surface, id];
  })) as Record<CoveredSurface, SkinId>;

  /**
   * One row per legacy twin the channel covers: which surface's zone retires
   * it, and where it lives.
   *
   * ALL GRADUATED, same set as `ZONES` above: `filter`/`rfFrontEnd` (S7),
   * `band`/`antenna`/`ritXitScan` (S8) and `rxAudio`/`dsp`/`cwKeyer` (S9) are
   * NOT rows here any more — the real `desktop-v2` manifest now declares
   * every one of their zones, so those twins no longer render at all —
   * asserted directly against the REAL registration in each family's own
   * dedicated test instead of through this "no zone declaring it yet"
   * inventory. `dsp` alone would have owned FIVE rows (`DspSurface` also
   * covers the AGC leaf, 5A/MOR-1290) had it still been here.
   */
  const TWINS: readonly (readonly [CoveredSurface, string, string])[] = [];

  /**
   * The ten twins S9 retired, kept as a named inventory so the *survival*
   * direction is still asserted somewhere: none of them may come back on any
   * probe, and a suppression that covers only one sidebar dies here.
   */
  const S9_RETIRED = [
    ['rxAudio', 'left sidebar RX AUDIO', '.left-sidebar [data-panel-id="rx-audio"]'],
    ['rxAudio', 'right sidebar RX AUDIO', '.right-sidebar [data-panel-id="rx-audio"]'],
    ['dsp', 'left sidebar AGC', '.left-sidebar [data-panel-id="agc"]'],
    ['dsp', 'left sidebar DSP', '.left-sidebar [data-panel-id="dsp"]'],
    ['dsp', 'right sidebar DSP', '.right-sidebar [data-panel-id="dsp"]'],
    ['dsp', 'settings modal DSP', '[data-panel-id="desktop-dsp"]'],
    ['dsp', 'settings modal AGC', '[data-panel-id="desktop-agc"]'],
    ['cwKeyer', 'left sidebar CW', '.left-sidebar [data-panel-id="cw"]'],
    ['cwKeyer', 'right sidebar CW', '.right-sidebar [data-panel-id="cw"]'],
    ['cwKeyer', 'settings modal CW', '[data-panel-id="desktop-cw"]'],
  ] as const;

  /**
   * The BAND twin is never MOUNT-gated, on any manifest, ever (S10 rows 6/10,
   * §4a): `BandSelector` hosts the LW/MW + SWL broadcast tabs that are
   * deliberately not facts and have no other production host, so unmounting the
   * component would orphan them. MOR-1367 (S8) retires the HAM half by PROP
   * instead — the hosts below keep rendering the component either way, which is
   * exactly what these two rows say. See the split pins in the zone-ownership
   * describe for the HAM-vs-broadcast half of the statement.
   */
  const BAND_TWINS = [
    ['left sidebar BAND', '.left-sidebar [data-panel-id="band"]'],
    ['settings modal BAND', '[data-panel-id="desktop-vfo-ops"] .band-tabs'],
  ] as const;

  /** Opens the settings modal too — the third host, and the only one that is
   *  not on screen by default. */
  function renderAll(skinId: SkinId): HTMLElement {
    const target = render(skinId);
    (target.querySelector('.settings-btn') as HTMLElement | null)?.click();
    flushSync();
    return target;
  }

  beforeEach(() => {
    // A radio that actually emits every gated family: two antennas (the
    // ANTENNA panel self-gates on the count), CW, and a scope.
    h.caps = { ...(capsFor('2/main_sub') as object), antennas: 2 } as Capabilities;
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    vi.mocked(hasAnyScope).mockReturnValue(true);
    localStorage.setItem('rigplane:panel-order', JSON.stringify(LEFT_ALL));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(RIGHT_ALL));
  });

  afterEach(() => {
    vi.mocked(hasAnyScope).mockReturnValue(false);
    localStorage.clear();
  });

  // INERTNESS, half one: every covered twin is on screen TODAY. Without this
  // the suppression pins below would be satisfied by a twin that never
  // rendered in the first place.
  it.each(TWINS)('[%s] %s renders on desktop-v2, no zone declaring it', (_surface, _host, selector) => {
    expect(renderAll('desktop-v2').querySelector(selector)).not.toBeNull();
  });

  // INERTNESS, half two, stated as an inventory rather than per-row: the exact
  // set of panels desktop-v2 renders. S6-pre landed it as "unchanged by the
  // channel"; every zone slice after it SHRINKS this literal by the ids its
  // zones retire, and the shrink is the intended signal (S6-pre verify N3 — a
  // builder who sees this go red must remove ids, never weaken the literal).
  //
  // MOR-1366 (S7) removed four: `desktop-rf` (settings modal RF FRONT END),
  // `filter`, `mode` and `rf-front-end` — the `filter`/`rf-front-end` zones are
  // now REAL on `desktop-v2`, not synthetic probes.
  //
  // MOR-1367 (S8) removed four more: `antenna`, `rit-xit`, `scan` (left
  // sidebar) and `desktop-rit` (settings modal). `band` STAYS — the band zone
  // retires only the component's HAM half, by prop, so the panel itself keeps
  // rendering (S10 §4a).
  //
  // MOR-1368 (S9) removes the whole cross-sidebar family: both `rx-audio`s,
  // both `dsp`s, both `cw`s, `agc`, and the modal's
  // `desktop-dsp`/`desktop-agc`/`desktop-cw` — ten ids, the largest single
  // retirement in the tail. Non-vacuous proof is in the dedicated "drops the
  // legacy ..." tests below, which use caps that make each evidence gate fire.
  it('renders exactly the panel inventory desktop-v2 renders post-S9', () => {
    const ids = [...renderAll('desktop-v2').querySelectorAll('[data-panel-id]')]
      .map((el) => el.getAttribute('data-panel-id'))
      .sort();
    expect(ids).toEqual([
      'band',
      'desktop-language', 'desktop-vfo-ops', 'desktop-workspace',
      'memory', 'memory',
    ]);
  });

  /**
   * MOR-1317 CLOSURE, panel side. `zone-ownership-coverage.test.ts` is the
   * ledger for the SURFACE direction (every `SEMANTIC_SURFACE_NAMES` member is
   * owned or excused in writing); this is the mirror for the PANEL direction —
   * every legacy panel still on screen on `desktop-v2` is a recorded decision,
   * not an oversight. Together they are what makes "MOR-1317 is closed for the
   * sidebar family" a checkable statement rather than a claim.
   *
   * Derived from the live DOM, so a panel that survives without an entry fails
   * here the moment it appears — the same shape of gap MOR-1317 names.
   */
  // Pruned by MOR-1368 (S9)'s rebase: `antenna`, `desktop-rf`, `desktop-rit`,
  // `filter`, `mode`, `rf-front-end`, `rit-xit` and `scan` graduated with S7
  // (filter/rfFrontEnd) and S8 (antenna/ritXitScan) — those eight panels no
  // longer render on `desktop-v2` at all, so an entry for them would be
  // exactly the stale-ledger drift the N1a check below now enforces.
  const SURVIVING_PANEL_REASONS: Record<string, string> = {
    band: 'S10 row 10 (PERMANENT for the broadcast half): BandSelector hosts the LW/MW + SWL '
      + 'tabs and 16 presets that are deliberately not facts and have no other production host. '
      + 'S8 retires only the HAM half, by a `hamBands` prop, never by unmounting the section.',
    'desktop-language': 'S10 row 8 — PERMANENT. Locale is an app preference, not a radio fact.',
    'desktop-vfo-ops': 'S10 row 7 (split row) is already gated on `semanticDeck`; the section '
      + 'itself is PERMANENT because of the band tabs above (row 10).',
    'desktop-workspace': 'S10 row 9 — PERMANENT. Workspace preferences are not a radio fact.',
    memory: 'NO SEMANTIC SURFACE EXISTS. Memory channels are not in the MOR-1262 vocabulary at '
      + 'all, so there is nothing to relocate into and nothing to double-present.',
    // Not in the inventory above under the default fixture, but reachable and
    // decided, so recorded here rather than discovered later:
    tx: 'R9 — the ONE key/unkey authority. It follows the semantic DECK via `hideTxPanel` '
      + '(MOR-1313) and must never be folded into the `declared` channel; `desktop-v2` already '
      + 'suppresses it that way, which is why it is absent above.',
    'audio-scope': 'NO SEMANTIC TWIN. The spectrum panel is its own subsystem, outside the '
      + 'surface vocabulary; absent above only because the right sidebar renders it under a '
      + 'different host in this fixture.',
  };

  it('every legacy panel still on screen is a recorded decision, not an oversight', () => {
    const ids = new Set([...renderAll('desktop-v2').querySelectorAll('[data-panel-id]')]
      .map((el) => el.getAttribute('data-panel-id')!));
    const undecided = [...ids].filter((id) => !(id in SURVIVING_PANEL_REASONS)).sort();
    expect(undecided).toEqual([]);
    // ...and no reason may be a placeholder.
    for (const [id, reason] of Object.entries(SURVIVING_PANEL_REASONS)) {
      expect(reason.length, id).toBeGreaterThan(30);
    }
  });

  // N1a (MOR-1368 S9): the panel ledger must be bidirectional. Unrecorded
  // surviving panels are caught above; stale entries (a panel that no longer
  // renders but keeps its reason) must fail too. `tx` and `audio-scope` are
  // exempt: they are deliberately not in the default-fixture inventory.
  it('no stale entry remains in the panel ledger after a zone retires its panels', () => {
    const ids = new Set([...renderAll('desktop-v2').querySelectorAll('[data-panel-id]')]
      .map((el) => el.getAttribute('data-panel-id')!));
    const EXEMPT = new Set(['tx', 'audio-scope']);
    const stale = Object.keys(SURVIVING_PANEL_REASONS).filter((id) => !ids.has(id) && !EXEMPT.has(id));
    expect(stale).toEqual([]);
  });

  // THE CHANNEL ITSELF, both directions in one assertion per surface: a
  // declared zone retires every twin of ITS OWN family, in every host, and
  // touches no sibling family's twin. A `dsp`-only suppression that leaves
  // `AgcPanel`, or a one-sided suppression that leaves the right sidebar's
  // `rx-audio`/`dsp`/`cw` reachable by a cross-sidebar drag, dies here.
  it.each(ZONES.map(([s]) => s))(
    'declaring the %s zone retires that family\'s twins in every host, and no sibling\'s',
    (surface) => {
      const t = renderAll(ZONE_PROBE[surface]);
      for (const [twinSurface, host, selector] of TWINS) {
        if (twinSurface === surface) {
          expect(t.querySelector(selector), `${host} must be suppressed`).toBeNull();
        } else {
          expect(t.querySelector(selector), `${host} must survive`).not.toBeNull();
        }
      }
      // Every probe spreads `desktopV2Layout.zones`, so the S9 family is
      // retired on all of them too: a probe that resurrected one would mean
      // the manifest lost a zone, not that the probe gained a panel.
      for (const [, host, selector] of S9_RETIRED) {
        expect(t.querySelector(selector), `${host} must stay retired`).toBeNull();
      }
    },
  );

  // MOR-1367 (S8) flipped this from the S6-pre deferral pin ("declaring `band`
  // suppresses nothing yet") to its split-aware successor. The band zone IS
  // declared on `desktop-v2` now, and the statement that survives is the one
  // that matters permanently: declaring it never UNMOUNTS either band host.
  // Which half of the component goes is asserted in the zone-ownership
  // describe below; here we only pin that neither host disappears, so a future
  // slice that "simplifies" the prop into an `{#if}` dies on this row.
  it('the band zone never unmounts a band host — the split is a prop, not a mount gate', () => {
    const t = renderAll('desktop-v2');
    for (const [host, selector] of BAND_TWINS) {
      expect(t.querySelector(selector), `${host} must survive`).not.toBeNull();
    }
    for (const [, host, selector] of TWINS) {
      expect(t.querySelector(selector), `${host} must survive`).not.toBeNull();
    }
  });

  // R9, restated over the remaining quadrants: none of the six probes may
  // change the number of elements that can key or unkey the transmitter. The
  // channel deliberately does NOT carry `rxTx` — `hideTxPanel` still follows
  // the semantic DECK (MOR-1313) and must stay a separate prop.
  it.each(ZONES.map(([s]) => s))('leaves exactly one key authority with the %s zone declared', (surface) => {
    expect(renderAll(ZONE_PROBE[surface]).querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });

  /**
   * MOR-1366 (S7) — `filter` and `rfFrontEnd` join the matrix for real.
   *
   * Same shape as MOR-1341's meters test above: proven with caps that
   * actually make the evidence gate fire, not the bare `capsFor()` default
   * (`modes: [], filters: []`, no rf-front-end capability tag), which would
   * pass these assertions vacuously (both the legacy twin and the semantic
   * surface self-gate away) and prove nothing about suppression. This is
   * also the closing evidence for §0.1 of the rework tail: BEFORE this slice,
   * `desktop-v2` double-presented both families (bare deck render +
   * legacy twin); the surface's `data-testid` count pins "exactly ONE".
   */
  it('drops the legacy MODE/FILTER twins in favour of the semantic filter surface', () => {
    h.caps = { ...capsFor('2/main_sub'), modes: ['USB', 'CW', 'FM'], filters: [1, 2, 3] };
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="mode"]')).toBeNull();
    expect(t.querySelector('.left-sidebar [data-panel-id="filter"]')).toBeNull();
    expect(t.querySelectorAll('[data-testid="filter-surface"]').length).toBe(1);
  });

  it('drops the legacy RF FRONT END twins (sidebar + modal) in favour of the semantic rfFrontEnd surface', () => {
    h.caps = {
      ...capsFor('2/main_sub'),
      capabilities: [...capsFor('2/main_sub').capabilities, 'preamp'],
    };
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="rf-front-end"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="desktop-rf"]')).toBeNull();
    expect(t.querySelectorAll('[data-testid="rf-front-end-surface"]').length).toBe(1);
  });

  // DOUBLE-PRESENTATION CLOSED, stated over BOTH families in caps that make
  // BOTH evidence gates fire at once — the shape most likely to reveal a
  // suppression that only covers one of the two zones this slice declares.
  it('presents filter and rfFrontEnd controls exactly ONCE each on desktop-v2', () => {
    h.caps = {
      ...capsFor('2/main_sub'),
      modes: ['USB', 'CW', 'FM'], filters: [1, 2, 3],
      capabilities: [...capsFor('2/main_sub').capabilities, 'preamp'],
    };
    const t = renderAll('desktop-v2');
    expect(t.querySelectorAll('[data-testid="filter-surface"]').length).toBe(1);
    expect(t.querySelectorAll('[data-testid="rf-front-end-surface"]').length).toBe(1);
    expect(t.querySelectorAll('.left-sidebar [data-panel-id="mode"]').length).toBe(0);
    expect(t.querySelectorAll('.left-sidebar [data-panel-id="filter"]').length).toBe(0);
    expect(t.querySelectorAll('.left-sidebar [data-panel-id="rf-front-end"]').length).toBe(0);
    expect(t.querySelectorAll('[data-panel-id="desktop-rf"]').length).toBe(0);
  });

  /**
   * MOR-1368 (S9) — `rxAudio`, `dsp` and `cwKeyer` join the matrix for real.
   *
   * Same shape as MOR-1341's meters test and MOR-1366's filter/rfFrontEnd
   * pair: proven with caps that actually make each evidence gate fire, not the
   * bare `capsFor()` default (no `nr`/`nb`/`notch`/`agc` tag, no `cw` tag),
   * which would pass these assertions vacuously — both the legacy twin and the
   * semantic surface self-gate away — and prove nothing about suppression.
   *
   * BEFORE this slice `desktop-v2` DOUBLE-PRESENTED all three families: the
   * semantic surface rendered bare in the deck AND the legacy panel rendered
   * in the sidebar. The `data-testid` counts below pin "exactly ONE".
   */
  /** A radio that emits every S9 family at once: audio chain, DSP, keyer. */
  const S9_CAPS = () => ({
    ...(capsFor('2/main_sub') as object),
    antennas: 2,
    capabilities: [
      ...(capsFor('2/main_sub').capabilities as string[]),
      'nr', 'nb', 'notch', 'agc', 'af_level', 'cw', 'break_in', 'apf',
    ],
  }) as Capabilities;

  it('drops the legacy RX AUDIO twins (both sidebars) for the semantic rxAudio surface', () => {
    h.caps = S9_CAPS();
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="rx-audio"]')).toBeNull();
    expect(t.querySelector('.right-sidebar [data-panel-id="rx-audio"]')).toBeNull();
    expect(t.querySelectorAll('[data-testid="rx-audio-surface"]').length).toBe(1);
  });

  // THE AGC PAIRING, pinned on its own (S9's named risk): `DspSurface` covers
  // NR/NB/notch AND the AGC leaf (5A/MOR-1290), so a `dsp` zone that retired
  // `DspPanel` and left `AgcPanel` — in the sidebar or in the modal — would
  // ship a half-double beside a surface that already draws AGC.
  it('drops the legacy DSP twins AND AgcPanel with them (sidebars + modal)', () => {
    h.caps = S9_CAPS();
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="dsp"]')).toBeNull();
    expect(t.querySelector('.right-sidebar [data-panel-id="dsp"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="desktop-dsp"]')).toBeNull();
    // The AGC half of the same family — the row that makes this a pairing.
    expect(t.querySelector('.left-sidebar [data-panel-id="agc"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="desktop-agc"]')).toBeNull();
    expect(t.querySelectorAll('[data-testid="dsp-surface"]').length).toBe(1);
  });

  // SAFETY-CRITICAL (MOR-1310). After this, `CwKeyerSurface` is the SOLE
  // break-in affordance on the flagship skin: `CwPanel` is gone from both
  // sidebars and from the settings modal. Two break-in controls disagreeing
  // about one radio setting is the defect this closes; zero would be worse,
  // so the count is pinned at exactly one, not merely ">= 0 legacy panels".
  it('drops the legacy CW twins (both sidebars + modal) for the semantic cwKeyer surface', () => {
    h.caps = S9_CAPS();
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="cw"]')).toBeNull();
    expect(t.querySelector('.right-sidebar [data-panel-id="cw"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="desktop-cw"]')).toBeNull();
    expect(t.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
  });

  // DOUBLE-PRESENTATION CLOSED, stated over all three families at once — the
  // shape most likely to reveal a suppression that covers only some of the
  // three zones this slice declares, or only one of the two sidebars.
  it('presents rxAudio, dsp and cwKeyer controls exactly ONCE each on desktop-v2', () => {
    h.caps = S9_CAPS();
    const t = renderAll('desktop-v2');
    expect(t.querySelectorAll('[data-testid="rx-audio-surface"]').length).toBe(1);
    expect(t.querySelectorAll('[data-testid="dsp-surface"]').length).toBe(1);
    expect(t.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
    for (const [, host, selector] of S9_RETIRED) {
      expect(t.querySelectorAll(selector).length, host).toBe(0);
    }
    // ...and relocating three families adds no second key/unkey affordance.
    expect(t.querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });

  // The non-vacuity control for the three tests above: on an UNDECLARED
  // layout, with the identical caps, every one of the ten twins is on screen.
  // Without this the suppressions could be satisfied by a fixture in which the
  // panels never rendered at all.
  it.each(S9_RETIRED)('[%s] %s renders on an undeclared layout with the same caps', (_s, _host, selector) => {
    h.caps = S9_CAPS();
    expect(renderAll('no-such-layout' as SkinId).querySelector(selector)).not.toBeNull();
  });

  /**
   * THE NAMED EXCEPTION (S10 §4) — the one thing this slice changes on screen.
   *
   * `semanticDeck` is ALREADY true on desktop-v2 (MOR-1313 declared
   * `receiver-deck`), and `VfoSurface` has carried translated split/swap/
   * equalize controls since MOR-1321; only the settings modal's third copy of
   * that row was never gated. Gating it removes three hardcoded, untranslated
   * strings from the flagship skin the day this merges.
   *
   * The false branch cannot be `sdr-test` — it declares `vfo` too, as do all
   * five registered manifests — so it is taken from the `rxTx`-only probe and
   * the unregistered-layout fallback, the only two `semanticDeck === false`
   * configurations this shell can reach.
   *
   * The row is VFO-ops routing, NOT a key/unkey affordance (S10 §6), so it
   * gates on `semanticDeck` and never on `semanticRxTx`; the R9 counts above
   * cover both probes.
   */
  it('NAMED EXCEPTION: retires the settings-modal SPLIT/A↔B/A=B row where the semantic deck owns it', () => {
    expect(renderAll('desktop-v2').querySelector('.settings-vfo-ops-row')).toBeNull();
    expect(renderAll(RX_TX_ONLY).querySelector('.settings-vfo-ops-row')).not.toBeNull();
    expect(renderAll('no-such-layout' as SkinId).querySelector('.settings-vfo-ops-row')).not.toBeNull();
    // ...and the semantic replacement really is on screen where the row went.
    expect(renderAll('desktop-v2').querySelector('.receiver-deck [data-vfo-split]')).not.toBeNull();
    // The BAND half of the same modal section is untouched by this exception.
    expect(renderAll('desktop-v2').querySelector('[data-panel-id="desktop-vfo-ops"] .band-tabs')).not.toBeNull();
  });
});

/**
 * MOR-1371 (v3-rework S11) — the drag-order/workspace boundary
 * (`docs/plans/2026-08-06-panel-order-workspace-boundary.md`).
 *
 * `LeftSidebar` and `RightSidebar` do NOT prune or filter their `defaults`
 * literal — the doc's §1.2 ruling, reversed from the brief's tentative
 * recommendation on EMPIRICAL evidence, not just the LCD-sharing argument.
 * `rigplane:panel-order`/`rigplane:right-panel-order` are ONE storage key
 * shared by every skin that mounts these components (`desktop-v2`,
 * `sdr-test` via `RadioLayout`; `lcd-cockpit`/`lcd-scope` via `LcdLayout`),
 * and `loadPanelOrder` always prefers a valid STORED order over `defaults`.
 * A per-`declared` computation of `defaults` — tried and reverted during this
 * slice's build — only affects a brand-new user's very first load; a
 * RETURNING user who switches skins inherits whichever skin last wrote the
 * shared key, silently losing panels on the OTHER skin the moment the two
 * skins' id sets diverge. That is not hypothetical: it reproduced as a real
 * full-suite failure in `semantic-lcd-migration.component.test.ts` (a
 * `desktop-v2`-shaped write leaking into an LCD-context mount via the
 * `--localstorage-file`-backed store shared across the whole run) before
 * this file's tests below were changed to stop asserting it. The only safe
 * suppression point is the RENDER `{#if}` (`declared.has(...)`), which was
 * already correct before this slice — these tests pin that it stays correct
 * even when `order` still names every legacy id, on both a declaring and a
 * non-declaring manifest, so a future attempt to "clean up" the shared
 * literal has a regression test to fail against.
 */
describe('a stored order naming every legacy panelId cannot resurrect a declared-retired one (MOR-1371, S11)', () => {
  const RETIRED_ON_DESKTOP_V2 = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit',
    'antenna', 'scan', 'rx-audio', 'dsp', 'cw'];

  it('the ten desktop-v2-declared ids do not render even when a stored order still names them', () => {
    localStorage.setItem('rigplane:panel-order', JSON.stringify(
      ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna', 'scan']));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(
      ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory']));
    let t!: HTMLElement;
    expect(() => { t = render('desktop-v2'); }).not.toThrow();
    const ids = new Set([...t.querySelectorAll('[data-panel-id]')]
      .map((el) => el.getAttribute('data-panel-id')));
    for (const stale of RETIRED_ON_DESKTOP_V2) {
      expect(ids.has(stale), stale).toBe(false);
    }
  });

  // The reason `defaults` stays untouched (doc §1.2): the IDENTICAL stored
  // order, mounted where nothing is declared, renders every one of the same
  // ten ids normally. Deleting them from the shared literal would have been
  // wrong for this shape, not just untested for it.
  it('the identical stored order renders all ten ids on a layout that declares nothing', () => {
    localStorage.setItem('rigplane:panel-order', JSON.stringify(
      ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna', 'scan']));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(
      ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory']));
    h.caps = { ...(capsFor('2/main_sub') as object), antennas: 2 } as Capabilities;
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    const t = render('sdr-test');
    const ids = new Set([...t.querySelectorAll('[data-panel-id]')]
      .map((el) => el.getAttribute('data-panel-id')));
    for (const id of RETIRED_ON_DESKTOP_V2) {
      expect(ids.has(id), id).toBe(true);
    }
  });

  // F1: source-text literal pins (S9 zone-ownership-coverage precedent) — the
  // doc's §1.2 ruling has no regression test in pin 2 (which sets a stored
  // order that defaults are never read from). Pruning these shared `defaults`
  // arrays is unsafe, because `rigplane:panel-order` and `rigplane:right-panel-order`
  // are ONE storage key shared by every skin that mounts these sidebars
  // (RadioLayout: desktop-v2/sdr-test; LcdLayout: lcd-cockpit/lcd-scope).
  // `loadPanelOrder` always prefers a stored order over `defaults`, so a
  // pruned defaults only affects a brand-new user — a RETURNING user who
  // switches skins would inherit the pruned order and silently lose panels on
  // the OTHER skin. This pin catches that hazard by enforcing the shared
  // literals stay identical across all skins.
  it('the sidebars\' defaults arrays stay intact as shared storage across all skins', () => {
    expect(readFileSync('src/components-v2/layout/LeftSidebar.svelte', 'utf8'))
      .toContain("defaults: ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna', 'scan']");
    expect(readFileSync('src/components-v2/layout/RightSidebar.svelte', 'utf8'))
      .toContain("defaults: ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory']");
  });
});

/**
 * MOR-1365 (v3-rework S6a) — `scopeDisplay` GRADUATES from a synthetic probe
 * (the `ZONE_PROBE` shape above) to a REAL zone `desktopV2Layout` declares.
 * The status bar's legacy scope indicator retires through the SAME MOR-1364
 * suppression channel; this describe proves it directly against the real
 * manifest, not a hand-built one, so a regression here is a statement about
 * `desktop-declarations.ts` and not about test scaffolding.
 *
 * MUTATION PROBE (required by the ticket): dropping the `scope-display` zone
 * from `DESKTOP_V2_ZONES` turns this file's own
 * `scope-display-declarability.test.ts` / `desktop-v2-registration.test.ts` /
 * `preferences-adoption.test.ts` pins red (the S5 MM2 fan-out) AND turns the
 * first test below red too — five independent pins, not a thin margin.
 */
describe('scopeDisplay zone ownership retires the status bar scope indicator (MOR-1365, S6a)', () => {
  const SCOPE_INDICATOR = '.status-indicators [title^="Scope WebSocket"]';

  beforeEach(() => {
    h.caps = { ...(capsFor('2/main_sub') as object), antennas: 2 } as Capabilities;
    vi.mocked(hasAnyScope).mockReturnValue(true);
  });

  afterEach(() => {
    vi.mocked(hasAnyScope).mockReturnValue(false);
  });

  // THE CHANNEL, on the real manifest: the twin that rendered unconditionally
  // before this slice (proved by the S6-pre `TWINS` inventory prior to
  // MOR-1365 removing the row — see git history) is gone.
  it('suppresses the status bar scope indicator on real desktop-v2', () => {
    expect(render('desktop-v2').querySelector(SCOPE_INDICATOR)).toBeNull();
  });

  // Mirrors the S6-pre matrix's own inertness half: a layout that declares NO
  // scope-display zone still shows the legacy indicator — proves the
  // predicate reads the manifest, not a global flag.
  it('leaves the indicator on a layout that declares no scope-display zone', () => {
    expect(render(RX_TX_ONLY).querySelector(SCOPE_INDICATOR)).not.toBeNull();
  });

  // R9 restated once more: this zone carries no key/unkey authority.
  it('adds no key authority when the scope-display zone is declared', () => {
    expect(render('desktop-v2').querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });

  // MOR-1069, verified rather than assumed (ScopeDisplaySurface is a
  // ZERO-FOCUSABLE pure readout — see its own file header): declaring the
  // zone contributes NOTHING to desktop-v2's tab sequence. This shell mounts
  // `RadioLayout` directly (not under `App`), so it never provides a
  // `SurfacePlan` context and cannot see the `data-zone-id` wrapper itself
  // (`useSurfacePlan()` falls back to `NO_PLAN`, same as every other test in
  // this file) — the zone-binding half of MOR-1069 is proved with a real
  // context in `semantic-scope-display-wiring.component.test.ts` instead.
  // What THIS harness can and does prove is the surface's own contract.
  it('contributes no focusable control to desktop-v2 (MOR-1069 sequence unmoved)', () => {
    const surface = render('desktop-v2').querySelector('[data-testid="scope-display-surface"]');
    expect(surface).not.toBeNull();
    expect(surface!.querySelectorAll('button, input, select, a[href], [tabindex]')).toHaveLength(0);
  });
});

/**
 * MOR-1367 (v3-rework S8) — `band`, `antenna` and `ritXitScan` become
 * ZONE-OWNED on `desktop-v2`, closing the live double-presentation these three
 * families have shown on the flagship skin since their B-slices landed (7B/8C/
 * 8B mounted the surfaces; no manifest declared the zones, so `zoneOwning()`
 * returned `null` and each rendered BARE inside the receiver deck while the
 * sidebar and modal twins kept rendering too).
 *
 * Everything here is asserted against the REAL `desktop-v2` registration, not a
 * synthetic probe — the three surfaces graduated off the S6-pre probe apparatus
 * above because a probe would now declare a duplicate zone id.
 *
 * NON-VACUITY is the whole design of this describe (the MOR-1304-verify P1/P5
 * trap, and the S5/MOR-1341 `drops the legacy meters dock` pattern): the outer
 * file's default `capsFor('2/main_sub')` reports `freqRanges: []`, `antennas`
 * undefined and no `rit`/`xit` tag, so all three evidence gates would decline
 * their groups and every "the surface is present" assertion would pass for the
 * boring reason that nothing rendered at all. The fixture below makes each
 * `derive*` gate actually FIRE:
 *   - `deriveBand`   — non-empty `caps.freqRanges` (adapter:683);
 *   - `deriveAntenna`— `caps.antennas > 1` (adapter:788);
 *   - `deriveRitXit` — a `rit`/`xit` capability tag (adapter:761);
 *   - `deriveScan`   — at least one of scanning/scanType/scanResumeMode in state.
 *
 * S-ADJ NOTE: this slice writes NO gate logic. 7B's TX-permission readout, 8C's
 * under-power antenna gating and 8B's observation gates are untouched, and
 * their own tests are unedited by this change. What the slice does is remove
 * the legacy fallbacks, which makes those semantic gates the ONLY ones left —
 * see the build report's carry-forward section (MOR-1307-N4, MOR-1309-N1,
 * MOR-1308-N4).
 */
describe('band, antenna and ritXitScan are zone-owned on desktop-v2 (MOR-1367, S8)', () => {
  const LEFT_ALL = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band',
    'antenna', 'scan', 'rx-audio', 'dsp', 'tx', 'cw', 'memory'];
  const RIGHT_ALL = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

  /** Two HAM bands the legacy `BandSelector` grid renders by name — so the
   *  "HAM half is gone" pin below is about content, not just a tab strip. */
  const HAM_RANGES = [{
    start: 1_800_000, end: 30_000_000, label: 'HF',
    bands: [
      { name: '40m', start: 7_000_000, end: 7_300_000, default: 7_100_000 },
      { name: '20m', start: 14_000_000, end: 14_350_000, default: 14_225_000, bsrCode: 5 },
    ],
  }];
  const LW_MW_PRESETS = ['LW', 'MW', '120m', '90m', '75m', '60m'];
  const SW_PRESETS = ['49m', '41m', '31m', '25m', '22m', '19m', '16m', '15m', '13m', '11m'];

  function renderAll(skinId: SkinId): HTMLElement {
    const target = render(skinId);
    (target.querySelector('.settings-btn') as HTMLElement | null)?.click();
    flushSync();
    return target;
  }

  /**
   * The zone ELEMENT half of ownership needs the resolved plan, which reaches
   * `SemanticRadioSurfaces` through Svelte context that only `App.svelte`
   * provides — so `render()` above (a standalone `RadioLayout` mount) leaves
   * `useSurfacePlan()` at its `NO_PLAN` default and every surface renders bare,
   * declared or not. `resolution.ts:148` exports the context key for exactly
   * this: a test may supply a plan through `mount`'s `context` option.
   *
   * This is the S5 asymmetry made visible in one file: the LEGACY suppression
   * reads the MANIFEST (so it works in every mount above), while the ZONE
   * WRAPPER reads the PLAN (so it needs this).
   */
  function renderWithPlan(skinId: SkinId): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const plan = resolveSurfacePlan(desktopV2Layout, DEFAULT_WORKSPACE);
    mounted.push(mount(RadioLayout, {
      target,
      props: { skinId },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    }));
    flushSync();
    return target;
  }

  const texts = (root: Element | null, selector: string) =>
    [...(root?.querySelectorAll(selector) ?? [])].map((el) => el.textContent?.trim());

  beforeEach(() => {
    h.caps = {
      ...(capsFor('2/main_sub') as object),
      antennas: 2,
      capabilities: ['scope', 'audio', 'tx', 'dual_rx', 'rit', 'xit'],
      freqRanges: HAM_RANGES,
    } as Capabilities;
    h.state = {
      ...(liveState() as object),
      scanning: false, scanType: 0x34, scanResumeMode: 1,
      txAntenna: 1, rxAntenna1: 0, ritOn: false, ritTx: false, ritFreq: 0,
    };
    // The legacy `BandSelector` reads its HAM grid from the capabilities STORE,
    // not from `runtime.caps` — without this the grid is empty and the split
    // pin would only ever be about the tab strip.
    vi.mocked(getCapabilities).mockReturnValue(
      { freqRanges: HAM_RANGES, modes: [], filters: [] } as never,
    );
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    vi.mocked(hasAnyScope).mockReturnValue(true);
    localStorage.setItem('rigplane:panel-order', JSON.stringify(LEFT_ALL));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(RIGHT_ALL));
  });

  afterEach(() => {
    vi.mocked(getCapabilities).mockReturnValue({ freqRanges: [], modes: [], filters: [] } as never);
    vi.mocked(hasAnyScope).mockReturnValue(false);
    localStorage.clear();
  });

  // Non-vacuity, stated first and on its own: every one of the three evidence
  // gates fires under this fixture. If one stops firing, the suppression pins
  // below would still pass while asserting nothing — this row is what makes
  // them mean something.
  it('the fixture actually emits all three groups (non-vacuity)', () => {
    const t = renderAll('desktop-v2');
    expect(t.querySelector('[data-testid="band-surface"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="antenna-surface"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="ritxit-scan-surface"]')).not.toBeNull();
  });

  // Each declared zone actually binds a zone element and OWNS its surface —
  // the difference between "declared" and "still rendering bare inside the
  // deck", which is the double-presentation defect this slice closes. Run
  // against the real resolved plan (see `renderWithPlan`), because that is the
  // read `zoneOwning()` makes.
  it.each([['band', 'band-surface'], ['antenna', 'antenna-surface'],
    ['rit-xit-scan', 'ritxit-scan-surface']])(
    'mounts %s inside its own declared zone element', (zoneId, testid) => {
      const t = renderWithPlan('desktop-v2');
      const zone = t.querySelector(`[data-zone-id="${zoneId}"]`);
      expect(zone, `${zoneId} zone element`).not.toBeNull();
      expect(zone!.querySelector(`[data-testid="${testid}"]`)).not.toBeNull();
      // …and it is the ONLY instance — no second, bare mount alongside it.
      expect(t.querySelectorAll(`[data-testid="${testid}"]`).length).toBe(1);
    },
  );

  // The ritXitScan zone retires THREE twins across two hosts. A slice that
  // gated RIT/XIT and forgot SCAN — they are separate legacy panels sharing one
  // surface, the same shape as the `dsp`→`agc` pairing — dies here.
  it('drops the legacy RIT / XIT + SCAN twins (sidebar + modal) for the semantic surface', () => {
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="rit-xit"]')).toBeNull();
    expect(t.querySelector('.left-sidebar [data-panel-id="scan"]')).toBeNull();
    expect(t.querySelector('[data-panel-id="desktop-rit"]')).toBeNull();
    expect(t.querySelector('[data-testid="ritxit-scan-surface"]')).not.toBeNull();
  });

  // 8C's own gate (`antennaCount > 1`) and the sidebar panel's identical
  // self-gate both pass under this fixture, so the twin genuinely WOULD render
  // without the zone — see the mutation battery.
  it('drops the legacy ANTENNA twin for the semantic surface', () => {
    const t = renderAll('desktop-v2');
    expect(t.querySelector('.left-sidebar [data-panel-id="antenna"]')).toBeNull();
    expect(t.querySelector('[data-testid="antenna-surface"]')).not.toBeNull();
  });

  /**
   * THE SPLIT (S10 §4a, rows 6 and 10) — the one asymmetric retirement in the
   * wave. `BandSelector` keeps its mount in BOTH hosts; only its HAM tab and
   * HAM grid go, because `BandSurface` duplicates those and nothing duplicates
   * the broadcast presets (`semantic/radio-view-model.ts:494-496` excludes them
   * from the vocabulary BY NAME) and `BandSelector` is their only production
   * consumer.
   */
  it('retires the HAM half of BandSelector in BOTH hosts and keeps the broadcast half', () => {
    const t = renderAll('desktop-v2');
    for (const [host, root] of [
      ['left sidebar', t.querySelector('.left-sidebar [data-panel-id="band"]')],
      ['settings modal', t.querySelector('[data-panel-id="desktop-vfo-ops"]')],
    ] as const) {
      expect(root, `${host} still hosts BandSelector`).not.toBeNull();
      // The HAM tab is gone; the two broadcast tabs are not.
      expect(texts(root, '.band-tab'), `${host} tabs`).toEqual(['LW/MW', 'SWL']);
      // …and so is the HAM grid's content — the default landed on LW/MW, which
      // is the other half of §4a's instruction ("gate the `bandMode === 'ham'`
      // DEFAULT too"): without it the component would open on an empty grid.
      expect(texts(root, '.grid button'), `${host} grid`).toEqual(LW_MW_PRESETS);
      expect(texts(root, '.grid button')).not.toContain('20m');
    }
    // The semantic replacement really is on screen where the HAM grid went.
    expect(t.querySelector('[data-testid="band-choices"]')).not.toBeNull();
  });

  // PRESET SURVIVAL. Both broadcast tabs remain reachable and every preset is
  // still there. Note the count: `broadcast-presets.ts` ships SIXTEEN presets
  // (6 LW/MW + 10 SW), not the seventeen the S10 doc states — the doc counted
  // the `BroadcastPreset` interface's own `name:` field. Pinned as the measured
  // number, with the discrepancy recorded rather than propagated.
  it('keeps all 16 broadcast presets reachable after the split', () => {
    const t = renderAll('desktop-v2');
    const panel = t.querySelector('.left-sidebar [data-panel-id="band"]')!;
    expect(texts(panel, '.grid button')).toEqual(LW_MW_PRESETS);
    (texts(panel, '.band-tab').indexOf('SWL') >= 0
      ? (panel.querySelectorAll('.band-tab')[1] as HTMLElement)
      : null)?.click();
    flushSync();
    expect(texts(panel, '.grid button')).toEqual(SW_PRESETS);
    expect(LW_MW_PRESETS.length + SW_PRESETS.length).toBe(16);
  });

  // The un-split direction: a host that does NOT declare `band` renders the
  // pre-split three-tab component, HAM grid and all. `hamBands` defaults to
  // `true`, so mobile/LCD and every other caller are untouched by the split.
  it('an undeclared layout still renders the full three-tab BandSelector', () => {
    const panel = renderAll('no-such-layout' as SkinId)
      .querySelector('.left-sidebar [data-panel-id="band"]')!;
    expect(texts(panel, '.band-tab')).toEqual(['HAM', 'LW/MW', 'SWL']);
    expect(texts(panel, '.grid button')).toEqual(['40m', '20m']);
  });

  // §1.5 / MOR-1339: `compositionSurfaces(plan(desktopV2Layout))` grew by three
  // members, and NO `{#each singleOrder}` branch was added for any of them, so
  // each surface must render exactly ONCE. This is the double-presentation
  // CLOSED assertion — the shape most likely to reveal a suppression that
  // covers only some of the three zones, or a second mount path.
  it('presents band, antenna and ritXitScan exactly ONCE each on desktop-v2', () => {
    const t = renderAll('desktop-v2');
    for (const testid of ['band-surface', 'antenna-surface', 'ritxit-scan-surface']) {
      expect(t.querySelectorAll(`[data-testid="${testid}"]`).length, testid).toBe(1);
    }
    for (const selector of [
      '.left-sidebar [data-panel-id="rit-xit"]', '.left-sidebar [data-panel-id="scan"]',
      '[data-panel-id="desktop-rit"]', '.left-sidebar [data-panel-id="antenna"]',
    ]) {
      expect(t.querySelectorAll(selector).length, selector).toBe(0);
    }
    // The band family's "exactly once" is tab-shaped, not panel-shaped: one
    // semantic band grid, and zero HAM tabs anywhere on the flagship skin.
    expect(t.querySelectorAll('[data-testid="band-choices"]').length).toBe(1);
    expect([...t.querySelectorAll('.band-tab')].filter((b) => b.textContent?.trim() === 'HAM').length)
      .toBe(0);
  });

  // R9, over the real manifest rather than a probe: three more declared zones
  // must not change the number of elements that can key or unkey. `hideTxPanel`
  // still follows the semantic DECK and stays a separate prop (§1.4).
  it('leaves exactly one key authority with all three zones declared', () => {
    expect(renderAll('desktop-v2').querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });
});

/**
 * MOR-1370 (v3-rework S6b-2) — `scopeControls` becomes ZONE-OWNED on
 * `desktop-v2`, the LAST surface in the whole MOR-1262 vocabulary to
 * graduate; `zone-ownership-coverage.test.ts`'s `RECORDED_REASONS` ledger is
 * empty after this slice.
 *
 * Unlike every other family in this file, its legacy twin is not a
 * sidebar/modal panel but the scope toolbar's fact-backed half
 * (`SpectrumToolbar.svelte`, behind the MOR-1369 (S6b-1) `hideScopeControls`
 * suppression channel — `RadioLayout.svelte` forwards
 * `declared.has('scopeControls')` straight through to `SpectrumPanel`, which
 * this file's `SpectrumPanelStub` mock records as `data-hide-scope-controls`).
 * The toolbar's own leaf-level removal (the twelve `scopeControls.*` fields
 * gone, the eight client-side view options untouched) is proven directly in
 * `SpectrumToolbar.component.test.ts`'s S6b-1 pins — not re-proven here.
 * `ScopeControlsSurface` is control-bearing (MOR-1304 canon) and mounts
 * single-composition-only, so this describe has no dual-composition half to
 * prove — that half, plus the zone-binding assertion (S6a's context-injection
 * recipe), lives in `semantic-scope-controls-wiring.component.test.ts`.
 *
 * NON-VACUITY: `deriveScopeControls` gates only on the `scope` capability tag
 * (`radio-view-model-adapter.ts:963`), which the outer file's default
 * `capsFor('2/main_sub')` already declares — no special fixture is needed for
 * the surface itself to fire, unlike S8's band/antenna/ritXitScan gates.
 */
describe('scopeControls is zone-owned on desktop-v2, retiring the toolbar fact-backed half (MOR-1370, S6b-2)', () => {
  // THE CHANNEL, on the real manifest.
  it('suppresses the fact-backed toolbar half on real desktop-v2', () => {
    const t = render('desktop-v2');
    const stub = t.querySelector('.spectrum-panel-stub');
    expect(stub?.getAttribute('data-hide-scope-controls')).toBe('true');
  });

  // Mirrors the S6-pre matrix's own inertness half: a layout that declares NO
  // scope-controls zone leaves the toolbar's fact-backed half on screen —
  // proves the predicate reads the manifest, not a global flag.
  it('leaves the toolbar unsuppressed on a layout that declares no scope-controls zone', () => {
    const t = render(RX_TX_ONLY);
    const stub = t.querySelector('.spectrum-panel-stub');
    expect(stub?.getAttribute('data-hide-scope-controls')).toBe('false');
  });

  // §1.5 / MOR-1339: `compositionSurfaces(plan(desktopV2Layout))` grew by one
  // more member, and no `{#each singleOrder}` branch was added for it — the
  // semantic surface must render exactly ONCE. This is the double-presentation
  // CLOSED assertion for the last family in the vocabulary.
  it('presents the scopeControls surface exactly ONCE on desktop-v2', () => {
    expect(render('desktop-v2').querySelectorAll('[data-testid="scope-controls-surface"]').length).toBe(1);
  });

  // R9: relocating the toolbar's fact-backed half adds no key/unkey authority.
  it('adds no key authority when the scope-controls zone is declared', () => {
    expect(render('desktop-v2').querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });
});
