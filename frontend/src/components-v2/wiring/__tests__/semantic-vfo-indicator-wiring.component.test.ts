/** MOR-2299 slice 1: the production dual composition partitions indicators. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

const h = vi.hoisted(() => ({
  state: null as ServerState | null, caps: null as Capabilities | null, noop: vi.fn(),
  txController: null as ManagedAppTxController | null,
  main: vi.fn(), sub: vi.fn(), equalize: vi.fn(), swap: vi.fn(), split: vi.fn(),
  dualWatch: vi.fn(), speak: vi.fn(),
}));
const group = new Proxy({}, { get: () => h.noop });

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; }, get caps() { return h.caps; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    get defaultScopeStatus() {
      return { source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false };
    },
    get radioPowerOn() { return null; },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: h.noop, onDismiss: h.noop }),
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  bindSemanticSurfaceHandlers: () => new Proxy({}, { get: (_target, family) => family === 'vfo'
    ? new Proxy({}, { get: (_vfo, handler) => ({
      onMainVfoClick: h.main, onSubVfoClick: h.sub, onEqual: h.equalize, onSwap: h.swap,
      onQuickSplit: h.split, onQuickDw: h.dualWatch,
    } as Record<PropertyKey, unknown>)[handler] ?? h.noop })
    : group }),
  getSystemHandlers: () => ({ onSpeak: h.speak }),
  getBreakInDelayControlFeedback: () => null, getPendingFrequencyHz: () => null,
  getPendingFilterSelection: () => null, getPendingNbOn: () => null,
  getPendingNrOn: () => null, getPendingPreampLevel: () => null,
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import {
  ManagedAppTxHarness, type ManagedAppTxServerSnapshot,
} from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (frequency: number) => ({ freqHz: frequency, mode: 'USB', filterNum: 1, dataMode: 0 });
function state(overrides: Partial<ServerState> = {}): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter', 'main.activeSlot', 'sub.activeSlot',
    'main.sMeter', 'sub.sMeter', 'tunerStatus', 'ritOn', 'ritTx', 'ritFreq', 'txAntenna'];
  for (const rx of ['main', 'sub']) for (const vfo of ['vfoA', 'vfoB']) {
    paths.push(`${rx}.${vfo}.freqHz`, `${rx}.${vfo}.mode`, `${rx}.${vfo}.filterNum`);
  }
  const receiver = (frequency: number) => ({ ...slot(frequency), filter: 1, activeSlot: 'A',
    vfoA: slot(frequency), vfoB: slot(frequency + 50_000), sMeter: -12 });
  return { active: 'MAIN', split: false, dualWatch: false,
    tunerStatus: 0, ritOn: false, ritTx: true, ritFreq: 0, txAntenna: 1,
    main: receiver(14_200_000), sub: receiver(7_100_000),
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])),
    ...overrides } as unknown as ServerState;
}
function caps(vfoScheme: Capabilities['vfoScheme'], receivers: number, dual = receivers === 2): Capabilities {
  const common = ['vfo_equalize', 'vfo_swap', 'split', 'speech', 'tuner', 'rit', 'xit'];
  return { model: 'fixture', scope: false, audio: false, tx: true,
    capabilities: dual ? ['dual_rx', 'dual_watch', ...common] : common, receivers, vfoScheme,
    antennas: 1,
    freqRanges: [], modes: [], filters: [], scopeSource: null, audioFftAvailable: false,
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false } } as unknown as Capabilities;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;
function render(
  capabilities: Capabilities,
  stateValue: ServerState = state(),
  txSnapshot: ManagedAppTxServerSnapshot = {},
): void {
  h.state = stateValue; h.caps = capabilities; txHarness.emitServerSnapshot(txSnapshot);
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props: { strips: 'dual' } }); flushSync();
}
const rowReceivers = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-testid="vfo-indicator-row"]')]
  .map((row) => row.dataset.indicatorReceiver);

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  for (const mock of [
    h.noop, h.main, h.sub, h.equalize, h.swap, h.split, h.dualWatch, h.speak,
  ]) mock.mockReset();
});
afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  document.body.innerHTML = '';
});

describe('production receiver-indicator partitioning', () => {
  it.each([
    ['1/single', caps('single', 1), ['MAIN']], ['1/ab', caps('ab', 1), ['MAIN']],
    ['2/ab_shared', caps('ab_shared', 2), ['MAIN', 'SUB']],
    ['2/main_sub', caps('main_sub', 2), ['MAIN', 'SUB']],
  ] as const)('%s mounts exactly one addressed row in each structural strip', (_id, capabilities, receivers) => {
    render(capabilities);
    expect(rowReceivers(target)).toEqual(receivers);
    for (const receiver of receivers) {
      expect(rowReceivers(target.querySelector(`[data-testid="channel-strip-${receiver}"]`)!))
        .toEqual([receiver]);
    }
    expect(rowReceivers(target.querySelector('[data-testid="cockpit-zone-global"]')!)).toEqual([]);
  });

  it('keeps an unavailable structural SUB present, unknown, and disabled', () => {
    render(caps('main_sub', 2, false));
    const sub = target.querySelector<HTMLElement>('[data-indicator-receiver="SUB"]')!;
    expect(rowReceivers(target)).toEqual(['MAIN', 'SUB']);
    expect(sub.dataset.indicatorOperational).toBe('false');
    expect(sub.querySelector('[data-testid="receiver-s-meter-unknown"]')).not.toBeNull();
  });

  it('mounts one singleton shared row/block and maps each production-admitted button exactly once', () => {
    render(caps('main_sub', 2));
    expect(target.querySelectorAll('[data-testid="vfo-shared-indicators"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-dual-action-block]')).toHaveLength(1);
    const cases = [
      ['main', h.main], ['sub', h.sub], ['equalize', h.equalize],
      ['swap', h.swap], ['speak', h.speak],
    ] as const;
    expect([...target.querySelectorAll<HTMLElement>('[data-dual-action]')]
      .map((button) => button.dataset.dualAction)).toEqual(cases.map(([id]) => id));
    for (const [action, selected] of cases) {
      for (const mock of [h.main, h.sub, h.equalize, h.swap, h.split, h.dualWatch, h.speak]) {
        mock.mockClear();
      }
      target.querySelector<HTMLButtonElement>(`[data-dual-action="${action}"]`)!.click();
      for (const mock of [h.main, h.sub, h.equalize, h.swap, h.split, h.dualWatch, h.speak]) {
        expect(mock).toHaveBeenCalledTimes(mock === selected ? 1 : 0);
      }
    }
    expect(target.querySelector('[data-dual-action="quick-split"]')).toBeNull();
    expect(target.querySelector('[data-dual-action="quick-dual-watch"]')).toBeNull();
  });

  it('keeps unavailable SUB and unsupported actions absent/disabled in production wiring', () => {
    render(caps('main_sub', 2, false));
    expect(target.querySelector<HTMLButtonElement>('[data-dual-action="sub"]')?.disabled).toBe(true);
    expect(target.querySelector('[data-dual-action="quick-dual-watch"]')).toBeNull();
  });

  it.each([
    ['1/single', caps('single', 1), 1], ['1/ab', caps('ab', 1), 3],
    ['2/ab_shared', caps('ab_shared', 2), 5], ['2/main_sub', caps('main_sub', 2), 5],
  ] as const)('%s keeps shared facts/actions global and absent from receiver strips', (_id, capabilities, actions) => {
    render(capabilities);
    expect(target.querySelectorAll('[data-testid="vfo-shared-indicators"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-dual-action]')).toHaveLength(actions);
    for (const strip of target.querySelectorAll('[data-testid^="channel-strip-"]')) {
      expect(strip.querySelector('[data-testid="vfo-shared-indicators"]')).toBeNull();
      expect(strip.querySelector('[data-dual-action-block]')).toBeNull();
    }
  });

  it('raw ptt and TX assignment cannot override the App receiving authority', () => {
    render(caps('main_sub', 2), state({
      ptt: true,
      txTarget: { status: 'known', receiver: 'SUB', slot: 'A', frequencyHz: 7_100_000 },
    }), { intent: 'rx', observedPtt: 'off' });
    expect(target.querySelector('[data-indicator-fact="rf-authority"]')
      ?.getAttribute('data-indicator-rf')).toBe('receiving');
  });

  it('removes capability-absent actions and keeps unavailable SUB natively disabled', () => {
    const capabilities = caps('main_sub', 2, false);
    capabilities.capabilities = capabilities.capabilities
      .filter((capability) => capability !== 'vfo_equalize' && capability !== 'speech');
    render(capabilities);
    expect(target.querySelector('[data-dual-action="equalize"]')).toBeNull();
    expect(target.querySelector('[data-dual-action="speak"]')).toBeNull();
    expect(target.querySelector<HTMLButtonElement>('[data-dual-action="sub"]')?.disabled).toBe(true);
  });
});
