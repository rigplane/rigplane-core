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
 *   2. everything else in that layout (status bar, sidebars, spectrum)
 *      is untouched.
 *
 * What decides (1) is no longer a skin id but the ACTIVE layout manifest's zone
 * declarations, so the last two describes below render the matrix itself: a
 * declared surface is semantic and its legacy twin is gone; a surface no zone
 * declares keeps its legacy presentation. `sdr-test` declares both, so it is
 * the all-semantic case.
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
  getWsConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  getRadioStatus: vi.fn(() => 'disconnected'),
  getRadioLinkState: vi.fn(() => 'disconnected'),
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
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
import { desktopV2Layout, sdrTestLayout } from '../../../presentation/layouts/declarations';
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

  // MOR-1346: `.bottom-dock` dropped out of this list — sdr-test now
  // declares a `meters` zone too, so the legacy dock retires unconditionally
  // (see the dedicated meters-suppression test below, which is what actually
  // proves the dock/semantic-surface swap with real meter data).
  it('leaves the rest of the layout intact', () => {
    const t = render('sdr-test');
    expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.center-column .spectrum-slot')).not.toBeNull();
    expect(t.querySelector('.spectrum-panel-stub')).not.toBeNull();
    // A non-TX sidebar panel is untouched by the TX suppression. `rx-audio`
    // used to stand here beside `memory` and left the list in MOR-2231 batch 3,
    // which declares that surface: the panel now retires on the `declared`
    // channel, so it can no longer witness anything about `hideTxPanel`.
    // `memory` still can — neither sidebar puts any `declared.has(...)` guard
    // on it, so no manifest can retire it.
    expect(t.querySelector('[data-panel-id="memory"]')).not.toBeNull();
  });

  // MUTATION KILLED: widening `hideTxPanel` to the sidebar's whole `showTx`
  // branch, which also guards CW. The suppression is TX-panel-scoped by
  // construction, but nothing pinned it — `hasCapability` is mocked false
  // everywhere else in this file, so the CW block never renders to be checked.
  //
  // Runs on the `vfo`-only probe, not on `sdr-test`: MOR-2231 batch 3 declares
  // `cwKeyer` there, so `CwPanel` now retires on the `declared` channel and
  // `sdr-test` can no longer separate the two suppressions. The probe restores
  // exactly the configuration the mutation needs — `semanticDeck` true, so
  // `hideTxPanel` is on, and no `cw-keyer` zone, so only the TX branch could
  // remove the CW panel.
  it('keeps the CW panel, which shares the sidebar\'s TX branch', () => {
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    const t = render(VFO_ONLY);
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

  // MOR-1346 — the bottom dock joins the matrix for `sdr-test` too, the same
  // move MOR-1341 (S5) made for `desktop-v2` below. Proven with a state that
  // reports a real meter reading, not the bare `liveState()` fixture every
  // other test in this describe uses — see the MOR-1341 comment on the
  // `desktop-v2` block below for why a bare fixture (no meter fields at all)
  // would prove nothing about suppression.
  it('drops the legacy meters dock in favour of the semantic meters surface', () => {
    const state = liveState() as { main: Record<string, unknown> };
    h.state = { ...state, main: { ...state.main, sMeter: 120 } };
    const t = render('sdr-test');
    expect(t.querySelector('.bottom-dock')).toBeNull();
    expect(t.querySelector('[data-testid="meters-dock-panel"]')).toBeNull();
    expect(t.querySelector('[data-testid="meters-surface"]')).not.toBeNull();
  });

  // MOR-1346 F1 — `VFO_ONLY` declares `vfo` but not `meters`, so it answers
  // `declared.has('meters')` and `declared.has('vfo')` DIFFERENTLY. `declared.
  // has('vfo')` in `semanticMeters`'s place would wrongly suppress the dock
  // here; `UNDECLARED` (declares nothing) cannot catch that mutation, because
  // an empty set answers both predicates the same way (false).
  it('keeps the dock for a layout that declares vfo but not meters', () => {
    const t = render(VFO_ONLY);
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
  });

  it('keeps the dock for a layout that declares rxTx but not meters', () => {
    const t = render(RX_TX_ONLY);
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
  });

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
 * `rx-tx: [rxTx]`), so suppression covering both twins is what proves it is
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
 * MOR-2231 (step 1, batch 1) — the SDR face's two zone HOSTS.
 *
 * `sdrTestLayout` splits the pair into `receiver-deck: [vfo]` + `rx-tx:
 * [rxTx]`, reusing desktop-v2's ids, and `RadioLayout` asks
 * `SemanticRadioSurfaces` for the wrapper elements on that face alone
 * (`regions={skinId === 'sdr-test'}`).
 *
 * Why the second `it` is the one that earns this block: BOTH manifests now
 * declare zones owning `vfo` and `rxTx`, so `zoneOwning()` answers non-null on
 * either face and the PLAN cannot be what separates them. Only the `regions`
 * prop can, and until this test existed nothing in `frontend/src` inspected
 * the SHAPE of desktop-v2's rendered subtree at all.
 *
 * Both mounts hand the plan in through context for the reason `renderWithPlan`
 * in the S8 describe records: a standalone `RadioLayout` mount leaves
 * `useSurfacePlan()` at `NO_PLAN`, and every zone wrapper then disappears
 * whatever the manifest declares.
 */
describe('vfo and rxTx gain zone hosts on the SDR face only (MOR-2231)', () => {
  function renderZoned(skinId: SkinId, manifest: LayoutManifest): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const plan = resolveSurfacePlan(manifest, DEFAULT_WORKSPACE);
    mounted.push(mount(RadioLayout, {
      target,
      props: { skinId },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    }));
    flushSync();
    return target;
  }

  // MUTATION KILLED: leaving `vfo`/`rxTx` outside `zoned()`, or declaring the
  // split zones without routing the surfaces through them — the manifest would
  // name two hosts the DOM never builds.
  it('hosts each sdr-test surface in the zone element its manifest declares', () => {
    const t = renderZoned('sdr-test', sdrTestLayout);
    expect(t.querySelector('[data-zone-id="receiver-deck"] [data-testid="vfo-surface"]'))
      .not.toBeNull();
    expect(t.querySelector('[data-zone-id="rx-tx"] [data-testid="rx-tx-surface"]'))
      .not.toBeNull();
  });

  // MUTATION KILLED: passing `regions` unconditionally (or letting `zoned()`
  // take `vfo`/`rxTx` on every face), which would wrap desktop-v2's two
  // surfaces too. That is the byte-identity claim this batch rests on, and
  // this is the only assertion in the suite that can refuse it.
  it('leaves the desktop-v2 surfaces bare, with no zone element around either', () => {
    const t = renderZoned('desktop-v2', desktopV2Layout);
    const vfo = t.querySelector('[data-testid="vfo-surface"]');
    const rxTx = t.querySelector('[data-testid="rx-tx-surface"]');
    expect(vfo).not.toBeNull();
    expect(rxTx).not.toBeNull();
    expect(vfo!.closest('.surface-zone')).toBeNull();
    expect(rxTx!.closest('.surface-zone')).toBeNull();
    expect(t.querySelector('[data-zone-id="receiver-deck"]')).toBeNull();
    expect(t.querySelector('[data-zone-id="rx-tx"]')).toBeNull();
  });
});

