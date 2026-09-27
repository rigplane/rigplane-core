import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const txHarness = new ManagedAppTxHarness({ stale: true });

vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));

vi.mock('../../../skins/registry', () => ({
  resolveSkinId: vi.fn(() => 'desktop-v2'),
  presentationHostMode: () => 'self-contained',
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    state: null,
    caps: null,
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
    // MOR-1312 slice 12B (rebase fix): `SemanticRadioSurfaces`'s scope-display
    // snapshot (the FIFTH adapter argument) reads these two directly; this
    // fixture declares no scope capability.
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    scope: { hardwareScopeConnected: false },
  },
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getWsConnected: vi.fn(() => false),
  hasEverConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  getRadioStatus: vi.fn(() => 'disconnected'),
  getRadioLinkState: vi.fn(() => 'disconnected'),
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
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

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasDualReceiver: vi.fn(() => true),
  hasTx: vi.fn(() => true),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
  isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false),
  getScopeSource: vi.fn(() => null),
  hasCapability: vi.fn(() => false),
  getKeyboardConfig: vi.fn(() => null),
  getVfoScheme: vi.fn(() => 'main_sub'),
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  getCapabilities: vi.fn(() => ({
    freqRanges: [
      {
        start: 7000000,
        end: 7300000,
        bands: [{ name: '40m', start: 7000000, end: 7300000, default: 7074000 }],
      },
      {
        start: 14000000,
        end: 14350000,
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000 }],
      },
    ],
  })),
  setCapabilities: vi.fn(),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]),
  getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]),
  getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

import RadioLayout from '../RadioLayout.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountWithCleanup(component: typeof RadioLayout, props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(component, { target, props });
  flushSync();
  components.push(instance);
  return target;
}

class ResizeObserverStub {
  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.callback([
      {
        target,
        contentRect: { width: 1600 } as DOMRectReadOnly,
      } as ResizeObserverEntry,
    ], this as unknown as ResizeObserver);
  }

  disconnect() {}
  unobserve() {}
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  components = [];
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('RadioLayout top-row profile switching', () => {
  // MOR-2728: the legacy VfoHeader top row and its undeclared-layout branch
  // are deleted. What stays pinned here is the deck's own scale chrome —
  // `resolveVfoLayoutProfile`/`vfoLayoutStyleVars` applied to the
  // `.receiver-deck` section — written regardless of which deck renders
  // inside it, so an undeclared id still exercises the width threshold and
  // the manual URL overrides.
  const UNDECLARED = { skinId: 'no-such-layout' };

  beforeEach(() => {
    // JSDOM defaults to 0x0 — force desktop dimensions so isMobile stays false
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
  });

  it('promotes the top row to wide profile when the deck width crosses the threshold', () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);

    const target = mountWithCleanup(RadioLayout, UNDECLARED);
    const receiverDeck = target.querySelector('.receiver-deck');

    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-frequency-size: 22px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-badge-inset-y: 3px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-strip-gap: 4px');
  });

  it('applies manual URL overrides to the shared top-row scale', () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);

    const previousUrl = window.location.href;
    window.history.replaceState({}, '', '/?vfoScale=1.05&vfoFreqScale=0.9');

    const target = mountWithCleanup(RadioLayout, UNDECLARED);
    const receiverDeck = target.querySelector('.receiver-deck');

    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-ops-badge-height: 22.05px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-badge-height: 17px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-header-badge-padding-x: 5.25px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-badge-padding-x: 6.3px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-frequency-size: 20.79px');

    window.history.replaceState({}, '', previousUrl);
  });
});
