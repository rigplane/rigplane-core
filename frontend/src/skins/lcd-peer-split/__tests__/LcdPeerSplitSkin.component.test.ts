/**
 * MOR-2153 PR-1 — `LcdPeerSplitSkin` mounts the LCD shell (`LcdLayout`
 * variant="peer-split") around the peer-split glass (`PeerSplitLayout`),
 * where `AmberCockpit`/`AmberScope` sit for `cockpit`/`scope`.
 *
 * Mock set copied from
 * `components-v2/layout/__tests__/LcdLayout.command-bus-migration.isolated.test.ts`,
 * the existing proof that this exact set mounts a REAL `LcdLayout` — and,
 * for `cockpit`/`scope`, a REAL `SemanticRadioSurfaces` inside it — without
 * throwing. `peer-split` additionally mounts a REAL `PeerSplitLayout` (its
 * own `ScaledStage` needs the same `ResizeObserver` stub already present
 * here) and, inside it, a SECOND real `SemanticRadioSurfaces` — which is
 * exactly the collision this file's last test pins.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const txHarness = new ManagedAppTxHarness();
import { mount, unmount, flushSync } from 'svelte';

vi.mock('../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../../../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => true),
  getLayoutMode: vi.fn(() => 'peer-split'),
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

const rt = vi.hoisted(() => ({ state: null as unknown }));

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
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

vi.mock('$lib/runtime/tx-controller/managed-app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/managed-app-host')>();
  return {
    ...actual,
    getManagedAppTxController: () => txHarness.controller,
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

import LcdPeerSplitSkin from '../LcdPeerSplitSkin.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountSkin() {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(LcdPeerSplitSkin, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  txHarness.reset();
  components = [];
  rt.state = null;
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  rt.state = null;
});

describe('LcdPeerSplitSkin (MOR-2153 PR-1)', () => {
  it('renders the LCD shell without throwing', () => {
    const t = mountSkin();
    expect(t.querySelector('.lcd-layout')).not.toBeNull();
  });

  it('mounts shell chrome (status bar, both sidebars), not a bare glass', () => {
    const t = mountSkin();
    expect(t.querySelector('.status-bar')).not.toBeNull();
    expect(t.querySelector('.left-sidebar')).not.toBeNull();
    expect(t.querySelector('.right-sidebar')).not.toBeNull();
  });

  it('mounts the peer-split glass inside .lcd-frame, where AmberCockpit sits for lcd-cockpit', () => {
    const t = mountSkin();
    const frame = t.querySelector('.lcd-frame[data-lcd-variant="peer-split"]');
    expect(frame).not.toBeNull();
    expect(frame!.querySelector('[data-testid="peer-split-glass"]')).not.toBeNull();
  });

  // Collision 1 (PR-1 brief): LcdLayout's `.content-right` mounts
  // SemanticRadioSurfaces for cockpit/scope; PeerSplitLayout's own glass
  // mounts a SECOND SemanticRadioSurfaces (`strips="dual"`) unconditionally.
  // Mounting the glass inside the unmodified shell would give the operator
  // two TX affordances — counted here rather than asserted as a boolean.
  //
  // MUTATION KILLED: reverting LcdLayout's `{#if variant !== 'peer-split'}`
  // guard around `.content-right`'s `<SemanticRadioSurfaces />` back to
  // unconditional reproduces the exact duplicate this test exists to catch
  // — observed red (2 instances) against that reverted guard, green (1)
  // against the guarded version committed here.
  it('mounts exactly one SemanticRadioSurfaces instance, not two', () => {
    const t = mountSkin();
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]').length).toBe(1);
  });
});
