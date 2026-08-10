/**
 * MOR-1409 A13b — LcdLayout command-bus migration (correction 5246842617,
 * the second A13 split leg).
 *
 * LcdLayout has exactly one command-bus import: `makeKeyboardHandlers`,
 * wired once to `KeyboardHandler`'s `onAction`. This gate re-points it at
 * the sanctioned `getKeyboardHandlers()` accessor A13a added to
 * `lib/runtime/adapters/panel-adapters.ts`, closing the last layout
 * importer of the `wiring/command-bus` shim (A15's deletion clause is
 * satisfiable once RadioLayout/LcdLayout both migrate — see the sibling
 * RadioLayout.command-bus-migration.isolated.test.ts for that half).
 *
 * Each test names the mutation it exists to kill.
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
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/lcd-display-mode.svelte', () => ({
  getLcdDisplayMode: vi.fn(() => 'clean'),
  setLcdDisplayMode: vi.fn(),
  LCD_DISPLAY_MODES: ['clean', 'vintage', 'crt', 'flicker'],
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
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  rt.state = null;
});

describe('LcdLayout canonical module surface (MOR-1409 A13b)', () => {
  // Kills: leaving `makeKeyboardHandlers` on the `wiring/command-bus` shim —
  // this is the shim's LAST layout importer besides RadioLayout; A15's
  // deletion clause needs both migrated.
  it('imports no handler family from the legacy command-bus shim', () => {
    expect(lcdLayoutSource).not.toContain('wiring/command-bus');
  });

  it('does not reach into lib/runtime/commands directly', () => {
    expect(lcdLayoutSource).not.toContain('runtime/commands/panel-commands');
  });

  it('binds its keyboard handler through the sanctioned panel-adapters layer', () => {
    expect(lcdLayoutSource).toContain('$lib/runtime/adapters/panel-adapters');
    expect(lcdLayoutSource).toContain('getKeyboardHandlers');
  });

  it('contains zero runtime.send( references', () => {
    expect(lcdLayoutSource).not.toMatch(/runtime\.send\(/);
  });
});

describe('LcdLayout mounts on the migrated handler surface (MOR-1409 A13b)', () => {
  it('renders .lcd-layout for the cockpit variant without throwing', () => {
    const t = mountLayout('cockpit');
    expect(t.querySelector('.lcd-layout')).not.toBeNull();
  });

  it('renders .lcd-layout for the scope variant without throwing', () => {
    const t = mountLayout('scope');
    expect(t.querySelector('.lcd-layout')).not.toBeNull();
  });
});