/**
 * MOR-2231 (step 1, batch 2) — the SDR face's five control families, at the
 * RENDER level.
 *
 * `sdrTestLayout` declares `filter`, `rf-front-end`, `band`, `antenna` and
 * `rit-xit-scan`. That has two effects and this describe pins both, because
 * until it existed the batch's PRINCIPAL effect was asserted nowhere: the
 * manifest tests prove what is declared, not what the declaration does to the
 * screen.
 *
 *   1. ZONE HOSTS. Each surface already mounted on this face BARE, through the
 *      single composition's `zoned()` calls (`allowBare` defaults true), so the
 *      declaration moves it inside a `[data-zone-id]` element. Read against a
 *      resolved plan — `zoneOwning()` reads the PLAN, so a standalone mount
 *      shows no wrapper whatever the manifest says (the S5 asymmetry
 *      `renderWithPlan` in the S8 describe records).
 *   2. SUPPRESSION. `declared.has(<surface>)` retires the legacy twins, which
 *      reads the MANIFEST and so needs no plan.
 *
 * THE CONTROL IS A REGISTERED MANIFEST, NOT AN ARGUMENT. `PRE_BATCH_2` below
 * is `sdr-test`'s zone list from before this batch. Suppression derives from
 * `getLayout(skinId)` — the REGISTRY — so passing a different manifest object
 * to a render helper could not have produced the "before" state; only a
 * separately registered id can. It differs from `sdr-test` in exactly the
 * dimension under test and nothing else.
 */
