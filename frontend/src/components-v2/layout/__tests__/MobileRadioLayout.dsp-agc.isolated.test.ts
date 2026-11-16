/**
 * MOR-4528 — the phone DSP tab hosts the AGC choice.
 *
 * The real `MobileRadioLayout` is mounted with its real children, the real
 * projections and the real command path; only the spectrum pane, the icons,
 * the stores (radio state, capabilities) and the WebSocket send are fixtures.
 * The radios below are capability sets shaped like the shipped profiles'
 * `[agc]` tables (`rigs/ic705.toml`, `rigs/x6200.toml`) and a profile that
 * declares no AGC at all (`rigs/tx500.toml`).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { Capabilities } from '$lib/types/capabilities';

const txHarness = new ManagedAppTxHarness();

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return {
    Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S,
    Sliders: S, Radio: S, Mic: S, MicOff: S,
  };
});
// ── Stores: the radio under test ───────────────────────────────────────────
const radioStore = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
const capsStore = vi.hoisted(() => ({ current: null as Record<string, unknown> | null }));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: radioStore,
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(() => radioStore.current),
  subscribeRadioState: vi.fn((handler: (state: Record<string, unknown> | null) => void) => {
    handler(radioStore.current);
    return () => {};
  }),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'connected'),
  getRadioPowerOn: vi.fn(() => null),
  getWsConnected: vi.fn(() => true),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => 'ok'),
  isAudioConnected: vi.fn(() => false),
  getRadioLinkState: vi.fn(() => 'connected'),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    volume: 50, muted: false, rxEnabled: false, txEnabled: false,
    micEnabled: false, bridgeRunning: false,
  })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    onTxAudioDied: () => () => {},
    setOperatorNotifier: vi.fn(),
    start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(),
  },
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => false), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => capsStore.current),
  getScopeSource: vi.fn(() => null),
  subscribeCapabilities: vi.fn((handler: (caps: Record<string, unknown> | null) => void) => {
    handler(capsStore.current);
    return () => {};
  }),
  getKeyboardConfig: vi.fn(() => null),
  hasCapability: vi.fn((name: string) =>
    (capsStore.current?.capabilities as string[] | undefined)?.includes(name) ?? false),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  hasAudioFft: vi.fn(() => false),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));

// ── The wire: every command the UI sends lands here ────────────────────────
const wsFrameSpy = vi.hoisted(() => vi.fn(() => true));
vi.mock('$lib/transport/ws-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/ws-client')>(),
  sendCommand: wsFrameSpy,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import AgcPanel from '../../panels/AgcPanel.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

interface RadioFixture {
  readonly name: string;
  readonly caps: Capabilities;
  /** The AGC keys this radio must offer, in profile order. */
  readonly keys: readonly string[];
}

function caps(capabilities: string[], agc?: { modes: number[]; labels: Record<string, string> }): Capabilities {
  return {
    freqRanges: [], modes: ['USB', 'LSB'], filters: ['FIL1', 'FIL2'],
    stateContractVersion: 1, providerGeneration: 1,
    receivers: 1, vfoScheme: 'ab', capabilities,
    ...(agc ? { agcModes: agc.modes, agcLabels: agc.labels } : {}),
  } as unknown as Capabilities;
}

const IC705: RadioFixture = {
  name: 'IC-705-shaped (FAST/MID/SLOW, no OFF)',
  caps: caps(['agc', 'nb', 'nr'], { modes: [1, 2, 3], labels: { 1: 'FAST', 2: 'MID', 3: 'SLOW' } }),
  keys: ['FAST', 'MID', 'SLOW'],
};
const X6200: RadioFixture = {
  name: 'X6200-shaped (OFF/FAST/SLOW/AUTO)',
  caps: caps(['agc', 'nb', 'nr'], {
    modes: [0, 1, 2, 3], labels: { 0: 'OFF', 1: 'FAST', 2: 'SLOW', 3: 'AUTO' },
  }),
  keys: ['OFF', 'FAST', 'SLOW', 'AUTO'],
};
const NO_AGC: RadioFixture = {
  name: 'a radio whose profile declares no AGC',
  caps: caps(['nb', 'nr']),
  keys: [],
};

/** Tune the fixture stores to one radio reporting `agc` on its receiver. */
function connect(radio: RadioFixture, agc?: number): void {
  capsStore.current = radio.caps as unknown as Record<string, unknown>;
  const seen = { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1 };
  radioStore.current = {
    active: 'MAIN', providerGeneration: 1,
    main: {
      freqHz: 7_215_000, mode: 'LSB', filter: 1, dataMode: 0, activeSlot: 'A',
      vfoA: {}, vfoB: {}, ...(agc === undefined ? {} : { agc }),
    },
    connection: {},
    fieldStatus: agc === undefined ? {} : { 'main.agc': seen },
  };
}

let hosts: HTMLElement[] = [];
let instances: Record<string, unknown>[] = [];

function mountInto(component: typeof MobileRadioLayout | typeof AgcPanel): HTMLElement {
  const host = document.createElement('div');
  document.body.appendChild(host);
  hosts.push(host);
  instances.push(mount(component as typeof MobileRadioLayout, { target: host }));
  flushSync();
  return host;
}

