/**
 * MOR-1092 — the LCD/scope presentation entrypoints migrated to the semantic
 * VFO / RX-TX surfaces, mirroring the MOR-1065 desktop slice.
 *
 * SAFETY-ADJACENT. The LCD entrypoints carry TX indication and the audio-FFT
 * stream, so four things must hold at once:
 *   1. the semantic surfaces own VFO/TX in the LCD chrome, and nothing in the
 *      layout ships a second PTT affordance or a second split/dual-watch
 *      truth (the duplicate ownership MOR-1099 exists to retire);
 *   2. the retained legacy amber glass still UPDATES from live state — the
 *      MOR-557 lesson (a panel left wired to a path nobody feeds is a dead
 *      panel that looks alive);
 *   3. the App-global Toast / power overlay / TX lamp stay singular and
 *      global (MOR-1059) — the LCD used to host duplicates;
 *   4. the LCD's audio-FFT demand keeps its handle identity: acquired exactly
 *      once per lifecycle, released exactly once, and never disturbed by the
 *      semantic subtree (MOR-1086 doctrine, ResourceDemand handle identity).
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};

const h = vi.hoisted(() => {
  const box = {
    state: null as unknown,
    caps: null as unknown,
    audioFft: false,
    /** [resource, consumer] pairs, in order, against the fake App session. */
    acquired: [] as [string, string][],
    released: [] as unknown[],
    leases: [] as unknown[],
    snapshot: null as unknown,
    listeners: new Set<(next: unknown) => void>(),
    start: vi.fn(),
    release: vi.fn(),
    resetFault: vi.fn(),
  };
  return {
    ...box,
    /**
     * A stand-in App resource session with the one property under test:
     * every acquisition returns its OWN lease object, so a release can be
     * matched back to the exact binding it cancels (the identity trap).
     */
    presentationResources: {
      acquire(resource: string, consumer: string) {
        h.acquired.push([resource, consumer]);
        const lease = { resource, consumer };
        h.leases.push(lease);
        return lease;
      },
      release(lease: unknown) { h.released.push(lease); },
      configure: vi.fn(),
      snapshot: vi.fn(() => ({ demand: 0, health: 'inactive' })),
    },
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: vi.fn(() => () => {}),
      // MOR-1312 slice 12B: `SemanticRadioSurfaces`'s scope-display snapshot
      // reads `runtime.scope.hardwareScopeConnected` directly.
      hardwareScopeConnected: false,
    },
    /**
     * The runtime facade is REACTIVE, not a plain getter box: the MOR-557
     * pin below turns on state actually propagating into a mounted panel, and
     * a non-reactive stub would pass that test for the wrong reason (nothing
     * ever re-renders, so nothing can go stale). Same `createSubscriber`
     * idiom the MOR-1086 App-level suite uses for capabilities. The promise
     * is memoised so all three mocked specifiers share ONE runtime object.
     */
    runtimePromise: null as Promise<unknown> | null,
    notify: () => {},
    runtime(): Promise<unknown> {
      h.runtimePromise ??= (async () => {
        const { createSubscriber } = await import('svelte/reactivity');
        let update = () => {};
        const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
        h.notify = () => update();
        return {
          get state() { subscribe(); return h.state; },
          get caps() { subscribe(); return h.caps; },
          get scope() { return h.scope; },
          connectionStatus: 'disconnected',
          radioPowerOn: null,
          connection: { status: 'disconnected', radioPowerOn: null },
          audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
          connectionAudio: false,
          // MOR-1312 slice 12B: the scope-display snapshot (the FIFTH
          // adapter argument) — no fixture here declares a scope capability.
          defaultScopeStatus: {
            source: null, available: false, resourceSelected: false, demand: 0,
            lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
          },
          bootstrap: async () => () => {},
        };
      })();
      return h.runtimePromise;
    },
  };
});