describe("the SDR face's five control families are zone-owned (MOR-2231, batch 2)", () => {
  const LEFT_ALL = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band',
    'antenna', 'scan', 'rx-audio', 'dsp', 'tx', 'cw', 'memory'];
  const RIGHT_ALL = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

  /** Same shape the S8 describe uses, for the same reason: without a real HAM
   *  grid the band split pin would only ever be about the tab strip. */
  const HAM_RANGES = [{
    start: 1_800_000, end: 30_000_000, label: 'HF',
    bands: [
      { name: '40m', start: 7_000_000, end: 7_300_000, default: 7_100_000 },
      { name: '20m', start: 14_000_000, end: 14_350_000, default: 14_225_000, bsrCode: 5 },
    ],
  }];

  /** `sdr-test`'s zones as they stood BEFORE this batch — the "before" half of
   *  every row below, registered so it is a real mount. */
  const PRE_BATCH_2 = 'sdr-pre-batch-2-probe' as SkinId;
  const PRE_BATCH_2_MANIFEST = probeManifest(PRE_BATCH_2, [
    { id: 'receiver-deck', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
    { id: 'meters', surfaces: ['meters'] },
  ], ['vfo', 'rxTx']);
  registerLayout(PRE_BATCH_2_MANIFEST);

  /** zone id → the surface testid it must own. */
  const FIVE = [
    ['filter', 'filter-surface'],
    ['rf-front-end', 'rf-front-end-surface'],
    ['band', 'band-surface'],
    ['antenna', 'antenna-surface'],
    ['rit-xit-scan', 'ritxit-scan-surface'],
  ] as const;

  /**
   * The eight legacy hosts these five declarations UNMOUNT on this face. BAND
   * is deliberately absent: it is retired by PROP, never by mount (S10 §4a),
   * and has its own row below.
   */
  const RETIRED = [
    ['left sidebar MODE', '.left-sidebar [data-panel-id="mode"]'],
    ['left sidebar FILTER', '.left-sidebar [data-panel-id="filter"]'],
    ['left sidebar RF FRONT END', '.left-sidebar [data-panel-id="rf-front-end"]'],
    ['left sidebar RIT / XIT', '.left-sidebar [data-panel-id="rit-xit"]'],
    ['left sidebar SCAN', '.left-sidebar [data-panel-id="scan"]'],
    ['left sidebar ANTENNA', '.left-sidebar [data-panel-id="antenna"]'],
    ['settings modal RF FRONT END', '[data-panel-id="desktop-rf"]'],
    ['settings modal RIT / XIT', '[data-panel-id="desktop-rit"]'],
  ] as const;

  /** Opens the settings modal — two of the eight retired hosts live there. */
  function renderAll(skinId: SkinId): HTMLElement {
    const target = render(skinId);
    (target.querySelector('.settings-btn') as HTMLElement | null)?.click();
    flushSync();
    return target;
  }

  /** The zone-ELEMENT half needs the resolved plan in context — see the S8
   *  describe's `renderWithPlan` for why `render()` alone cannot show one.
   *  Takes the manifest, so the control resolves ITS OWN plan. */
  function renderWithPlan(skinId: SkinId, manifest: LayoutManifest): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const plan = resolveSurfacePlan(manifest, DEFAULT_WORKSPACE);
    mounted.push(mount(RadioLayout, {
      target, props: { skinId },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    }));
    flushSync();
    return target;
  }

  const texts = (root: Element | null, selector: string) =>
    [...(root?.querySelectorAll(selector) ?? [])].map((el) => el.textContent?.trim());

  beforeEach(() => {
    // A radio that fires all five evidence gates. Each field is here because a
    // named gate in `radio-view-model-adapter.ts` reads it, and a fixture that
    // missed one would make this whole describe pass vacuously:
    //   `deriveModeFilter`   — non-empty `caps.modes` OR `caps.filters`;
    //   `deriveRfFrontEnd`   — any of preamp/attenuator/rf_gain/squelch/
    //                          digisel/ip_plus;
    //   `deriveBand`         — non-empty `caps.freqRanges`;
    //   `deriveAntenna`      — `caps.antennas > 1`;
    //   `deriveRitXit`       — a `rit`/`xit` capability tag;
    //   `deriveScan`         — scanning/scanType/scanResumeMode in state.
    // The first two are this batch's additions to the S8 fixture; without them
    // `filter-surface` and `rf-front-end-surface` never render at all, which is
    // how the first draft of this describe failed.
    h.caps = {
      ...(capsFor('2/main_sub') as object),
      antennas: 2,
      capabilities: ['scope', 'audio', 'tx', 'dual_rx', 'rit', 'xit',
        'preamp', 'attenuator', 'rf_gain'],
      modes: ['LSB', 'USB', 'CW'],
      filters: ['FIL1', 'FIL2', 'FIL3'],
      freqRanges: HAM_RANGES,
    } as Capabilities;
    h.state = {
      ...(liveState() as object),
      scanning: false, scanType: 0x34, scanResumeMode: 1,
      txAntenna: 1, rxAntenna1: 0, ritOn: false, ritTx: false, ritFreq: 0,
    };
    // The legacy `BandSelector` reads its HAM grid from the capabilities STORE,
    // not from `runtime.caps` — without this the grid is empty and the band
    // pin below would only ever be about the tab strip.
    vi.mocked(getCapabilities).mockReturnValue(
      { freqRanges: HAM_RANGES, modes: ['LSB', 'USB', 'CW'], filters: ['FIL1', 'FIL2', 'FIL3'] } as never,
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

  // NON-VACUITY, half one: under this fixture the "before" face really does
  // render all five surfaces AND all eight legacy hosts. Without this row every
  // suppression pin below could pass because nothing ever rendered.
  it('the pre-batch manifest renders all five surfaces and all eight legacy hosts', () => {
    const t = renderAll(PRE_BATCH_2);
    for (const [, testid] of FIVE) {
      expect(t.querySelector(`[data-testid="${testid}"]`), testid).not.toBeNull();
    }
    for (const [host, selector] of RETIRED) {
      expect(t.querySelector(selector), host).not.toBeNull();
    }
  });

  // NON-VACUITY, half two: on the "before" face those five surfaces mount BARE.
  // This is the state the declaration replaces, and the row that makes the zone
  // assertions below a change rather than a restatement.
  it.each(FIVE)('%s: the pre-batch manifest mounts its surface bare, in no zone', (zoneId, testid) => {
    const t = renderWithPlan(PRE_BATCH_2, PRE_BATCH_2_MANIFEST);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, testid).not.toBeNull();
    expect(el!.closest('.surface-zone')).toBeNull();
    expect(t.querySelector(`[data-zone-id="${zoneId}"]`)).toBeNull();
  });

  // EFFECT 1 — each declared zone binds a real element that OWNS its surface,
  // and there is no second bare mount beside it.
  it.each(FIVE)('%s: sdr-test hosts its surface inside the declared zone element', (zoneId, testid) => {
    const t = renderWithPlan('sdr-test', sdrTestLayout);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, `${testid} on screen`).not.toBeNull();
    // Containment read from the SURFACE upward, not "some element with this id
    // exists somewhere": the surface's own wrapper must be the declared zone.
    const zone = el!.closest('.surface-zone');
    expect(zone, `${testid} inside a zone element`).not.toBeNull();
    expect(zone!.getAttribute('data-zone-id')).toBe(zoneId);
    expect(t.querySelectorAll(`[data-testid="${testid}"]`).length).toBe(1);
  });

  // EFFECT 2 — the batch's principal effect. Each of the eight legacy hosts is
  // gone on `sdr-test`, under the identical fixture that renders all eight on
  // the pre-batch face.
  it.each(RETIRED)('[%s] is unmounted on sdr-test', (host, selector) => {
    expect(renderAll('sdr-test').querySelector(selector), host).toBeNull();
  });

  // THE BAND ASYMMETRY (S10 §4a) — the one family that is NOT unmounted, which
  // is why it is not a `RETIRED` row. Both `BandSelector` mounts survive and
  // keep the broadcast presets they alone host; only the HAM half goes.
  it('drops the HAM half of both BandSelector mounts and keeps the BAND panel', () => {
    const t = renderAll('sdr-test');
    for (const [host, root] of [
      ['left sidebar', t.querySelector('.left-sidebar [data-panel-id="band"]')],
      ['settings modal', t.querySelector('[data-panel-id="desktop-vfo-ops"]')],
    ] as const) {
      expect(root, `${host} still hosts BandSelector`).not.toBeNull();
      expect(texts(root, '.band-tab'), `${host} tabs`).toEqual(['LW/MW', 'SWL']);
    }
    // Zero HAM tabs anywhere, and the semantic replacement is on screen.
    expect([...t.querySelectorAll('.band-tab')].filter((b) => b.textContent?.trim() === 'HAM').length)
      .toBe(0);
    expect(t.querySelectorAll('[data-testid="band-choices"]').length).toBe(1);
  });
});

