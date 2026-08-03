/**
 * MOR-1065 slice b — one current desktop layout migrated to the semantic VFO
 * surface.
 *
 * The migrated layout is `sdr-test`: its VFO presentation is now owned by the
 * MOR-1063 surface. Two things must hold at once:
 *   1. the semantic VFO surface renders IN PLACE of the legacy twin-VFO block
 *      — a layout carrying both would ship two VFO truths;
 *   2. everything else in that layout (status bar, sidebars — INCLUDING the
 *      legacy TX panel, which slice c replaces — spectrum, meters dock) is
 *      untouched, and `desktop-v2` still renders the legacy VFO header for the
 *      compatibility window (MOR-1099).
 *
 * The RX/TX surface, the TX-panel suppression and the CW-survival pin arrive
 * with slice c, together with the code they pin.
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


describe('the migrated desktop layout owns its VFO through the semantic surface', () => {
  it('renders the semantic VFO surface inside the receiver deck', () => {
    const t = render('sdr-test');
    const deck = t.querySelector('.receiver-deck')!;
    expect(deck.querySelector('[data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(deck.querySelector('[data-testid="vfo-surface"]')).not.toBeNull();
  });

  // MUTATION KILLED: adding the surface alongside the block it replaces. Two
  // VFO readouts in one layout is exactly the "duplicate presentation
  // ownership" MOR-1099 exists to retire.
  it('renders none of the legacy VFO presentation it replaces', () => {
    const t = render('sdr-test');
    expect(t.querySelector('.vfo-header')).toBeNull();
    expect(t.querySelector('.sdr-host')).toBeNull();
  });

  // The coherent slice-b intermediate: the legacy TX panel is STILL the only
  // PTT affordance in this layout, so no TX capability is lost between b and c.
  // Slice c replaces it with the semantic RX/TX surface in the same commit that
  // introduces `hideTxPanel`.
  it('leaves the legacy TX panel in place until the RX/TX surface lands', () => {
    const t = render('sdr-test');
    expect(t.querySelector('[data-panel-id="tx"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="rx-tx-surface"]')).toBeNull();
  });

  it('leaves the rest of the layout intact', () => {
    const t = render('sdr-test');
    expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
    expect(t.querySelector('.center-column .spectrum-slot')).not.toBeNull();
    expect(t.querySelector('.spectrum-panel-stub')).not.toBeNull();
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="rx-audio"]')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="memory"]')).not.toBeNull();
  });

  it.each(['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const)(
    'renders safely on the %s topology', (id) => {
      h.caps = capsFor(id);
      const t = render('sdr-test');
      const tiles = t.querySelectorAll('[data-vfo-tile]');
      expect(tiles.length).toBe(topologyFixtures[id].vfos.length);
      expect(t.querySelector('[data-testid="vfo-surface"]')).not.toBeNull();
    },
  );

  it('renders the chrome but no surface when capabilities have not loaded', () => {
    h.caps = null;
    const t = render('sdr-test');
    expect(t.querySelector('.receiver-deck')).not.toBeNull();
    expect(t.querySelector('[data-testid="vfo-surface"]')).toBeNull();
  });
});

describe('the unmigrated desktop layout is unchanged', () => {
  // MUTATION KILLED: flipping the whole desktop family over in one step. The
  // compatibility window (MOR-1099) requires desktop-v2 to keep the legacy
  // panels until the parity programme (MOR-1084) clears them.
  it('keeps the legacy VFO header and TX panel on desktop-v2', () => {
    const t = render('desktop-v2');
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
    expect(t.querySelector('[data-panel-id="tx"]')).not.toBeNull();
    expect(t.querySelector('[data-testid="semantic-radio-surfaces"]')).toBeNull();
  });
});
