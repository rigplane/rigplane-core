import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

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

// Only the resolver is faked; `loadSkin` stays real so the App-mount test
// below still exercises the actual lazy entrypoint → RadioLayout path
// (MOR-1060).
vi.mock('../../../skins/registry', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../skins/registry')>(),
  resolveSkinId: vi.fn(() => 'desktop-v2'),
}));

vi.mock('../../../lib/utils/battery', () => ({
  initBatteryMonitor: vi.fn(async () => vi.fn()),
}));

vi.mock('../../../lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));

vi.mock('../../../lib/runtime/frontend-runtime', () => ({
  runtime: {
    state: null,
    caps: { scope: true },
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
    connectionAudio: false,
    // MOR-1312 slice 12B: see the `$lib/runtime` mock below for why these
    // are fixed, honest "never observed" defaults.
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    scope: { hardwareScopeConnected: false },
    bootstrap: vi.fn(async () => vi.fn()),
    setPollingMultiplier: vi.fn(),
    send: vi.fn(),
  },
}));

// MOR-1235: `state` is a live getter over a mutable holder (default `null`,
// reset per test) so a test can put a REAL `ptt` on the runtime state and
// prove the meters dock ignores it in favour of the App TX authority.
const rt = vi.hoisted(() => ({ state: null as unknown }));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return rt.state; },
    caps: null,
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
    connectionAudio: false,
    // MOR-1312 slice 12B: `SemanticRadioSurfaces` now also reads
    // `runtime.defaultScopeStatus` / `runtime.scope.hardwareScopeConnected`
    // for the scope-display snapshot (the FIFTH adapter argument). `caps` is
    // `null` here, so `toRadioViewModel` returns `null` regardless — these
    // are fixed, honest "never observed" defaults, not exercised behavior.
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    scope: { hardwareScopeConnected: false },
    send: vi.fn(),
  },
}));

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
  // Under the fast pool's ``isolate: false`` this hoisted mock is shared
  // module-wide; scope-controller.svelte (loaded by a sibling fast-pool
  // test) imports the real ``markScopeFrame``, so it must be stubbed here
  // or that sibling throws "No markScopeFrame export". See issue #771.
  markScopeFrame: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

import RadioLayout from '../RadioLayout.svelte';
import App from '../../../App.svelte';
import { extractVfoState, extractMeterState, hasLiveAudioFromState } from '../layout-utils';
import { radio } from '$lib/stores/radio.svelte';
import { resolveSkinId, type SkinId } from '../../../skins/registry';

// ---------------------------------------------------------------------------
// extractVfoState
// ---------------------------------------------------------------------------

describe('extractVfoState', () => {
  it('returns defaults when radioState is null', () => {
    const result = extractVfoState(null, 'main');
    expect(result.receiver).toBe('main');
    expect(result.freq).toBe(14074000);
    expect(result.mode).toBe('USB');
    expect(result.filter).toBe('FIL1');
    expect(result.sValue).toBe(0);
    expect(result.badges).toEqual({});
    expect(result.rit).toBeUndefined();
  });

  it('returns defaults when radioState is empty object', () => {
    const result = extractVfoState({}, 'sub');
    expect(result.receiver).toBe('sub');
    expect(result.freq).toBe(14074000);
    expect(result.mode).toBe('USB');
  });

  it('returns main vfo data from radioState', () => {
    const state = {
      main: { freq: 7074000, mode: 'LSB', filter: 'FIL2', sValue: 100, badges: { nr: true } },
      activeReceiver: 'main',
    };
    const result = extractVfoState(state, 'main');
    expect(result.freq).toBe(7074000);
    expect(result.mode).toBe('LSB');
    expect(result.filter).toBe('FIL2');
    expect(result.sValue).toBe(100);
    expect(result.badges).toEqual({ nr: true });
    expect(result.isActive).toBe(true);
  });

  it('returns sub vfo data from radioState', () => {
    const state = {
      sub: { freq: 3573000, mode: 'LSB', filter: 'FIL1', sValue: 50, badges: {} },
      activeReceiver: 'main',
    };
    const result = extractVfoState(state, 'sub');
    expect(result.freq).toBe(3573000);
    expect(result.receiver).toBe('sub');
    expect(result.isActive).toBe(false);
  });

  it('isActive true when activeReceiver matches receiver', () => {
    const state = { activeReceiver: 'sub', sub: {} };
    const result = extractVfoState(state, 'sub');
    expect(result.isActive).toBe(true);
  });

  it('isActive false when activeReceiver does not match receiver', () => {
    const state = { activeReceiver: 'main', sub: {} };
    const result = extractVfoState(state, 'sub');
    expect(result.isActive).toBe(false);
  });

  it('defaults activeReceiver to main when missing', () => {
    const state = { main: { freq: 14200000 } };
    const mainResult = extractVfoState(state, 'main');
    const subResult = extractVfoState(state, 'sub');
    expect(mainResult.isActive).toBe(true);
    expect(subResult.isActive).toBe(false);
  });

  it('passes rit object when present', () => {
    const state = {
      main: { rit: { active: true, offset: 120 } },
      activeReceiver: 'main',
    };
    const result = extractVfoState(state, 'main');
    expect(result.rit).toEqual({ active: true, offset: 120 });
  });
});