/**
 * MOR-2231 (step 1, batch 3) — the SDR face's RIGHT-COLUMN families, at the
 * RENDER level. Same two effects and the same shape as the batch-2 describe
 * above; only the families and the odd-one-out differ.
 *
 * `sdrTestLayout` declares `rx-audio`, `dsp`, `cw-keyer` and `tx-aux`.
 *
 *   1. ZONE HOSTS. All four already mounted on this face BARE, through the
 *      single composition's `zoned()` calls (each takes the default
 *      `allowBare`), so the declaration moves each inside a `[data-zone-id]`
 *      element. Read against a resolved plan — `zoneOwning()` reads the PLAN.
 *   2. SUPPRESSION, for THREE of the four. `declared.has(<surface>)` retires
 *      ten legacy hosts, which reads the MANIFEST and so needs no plan.
 *
 * `txAux` IS THE ODD ONE OUT, and more sharply than `band` was in batch 2:
 * `band` at least flips a prop, while no `declared.has('txAux')` predicate
 * exists on any host, so declaring `tx-aux` retires nothing whatever. That is
 * a negative, so it gets a row that can actually fail: the last case reads the
 * whole panel inventory before and after and asserts the delta is EXACTLY the
 * ten hosts below — an eleventh retirement, from a `txAux` guard added later,
 * reddens it. The inventory covers the two sidebars and the settings modal,
 * which with `StatusBar` (it reads only `scopeDisplay`) and `SpectrumPanel`
 * (only `scopeControls`) are every consumer `RadioLayout` hands `declared` to.
 *
 * THE CONTROL IS A REGISTERED MANIFEST, for the reason the batch-2 describe
 * states: suppression derives from `getLayout(skinId)`, so only a separately
 * registered id can produce the "before" state.
 */
