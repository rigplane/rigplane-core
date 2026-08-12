/**
 * MOR-1486 — amber-lcd mode-follow, restored (owner ruling, session 19).
 *
 * An earlier round of this PR removed `LcdLayout.svelte`'s
 * `applyModeDefault(activeMode)` `$effect` on the premise that amber-lcd
 * (both `cockpit` and `scope` variants) has "no tuning-STEP control
 * anywhere". That premise was false: neither `AmberCockpit.svelte` nor
 * `AmberScope.svelte` render a visible STEP readout or an AUTO indicator —
 * that part is true — but the shared tuning-step store is actively WRITTEN
 * and READ on this skin regardless. The global ArrowUp/Down keyboard
 * binding (`keyboard-map.ts`'s `step-up`/`step-down`, routed through the
 * `KeyboardHandler` this layout mounts) calls `adjustTuningStep()`,
 * ArrowLeft/Right tuning (`panel-commands.ts`'s `tune` case, ~line 1389)
 * reads `getTuningStep()` for the increment, and MediaSession volume-key
 * tuning (`lib/media/media-session.ts`) reads it too. Freezing mode-follow
 * here while every other consumption path keeps working would silently
 * change arrow-tuning granularity across mode changes with no operator
 * feedback — worse than the missing on-screen indicator this ticket set
 * out to fix. The owner restored the `$effect` and accepted the indicator
 * gap as a tracked, separate concern (see the PR body).
 *
 * The previous version of this suite asserted `applyModeDefault` was NEVER
 * called, driving "mode changes" by reassigning `rt.state` after mount and
 * calling `flushSync()`. That was vacuous: `runtime.state` in the mock
 * below is a plain JS getter over a module-level variable, not a Svelte
 * reactive source, so mutating `rt.state` post-mount never actually
 * re-triggers `$derived`/`$effect` — the assertions passed regardless of
 * whether the effect existed at all. This version uses the discriminating
 * pattern instead: mount a FRESH component per mode, with `rt.state` set
 * BEFORE mount, so each case exercises a genuine one-time read of a
 * different value at initialization and the assertions can actually fail.
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

let components: ReturnType<typeof mount>[] = [];

/**
 * Mounts a FRESH LcdLayout instance. `rt.state` must be set before calling
 * this — the discriminating pattern the verifier prescribed. Unlike
 * reassigning `rt.state` on an already-mounted instance (which the old,
 * vacuous version of this suite did), a fresh mount forces a genuine
 * initial read of the current `rt.state` value through `$derived`, so
 * different inputs across separate mounts produce genuinely different,
 * assertable behavior.
 */
function mountFresh(variant: 'cockpit' | 'scope' = 'cockpit') {
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

describe('LcdLayout mode-follow, restored (MOR-1486 ruling A)', () => {
  it('calls applyModeDefault with the MAIN mode on initial mount (cockpit)', () => {
    rt.state = { active: 'MAIN', main: { mode: 'USB' }, sub: null };
    mountFresh('cockpit');
    expect(tuningHarness.applyModeDefault).toHaveBeenCalledWith('USB');
  });

  it('calls applyModeDefault with a different MAIN mode on a fresh mount — proving the effect actually reads current state, not a stale capture', () => {
    rt.state = { active: 'MAIN', main: { mode: 'CW' }, sub: null };
    mountFresh('cockpit');
    expect(tuningHarness.applyModeDefault).toHaveBeenCalledWith('CW');
    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalledWith('USB');
  });

  it('calls applyModeDefault with the SUB mode when SUB is the active receiver (scope variant)', () => {
    rt.state = { active: 'SUB', main: { mode: 'USB' }, sub: { mode: 'RTTY' } };
    mountFresh('scope');
    expect(tuningHarness.applyModeDefault).toHaveBeenCalledWith('RTTY');
    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalledWith('USB');
  });

  it('does not call applyModeDefault when there is no known active mode yet', () => {
    rt.state = { active: 'MAIN', main: {}, sub: null };
    mountFresh('cockpit');
    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalled();
  });

  it('does not call applyModeDefault when runtime state is entirely unset', () => {
    rt.state = null;
    mountFresh('cockpit');
    expect(tuningHarness.applyModeDefault).not.toHaveBeenCalled();
  });

  it('still renders .lcd-layout for both variants without throwing', () => {
    rt.state = { active: 'MAIN', main: { mode: 'USB' }, sub: null };
    expect(mountFresh('cockpit').querySelector('.lcd-layout')).not.toBeNull();
    expect(mountFresh('scope').querySelector('.lcd-layout')).not.toBeNull();
  });
});
