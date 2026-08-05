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
import { hasCapability } from '$lib/stores/capabilities.svelte';
import { topologyFixtures, type TopologyFixtureId } from '../../../semantic/fixtures/topologies';
import { registerLayout, type LayoutManifest } from '../../../presentation/layouts/contract';
// Barrel, for the same reason `skins/dual-receiver-cockpit/__tests__/
// DualReceiverCockpit.component.test.ts` uses it: a real registered manifest to
// derive the probes below from. RadioLayout.svelte already pulls this module in
// (its side-effect registry import), so this adds no registration of its own.
import { desktopV2Layout } from '../../../presentation/layouts/declarations';

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
  // family that actually ships to every Icom radio.
  it('leaves the rest of the layout intact', () => {
    const t = render('desktop-v2');
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.center-column .spectrum-slot')).not.toBeNull();
    expect(t.querySelector('.bottom-dock [data-testid="meters-dock-panel"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="rx-audio"]')).not.toBeNull();
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