describe("the SDR face's right-column families are zone-owned (MOR-2231, batch 3)", () => {
  const LEFT_ALL = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band',
    'antenna', 'scan', 'rx-audio', 'dsp', 'tx', 'cw', 'memory'];
  const RIGHT_ALL = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

  /** `sdr-test`'s zones as they stood BEFORE this batch — the "before" half of
   *  every row below, registered so it is a real mount. */
  const PRE_BATCH_3 = 'sdr-pre-batch-3-probe' as SkinId;
  const PRE_BATCH_3_MANIFEST = probeManifest(PRE_BATCH_3, [
    { id: 'receiver-deck', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
    { id: 'meters', surfaces: ['meters'] },
    { id: 'filter', surfaces: ['filter'] },
    { id: 'rf-front-end', surfaces: ['rfFrontEnd'] },
    { id: 'band', surfaces: ['band'] },
    { id: 'antenna', surfaces: ['antenna'] },
    { id: 'rit-xit-scan', surfaces: ['ritXitScan'] },
  ], ['vfo', 'rxTx']);
  registerLayout(PRE_BATCH_3_MANIFEST);

  /** zone id → the surface testid it must own. */
  const FOUR = [
    ['rx-audio', 'rx-audio-surface'],
    ['dsp', 'dsp-surface'],
    ['cw-keyer', 'cw-keyer-surface'],
    ['tx-aux', 'tx-aux-surface'],
  ] as const;

  /**
   * The ten legacy hosts these declarations UNMOUNT on this face, as
   * [label, container, panelId] so the selector and the inventory key below are
   * both DERIVED from one list rather than hand-written twice. `tx-aux` is
   * deliberately absent: it retires nothing, and the delta row is its pin.
   *
   * `AgcPanel` and the modal's `desktop-agc` are here under `dsp`, not a zone
   * of their own: `DspSurface` owns the AGC leaf (5A/MOR-1290).
   */
  const RETIRED = [
    ['left sidebar RX AUDIO', '.left-sidebar', 'rx-audio'],
    ['right sidebar RX AUDIO', '.right-sidebar', 'rx-audio'],
    ['left sidebar AGC', '.left-sidebar', 'agc'],
    ['left sidebar DSP', '.left-sidebar', 'dsp'],
    ['right sidebar DSP', '.right-sidebar', 'dsp'],
    ['left sidebar CW', '.left-sidebar', 'cw'],
    ['right sidebar CW', '.right-sidebar', 'cw'],
    ['settings modal DSP', '.settings-modal', 'desktop-dsp'],
    ['settings modal AGC', '.settings-modal', 'desktop-agc'],
    ['settings modal CW', '.settings-modal', 'desktop-cw'],
  ] as const;

  const sel = (scope: string, panelId: string) => `${scope} [data-panel-id="${panelId}"]`;

  /** Every legacy panel on screen, scoped by the container that owns it —
   *  `rx-audio`/`dsp`/`cw` exist in BOTH sidebars, so a bare id set would
   *  collapse the two halves of each pair into one entry. */
  const inventory = (t: HTMLElement): string[] =>
    ['.left-sidebar', '.right-sidebar', '.settings-modal']
      .flatMap((scope) => [...t.querySelectorAll(`${scope} [data-panel-id]`)]
        .map((el) => `${scope} ${el.getAttribute('data-panel-id')}`))
      .sort();

  /** Opens the settings modal — three of the ten retired hosts live there. */
  function renderAll(skinId: SkinId): HTMLElement {
    const target = render(skinId);
    (target.querySelector('.settings-btn') as HTMLElement | null)?.click();
    flushSync();
    return target;
  }

  /** The zone-ELEMENT half needs the resolved plan in context — see the S8
   *  describe's `renderWithPlan`. Takes the manifest, so the control resolves
   *  ITS OWN plan. */
  function renderWithPlan(skinId: SkinId, manifest: LayoutManifest): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const plan = resolveSurfacePlan(manifest, DEFAULT_WORKSPACE);
    mounted.push(mount(RadioLayout, {
      target, props: { skinId },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    }));
    flushSync();
    return target;
  }

  beforeEach(() => {
    // A radio that fires all four evidence gates. Every tag is here because a
    // NAMED gate in `radio-view-model-adapter.ts` reads it; a fixture that
    // missed one would make this whole describe pass vacuously, which is the
    // failure the batch-2 draft nearly shipped:
    //   `deriveRxAudio` — a non-null audio snapshot (the harness's fixed
    //                     `runtime.audio`) AND one of af_level / audio /
    //                     dual_rx / MOD-input routing;
    //   `deriveDsp`     — any of nr / nb / notch / agc;
    //   `deriveCwKeyer` — the `cw` tag, and nothing else (break_in and apf
    //                     only populate leaves once that gate has opened);
    //   `deriveTxAux`   — the `tx` tag `capsFor` already supplies, PLUS
    //                     evidence: any of tuner / vox / compressor / monitor
    //                     / drive_gain, or an observed TX-aux state field.
    h.caps = {
      ...(capsFor('2/main_sub') as object),
      capabilities: [
        'scope', 'audio', 'tx', 'dual_rx',
        'af_level',
        'nr', 'nb', 'notch', 'agc',
        'cw', 'break_in', 'apf',
        'tuner', 'vox', 'compressor', 'monitor', 'drive_gain',
      ],
    } as Capabilities;
    // The legacy CW panels read the capabilities STORE, not `runtime.caps`;
    // without this they never mount and their two rows would assert nothing.
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    localStorage.setItem('rigplane:panel-order', JSON.stringify(LEFT_ALL));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(RIGHT_ALL));
  });

  afterEach(() => {
    localStorage.clear();
  });

  // NON-VACUITY, half one: under this fixture the "before" face really does
  // render all four surfaces AND all ten legacy hosts. Presence is asserted
  // FIRST, so a dead evidence gate fails here rather than passing silently
  // through every suppression row below.
  it('the pre-batch manifest renders all four surfaces and all ten legacy hosts', () => {
    const t = renderAll(PRE_BATCH_3);
    for (const [, testid] of FOUR) {
      expect(t.querySelector(`[data-testid="${testid}"]`), testid).not.toBeNull();
    }
    for (const [host, scope, panelId] of RETIRED) {
      expect(t.querySelector(sel(scope, panelId)), host).not.toBeNull();
    }
  });

  // NON-VACUITY, half two: on the "before" face those four surfaces mount BARE.
  // This is the state the declaration replaces, and the row that makes the zone
  // assertions below a change rather than a restatement.
  it.each(FOUR)('%s: the pre-batch manifest mounts its surface bare, in no zone', (zoneId, testid) => {
    const t = renderWithPlan(PRE_BATCH_3, PRE_BATCH_3_MANIFEST);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, testid).not.toBeNull();
    expect(el!.closest('.surface-zone')).toBeNull();
    expect(t.querySelector(`[data-zone-id="${zoneId}"]`)).toBeNull();
  });

  // EFFECT 1 — each declared zone binds a real element that OWNS its surface,
  // and there is no second bare mount beside it.
  it.each(FOUR)('%s: sdr-test hosts its surface inside the declared zone element', (zoneId, testid) => {
    const t = renderWithPlan('sdr-test', sdrTestLayout);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, `${testid} on screen`).not.toBeNull();
    // Containment read from the SURFACE upward, not "some element with this id
    // exists somewhere": the surface's own wrapper must be the declared zone.
    const zone = el!.closest('.surface-zone');
    expect(zone, `${testid} inside a zone element`).not.toBeNull();
    expect(zone!.getAttribute('data-zone-id')).toBe(zoneId);
    expect(t.querySelectorAll(`[data-testid="${testid}"]`).length).toBe(1);
  });

  // EFFECT 2 — the batch's principal effect. Each of the ten legacy hosts is
  // gone on `sdr-test`, under the identical fixture that renders all ten on the
  // pre-batch face. `CwKeyerSurface` becoming the SOLE break-in affordance is
  // the safety-critical half (MOR-1310), so its presence is pinned separately
  // below rather than left to follow from `CwPanel`'s absence.
  it.each(RETIRED)('[%s] is unmounted on sdr-test', (host, scope, panelId) => {
    expect(renderAll('sdr-test').querySelector(sel(scope, panelId)), host).toBeNull();
  });

  // THE TX-AUX ASYMMETRY — the family that retires NOTHING, given a row that
  // can fail. The panel inventory loses exactly the ten hosts above and gains
  // none, so a `declared.has('txAux')` guard added to either sidebar or to the
  // settings modal would redden this even though every row above stays green.
  it('declaring tx-aux retires nothing: the inventory delta is exactly the ten', () => {
    const before = inventory(renderAll(PRE_BATCH_3));
    const after = inventory(renderAll('sdr-test'));
    expect(before.length).toBeGreaterThan(RETIRED.length);
    expect(before.filter((p) => !after.includes(p)))
      .toEqual(RETIRED.map(([, scope, panelId]) => `${scope} ${panelId}`).sort());
    expect(after.filter((p) => !before.includes(p))).toEqual([]);
    // ...and the surface it DOES place is on screen exactly once, which is the
    // whole of what this declaration buys.
    expect(after.filter((p) => p.endsWith(' tx-aux'))).toEqual([]);
    expect(renderAll('sdr-test').querySelectorAll('[data-testid="tx-aux-surface"]').length).toBe(1);
  });

  // SAFETY-CRITICAL (MOR-1310), stated positively. `CwPanel` is gone from both
  // sidebars and the modal, so `CwKeyerSurface` is now the only break-in
  // affordance on this face — zero would be worse than the double it replaces.
  it('leaves CwKeyerSurface as the sole break-in affordance, and one key authority', () => {
    const t = renderAll('sdr-test');
    expect(t.querySelectorAll('[data-testid="cw-keyer-surface"]').length).toBe(1);
    expect(t.querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
  });
});