/** Mount the phone layout in portrait and open its DSP tab. */
function openPhoneDspTab(): HTMLElement {
  const root = mountInto(MobileRadioLayout);
  const chip = [...root.querySelectorAll<HTMLButtonElement>('.m-chip')]
    .find((el) => el.getAttribute('aria-controls') === 'm-chip-panel-dsp');
  expect(chip, 'the DSP chip').toBeDefined();
  chip!.click();
  flushSync();
  const panel = root.querySelector<HTMLElement>('#m-chip-panel-dsp');
  expect(panel, 'the DSP tab panel').not.toBeNull();
  return panel!;
}

const agcPanelIn = (tab: HTMLElement) =>
  tab.querySelector<HTMLElement>('[data-panel-id="m-agc-chip"]');
const keysIn = (scope: HTMLElement) => [...scope.querySelectorAll<HTMLButtonElement>('button')]
  .filter((b) => !b.classList.contains('panel-header'));
const labels = (keys: HTMLButtonElement[]) => keys.map((k) => k.textContent?.trim());
const lit = (keys: HTMLButtonElement[]) =>
  keys.filter((k) => k.getAttribute('aria-pressed') === 'true').map((k) => k.textContent?.trim());

const originalWidth = window.innerWidth;
const originalHeight = window.innerHeight;
function setViewport(width: number, height: number): void {
  Object.defineProperty(window, 'innerWidth', { value: width, configurable: true, writable: true });
  Object.defineProperty(window, 'innerHeight', { value: height, configurable: true, writable: true });
}

beforeEach(() => {
  txHarness.reset();
  setViewport(390, 844);
  radioStore.current = null;
  capsStore.current = null;
  wsFrameSpy.mockClear();
  resetCommandLifecycle();
});

afterEach(() => {
  instances.forEach((instance) => unmount(instance));
  instances = [];
  hosts.forEach((host) => host.remove());
  hosts = [];
  setViewport(originalWidth, originalHeight);
});

describe('phone DSP tab AGC choice (MOR-4528)', () => {
  it.each([IC705, X6200])('offers exactly the AGC modes of $name', (radio) => {
    connect(radio, radio.caps.agcModes![1]);
    const agc = agcPanelIn(openPhoneDspTab());
    expect(agc, 'the AGC panel in the DSP tab').not.toBeNull();
    expect(labels(keysIn(agc!))).toEqual(radio.keys);
  });

  it('draws no AGC panel for a radio whose profile declares no AGC', () => {
    connect(NO_AGC);
    const tab = openPhoneDspTab();
    expect(agcPanelIn(tab)).toBeNull();
    expect(tab.querySelectorAll('.collapsible-panel')).toHaveLength(1);
  });

  it.each([
    { radio: IC705, reported: 1, key: 'FAST' },
    { radio: IC705, reported: 3, key: 'SLOW' },
    { radio: X6200, reported: 0, key: 'OFF' },
    { radio: X6200, reported: 3, key: 'AUTO' },
  ])('lights $key when the $radio.name radio reports AGC $reported', ({ radio, reported, key }) => {
    connect(radio, reported);
    expect(lit(keysIn(agcPanelIn(openPhoneDspTab())!))).toEqual([key]);
  });

  it.each([
    { radio: IC705, reported: 1, choose: 'SLOW', mode: 3 },
    { radio: X6200, reported: 1, choose: 'OFF', mode: 0 },
  ])('choosing $choose on $radio.name sends the desktop control\'s command', ({ radio, reported, choose, mode }) => {
    connect(radio, reported);

    // The desktop control: the sidebar's own `AgcPanel`, mounted bare.
    const desktop = mountInto(AgcPanel);
    keysIn(desktop).find((k) => k.textContent?.trim() === choose)!.click();
    expect(wsFrameSpy).toHaveBeenCalledTimes(1);
    const [desktopName, desktopParams] = wsFrameSpy.mock.calls[0] as unknown as [string, unknown];
    expect(desktopName).toBe('set_agc');
    expect(desktopParams).toEqual({ mode, receiver: 0 });

    wsFrameSpy.mockClear();
    resetCommandLifecycle();
    const phone = agcPanelIn(openPhoneDspTab())!;
    keysIn(phone).find((k) => k.textContent?.trim() === choose)!.click();
    expect(wsFrameSpy).toHaveBeenCalledTimes(1);
    const [phoneName, phoneParams] = wsFrameSpy.mock.calls[0] as unknown as [string, unknown];
    expect([phoneName, phoneParams]).toEqual([desktopName, desktopParams]);
  });

  it('keeps one AGC home on the phone: the SETUP sheet no longer carries it', () => {
    connect(IC705, 2);
    const root = mountInto(MobileRadioLayout);
    root.querySelector<HTMLButtonElement>('.m-settings-btn')!.click();
    flushSync();
    expect(document.body.querySelector('[data-panel-id="m-rit"]'), 'the SETUP sheet is open')
      .not.toBeNull();
    expect(document.body.querySelector('[data-panel-id="m-agc"]')).toBeNull();
  });
});
