import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { fromStore, writable } from 'svelte/store';
import { readFileSync } from 'node:fs';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { FrequencyRenderer } from '../../../component-kit-api/src/index';
const selectedFrequency = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('../../component-kits/activation', () => ({
  getSelectedFrequencyReadout: () => selectedFrequency.current,
}));
import Fixture from './fixtures/ReceiverInstrumentHostFixture.svelte';
import AlternateFrequencyReadoutHarness, {
  clearRetainedInteractions, retainedInteractions,
} from '../../primitives/frequency/__tests__/AlternateFrequencyReadoutHarness.svelte';
import { projectFrequencyReadout } from '../../primitives/frequency/frequency-readout';
import type { SubscribeReceiverAuthority } from '../ReceiverInstrumentHost.svelte';
type Publication = Parameters<Parameters<SubscribeReceiverAuthority>[0]>[0];
function capabilities(receivers = 2, generation = 1, scheme?: VfoScheme): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false,
    capabilities: receivers === 2 ? ['dual_rx'] : [], receivers,
    vfoScheme: scheme ?? (receivers === 2 ? 'main_sub' : 'single'),
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: generation,
  } as Capabilities;
}
const available = () => ({
  observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1,
});
function state(options: {
  receivers?: number; generation?: number; mainHz?: number; subHz?: number;
  mainS?: number; subS?: number; active?: 'MAIN' | 'SUB'; meterKnown?: boolean;
  activeKnown?: boolean; mainActiveSlot?: 'A' | 'B'; mainActiveSlotKnown?: boolean;
} = {}): ServerState {
  const {
    receivers = 2, generation = 1, mainHz = 14_250_000, subHz = 7_100_000,
    mainS = 200, subS = 78, active = 'MAIN', meterKnown = true, activeKnown = true,
    mainActiveSlot = 'A', mainActiveSlotKnown = true,
  } = options;
  const slot = (frequencyHz: number) => ({ freqHz: frequencyHz, mode: 'USB', filterNum: 1, dataMode: 0 });
  const receiver = (frequencyHz: number, sMeter: number, activeSlot: 'A' | 'B') => ({
    freqHz: frequencyHz, mode: 'USB', filter: 1, dataMode: 0, sMeter, activeSlot,
    vfoA: slot(frequencyHz), vfoB: slot(frequencyHz + 50_000),
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 100, rfGain: 255, squelch: 0,
  });
  const paths = ['main', 'main.freqHz', 'main.mode', 'main.filter',
    'main.vfoA.freqHz', 'main.vfoA.mode', 'main.vfoA.filterNum',
    'main.vfoB.freqHz', 'main.vfoB.mode', 'main.vfoB.filterNum'];
  if (activeKnown) paths.push('active');
  if (mainActiveSlotKnown) paths.push('main.activeSlot');
  if (meterKnown) paths.push('main.sMeter');
  if (receivers === 2) {
    paths.push('sub', 'sub.freqHz', 'sub.mode', 'sub.filter', 'sub.activeSlot',
      'sub.vfoA.freqHz', 'sub.vfoA.mode', 'sub.vfoA.filterNum',
      'sub.vfoB.freqHz', 'sub.vfoB.mode', 'sub.vfoB.filterNum');
    if (meterKnown) paths.push('sub.sMeter');
  }
  return {
    stateContractVersion: 1, providerGeneration: generation, revision: 1, stateRevision: 1,
    freshnessRevision: 1, observationSeq: 1, updatedAt: '2026-09-06T00:00:00Z',
    active, ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: receiver(mainHz, mainS, mainActiveSlot),
    ...(receivers === 2 ? { sub: receiver(subHz, subS, 'A') } : {}),
    connection: {} as ServerState['connection'],
    fieldStatus: Object.fromEntries(paths.map((path) => [path, available()])),
  } as ServerState;
}
function publication(options: Parameters<typeof state>[0] & {
  epoch?: number; sessionState?: Publication['session']['state'];
  capsGeneration?: number; scheme?: VfoScheme;
} = {}): Publication {
  const { epoch = 1, sessionState = 'connected', capsGeneration, scheme, ...stateOptions } = options;
  const generation = stateOptions.generation ?? 1;
  const receivers = stateOptions.receivers ?? 2;
  return { state: state(stateOptions), caps: capabilities(receivers, capsGeneration ?? generation, scheme),
    session: { state: sessionState, epoch } };
}
class Publisher {
  handlers = new Set<(next: Publication) => void>();
  constructor(public current: Publication) {}
  subscribe: SubscribeReceiverAuthority = (handler) => {
    this.handlers.add(handler); handler(this.current);
    return () => { this.handlers.delete(handler); };
  };
  emit(next: Publication): void { this.current = next; this.handlers.forEach((handler) => handler(next)); }
}
interface MotionHarness {
  readonly frames: number;
  readonly listeners: number;
  reduced(next: boolean): void;
  restore(): void;
}
function installMotionHarness(): MotionHarness {
  let reduced = false; let id = 0;
  const frames = new Map<number, FrameRequestCallback>();
  const listeners = new Set<() => void>();
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = vi.fn().mockReturnValue({
    get matches() { return reduced; },
    addEventListener: (_: string, callback: () => void) => listeners.add(callback),
    removeEventListener: (_: string, callback: () => void) => listeners.delete(callback),
  }) as unknown as typeof window.matchMedia;
  const request = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    frames.set(++id, callback); return id;
  });
  const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frame) => { frames.delete(frame); });
  return {
    get frames() { return frames.size; }, get listeners() { return listeners.size; },
    reduced(next) { reduced = next; listeners.forEach((listener) => listener()); },
    restore() { window.matchMedia = originalMatchMedia; request.mockRestore(); cancel.mockRestore(); },
  };
}
let components: ReturnType<typeof mount>[] = [];
let motion: MotionHarness;
beforeEach(() => {
  selectedFrequency.current = AlternateFrequencyReadoutHarness as FrequencyRenderer;
  clearRetainedInteractions(); motion = installMotionHarness();
});
afterEach(() => {
  components.forEach((component) => unmount(component)); components = [];
  document.body.replaceChildren(); selectedFrequency.current = undefined; motion.restore();
});
function mountFixture(publisher: Publisher, props: Record<string, unknown> = {}): HTMLElement {
  const target = document.createElement('div'); document.body.appendChild(target);
  const mountedProps = { subscribeControlAuthority: publisher.subscribe } as Record<string, unknown>;
  Object.defineProperties(mountedProps, Object.getOwnPropertyDescriptors(props));
  components.push(mount(Fixture, { target, props: mountedProps as never }));
  flushSync(); return target;
}