vi.mock('../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => true),
  getLayoutMode: vi.fn(() => 'lcd-cockpit'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/lcd-display-mode.svelte', () => ({
  getLcdDisplayMode: vi.fn(() => 'clean'),
  setLcdDisplayMode: vi.fn(),
  LCD_DISPLAY_MODES: ['clean', 'vintage', 'crt', 'flicker'],
}));
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

vi.mock('../../../lib/runtime/frontend-runtime', async () => ({
  runtime: await h.runtime(),
  presentationResources: h.presentationResources,
}));
vi.mock('$lib/runtime/frontend-runtime', async () => ({
  runtime: await h.runtime(),
  presentationResources: h.presentationResources,
}));
vi.mock('$lib/runtime', async () => ({ runtime: await h.runtime() }));

// The App TX controller is provided by App.svelte in production; LcdLayout is
// mounted here without it, so the authority is a recording spy (same idiom as
// the MOR-1065 desktop suite, extended to observe owner/guard identity).
vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => h.snapshot,
      subscribe: (listener: (next: unknown) => void) => {
        h.listeners.add(listener);
        return () => { h.listeners.delete(listener); };
      },
      start: h.start,
      setIntent: vi.fn(),
      release: h.release,
      resetFault: h.resetFault,
    }),
  };
});

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
  hasDualReceiver: vi.fn(() => true),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
  isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => h.audioFft),
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

import type { Component } from 'svelte';
import LcdLayout from '../LcdLayout.svelte';
import SemanticRadioSurfaces from '../../wiring/SemanticRadioSurfaces.svelte';
import { hasCapability } from '$lib/stores/capabilities.svelte';
import { topologyFixtures, type TopologyFixtureId } from '../../../semantic/fixtures/topologies';
import { lcdCockpitLayout, lcdScopeLayout } from '../../../presentation/layouts/lcd-declarations';

type Variant = 'cockpit' | 'scope';
const VARIANTS: readonly Variant[] = ['cockpit', 'scope'];

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });
const receiver = (hz: number) => ({
  ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
});

function liveState(mainHz = 14250000): unknown {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: mainHz },
    main: receiver(mainHz), sub: receiver(mainHz + 50000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  };
}

/**
 * `VfoControlPanel` reads its per-button gates off `caps.capabilities`
 * (`toVfoControlProps`), not off the `hasCapability` store helper — so the
 * soft-button assertions below drive the real production gate.
 */
function capsFor(id: TopologyFixtureId, extra: readonly string[] = []): Capabilities {
  const scheme = topologyFixtures[id].vfoScheme;
  const dual = scheme === 'ab_shared' || scheme === 'main_sub';
  return {
    model: 'fixture', scope: false, audio: true, tx: true,
    capabilities: [...(dual ? ['audio', 'tx', 'dual_rx'] : ['audio', 'tx']), ...extra],
    receivers: dual ? 2 : 1, vfoScheme: scheme, freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: null, audioFftAvailable: false,
  } as unknown as Capabilities;
}

let mounted: ReturnType<typeof mount>[] = [];
let target: HTMLElement;

function render(variant: Variant = 'cockpit'): HTMLElement {
  target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(LcdLayout, { target, props: { variant } }));
  flushSync();
  return target;
}

/** Push a new authority snapshot exactly as the real controller would. */
function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}