/**
 * MOR-2231 (step 1, batch 4) — the SDR face's CENTRE-TOP pair, at the RENDER
 * level, and the last two of the fourteen. Same two effects as the batch-2 and
 * batch-3 describes above, but here BOTH families depart from that shape.
 *
 * `sdrTestLayout` declares `scope-display` and `scope-controls`.
 *
 *   1. ZONE HOSTS. Both already mounted on this face BARE, through the single
 *      composition's `zoned()` calls — both take the default `allowBare` on
 *      THAT path (the dual composition passes `false` for `scopeControls`,
 *      which is the cockpit's path, not this one). Read against a resolved
 *      plan: `zoneOwning()` reads the PLAN.
 *   2. SUPPRESSION for ONE of the two, and a PROP for the other.
 *
 * `scopeDisplay` retires exactly ONE host: `StatusBar`'s scope indicator,
 * `!declared.has('scopeDisplay')`. Its own
 * render gate is `hasAnyScope()`, which this file mocks FALSE by default — so
 * the fixture below turns it on. Without that the suppression row would pass
 * on an absence it did not cause.
 *
 * `scopeControls` unmounts NOTHING. Unlike `txAux` a predicate does exist, but
 * it is a PROP: `RadioLayout` forwards
 * `hideScopeControls={declared.has('scopeControls')}` to `SpectrumPanel`, which
 * keeps rendering. That is batch 2's `band` shape, reached through the CENTRE
 * column's `{#if hasSpectrum()}` rather than a sidebar's `drag.order`. WHICH
 * toolbar controls that prop removes is proved in
 * `SpectrumToolbar.component.test.ts`'s S6b-1 pins, not here: this file mocks
 * `SpectrumPanel` with a stub that only records the prop.
 *
 * THE FIXTURE NAMES THE GATE THAT READS EACH FIELD — and the two gates read
 * DIFFERENT fields of the same capabilities object, which is why one fixture
 * satisfying one of them proves nothing about the other:
 *   `deriveScopeControls` — `hasCap(caps, 'scope')`, the `capabilities` ARRAY;
 *   `deriveScopeDisplay`  — `hasAnyScopeCap(caps)`, the `scope` BOOLEAN (or
 *                           `scopeSource === 'audio_fft'`), AND a non-null
 *                           scope-display snapshot.
 * `capsFor('2/main_sub')` supplies both capability shapes and the harness's
 * fixed `runtime.defaultScopeStatus` supplies the snapshot, so no extra caps
 * fixture is needed here. Presence is still asserted FIRST, because a fixture
 * that satisfied only one gate would leave half this describe vacuous.
 *
 * THE CONTROL IS A REGISTERED MANIFEST, for the reason the batch-2 describe
 * states: suppression derives from `getLayout(skinId)`, so only a separately
 * registered id can produce the "before" state.
 */
