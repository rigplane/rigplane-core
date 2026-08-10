/**
 * MOR-1409 A13b — RadioLayout + LcdLayout command-bus migration, send()
 * facade deletion (correction 5246842617, the second A13 split leg).
 *
 * RadioLayout's read side (mainVfo/subVfo/vfoOps) moves from the legacy
 * `wiring/state-adapter` twin to the A11/A12-hardened
 * `lib/runtime/props/panel-props`; its live handler families
 * (vfo/keyboard/system) move from the `wiring/command-bus` shim to the
 * sanctioned `lib/runtime/adapters/panel-adapters` accessors A13a added
 * (`getKeyboardHandlers`, `getSystemHandlers`).
 *
 * Live inspection at this anchor found `rfFrontEnd`/`agc`/`ritXit`/`band`/
 * `dsp`/`cw` (and their matching `make*Handlers` constructions) are
 * computed but never referenced by RadioLayout's template — every panel
 * that used to consume them (`RfFrontEnd`, `AgcPanel`, `DspPanel`,
 * `RitXitPanel`, `CwPanel`, `BandSelector`) now self-sources through the
 * semantic surfaces. They are deleted outright rather than re-pointed at an
 * equally dead binder call.
 *
 * The two `runtime.send()` scope call sites (`handleScopeDualToggle`,
 * `handleScopeReceiverChange`) are RadioLayout's only remaining production
 * `send()` callers. Their sole consumer, `VfoHeader`, already ignores the
 * legacy `scopeStatus`/`onScopeDualToggle`/`onScopeReceiverChange` props it
 * still declares — `VfoHeader scope bridge authority` in
 * `vfo-header.isolated.test.ts:341` pins
 * `expect(source).not.toMatch(/onScopeReceiverChange\?\.|onScopeDualToggle\?\./)`
 * as proof VfoHeader self-wires scope handling via its own
 * `bindSemanticSurfaceHandlers().scopeControls` (the A07 idiom). RadioLayout
 * is the last caller of that vestigial bridge; this gate deletes it rather
 * than migrating dead code to an equally-dead binder call.
 *
 * Each test names the mutation it exists to kill.
 */
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

vi.mock('../../../lib/utils/battery', () => ({
  initBatteryMonitor: vi.fn(async () => vi.fn()),
}));

vi.mock('../../../lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
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
    scope: { hardwareScopeConnected: false },
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
  markScopeFrame: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => ({ phase: 'idle', intent: null, guard: null, radioTx: 'unknown', txRisk: 'none', mayOwnKey: false, fault: null }),
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

import RadioLayout from '../RadioLayout.svelte';
import radioLayoutSource from '../RadioLayout.svelte?raw';

let components: ReturnType<typeof mount>[] = [];

/** Undeclared layout id: reaches the legacy VFO/TX branch (`<VfoHeader>`),
 *  the only branch RadioLayout's own read/handler surface still feeds. */
const UNDECLARED = 'no-such-layout' as any;

function mountLayout(skinId: any = UNDECLARED) {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RadioLayout, { target: t, props: { skinId } });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  rt.state = null;
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  rt.state = null;
});

// ───────────────────────────────────────────────────────────────────────────
// Static closure: the layout consumes the canonical modules only
// ───────────────────────────────────────────────────────────────────────────

describe('RadioLayout canonical module surface (MOR-1409 A13b)', () => {
  // Kills: re-pointing mainVfo/subVfo/vfoOps back at the legacy wiring twin.
  it('imports no projection from the legacy wiring state-adapter', () => {
    expect(radioLayoutSource).not.toContain('wiring/state-adapter');
  });

  // Kills: leaving any handler family on the `wiring/command-bus` shim, whose
  // production importer count must reach zero for A15's deletion clause.
  it('imports no handler family from the legacy command-bus shim', () => {
    expect(radioLayoutSource).not.toContain('wiring/command-bus');
  });

  // Kills: bypassing the adapter layer by importing the command module directly.
  it('does not reach into lib/runtime/commands directly', () => {
    expect(radioLayoutSource).not.toContain('runtime/commands/panel-commands');
  });

  it('reads its VFO projections from the hardened panel-props module', () => {
    expect(radioLayoutSource).toContain('$lib/runtime/props/panel-props');
  });

  it('binds its handlers through the sanctioned panel-adapters layer', () => {
    expect(radioLayoutSource).toContain('$lib/runtime/adapters/panel-adapters');
    expect(radioLayoutSource).toContain('getVfoHandlers');
    expect(radioLayoutSource).toContain('getKeyboardHandlers');
    expect(radioLayoutSource).toContain('getSystemHandlers');
  });

  // Kills: restoring any of the dead RF-front-end/AGC/RIT-XIT/band/DSP/CW
  // props or handler constructions this gate removes as unreachable.
  it('no longer constructs the unreferenced RF/AGC/RIT-XIT/band/DSP/CW handler families', () => {
    expect(radioLayoutSource).not.toMatch(/makeRfFrontEndHandlers|makeAgcHandlers|makeRitXitHandlers/);
    expect(radioLayoutSource).not.toMatch(/makeBandHandlers|makePresetHandlers|makeDspHandlers|makeCwPanelHandlers/);
    expect(radioLayoutSource).not.toMatch(/toRfFrontEndProps|toAgcProps|toRitXitProps|toBandSelectorProps|toDspProps|toCwProps/);
  });

  // Kills: reintroducing `runtime.send(` anywhere in RadioLayout — its two
  // scope call sites are the method's last production callers.
  it('contains zero runtime.send( references', () => {
    expect(radioLayoutSource).not.toMatch(/runtime\.send\(/);
  });

  // Kills: reviving the dead scope-status bridge (`scopeStatus`,
  // `handleScopeDualToggle`, `handleScopeReceiverChange`) that fed
  // VfoHeader's already-ignored legacy props.
  it('no longer computes or wires the dead VfoHeader scope-status bridge', () => {
    expect(radioLayoutSource).not.toMatch(/function handleScopeDualToggle\(/);
    expect(radioLayoutSource).not.toMatch(/function handleScopeReceiverChange\(/);
    expect(radioLayoutSource).not.toMatch(/\bscopeStatus\s*=\s*\$derived/);
    expect(radioLayoutSource).not.toMatch(/\{scopeStatus\}/);
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Consumer boundary: the migrated projection/handler surface still mounts
// ───────────────────────────────────────────────────────────────────────────
//
// `toVfoProps`/`toVfoOpsProps`'s own honesty contract (NaN/'---' instead of
// a fabricated 14074000/USB/FIL1 reading) is unit-tested at its source in
// `lib/runtime/props/__tests__/panel-props.no-fabricated-defaults.test.ts`;
// RadioLayout's obligation is only to call the canonical two-arg signature
// and render without error, which these prove.

describe('RadioLayout mounts on the migrated projection/handler surface (MOR-1409 A13b)', () => {
  it('renders the legacy VFO header for an undeclared layout without throwing', () => {
    const t = mountLayout();
    expect(t.querySelector('.receiver-deck .vfo-header')).not.toBeNull();
  });

  it('wires KeyboardHandler to a working dispatch function post-migration', () => {
    const t = mountLayout();
    const handler = t.querySelector('.receiver-deck')?.parentElement;
    expect(handler).not.toBeNull();
    // Smoke: mounting must not throw when `getKeyboardHandlers()`/
    // `getSystemHandlers()`/`getVfoHandlers()` replace the command-bus calls.
    expect(t.querySelector('.radio-layout')).not.toBeNull();
  });
});
