/** MOR-2299 slice 1: the production dual composition partitions indicators. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount, type ComponentProps } from 'svelte';
import { readFileSync } from 'node:fs';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';
import type { FrequencyRenderer } from '../../../../component-kit-api/src/index';

const h = vi.hoisted(() => ({
  state: null as ServerState | null, caps: null as Capabilities | null, noop: vi.fn(),
  txController: null as ManagedAppTxController | null,
  main: vi.fn(), sub: vi.fn(), equalize: vi.fn(), swap: vi.fn(), split: vi.fn(),
  dualWatch: vi.fn(), speak: vi.fn(),
  filterWidthFeedback: vi.fn(), cwPitchFeedback: vi.fn(), keySpeedFeedback: vi.fn(),
  txAuxFeedback: vi.fn(),
  session: { state: 'connected', epoch: 1 } as ControlSessionSnapshot,
  sessionSubscriber: null as ((next: ControlSessionSnapshot) => void) | null,
  authoritySubscribers: new Set<(next: {
    state: ServerState | null; caps: Capabilities | null; session: ControlSessionSnapshot;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: true, rxEnabled: false, volume: 0 },
}));
const selectedFrequency = vi.hoisted(() => ({ current: undefined as unknown }));
const group = new Proxy({}, { get: () => h.noop });

vi.mock('../../../component-kits/activation', () => ({
  getSelectedFrequencyReadout: () => selectedFrequency.current,
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; }, get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (next: ControlSessionSnapshot) => void) {
      h.sessionSubscriber = handler;
      return () => { if (h.sessionSubscriber === handler) h.sessionSubscriber = null; };
    },
    authoritySubscribers: h.authoritySubscribers,
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      this.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { this.authoritySubscribers.delete(handler); };
    },
    get audio() { return h.audio; },
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
  getDataModeArmed: () => ({ armed: false, value: null }),
  getBreakInDelayControlFeedback: () => null,
  getFilterWidthControlFeedback: h.filterWidthFeedback,
  getCwPitchControlFeedback: h.cwPitchFeedback,
  getKeySpeedControlFeedback: h.keySpeedFeedback,
  getTxAuxControlFeedback: h.txAuxFeedback,
  getPendingFrequencyHz: () => null,
  getPendingFilterSelection: () => null, getPendingNbOn: () => null,
  getPendingNrOn: () => null, getPendingPreampLevel: () => null,
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import AlternateFrequencyReadoutHarness, {
  clearRetainedInteractions, retainedInteractions,
} from '../../../primitives/frequency/__tests__/AlternateFrequencyReadoutHarness.svelte';
import { projectFrequencyReadout } from '../../../primitives/frequency/frequency-readout';
import {
  ManagedAppTxHarness, type ManagedAppTxServerSnapshot,
} from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 0,
};
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
  return { stateContractVersion: 1, providerGeneration: 1,
    active: 'MAIN', split: false, dualWatch: false,
    tunerStatus: 0, ritOn: false, ritTx: true, ritFreq: 0, txAntenna: 1,
    main: receiver(14_200_000), sub: receiver(7_100_000),
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])),
    ...overrides } as unknown as ServerState;
}
function caps(vfoScheme: Capabilities['vfoScheme'], receivers: number, dual = receivers === 2): Capabilities {
  const common = ['vfo_equalize', 'vfo_swap', 'split', 'speech', 'tuner', 'rit', 'xit'];
  return { model: 'fixture', scope: false, audio: false, tx: true,
    stateContractVersion: 1, providerGeneration: 1,
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
  props: ComponentProps<typeof SemanticRadioSurfaces> = { strips: 'dual' },
): void {
  h.state = stateValue; h.caps = capabilities; txHarness.emitServerSnapshot(txSnapshot);
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props }); flushSync();
}
function publishAuthority(): void {
  for (const subscriber of h.authoritySubscribers) subscriber({
    state: h.state, caps: h.caps, session: h.session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}
function pushSession(next: ControlSessionSnapshot): void {
  h.session = next; h.sessionSubscriber?.(next);
  publishAuthority();
  flushSync();
}
function pushMeter(value: number, providerGeneration = 1): void {
  const next = state({ providerGeneration });
  next.main!.sMeter = value;
  h.state = next;
  h.caps = { ...caps('main_sub', 2), providerGeneration };
  txHarness.emitServerSnapshot({});
  publishAuthority();
  flushSync();
}
function frequencyState(display: 'unknown' | 'current' | 'stale'): ServerState {
  const next = state();
  for (const path of ['main.freqHz', 'main.vfoA.freqHz']) {
    if (display === 'unknown') delete next.fieldStatus?.[path];
    else if (display === 'stale') next.fieldStatus![path] = {
      ...fresh, freshness: 'stale', availability: 'stale',
    };
  }
  return next;
}
function pushState(next: ServerState): void {
  h.state = next; txHarness.emitServerSnapshot({}); publishAuthority(); flushSync();
}
const rowReceivers = (root: ParentNode) => [...root.querySelectorAll<HTMLElement>('[data-testid="vfo-indicator-row"]')]
  .map((row) => row.dataset.indicatorReceiver);

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.session = { state: 'connected', epoch: 1 };
  h.sessionSubscriber = null;
  selectedFrequency.current = undefined;
  clearRetainedInteractions();
  h.filterWidthFeedback.mockReturnValue(Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable', busy: false, availability: 'unavailable',
    outcome: null, lifecycleId: null, transitionId: null, sessionEpoch: 1,
    scope: Object.freeze({ control: 'filter-width', receiver: 0 }),
    repeatPolicy: 'latest-target-wins',
  }));
  h.cwPitchFeedback.mockReturnValue(Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable', busy: false, availability: 'unavailable',
    outcome: null, lifecycleId: null, transitionId: null, sessionEpoch: 1,
    scope: Object.freeze({ control: 'cw-pitch', receiver: 0 }),
    repeatPolicy: 'latest-target-wins',
  }));
  h.keySpeedFeedback.mockReturnValue(Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable', busy: false, availability: 'unavailable',
    outcome: null, lifecycleId: null, transitionId: null, sessionEpoch: 1,
    scope: Object.freeze({ control: 'keyer-speed', receiver: 0 }),
    repeatPolicy: 'latest-target-wins',
  }));
  h.txAuxFeedback.mockImplementation((field: string) => Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable', busy: false, availability: 'unavailable',
    outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: null, sessionEpoch: h.session.epoch,
    scope: Object.freeze({ control: field, receiver: 0 as const }),
    repeatPolicy: 'latest-target-wins',
  }));
  for (const mock of [
    h.noop, h.main, h.sub, h.equalize, h.swap, h.split, h.dualWatch, h.speak,
  ]) mock.mockReset();
});
afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  expect(h.sessionSubscriber).toBeNull();
  expect(h.authoritySubscribers.size).toBe(0);
  selectedFrequency.current = undefined;
  clearRetainedInteractions();
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('production receiver-indicator partitioning', () => {
  it.each([
    ['dual semantic receiver strip', { strips: 'dual' }],
    ['single semantic VFO surface', { strips: 'single' }],
    ['live Standard composition', { strips: 'single', vfoAppearance: 'standard' }],
  ] as const)('keeps one %s renderer through unknown/current/stale/current', (_name, props) => {
    selectedFrequency.current = AlternateFrequencyReadoutHarness as FrequencyRenderer;
    render(caps('main_sub', 2), frequencyState('unknown'), {}, props);
    const selector = '[data-vfo-receiver="MAIN"] [data-alternate-frequency-readout]';
    const renderer = target.querySelector<HTMLElement>(selector)!;
    expect(renderer).not.toBeNull();
    expect(renderer.getAttribute('aria-disabled')).toBe('true');
    const cardinality = () => [
      target.querySelectorAll('[data-testid="vfo-surface"]').length,
      target.querySelectorAll('[data-testid="vfo-ops"]').length,
    ];
    const initialCardinality = cardinality();

    pushState(frequencyState('current'));
    expect(target.querySelector(selector)).toBe(renderer);
    const digit = renderer.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!;
    digit.click();
    renderer.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    const currentCalls = h.noop.mock.calls.length;
    expect(currentCalls).toBeGreaterThan(0);

    pushState(frequencyState('stale'));
    expect(target.querySelector(selector)).toBe(renderer);
    expect(renderer.textContent).toContain('14200000');
    const staleWheel = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
    renderer.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.dispatchEvent(staleWheel);
    expect(renderer.getAttribute('aria-disabled')).toBe('true');
    expect(staleWheel.defaultPrevented).toBe(false);
    expect(h.noop).toHaveBeenCalledTimes(currentCalls);
    expect(cardinality()).toEqual(initialCardinality);

    pushState(frequencyState('current'));
    expect(target.querySelector(selector)).toBe(renderer);
    expect(renderer.getAttribute('aria-disabled')).toBe('false');
  });

  it.each([
    ['grouped Standard', { strips: 'single', vfoAppearance: 'standard' }, 1, 'standard'],
    ['independent dual SDR', { strips: 'dual', vfoAppearance: 'sdr' }, 3, 'sdr'],
  ] as const)('renders one operation group and status in %s', (_name, props, surfaces, appearance) => {
    render(caps('main_sub', 2), state(), {}, props);
    expect(target.querySelectorAll('[data-testid="vfo-surface"]')).toHaveLength(surfaces);
    expect(target.querySelectorAll('[data-testid="vfo-active-receiver"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="vfo-ops"]')).toHaveLength(1);
    expect(target.querySelector('[data-vfo-operation-appearance]')?.getAttribute('data-vfo-operation-appearance')).toBe(appearance);
  });

  it.each([
    ['dual receiver strip', { strips: 'dual' }],
    ['single VFO surface', { strips: 'single' }],
    ['live Standard composition', { strips: 'single', vfoAppearance: 'standard' }],
  ] as const)('closes the %s before-flush A-B-A gap and rotates provider identity', (
    _name, props,
  ) => {
    selectedFrequency.current = AlternateFrequencyReadoutHarness as FrequencyRenderer;
    render(caps('main_sub', 2), state(), {}, props);
    expect(h.authoritySubscribers.size).toBe(2);
    const digit = projectFrequencyReadout({ confirmedHz: 14_200_000 }).digits[0];
    const first = retainedInteractions().find((interaction) => !interaction.inert)!;
    first.handleDigitClick(digit, new MouseEvent('click'));
    expect(first.selectedDigitIndex).toBe(digit.digitIndex);
    h.session = { state: 'connected', epoch: 2 }; publishAuthority();
    h.session = { state: 'connected', epoch: 1 }; publishAuthority();
    const callsAfterSession = h.noop.mock.calls.length;
    const staleSessionWheel = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
    first.handleDigitClick(digit, new MouseEvent('click'));
    first.handleWheel(digit, staleSessionWheel);
    expect(staleSessionWheel.defaultPrevented).toBe(false);
    expect(first.inert).toBe(true);
    expect(h.noop).toHaveBeenCalledTimes(callsAfterSession);

    flushSync();
    const second = retainedInteractions().find((interaction) => !interaction.inert)!;
    expect(second).not.toBe(first);
    second.handleDigitClick(digit, new MouseEvent('click'));
    expect(second.selectedDigitIndex).toBe(digit.digitIndex);
    const freshKey = new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true });
    second.handleKeyDown(freshKey);
    expect(freshKey.defaultPrevented).toBe(true);
    expect(h.noop).toHaveBeenCalledTimes(callsAfterSession + 1);
    pushMeter(50, 2);
    expect(second.inert).toBe(true);
    const callsAfterProvider = h.noop.mock.calls.length;
    const staleProviderKey = new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true });
    second.handleDigitClick(digit, new MouseEvent('click'));
    second.handleKeyDown(staleProviderKey);
    expect(staleProviderKey.defaultPrevented).toBe(false);
    expect(h.noop).toHaveBeenCalledTimes(callsAfterProvider);
  });

  it.each([
    ['dual semantic receiver strip', { strips: 'dual' }],
    ['live Standard composition', { strips: 'single', vfoAppearance: 'standard' }],
  ] as const)('re-seeds %s at equal-value session and provider boundaries', (_name, props) => {
    vi.stubGlobal('matchMedia', (query: string): MediaQueryList => ({
      matches: query === '(prefers-reduced-motion: reduce)', media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(() => false),
    }));
    render(caps('main_sub', 2), state(), {}, props);
    const meter = () => target.querySelector('[data-indicator-receiver="MAIN"] [data-testid="receiver-s-meter"] svg')!;
    const fillCount = () => meter().querySelectorAll('[data-meter-fill]').length;
    const hasPeak = () => meter().querySelector('[data-meter-peak]') !== null;
    const arm = (generation = 1) => {
      pushMeter(240, generation); pushMeter(60, generation);
      expect(hasPeak()).toBe(true);
      return fillCount();
    };

    const epochFill = arm();
    pushSession({ state: 'connected', epoch: 2 });
    expect(fillCount()).toBe(epochFill);
    expect(hasPeak()).toBe(false);

    const generationFill = arm();
    pushMeter(60, 2);
    expect(fillCount()).toBe(generationFill);
    expect(hasPeak()).toBe(false);

    const reconnectFill = arm(2);
    pushSession({ state: 'disconnected', epoch: 3 });
    expect(fillCount()).toBe(0);
    expect(hasPeak()).toBe(false);
    pushSession({ state: 'connected', epoch: 4 });
    expect(fillCount()).toBe(reconnectFill);
    expect(hasPeak()).toBe(false);
  });

  it.each([
    ['dual semantic receiver strip', { strips: 'dual' }],
    ['live Standard composition', { strips: 'single', vfoAppearance: 'standard' }],
  ] as const)('stops every %s meter scheduler and the control-session join on unmount', (_name, props) => {
    vi.stubGlobal('matchMedia', (query: string): MediaQueryList => ({
      matches: false, media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(),
      addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(() => false),
    }));
    let nextFrameId = 0;
    const requestedFrameIds = new Set<number>();
    const outstandingFrameIds = new Set<number>();
    const cancelledFrameIds = new Set<number>();
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => {
      const frameId = ++nextFrameId;
      requestedFrameIds.add(frameId);
      outstandingFrameIds.add(frameId);
      return frameId;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frameId) => {
      cancelledFrameIds.add(frameId);
      outstandingFrameIds.delete(frameId);
    });
    render(caps('main_sub', 2), state(), {}, props);
    expect(requestedFrameIds.size).toBeGreaterThan(0);
    expect(outstandingFrameIds).toEqual(requestedFrameIds);
    unmount(component!);
    component = null;
    expect(h.sessionSubscriber).toBeNull();
    expect(outstandingFrameIds).toEqual(new Set());
    expect(cancelledFrameIds).toEqual(requestedFrameIds);
  });

  it('passes the complete Filter Width feedback projection through production wiring', () => {
    const source = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
    expect(source).toMatch(
      /filterWidthFeedback\s*=\s*\$derived\(getFilterWidthControlFeedback\(\)\)/,
    );
    expect(source).toMatch(/<FilterSurface[\s\S]*?\{filterWidthFeedback\}[\s\S]*?\/>/);
    expect(h.filterWidthFeedback()).toMatchObject({
      phase: 'unavailable', availability: 'unavailable',
      scope: { control: 'filter-width', receiver: 0 },
    });
  });

  it('keeps the complete panel-adapter facade while wiring both CW lanes to the live session', () => {
    const source = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
    expect(source).toMatch(
      /cwPitchFeedback\s*=\s*\$derived\(getCwPitchControlFeedback\(controlSession\)\)/,
    );
    expect(source).toMatch(
      /keySpeedFeedback\s*=\s*\$derived\(getKeySpeedControlFeedback\(controlSession\)\)/,
    );
    expect(source).toMatch(
      /<CwKeyerSurface[\s\S]*?\{cwPitchFeedback\}[\s\S]*?\{keySpeedFeedback\}[\s\S]*?\/>/,
    );
    expect(h.cwPitchFeedback()).toMatchObject({
      phase: 'unavailable', scope: { control: 'cw-pitch', receiver: 0 },
    });
    expect(h.keySpeedFeedback()).toMatchObject({
      phase: 'unavailable', scope: { control: 'keyer-speed', receiver: 0 },
    });
  });

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
      expect(target.querySelector(
        `[data-indicator-receiver="${receiver}"] [data-testid="receiver-s-meter-unknown"]`,
      )).toBeNull();
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

  it.each([
    ['semantic', { strips: 'dual' }],
    ['Standard', { strips: 'single', vfoAppearance: 'standard' }],
  ] as const)('matches unavailable SUB wrapper metadata to its inert renderer in %s', (_name, props) => {
    render(caps('main_sub', 2, false), state(), {}, props);
    const instrument = target.querySelector<HTMLElement>(props.strips === 'dual'
      ? '[data-testid="channel-strip-SUB"]' : '[data-receiver-instrument="SUB"]')!;
    const frequency = instrument.querySelector<HTMLElement>('[data-vfo-freq]')!;
    expect(frequency.dataset.freqTunable).toBe('false');
    expect(frequency.querySelector('.freq')?.getAttribute('aria-disabled')).toBe('true');
    expect(frequency.querySelector('.freq')?.getAttribute('tabindex')).toBe('-1');
  });

  it('keeps both S-meter shells mounted but unknown across a provider mismatch', () => {
    render({ ...caps('main_sub', 2), providerGeneration: 2 });
    expect(rowReceivers(target)).toEqual(['MAIN', 'SUB']);
    expect(target.querySelectorAll('[data-testid="receiver-s-meter-unknown"]')).toHaveLength(2);
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