describe('ReceiverInstrumentHost', () => {
  const authorityChanges = [
    ['session epoch', () => publication({ epoch: 2 })],
    ['matched provider generation', () => publication({ generation: 2 })],
    ['topology', () => publication({ scheme: 'ab_shared' })],
    ['active receiver', () => publication({ active: 'SUB' })],
    ['active receiver known status', () => publication({ activeKnown: false })],
    ['receiver active slot', () => publication({ mainActiveSlot: 'B' })],
    ['receiver active slot known status', () => publication({ mainActiveSlotKnown: false })],
  ] as const;

  it.each(authorityChanges)('closes the unobserved %s A-B-A gap and steps confirmed Hz', (_name, middle) => {
    const publisher = new Publisher(publication()); const tune = vi.fn();
    const root = mountFixture(publisher, { pendingFrequencyHz: { MAIN: 14_300_000 }, onTuneFrequency: tune });
    expect(publisher.handlers.size).toBe(1);
    const oldMain = retainedInteractions()[0];
    const digit = projectFrequencyReadout({ confirmedHz: 14_250_000 }).digits.find((item) => item.multiplier === 1)!;
    oldMain.handleDigitClick(digit, new MouseEvent('click'));
    publisher.emit(middle()); publisher.emit(publication());
    const staleEvent = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
    oldMain.handleWheel(digit, staleEvent);
    expect(staleEvent.defaultPrevented).toBe(false); expect(oldMain.inert).toBe(true); expect(tune).not.toHaveBeenCalled();
    flushSync();
    const readout = root.querySelector<HTMLElement>('[data-frequency-owner="MAIN"] [data-alternate-frequency-readout]')!;
    expect(readout.dataset.source).toBe('pending');
    readout.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
    readout.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true, bubbles: true }));
    expect(tune).toHaveBeenCalledExactlyOnceWith('MAIN', 14_250_001);
  });

  it.each([
    ['invalid state generation', () => publication({ generation: -1, capsGeneration: 1, mainHz: 99_000_000 })],
    ['invalid capability generation', () => publication({ generation: 1, capsGeneration: -1, mainHz: 99_000_000 })],
    ['mismatched generations', () => publication({ generation: 1, capsGeneration: 2, mainHz: 99_000_000 })],
  ] as const)('preserves current truth through %s and recovers valid authority', (_name, invalid) => {
    const publisher = new Publisher(publication()); const tune = vi.fn();
    const root = mountFixture(publisher, { onTuneFrequency: tune });
    const oldMain = retainedInteractions()[0];
    const digit = projectFrequencyReadout({ confirmedHz: 14_250_000 }).digits.find((item) => item.multiplier === 1)!;
    oldMain.handleDigitClick(digit, new MouseEvent('click'));
    publisher.emit(invalid());
    const staleEvent = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
    oldMain.handleWheel(digit, staleEvent);
    expect(staleEvent.defaultPrevented).toBe(false); expect(oldMain.inert).toBe(true);
    expect(tune).not.toHaveBeenCalled();
    flushSync();
    const currentDigits = Array.from(root.querySelectorAll(
      '[data-frequency-owner="MAIN"] [data-alternate-frequency-readout] button',
    )).map((button) => button.textContent).join('');
    expect(currentDigits).toBe('14250000');
    expect(retainedInteractions().at(-1)?.inert).toBe(true);
    publisher.emit(publication({ generation: 2, mainHz: 18_100_000 })); flushSync();
    const recovered = root.querySelector<HTMLElement>(
      '[data-frequency-owner="MAIN"] [data-alternate-frequency-readout]',
    )!;
    recovered.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
    recovered.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(tune).toHaveBeenCalledExactlyOnceWith('MAIN', 18_100_001);
  });

  it('retains owners across irrelevant publications and keyed grouped/independent replacement', () => {
    const publisher = new Publisher(publication()); const layout = writable('grouped'); const live = fromStore(layout);
    const root = mountFixture(publisher, { get layout() { return live.current; }, get layoutKey() { return live.current; } });
    expect(motion.frames).toBe(4); expect(root.querySelectorAll('[data-vfo-operations]')).toHaveLength(1);
    const first = retainedInteractions()[0]; const count = retainedInteractions().length;
    publisher.emit(publication({ mainS: 26 })); flushSync();
    expect(retainedInteractions()).toHaveLength(count); expect(first.inert).toBe(false);
    layout.set('independent'); flushSync();
    expect(first.inert).toBe(true); expect(retainedInteractions().length).toBeGreaterThan(count);
    expect(motion.frames).toBe(4); expect(root.querySelectorAll('[data-vfo-operations]')).toHaveLength(1);
  });

  it('isolates receiver commands and recreates a disposed owner after structural removal', () => {
    const publisher = new Publisher(publication()); const tune = vi.fn(); const root = mountFixture(publisher, { onTuneFrequency: tune });
    const sub = root.querySelector<HTMLElement>('[data-frequency-owner="SUB"] [data-alternate-frequency-readout]')!;
    sub.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
    sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(tune).toHaveBeenCalledExactlyOnceWith('SUB', 7_100_001);
    publisher.emit(publication({ receivers: 1 })); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).toBeNull(); expect(motion.frames).toBe(2);
    publisher.emit(publication()); flushSync(); expect(motion.frames).toBe(4);
  });

  it('keeps selected external renderers mounted and inert while authority is disconnected', () => {
    const publisher = new Publisher(publication()); const root = mountFixture(publisher);
    const oldMain = retainedInteractions()[0];
    publisher.emit(publication({ sessionState: 'disconnected', epoch: -1 })); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).not.toBeNull();
    expect(oldMain.inert).toBe(true); expect(motion.frames).toBe(4);
  });

  it('uses the shared meter continuity and tears down every owned schedule and subscriber', () => {
    const publisher = new Publisher(publication({ mainS: 200 })); const root = mountFixture(publisher);
    const meter = () => root.querySelector<HTMLElement>('[data-meter-owner="MAIN"]')!;
    const fills = () => meter().querySelectorAll('[data-meter-fill]').length;
    const high = fills();
    publisher.emit(publication({ mainS: 26 })); flushSync();
    expect(fills()).toBe(high); expect(meter().textContent).toContain('26');
    publisher.emit(publication({ mainS: 26, epoch: 2 })); flushSync(); expect(fills()).toBeLessThan(high);
    publisher.emit(publication({ mainS: 26, generation: 2 })); flushSync(); expect(meter().textContent).toContain('26');
    publisher.emit(publication({ meterKnown: false, generation: 2 })); flushSync();
    expect(fills()).toBe(0); expect(meter().textContent).toContain('S ?');
    motion.reduced(true); expect(motion.frames).toBe(0); motion.reduced(false); expect(motion.frames).toBe(4);
    const component = components.pop()!; unmount(component);
    expect(motion.frames).toBe(0); expect(motion.listeners).toBe(0); expect(publisher.handlers.size).toBe(0);
  });

  it('requires the synchronous publisher and owns no fallback clocks or continuity comparison', () => {
    const source = readFileSync('src/semantic/ReceiverInstrumentHost.svelte', 'utf8');
    expect(source).toMatch(/subscribeControlAuthority: SubscribeReceiverAuthority/);
    expect(source).not.toMatch(/subscribeControlAuthority\?|requestAnimationFrame|setInterval|setTimeout|Date\.now/);
    expect(source).toContain('source: indicator?.sMeter.source');
    expect(source).not.toMatch(/providerGeneration === .*source|controlSessionEpoch ===/);
  });
});
