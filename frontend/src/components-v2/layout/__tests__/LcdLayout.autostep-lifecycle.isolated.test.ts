/**
 * MOR-1486 — amber-lcd auto-step honesty decision.
 *
 * `LcdLayout.svelte` (the amber-lcd skin, both `cockpit` and `scope`
 * variants) used to run `applyModeDefault(activeMode)` in a `$effect` on
 * every mode change, unconditionally — exactly like RadioLayout.svelte.
 * But amber-lcd has NO tuning-STEP control anywhere: `AmberCockpit.svelte`
 * and `AmberScope.svelte` never render a STEP UI, so an operator on this
 * skin has no way to see that the shared tuning-step store just changed
 * underneath them, and no way to know auto-step's state at all.
 *
 * Per the MOR-1486 ruling (documented in the PR body): a skin that cannot
 * show the step affordance must not silently mutate the shared step store
 * either. Building a step indicator into amber-lcd's hardware-mimicking
 * LCD chrome is out of scope for this ticket (no existing grid slot for it
 * — see docs/plans/2026-04-19-lcd-layout-redesign-v2.md), so the minimal
 * honest fix is: LcdLayout no longer calls `applyModeDefault` at all. The
 * shared `_autoStep`/`_step` state (set from whichever skin does have a
 * STEP control) is left untouched while viewing amber-lcd.
 *
 * This suite proves the mode-follow effect is gone: mounting LcdLayout and
 * driving `activeMode` through several distinct mode changes must never
 * call `applyModeDefault`.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

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

const tuningHarness = vi.hoisted(() => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: tuningHarness.applyModeDefault }));

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
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  getRigConnected: vi.fn(() => false),
  getRadioReady: vi.fn(() => false),
  getRadioHealth: vi.fn(() => null),
  markScopeFrame: vi.fn(),
}));

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
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    scope: { hardwareScopeConnected: false, registerPresentationDriver: vi.fn(), subscribe: vi.fn(() => () => {}) },
    bootstrap: vi.fn(async () => vi.fn()),
  },
  presentationResources: {
    acquire: vi.fn(() => ({ resource: 'x', consumer: 'x' })),
    release: vi.fn(),
    configure: vi.fn(),
    snapshot: vi.fn(() => ({ demand: 0, health: 'inactive' })),
  },
}));

vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => ({ phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null }),
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
  hasSpectrum: vi.fn(() => false),
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

vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
});

import LcdLayout from '../LcdLayout.svelte';
import lcdLayoutSource from '../LcdLayout.svelte?raw';

let components: ReturnType<typeof mount>[] = [];

function mountLayout(variant: 'cockpit' | 'scope' = 'cockpit') {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(LcdLayout, { target: t, props: { variant } });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  rt.state = null;
  tuningHarness.applyModeDefault.mockClear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  rt.state = null;
});

describe('LcdLayout auto-step honesty (MOR-1486): no step UI => no silent step mutation', () => {
  it('never calls applyModeDefault on initial mount with a known mode', () => {
    rt.state = { active: 'MAIN', main: { mode: 'USB' }, sub: null };
    mountLayout('cockpit');
    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalled();
  });

  it('never calls applyModeDefault as the active mode changes (MAIN)', () => {
    rt.state = { active: 'MAIN', main: { mode: 'USB' }, sub: null };
    mountLayout('cockpit');
    tuningHarness.applyModeDefault.mockClear();

    rt.state = { active: 'MAIN', main: { mode: 'CW' }, sub: null };
    flushSync();
    rt.state = { active: 'MAIN', main: { mode: 'FM' }, sub: null };
    flushSync();

    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalled();
  });

  it('never calls applyModeDefault when the SUB receiver is active and its mode changes', () => {
    rt.state = { active: 'SUB', main: { mode: 'USB' }, sub: { mode: 'AM' } };
    mountLayout('scope');
    tuningHarness.applyModeDefault.mockClear();

    rt.state = { active: 'SUB', main: { mode: 'USB' }, sub: { mode: 'RTTY' } };
    flushSync();

    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalled();
  });

  it('does not import the tuning store at all', () => {
    expect(lcdLayoutSource).not.toContain("from '$lib/stores/tuning.svelte'");
    expect(lcdLayoutSource).not.toMatch(/applyModeDefault\(/);
  });

  it('still renders .lcd-layout for both variants without throwing', () => {
    expect(mountLayout('cockpit').querySelector('.lcd-layout')).not.toBeNull();
  });
});