// ---------------------------------------------------------------------------
// extractMeterState
// ---------------------------------------------------------------------------

describe('extractMeterState', () => {
  it('returns defaults when radioState is null', () => {
    const result = extractMeterState(null);
    expect(result.sValue).toBe(0);
    expect(result.rfPower).toBe(0);
    expect(result.swr).toBe(0);
    expect(result.alc).toBe(0);
    expect(result.txActive).toBe(false);
    expect(result).not.toHaveProperty('meterSource');
  });

  it('extracts sValue from radioState.main', () => {
    const result = extractMeterState({ main: { sValue: 180 } });
    expect(result.sValue).toBe(180);
  });

  it('extracts tx values from top-level meter fields', () => {
    const result = extractMeterState({ powerMeter: 200, swrMeter: 30, alcMeter: 64 });
    expect(result.rfPower).toBe(200);
    expect(result.swr).toBe(30);
    expect(result.alc).toBe(64);
  });

  it('falls back to legacy tx sub-object', () => {
    const result = extractMeterState({ tx: { rfPower: 200, swr: 30, alc: 64 } });
    expect(result.rfPower).toBe(200);
    expect(result.swr).toBe(30);
    expect(result.alc).toBe(64);
  });

  it('extracts txActive without projecting local presentation state', () => {
    const result = extractMeterState({ txActive: true, meterSource: 'SWR' });
    expect(result.txActive).toBe(true);
    expect(result).not.toHaveProperty('meterSource');
  });

  it('extracts txActive from ptt field', () => {
    const result = extractMeterState({ ptt: true });
    expect(result.txActive).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// hasLiveAudioFromState
// ---------------------------------------------------------------------------

describe('hasLiveAudioFromState', () => {
  it('returns false when radioState is null', () => {
    expect(hasLiveAudioFromState(null)).toBe(false);
  });

  it('returns true when capabilities.audio is true', () => {
    expect(hasLiveAudioFromState({ capabilities: { audio: true } })).toBe(true);
  });

  it('returns false when capabilities.audio is false', () => {
    expect(hasLiveAudioFromState({ capabilities: { audio: false } })).toBe(false);
  });

  it('returns false when capabilities object is empty', () => {
    expect(hasLiveAudioFromState({ capabilities: {} })).toBe(false);
  });

  it('returns false when capabilities key is missing', () => {
    expect(hasLiveAudioFromState({ other: true })).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// extractMeterState — sMeter fallback path
// ---------------------------------------------------------------------------

describe('extractMeterState sMeter fallback', () => {
  it('prefers sValue over sMeter', () => {
    const result = extractMeterState({ main: { sValue: 100, sMeter: 50 } });
    expect(result.sValue).toBe(100);
  });

  it('falls back to sMeter when sValue is missing', () => {
    const result = extractMeterState({ main: { sMeter: 75 } });
    expect(result.sValue).toBe(75);
  });
});

// ---------------------------------------------------------------------------
// extractVfoState — partial nested objects
// ---------------------------------------------------------------------------

describe('extractVfoState partial data', () => {
  it('handles partial vfo data with some fields missing', () => {
    const state = { main: { freq: 7000000 }, activeReceiver: 'main' };
    const result = extractVfoState(state, 'main');
    expect(result.freq).toBe(7000000);
    expect(result.mode).toBe('USB');
    expect(result.filter).toBe('FIL1');
    expect(result.sValue).toBe(0);
    expect(result.badges).toEqual({});
  });

  it('returns undefined rit when vfo has no rit', () => {
    const state = { main: { freq: 14074000 } };
    const result = extractVfoState(state, 'main');
    expect(result.rit).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// RadioLayout component
// ---------------------------------------------------------------------------

// MOR-1011: TxPanel resolves the App TX controller from Svelte context, which
// only App.svelte provides. RadioLayout is mounted here without that provider,
// so stub the host lookup — the layout still renders the real panel tree.
// (Partial mock: the App-mount test below still needs the real provider.)
// MOR-1235: the snapshot is a MUTABLE holder now — the meters dock reads its
// TX chrome from this controller, so a test has to be able to key it.
const txAuthority = vi.hoisted(() => {
  const idle = {
    phase: 'idle', intent: null, guard: null, radioTx: 'unknown',
    txRisk: 'none', mayOwnKey: false, fault: null,
  };
  return { idle, current: idle as Record<string, unknown> };
});

vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => txAuthority.current,
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
  hasDualReceiver: vi.fn(() => false),
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
  getVfoScheme: vi.fn(() => 'ab'),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

import { hasDualReceiver } from '$lib/stores/capabilities.svelte';

let components: ReturnType<typeof mount>[] = [];

/**
 * MOR-1313 — an id no layout manifest is registered under, so nothing is
 * declared and every legacy twin renders. It is the only way to reach the
 * legacy VFO/TX branch now that both families sharing this shell resolve fully
 * semantic, and it exercises the fail-safe direction of the suppression rule.
 */
const UNDECLARED = 'no-such-layout' as SkinId;

function mountLayout(skinId: SkinId = 'desktop-v2') {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RadioLayout, { target: t, props: { skinId } });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  radio.current = null;
  rt.state = null;
  txAuthority.current = txAuthority.idle;
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  vi.mocked(resolveSkinId).mockReturnValue('desktop-v2');
  // JSDOM defaults to 0x0 — force desktop dimensions so isMobile stays false
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  // Hygiene WITHIN this file. It runs in the `isolated` pool
  // (`vite.config.ts`), so these holders cannot leak into another file — but
  // they are module-level and shared by every `it` here, so a non-null `state`
  // or a keyed authority left behind by one test would be read by the next one
  // that does not set its own. Reset on the way OUT as well as the way in.
  rt.state = null;
  txAuthority.current = txAuthority.idle;
});

describe('RadioLayout structure', () => {
  it('renders the SDR branch from its skinId prop', () => {
    const t = mountLayout('sdr-test');
    expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
  });

  it('renders the root .radio-layout element', () => {
    const t = mountLayout();
    expect(t.querySelector('.radio-layout')).not.toBeNull();
  });

  it('renders .receiver-deck', () => {
    const t = mountLayout();
    expect(t.querySelector('.receiver-deck')).not.toBeNull();
  });

  it('renders .content-row', () => {
    const t = mountLayout();
    expect(t.querySelector('.content-row')).not.toBeNull();
  });

  it('renders .left-sidebar inside .content-left', () => {
    const t = mountLayout();
    expect(t.querySelector('.content-left .left-sidebar')).not.toBeNull();
  });

  it('renders .right-sidebar inside .content-right', () => {
    const t = mountLayout();
    expect(t.querySelector('.content-right .right-sidebar')).not.toBeNull();
  });

  it('renders .center-column and .spectrum-slot in the center area', () => {
    const t = mountLayout();
    const center = t.querySelector('.center-column');
    expect(center).not.toBeNull();
    expect(center?.querySelector('.spectrum-slot')).not.toBeNull();
  });

  it('renders the SpectrumPanel stub inside the spectrum slot', () => {
    const t = mountLayout();
    expect(t.querySelector('.spectrum-panel-stub')).not.toBeNull();
  });

  // MOR-1341: `desktop-v2` (the default here) now declares a `meters` zone
  // and retires `.bottom-dock` — see the suppression matrix in
  // `semantic-desktop-migration.component.test.ts`. `sdr-test` declares no
  // such zone, so it stays the layout that proves the dock still exists.
  it('renders .bottom-dock for a layout that declares no meters zone', () => {
    const t = mountLayout('sdr-test');
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
  });

  // MOR-1313: `desktop-v2` resolves through its layout manifest now, so the
  // receiver deck hosts the semantic surfaces. The LEGACY deck is what an
  // undeclared layout gets — see `UNDECLARED` below and the full suppression
  // matrix in `semantic-desktop-migration.component.test.ts`.
  it('renders the semantic surfaces inside .receiver-deck for desktop-v2', () => {
    const t = mountLayout();
    expect(t.querySelector('.receiver-deck [data-testid="semantic-radio-surfaces"]')).not.toBeNull();
    expect(t.querySelector('.vfo-header')).toBeNull();
  });

  it('renders .vfo-header inside .receiver-deck for an undeclared layout', () => {
    const t = mountLayout(UNDECLARED);
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
  });
});

// MOR-1369 (v3-rework S6b-1) — the SpectrumPanel `hideScopeControls`
// suppression channel. `RadioLayout` reuses the SAME `declared` set the
// MOR-1364 (S6-pre) channel already derives (no second derivation), so this
// is the pass-through half of that channel, proven at the boundary
// (`SpectrumPanelStub`'s `data-hide-scope-controls`); the SpectrumToolbar
// half (which controls are fact-backed vs client-side view options, per the
// S10 boundary doc) is proven directly in `SpectrumToolbar.component.test.ts`.
//
// MOR-1370 (S6b-2) graduates the flip: `desktop-v2` now REALLY declares a
// `scope-controls` zone, so the synthetic probe manifest this describe used
// to register is retired in favour of the real manifest — the same
// graduation `semantic-desktop-migration.component.test.ts`'s `ZONES`
// literal recorded for S7/S8/S9.
describe('SpectrumPanel hideScopeControls channel (MOR-1369 S6b-1, MOR-1370 S6b-2)', () => {
  it('passes hideScopeControls=true on real desktop-v2, which declares the scope-controls zone', () => {
    const t = mountLayout('desktop-v2');
    const stub = t.querySelector('.spectrum-panel-stub');
    expect(stub?.getAttribute('data-hide-scope-controls')).toBe('true');
  });

  it('stays false on an undeclared layout (fail-safe direction)', () => {
    const t = mountLayout(UNDECLARED);
    const stub = t.querySelector('.spectrum-panel-stub');
    expect(stub?.getAttribute('data-hide-scope-controls')).toBe('false');
  });
});

describe('App presentation selection', () => {
  it('owns the resolver inputs and passes the resolved skinId to RadioLayout', async () => {
    vi.mocked(resolveSkinId).mockReturnValue('sdr-test');

    const t = document.createElement('div');
    document.body.appendChild(t);
    const component = mount(App, { target: t });
    flushSync();
    components.push(component);
    // MOR-1060: the presentation is loaded lazily (a real dynamic import of
    // the sdr-test entrypoint), so wait for the commit before asserting.
    await vi.waitFor(() => {
      flushSync();
      expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
    });

    expect(resolveSkinId).toHaveBeenLastCalledWith({
      capabilities: { scope: true },
      layoutPreference: 'standard',
      isMobile: false,
      hasAnyScope: false,
    });
    expect(t.querySelector('.radio-layout.sdr-test')).not.toBeNull();
  });
});

// MOR-1341: `desktop-v2` (this file's `mountLayout()` default) now suppresses
// `.bottom-dock` via its `meters` zone declaration. `sdr-test` declares none,
// so it stays the layout that exercises the dock's OWN behaviour — same move
// as `VfoHeader dual receiver` below, which tests the legacy deck the same way.
describe('Bottom dock MetersDockPanel', () => {
  it('renders the unified meters dock panel inside .bottom-dock', () => {
    const t = mountLayout('sdr-test');
    const dock = t.querySelector('.bottom-dock');
    expect(dock).not.toBeNull();
    expect(dock?.querySelector('[data-testid="meters-dock-panel"]')).not.toBeNull();
  });
});

/**
 * MOR-1235 — the discriminating pair. The dock's TX chrome must follow the
 * App TX controller (the same source as the authoritative AppGlobalHost lamp),
 * NOT `radioState.ptt`. Each case sets the two to OPPOSITE values, so routing
 * the prop back to `ptt` flips both assertions.
 */
describe('meters dock TX chrome follows the App TX authority (MOR-1235)', () => {
  const txTag = (t: HTMLElement) => t.querySelector('.bottom-dock .dock-tx-state');

  it('shows TX when the authority is keyed while radioState.ptt reads false', () => {
    rt.state = { active: 'MAIN', ptt: false, main: { sMeter: 120 } };
    txAuthority.current = { ...txAuthority.idle, radioTx: 'on', txRisk: 'confirmed-on' };
    const t = mountLayout('sdr-test');
    expect(txTag(t)?.getAttribute('data-active')).toBe('true');
    expect(txTag(t)?.textContent).toBe('TX');
  });

  it('shows RX when the authority is idle while radioState.ptt reads true', () => {
    rt.state = { active: 'MAIN', ptt: true, main: { sMeter: 120 } };
    txAuthority.current = { ...txAuthority.idle, radioTx: 'off', txRisk: 'none' };
    const t = mountLayout('sdr-test');
    expect(txTag(t)?.getAttribute('data-active')).toBe('false');
    expect(txTag(t)?.textContent).toBe('RX');
  });

  it('fails closed: an uncertain authority shows TX even with ptt false', () => {
    rt.state = { active: 'MAIN', ptt: false, main: { sMeter: 120 } };
    txAuthority.current = { ...txAuthority.idle, radioTx: 'off', txRisk: 'uncertain' };
    const t = mountLayout('sdr-test');
    expect(txTag(t)?.getAttribute('data-active')).toBe('true');
  });
});

// MOR-1313: the legacy VFO header lives on the undeclared branch now.
describe('VfoHeader dual receiver', () => {
  it('renders only one .panel in vfo-header when hasDualReceiver is false', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    const t = mountLayout(UNDECLARED);
    const vfoHeader = t.querySelector('.receiver-deck .vfo-header');
    const panels = vfoHeader?.querySelectorAll('.panel');
    expect(panels?.length).toBe(1);
  });

  it('renders two .panel elements in vfo-header when hasDualReceiver is true', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountLayout(UNDECLARED);
    const vfoHeader = t.querySelector('.receiver-deck .vfo-header');
    const panels = vfoHeader?.querySelectorAll('.panel');
    expect(panels?.length).toBe(2);
  });
});

describe('RadioLayout with radioState', () => {
  const sampleState = {
    revision: 1,
    updatedAt: '2026-03-18T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 120,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 128,
      rfGain: 100,
      squelch: 0,
    },
    sub: {
      freqHz: 7074000,
      mode: 'LSB',
      filter: 1,
      dataMode: 0,
      sMeter: 60,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 128,
      rfGain: 100,
      squelch: 0,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
  };

  it('renders without errors given a full radioState', () => {
    radio.current = sampleState as any;
    const t = mountLayout();
    expect(t.querySelector('.radio-layout')).not.toBeNull();
  });

  it('renders MetersDockPanel in the bottom dock for a layout with no meters zone', () => {
    radio.current = sampleState as any;
    const t = mountLayout('sdr-test');
    expect(t.querySelector('.bottom-dock [data-testid="meters-dock-panel"]')).not.toBeNull();
  });
});