beforeEach(() => {
  mounted = [];
  h.state = liveState();
  h.caps = capsFor('2/main_sub');
  h.audioFft = false;
  h.acquired = [];
  h.released = [];
  h.leases = [];
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.resetFault.mockReset();
  h.scope.registerPresentationDriver.mockClear();
  h.scope.subscribe.mockClear();
  vi.mocked(hasCapability).mockReturnValue(false);
  // The amber glass draws into a canvas behind a ResizeObserver; jsdom has
  // neither. Both are inert here — this suite measures ownership, not pixels.
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  mounted.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('the layout manifest loader reaches the migrated entrypoint (MOR-1066 bridge)', () => {
  // MUTATION KILLED: a placeholder loader, or a manifest pointing at the
  // wrong LCD variant. Registration alone proves nothing about what is on
  // screen — this mounts what the manifest actually resolves to and checks
  // it is the migrated LCD, in the right variant.
  it.each([
    ['cockpit', lcdCockpitLayout],
    ['scope', lcdScopeLayout],
  ] as const)('"%s" resolves to the migrated LCD entrypoint', async (variant, manifest) => {
    const { default: Entrypoint } = await manifest.loader();
    target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(Entrypoint as Component, { target }));
    flushSync();

    expect(target.querySelector(`.lcd-frame[data-lcd-variant="${variant}"]`)).not.toBeNull();
    expect(target.querySelector('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(target.querySelector('[data-panel-id="tx"]')).toBeNull();
  });
});

describe('the migrated LCD entrypoints own VFO/TX through the semantic surfaces', () => {
  it.each(VARIANTS)('mounts both surfaces in the %s variant\'s control column', (variant) => {
    const t = render(variant);
    const column = t.querySelector('.content-right')!;
    expect(column.querySelector('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(column.querySelector('[data-testid="vfo-surface"]')).not.toBeNull();
    expect(column.querySelector('[data-testid="rx-tx-surface"]')).not.toBeNull();
  });

  // MUTATION KILLED: adding the surfaces alongside the legacy TX panel. The
  // LCD would then ship two PTT affordances — the exact duplicate ownership
  // MOR-1099 exists to retire, on the layout that also renders TX indication.
  it.each(VARIANTS)('renders no legacy TX panel in the %s variant', (variant) => {
    const t = render(variant);
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
    expect(t.querySelector('.tx-panel')).toBeNull();
  });

  // MUTATION KILLED: leaving VfoControlPanel's SPLIT/DW buttons in place. The
  // semantic VFO surface now renders those same two facts as tri-state
  // switches, so the layout would present one fact through two affordances
  // that can disagree.
  it('drops the VFO facts the semantic surface now owns, keeping the rest of the soft-button panel', () => {
    // Every gate the panel can open — so an unsuppressed panel would render
    // DW and SPLIT here, and their absence is a decision, not a missing cap.
    h.caps = capsFor('2/main_sub', ['split', 'rit', 'tuner']);
    const t = render();
    const panel = t.querySelector('.vfo-ctrl-panel')!;
    const labels = [...panel.querySelectorAll('button')].map((b) => b.textContent?.trim());
    // Retained: no semantic equivalent exists for these yet (MOR-1084 parity).
    expect(labels).toEqual(['A↔B', 'A=B', 'XIT', 'CLR', 'TUNE']);
    // ...and the surface really is the one presenting the two dropped facts.
    expect(t.querySelector('[data-vfo-split]')).not.toBeNull();
    expect(t.querySelector('[data-vfo-dual-watch]')).not.toBeNull();
  });

  // MUTATION KILLED: widening the TX suppression to the sidebar's whole
  // `showTx` branch, which also guards CW.
  it('keeps the CW panel, which shares the sidebar\'s TX branch', () => {
    vi.mocked(hasCapability).mockImplementation((tag: string) => tag === 'cw');
    const t = render();
    expect(t.querySelector('[data-panel-id="cw"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).toBeNull();
  });

  it.each(VARIANTS)('leaves the %s variant\'s LCD chrome intact', (variant) => {
    const t = render(variant);
    expect(t.querySelector('.lcd-layout')).not.toBeNull();
    expect(t.querySelector(`.lcd-frame[data-lcd-variant="${variant}"]`)).not.toBeNull();
    expect(t.querySelector('.lcd-control-strip')).not.toBeNull();
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.vfo-ctrl-panel')).not.toBeNull();
    // Non-TX sidebar panels are untouched by the TX suppression.
    expect(t.querySelector('[data-panel-id="rx-audio"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="memory"]')).not.toBeNull();
  });

  it.each(['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const)(
    'renders safely on the %s topology the manifest declares', (id) => {
      h.caps = capsFor(id);
      const t = render();
      expect(t.querySelectorAll('[data-vfo-tile]')).toHaveLength(topologyFixtures[id].vfos.length);
      expect(t.querySelector('[data-testid="rx-tx-surface"]')).not.toBeNull();
    },
  );

  it('renders the LCD chrome but no surfaces when capabilities have not loaded', () => {
    h.caps = null;
    const t = render();
    expect(t.querySelector('.lcd-frame')).not.toBeNull();
    expect(t.querySelector('[data-testid="vfo-surface"]')).toBeNull();
    expect(t.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
  });
});

describe('no dead-panel regression in the retained legacy glass (MOR-557 class)', () => {
  // MUTATION KILLED: freezing the amber glass behind a snapshot taken at
  // mount, or leaving it wired to a legacy mirror nobody feeds any more. A
  // panel that renders once and never updates looks alive and is not.
  it.each(VARIANTS)('the %s glass still re-renders from live runtime state', (variant) => {
    const t = render(variant);
    const digits = () => {
      const active = t.querySelector('.lcd-freq .freq-active')!;
      return [...active.querySelectorAll('.seg-mhz, .seg-khz, .seg-hz')]
        .map((s) => s.textContent).join('.');
    };
    expect(digits()).toBe('14.250.000');

    h.state = liveState(21300000);
    h.notify();
    flushSync();
    expect(digits()).toBe('21.300.000');
  });

  // The retained soft-button panel reads through its adapter, not a mirror:
  // a capability change must reach it without a remount.
  it('the retained VFO soft-button panel still tracks capability changes', () => {
    const t = render();
    const labels = () =>
      [...t.querySelectorAll('.vfo-ctrl-panel button')].map((b) => b.textContent?.trim());
    expect(labels()).not.toContain('TUNE');

    h.caps = capsFor('2/main_sub', ['tuner']);
    h.notify();
    flushSync();
    expect(labels()).toContain('TUNE');
  });
});

describe('App-global singletons stay global (MOR-1059)', () => {
  // MUTATION KILLED: reintroducing a layout-local Toast, power overlay or TX
  // lamp while migrating. AppGlobalHost is a sibling of the presentation, so
  // a local copy would duplicate on screen and be recreated on every swap.
  it.each(VARIANTS)('the %s variant hosts none of the App-global surfaces', (variant) => {
    const t = render(variant);
    for (const testid of ['app-global-host', 'global-tx-indication', 'global-tx-fault', 'global-power-off']) {
      expect(t.querySelector(`[data-testid="${testid}"]`)).toBeNull();
    }
    expect(t.querySelector('.toast-container')).toBeNull();
    expect(t.querySelector('.power-off-overlay')).toBeNull();
  });
});

describe('TX authority: lease-safe teardown in the LCD context (MOR-1065 F1)', () => {
  function keyFromLcd(): { owner: string; guard: { leaseId: string } } {
    (target.querySelector('[data-testid="rx-tx-key"]') as HTMLButtonElement).click();
    flushSync();
    const [owner, leaseId] = h.start.mock.calls[0] as [string, string];
    const guard = { leaseId };
    push({ phase: 'active', intent: 'latched', guard, radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
    return { owner, guard };
  }

  it('keys as a latched lease under one owner identity', () => {
    render();
    const { owner } = keyFromLcd();
    expect(h.start).toHaveBeenCalledTimes(1);
    expect(h.start.mock.calls[0][2]).toBe('latched');
    expect(owner).toMatch(/^semantic-rx-tx-\d+$/);
  });

  // MUTATION KILLED: dropping the lease release from the LCD teardown path.
  // MOR-1060 destroys this subtree on any presentation change; the lease is
  // LATCHED and outlives the component, the model refuses a release from any
  // other sourceId and AppGlobalHost exposes no unkey — so swapping away
  // while keyed would strand the transmitter with no UI exit.
  it('releases the live lease exactly once when the LCD subtree is destroyed', () => {
    render();
    const { owner, guard } = keyFromLcd();

    unmount(mounted.pop()!);

    expect(h.release).toHaveBeenCalledTimes(1);
    expect(h.release).toHaveBeenCalledWith(owner, guard);
  });

  // MUTATION KILLED: releasing the render-time guard instead of the live one.
  // A lease regenerated after the last render would be released under a stale
  // guard, which the model rejects — indistinguishable from never releasing.
  it('releases the guard the authority holds at teardown, not the last rendered one', () => {
    render();
    const { owner } = keyFromLcd();
    h.snapshot = { ...(h.snapshot as Snapshot), guard: { leaseId: 'gen-2' } };

    unmount(mounted.pop()!);

    expect(h.release).toHaveBeenCalledWith(owner, { leaseId: 'gen-2' });
  });

  // The operator-visible half: after the swap the incoming LCD is usable.
  it('lets the LCD key again after a swap, under a fresh owner identity', () => {
    render();
    const { owner: first } = keyFromLcd();
    unmount(mounted.pop()!);
    expect(h.release).toHaveBeenCalledTimes(1);

    h.snapshot = { ...IDLE };
    render();
    (target.querySelector('[data-testid="rx-tx-key"]') as HTMLButtonElement).click();
    flushSync();

    expect(h.start).toHaveBeenCalledTimes(2);
    expect(h.start.mock.calls[1][0]).not.toBe(first);
  });
});

describe('scope continuity in the LCD (MOR-1086 doctrine, handle identity)', () => {
  // The LCD's audio-FFT viewers are the amber glass and the sidebar's audio
  // scope — exactly the two `skins/registry.ts` documents for this family.
  //
  // MUTATION KILLED: the migration adding a THIRD viewer, or demanding
  // `hardware-scope` (which no LCD variant renders, and which the MOR-1086
  // sweep relies on the LCD not holding).
  it.each(VARIANTS)('the %s variant takes only the two documented audio-FFT leases', (variant) => {
    h.audioFft = true;
    render(variant);
    expect(h.acquired).toEqual([
      ['audio-fft', variant === 'scope' ? 'AmberScope' : 'AmberCockpit'],
      ['audio-fft', 'AudioSpectrumPanel'],
    ]);
    expect(h.acquired.map(([resource]) => resource)).not.toContain('hardware-scope');
    expect(h.scope.registerPresentationDriver).toHaveBeenCalledWith(h.presentationResources);
  });

  // MUTATION KILLED: releasing a re-derived or shared lease object rather
  // than the binding that was acquired — a reused handle leaks one binding
  // and leaves the stream demanded by a subtree that is gone. Set equality
  // (not order) is the honest claim: each acquisition is released once.
  it('releases exactly the lease objects it acquired, each once, on teardown', () => {
    h.audioFft = true;
    render();
    expect(h.leases).toHaveLength(2);
    expect(new Set(h.leases).size).toBe(2);   // distinct handles, not one reused

    unmount(mounted.pop()!);

    expect(h.released).toHaveLength(2);
    expect(new Set(h.released)).toEqual(new Set(h.leases));
  });

  // MUTATION KILLED: giving the semantic surfaces their own resource demand.
  // They are destroyed and recreated on every presentation swap and on every
  // TX-authority churn, so any demand they held would bounce the LCD's live
  // FFT stream. The demand belongs to the glass, which the swap keeps.
  it('the semantic surfaces hold no App resource of their own', () => {
    h.audioFft = true;
    const surfaceTarget = document.createElement('div');
    document.body.appendChild(surfaceTarget);
    const surfaces = mount(SemanticRadioSurfaces, { target: surfaceTarget });
    flushSync();
    expect(h.acquired).toEqual([]);

    unmount(surfaces);
    expect(h.released).toEqual([]);
  });
});
