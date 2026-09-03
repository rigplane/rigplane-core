/** MOR-2299 slice 1: the production dual composition partitions indicators. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown, caps: null as unknown, noop: vi.fn(), listeners: new Set<() => void>(),
}));
const group = new Proxy({}, { get: () => h.noop });

vi.mock('$lib/runtime', () => ({
  runtime: {
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
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => ({ phase: 'idle', intent: null, guard: null, radioTx: 'off',
      txRisk: 'none', mayOwnKey: false, fault: null }),
    subscribe: (listener: () => void) => { h.listeners.add(listener); return () => h.listeners.delete(listener); },
    start: h.noop, setIntent: h.noop, release: h.noop, resetFault: h.noop,
  }),
}));
vi.mock('$lib/runtime/tx-controller/model', () => ({ txFaultObligation: () => null }));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: h.noop, onDismiss: h.noop }),
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  bindSemanticSurfaceHandlers: () => new Proxy({}, { get: () => group }),
  getBreakInDelayControlFeedback: () => null, getPendingFrequencyHz: () => null,
  getPendingFilterSelection: () => null, getPendingNbOn: () => null,
  getPendingNrOn: () => null, getPendingPreampLevel: () => null,
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (frequency: number) => ({ freqHz: frequency, mode: 'USB', filterNum: 1, dataMode: 0 });
function state(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter', 'main.activeSlot', 'sub.activeSlot',
    'main.sMeter', 'sub.sMeter'];
  for (const rx of ['main', 'sub']) for (const vfo of ['vfoA', 'vfoB']) {
    paths.push(`${rx}.${vfo}.freqHz`, `${rx}.${vfo}.mode`, `${rx}.${vfo}.filterNum`);
  }
  const receiver = (frequency: number) => ({ ...slot(frequency), filter: 1, activeSlot: 'A',
    vfoA: slot(frequency), vfoB: slot(frequency + 50_000), sMeter: -12 });
  return { active: 'MAIN', split: false, dualWatch: false,
    main: receiver(14_200_000), sub: receiver(7_100_000),
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])) } as unknown as ServerState;
}
function caps(vfoScheme: Capabilities['vfoScheme'], receivers: number, dual = receivers === 2): Capabilities {
  return { model: 'fixture', scope: false, audio: false, tx: false,
    capabilities: dual ? ['dual_rx'] : [], receivers, vfoScheme,
    freqRanges: [], modes: [], filters: [], scopeSource: null, audioFftAvailable: false,
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false } } as unknown as Capabilities;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
function render(capabilities: Capabilities): void {
  h.state = state(); h.caps = capabilities;
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props: { strips: 'dual' } }); flushSync();
}
const rowReceivers = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-testid="vfo-indicator-row"]')]
  .map((row) => row.dataset.indicatorReceiver);

beforeEach(() => { h.listeners.clear(); h.noop.mockReset(); });
afterEach(() => { if (component) unmount(component); component = null; document.body.innerHTML = ''; });

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
});
