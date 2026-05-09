import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));

vi.mock('../../../skins/registry', () => ({
  resolveSkinId: vi.fn(() => 'desktop-v2'),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    state: null,
    caps: null,
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
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
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

import RadioLayout from '../RadioLayout.svelte';
import { extractVfoState, extractMeterState, hasLiveAudioFromState } from '../layout-utils';
import { radio } from '$lib/stores/radio.svelte';

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
    expect(result.meterSource).toBe('S');
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

  it('extracts txActive and meterSource', () => {
    const result = extractMeterState({ txActive: true, meterSource: 'SWR' });
    expect(result.txActive).toBe(true);
    expect(result.meterSource).toBe('SWR');
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

function mountLayout() {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RadioLayout, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  radio.current = null;
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  // JSDOM defaults to 0x0 — force desktop dimensions so isMobile stays false
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('RadioLayout structure', () => {
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

  it('renders .bottom-dock', () => {
    const t = mountLayout();
    expect(t.querySelector('.bottom-dock')).not.toBeNull();
  });

  it('renders .vfo-header inside .receiver-deck', () => {
    const t = mountLayout();
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
  });
});

describe('Bottom dock MetersDockPanel', () => {
  it('renders the unified meters dock panel inside .bottom-dock', () => {
    const t = mountLayout();
    const dock = t.querySelector('.bottom-dock');
    expect(dock).not.toBeNull();
    expect(dock?.querySelector('[data-testid="meters-dock-panel"]')).not.toBeNull();
  });
});

describe('VfoHeader dual receiver', () => {
  it('renders only one .panel in vfo-header when hasDualReceiver is false', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    const t = mountLayout();
    const vfoHeader = t.querySelector('.receiver-deck .vfo-header');
    const panels = vfoHeader?.querySelectorAll('.panel');
    expect(panels?.length).toBe(1);
  });

  it('renders two .panel elements in vfo-header when hasDualReceiver is true', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountLayout();
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

  it('renders MetersDockPanel in the bottom dock', () => {
    radio.current = sampleState as any;
    const t = mountLayout();
    expect(t.querySelector('.bottom-dock [data-testid="meters-dock-panel"]')).not.toBeNull();
  });
});