describe("the SDR face's centre-top pair is zone-owned (MOR-2231, batch 4)", () => {
  /** `sdr-test`'s zones as they stood BEFORE this batch — the "before" half of
   *  every row below, registered so it is a real mount. */
  const PRE_BATCH_4 = 'sdr-pre-batch-4-probe' as SkinId;
  const PRE_BATCH_4_MANIFEST = probeManifest(PRE_BATCH_4, [
    { id: 'receiver-deck', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
    { id: 'meters', surfaces: ['meters'] },
    { id: 'filter', surfaces: ['filter'] },
    { id: 'rf-front-end', surfaces: ['rfFrontEnd'] },
    { id: 'band', surfaces: ['band'] },
    { id: 'antenna', surfaces: ['antenna'] },
    { id: 'rit-xit-scan', surfaces: ['ritXitScan'] },
    { id: 'rx-audio', surfaces: ['rxAudio'] },
    { id: 'dsp', surfaces: ['dsp'] },
    { id: 'cw-keyer', surfaces: ['cwKeyer'] },
    { id: 'tx-aux', surfaces: ['txAux'] },
  ], ['vfo', 'rxTx']);
  registerLayout(PRE_BATCH_4_MANIFEST);

  /** zone id → the surface testid it must own. */
  const TWO = [
    ['scope-display', 'scope-display-surface'],
    ['scope-controls', 'scope-controls-surface'],
  ] as const;

  /** The `scopeDisplay` twin: the status bar's own scope indicator. */
  const SCOPE_INDICATOR = '.status-indicators [title^="Scope WebSocket"]';
  /** The `scopeControls` twin's host — mocked by `SpectrumPanelStub`. */
  const SPECTRUM = '.content-center .spectrum-panel-stub';

  /**
   * FIVE selectors, and no more: `[data-panel-id]` inside each of the two
   * sidebars and the settings modal (scoped per container, since
   * `rx-audio`/`dsp`/`cw` exist in both sidebars), plus this batch's own two
   * hosts, which carry no `data-panel-id`. It is NOT an inventory of
   * everywhere `declared` reaches: it does not scan `.bottom-dock`
   * (`MetersDockPanel`, retired on `declared.has('meters')`), the legacy VFO
   * header (`declared.has('vfo')`), the status bar's other indicators, or
   * `BandSelector`'s `hamBands={!declared.has('band')}` prop. `BEFORE_HOSTS`
   * below pins what it does find.
   */
  const hosts = (t: HTMLElement): string[] => [
    ...['.left-sidebar', '.right-sidebar', '.settings-modal'].flatMap(
      (scope) => [...t.querySelectorAll(`${scope} [data-panel-id]`)]
        .map((el) => `${scope} ${el.getAttribute('data-panel-id')}`),
    ),
    ...(t.querySelector(SCOPE_INDICATOR) ? ['status-bar scope-indicator'] : []),
    ...(t.querySelector(SPECTRUM) ? ['content-center spectrum-panel'] : []),
  ].sort();

  /** Opens the settings modal — the inventory above reads it. */
  function renderAll(skinId: SkinId): HTMLElement {
    const target = render(skinId);
    (target.querySelector('.settings-btn') as HTMLElement | null)?.click();
    flushSync();
    return target;
  }

  /** The zone-ELEMENT half needs the resolved plan in context. Takes the
   *  manifest, so the control resolves ITS OWN plan. */
  function renderWithPlan(skinId: SkinId, manifest: LayoutManifest): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const plan = resolveSurfacePlan(manifest, DEFAULT_WORKSPACE);
    mounted.push(mount(RadioLayout, {
      target, props: { skinId },
      context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    }));
    flushSync();
    return target;
  }

  beforeEach(() => {
    // The status bar's scope indicator gates on `hasAnyScope()` BEFORE it
    // consults `declared`, and this file mocks that false by default. Without
    // this line the indicator is absent on both faces and its suppression row
    // asserts an absence this batch did not cause.
    vi.mocked(hasAnyScope).mockReturnValue(true);
  });

  afterEach(() => {
    vi.mocked(hasAnyScope).mockReturnValue(false);
  });

  // NON-VACUITY, half one: under this fixture the "before" face really renders
  // both surfaces AND both legacy hosts. Presence FIRST, so a dead view-model
  // gate fails here rather than passing silently through the rows below.
  it('the pre-batch manifest renders both surfaces and both legacy hosts', () => {
    const t = renderAll(PRE_BATCH_4);
    for (const [, testid] of TWO) {
      expect(t.querySelector(`[data-testid="${testid}"]`), testid).not.toBeNull();
    }
    expect(t.querySelector(SCOPE_INDICATOR), 'status bar scope indicator').not.toBeNull();
    expect(t.querySelector(SPECTRUM), 'spectrum panel').not.toBeNull();
    expect(t.querySelector(SPECTRUM)!.getAttribute('data-hide-scope-controls')).toBe('false');
  });

  // NON-VACUITY, half two: on the "before" face both surfaces mount BARE. This
  // is the state the declaration replaces, and what makes the zone assertions
  // below a change rather than a restatement.
  it.each(TWO)('%s: the pre-batch manifest mounts its surface bare, in no zone', (zoneId, testid) => {
    const t = renderWithPlan(PRE_BATCH_4, PRE_BATCH_4_MANIFEST);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, testid).not.toBeNull();
    expect(el!.closest('.surface-zone')).toBeNull();
    expect(t.querySelector(`[data-zone-id="${zoneId}"]`)).toBeNull();
  });

  // EFFECT 1 — each declared zone binds a real element that OWNS its surface,
  // and there is no second bare mount beside it.
  it.each(TWO)('%s: sdr-test hosts its surface inside the declared zone element', (zoneId, testid) => {
    const t = renderWithPlan('sdr-test', sdrTestLayout);
    const el = t.querySelector(`[data-testid="${testid}"]`);
    expect(el, `${testid} on screen`).not.toBeNull();
    // Containment read from the SURFACE upward, not "some element with this id
    // exists somewhere": the surface's own wrapper must be the declared zone.
    const zone = el!.closest('.surface-zone');
    expect(zone, `${testid} inside a zone element`).not.toBeNull();
    expect(zone!.getAttribute('data-zone-id')).toBe(zoneId);
    expect(t.querySelectorAll(`[data-testid="${testid}"]`).length).toBe(1);
  });

  // EFFECT 2a — the batch's ONE unmount, under the identical fixture that
  // renders the indicator on the pre-batch face.
  it('[status bar scope indicator] is unmounted on sdr-test', () => {
    expect(renderAll('sdr-test').querySelector(SCOPE_INDICATOR)).toBeNull();
  });

  // EFFECT 2b — the other mechanism. `scope-controls` unmounts nothing: its
  // host keeps rendering and only the prop flips. A mutation turning that prop
  // into a mount gate reddens the first assertion; one dropping the forward
  // reddens the second.
  it('keeps the spectrum panel mounted on sdr-test and flips hideScopeControls', () => {
    const t = renderAll('sdr-test');
    expect(t.querySelectorAll(SPECTRUM).length).toBe(1);
    expect(t.querySelector(SPECTRUM)!.getAttribute('data-hide-scope-controls')).toBe('true');
  });

  // THE ASYMMETRY, given a row that can fail — and given an explicit statement
  // of how far it can see, because the reach is much smaller than "everywhere
  // `declared` goes". Under this fixture `hosts()` finds exactly the seven
  // entries below: batches 2 and 3 already retired most sidebar panels, and
  // the rest of each sidebar's DEFAULT order never mounts here. So the first
  // assertion pins the reachable set itself. A `declared.has('scopeControls')`
  // mount gate placed on one of these seven reddens the delta; one placed
  // anywhere else — the status bar's other indicators, `.bottom-dock`, the
  // legacy VFO header, or a sidebar panel this fixture never renders — is
  // INVISIBLE to this row. Both directions are measured, not argued.
  const BEFORE_HOSTS = [
    '.left-sidebar band',
    '.right-sidebar memory',
    '.settings-modal desktop-language',
    '.settings-modal desktop-vfo-ops',
    '.settings-modal desktop-workspace',
    'content-center spectrum-panel',
    'status-bar scope-indicator',
  ];

  it('the host delta is exactly the status bar scope indicator', () => {
    const before = hosts(renderAll(PRE_BATCH_4));
    const after = hosts(renderAll('sdr-test'));
    expect(before).toEqual(BEFORE_HOSTS);
    expect(before.filter((p) => !after.includes(p))).toEqual(['status-bar scope-indicator']);
    expect(after.filter((p) => !before.includes(p))).toEqual([]);
  });

  // R9: the last two zones in the vocabulary add no key/unkey authority.
  it('adds no key authority with the centre-top pair declared', () => {
    expect(renderAll('sdr-test').querySelectorAll(KEY_AUTHORITIES).length).toBe(1);
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
 * Nothing rendered differently WHEN THIS LANDED — no manifest declared any of
 * these zones yet — which was the whole point: the risky plumbing lands once,
 * independently pinned, and each zone slice after it is a two-file manifest
 * edit. Those slices have since landed and `desktop-v2` declares every one of
 * these zones (S6a/S7/S8/S9), so the channel is LIVE on the flagship
 * skin rather than inert. The ONE
 * exception is the settings modal's SPLIT/A↔B/A=B row, which gates on the
 * already-true `semanticDeck` — a real, deliberate change, pinned by its own
 * named test below rather than folded into the inertness claim.
 *
 * The probe apparatus this paragraph used to describe — `desktop-v2`'s REAL
 * manifest plus exactly ONE synthetic zone — is GONE. Every surface graduated
 * to a real declaration, so `ZONES` below is an empty literal and THIS
 * describe registers no zone probe of its own (that literal's own docstring
 * records why it was left empty rather than deleted). Each graduate's coverage
 * moved to a describe asserting the REAL registration, which is the stronger
 * statement.
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
    h.caps = { ...capsFor('2/main_sub'), modes: ['USB', 'CW', 'FM'], filters: ['FIL1', 'FIL2', 'FIL3'] };
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
      modes: ['USB', 'CW', 'FM'], filters: ['FIL1', 'FIL2', 'FIL3'],
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
  //
  // MOR-2231 (step 1, batch 2) MOVED THIS PROBE off `sdr-test`. That face was
  // never the "declares nothing" layout the title names — it declared `vfo`,
  // `rxTx` and `meters`, none of which gates any of the ten — and it stopped
  // being usable here the moment it declared `filter`, `rfFrontEnd`, `band`,
  // `antenna` and `ritXitScan`, which retire six of the ten on that face. The
  // unregistered id is the honest control and the one the title already
  // described: `declaredSurfaces` resolves it to the empty set, so every
  // legacy twin survives. It is the same `'no-such-layout'` probe the
  // "an undeclared layout keeps its legacy presentation" describe above uses.
  it('the identical stored order renders all ten ids on a layout that declares nothing', () => {
    localStorage.setItem('rigplane:panel-order', JSON.stringify(
      ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna', 'scan']));
    localStorage.setItem('rigplane:right-panel-order', JSON.stringify(
      ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory']));
    h.caps = { ...(capsFor('2/main_sub') as object), antennas: 2 } as Capabilities;
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    const t = render('no-such-layout' as SkinId);
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
