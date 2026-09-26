import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { fromStore, writable } from 'svelte/store';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { FrequencyRenderer, MeterAppearance } from '../../../component-kit-api/src/index';
const selectedFrequency = vi.hoisted(() => ({ current: undefined as unknown }));
const selectedMeter = vi.hoisted(() => ({ current: undefined as MeterAppearance | undefined }));
vi.mock('../../component-kits/activation', () => ({
  getSelectedFrequencyReadout: () => selectedFrequency.current,
  getSelectedMeterAppearance: () => selectedMeter.current,
}));
import Fixture, {
  FIXTURE_LEVEL_SOURCE_SHA256, FIXTURE_SIGNAL_SOURCE_SHA256,
  FIXTURE_TARBALL_SHA256, fixtureMeterAppearance,
} from './fixtures/ReceiverInstrumentHostFixture.svelte';
import AlternateFrequencyReadoutHarness, {
  clearRetainedInteractions, retainedInteractions,
} from '../../primitives/frequency/__tests__/AlternateFrequencyReadoutHarness.svelte';
import { projectFrequencyReadout } from '../../primitives/frequency/frequency-readout';
import type { SubscribeReceiverAuthority } from '../ReceiverInstrumentHost.svelte';
import { buildPowerLowerScale } from '../ReceiverInstrumentHost.svelte';
import type { MetersViewModel } from '../radio-view-model';
type Publication = Parameters<Parameters<SubscribeReceiverAuthority>[0]>[0];
function capabilities(receivers = 2, generation = 1, scheme?: VfoScheme): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false,
    capabilities: receivers === 2 ? ['dual_rx'] : [], receivers,
    vfoScheme: scheme ?? (receivers === 2 ? 'main_sub' : 'single'),
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    meterCalibrations: { s_meter: [
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 130, actual: 0, label: 'S9' },
      { raw: 240, actual: 40, label: 'S9+40' },
    ] },
    stateContractVersion: 1, providerGeneration: generation,
  } as Capabilities;
}
const available = () => ({
  observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1,
});
function state(options: {
  receivers?: number; generation?: number; mainHz?: number; subHz?: number;
  mainS?: number; subS?: number; active?: 'MAIN' | 'SUB'; meterKnown?: boolean;
  meterQuality?: readonly string[];
  activeKnown?: boolean; mainActiveSlot?: 'A' | 'B'; mainActiveSlotKnown?: boolean;
} = {}): ServerState {
  const {
    receivers = 2, generation = 1, mainHz = 14_250_000, subHz = 7_100_000,
    mainS = 20, subS = -24, active = 'MAIN', meterKnown = true,
    meterQuality = ['calibrated'], activeKnown = true,
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
    fieldStatus: Object.fromEntries(paths.map((path) => [
      path,
      path === 'main.sMeter' || path === 'sub.sMeter'
        ? { ...available(), quality: meterQuality } : available(),
    ])),
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
  const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((frame) => {
    frames.delete(frame);
  });
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
  selectedMeter.current = undefined;
  clearRetainedInteractions(); motion = installMotionHarness();
  expect(setCapabilities(capabilities())).toBe(true);
});
let originalClientWidth: PropertyDescriptor | undefined;
afterEach(() => {
  components.forEach((component) => unmount(component)); components = [];
  document.body.replaceChildren(); selectedFrequency.current = undefined; motion.restore();
  selectedMeter.current = undefined;
  if (originalClientWidth) {
    Object.defineProperty(Element.prototype, 'clientWidth', originalClientWidth);
    originalClientWidth = undefined;
  }
  vi.unstubAllGlobals();
  clearCapabilities();
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
  ] as const;

  const slotAuthorityChanges = [
    ['receiver active slot', () => publication({ receivers: 1, scheme: 'ab', mainActiveSlot: 'B' })],
    ['receiver active slot known status', () => publication({ receivers: 1, scheme: 'ab', mainActiveSlotKnown: false })],
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

  it.each(slotAuthorityChanges)('closes the unobserved %s A-B-A gap and steps confirmed Hz', (_name, middle) => {
    const publisher = new Publisher(publication({ receivers: 1, scheme: 'ab' })); const tune = vi.fn();
    const root = mountFixture(publisher, { pendingFrequencyHz: { MAIN: 14_300_000 }, onTuneFrequency: tune });
    expect(publisher.handlers.size).toBe(1);
    const oldMain = retainedInteractions()[0];
    const digit = projectFrequencyReadout({ confirmedHz: 14_250_000 }).digits.find((item) => item.multiplier === 1)!;
    oldMain.handleDigitClick(digit, new MouseEvent('click'));
    publisher.emit(middle()); publisher.emit(publication({ receivers: 1, scheme: 'ab' }));
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

  it.each([
    ['disconnected', 'disconnected', -1, 1, 1, true],
    ['connecting', 'connecting', 1, 1, 1, true],
    ['reconnecting', 'reconnecting', 1, 1, 1, true],
    ['invalid epoch', 'connected', -1, 1, 1, true],
    ['invalid state generation', 'connected', 1, -1, 1, false],
    ['invalid capability generation', 'connected', 1, 1, -1, false],
    ['mismatched generations', 'connected', 1, 1, 2, false],
  ] as const)('installs inert structural owners for initial %s authority',
    (_name, sessionState, epoch, generation, capsGeneration, readingsKnown) => {
      const publisher = new Publisher(publication({ sessionState, epoch, generation, capsGeneration }));
      const tune = vi.fn(); const root = mountFixture(publisher, { onTuneFrequency: tune });
      expect(root.querySelector('[data-frequency-owner="SUB"]')).not.toBeNull();
      expect(root.querySelector('[data-frequency-owner="MAIN"]')?.getAttribute('data-frequency-tunable')).toBe('false');
      expect(root.querySelector('[data-frequency-owner="SUB"]')?.getAttribute('data-frequency-tunable')).toBe('false');
      expect(root.querySelectorAll('[data-frequency-owner="MAIN"] button')).toHaveLength(readingsKnown ? 8 : 0);
      expect(retainedInteractions()[0].inert).toBe(true);
      publisher.emit(publication({ generation: 2, mainHz: 18_100_000 })); flushSync();
      expect(root.querySelector('[data-frequency-owner="MAIN"]')?.getAttribute('data-frequency-tunable')).toBe('true');
      expect(root.querySelector('[data-frequency-owner="SUB"]')?.getAttribute('data-frequency-tunable')).toBe('true');
      const recovered = root.querySelector<HTMLElement>('[data-frequency-owner="MAIN"] [data-alternate-frequency-readout]')!;
      recovered.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
      recovered.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
      expect(tune).toHaveBeenCalledExactlyOnceWith('MAIN', 18_100_001);
    });

  it('publishes exact per-receiver operational, authority, and value tunability', () => {
    const publisher = new Publisher(publication()); const root = mountFixture(publisher);
    const tunable = (receiver: 'MAIN' | 'SUB') => root.querySelector(
      `[data-frequency-owner="${receiver}"]`,
    )?.getAttribute('data-frequency-tunable');
    expect([tunable('MAIN'), tunable('SUB')]).toEqual(['true', 'true']);
    const unavailable = publication();
    publisher.emit({ ...unavailable, caps: {
      ...unavailable.caps!, capabilities: unavailable.caps!.capabilities.filter((tag) => tag !== 'dual_rx'),
    } }); flushSync();
    expect([tunable('MAIN'), tunable('SUB')]).toEqual(['true', 'false']);
    publisher.emit(publication({ sessionState: 'disconnected', epoch: -1 })); flushSync();
    expect([tunable('MAIN'), tunable('SUB')]).toEqual(['false', 'false']);
    publisher.emit(publication({ mainHz: Number.NaN })); flushSync();
    expect([tunable('MAIN'), tunable('SUB')]).toEqual(['false', 'true']);
  });

  it('reconciles a disconnected capability topology change', () => {
    const publisher = new Publisher(publication({ receivers: 1, sessionState: 'disconnected', epoch: -1 }));
    const root = mountFixture(publisher); expect(root.querySelector('[data-frequency-owner="SUB"]')).toBeNull();
    publisher.emit(publication({ sessionState: 'disconnected', epoch: -1 })); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).not.toBeNull();
  });

  it('retains owners across irrelevant publications and keyed grouped/independent replacement', () => {
    const publisher = new Publisher(publication()); const layout = writable('grouped'); const live = fromStore(layout);
    const root = mountFixture(publisher, { get layout() { return live.current; }, get layoutKey() { return live.current; } });
    expect(motion.frames).toBe(4); expect(root.querySelectorAll('[data-vfo-operations]')).toHaveLength(1);
    const meterFrames = () => Array.from(root.querySelectorAll('[data-meter-frame]'))
      .map((node) => node.getAttribute('data-meter-frame'));
    const initialMeterFrames = meterFrames();
    // MOR-2521: fill rects are permanent nodes; count the lit ones.
    const meterFills = () => [...root.querySelectorAll<SVGRectElement>('[data-meter-owner="MAIN"] [data-meter-fill]')]
      .filter((rect) => rect.getAttribute('visibility') !== 'hidden').length;
    expect(initialMeterFrames).toHaveLength(2); expect(new Set(initialMeterFrames).size).toBe(2);
    const retainedFill = meterFills();
    const first = retainedInteractions()[0]; const count = retainedInteractions().length;
    publisher.emit(publication({ mainS: 26 })); flushSync();
    expect(meterFills()).toBe(retainedFill);
    expect(retainedInteractions()).toHaveLength(count); expect(first.inert).toBe(false);
    layout.set('independent'); flushSync();
    expect(first.inert).toBe(true); expect(retainedInteractions().length).toBeGreaterThan(count);
    expect(motion.frames).toBe(4); expect(meterFrames()).toEqual(initialMeterFrames);
    expect(meterFills()).toBe(retainedFill);
    expect(root.querySelectorAll('[data-vfo-operations]')).toHaveLength(1);
  });

  it('isolates receiver commands and recreates a disposed owner after structural removal', () => {
    const publisher = new Publisher(publication()); const tune = vi.fn(); const root = mountFixture(publisher, { onTuneFrequency: tune });
    const sub = root.querySelector<HTMLElement>('[data-frequency-owner="SUB"] [data-alternate-frequency-readout]')!;
    sub.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
    sub.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(tune).toHaveBeenCalledExactlyOnceWith('SUB', 7_100_001);
    publisher.emit(publication({ receivers: 1 })); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).toBeNull();
    expect(root.querySelector('[data-meter-owner="SUB"]')).toBeNull(); expect(motion.frames).toBe(2);
    publisher.emit(publication()); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).not.toBeNull();
    expect(root.querySelector('[data-meter-owner="SUB"]')).not.toBeNull(); expect(motion.frames).toBe(4);
  });

  it('keeps selected external renderers mounted and inert while authority is disconnected', () => {
    const publisher = new Publisher(publication()); const root = mountFixture(publisher);
    const oldMain = retainedInteractions()[0];
    publisher.emit(publication({ sessionState: 'disconnected', epoch: -1 })); flushSync();
    expect(root.querySelector('[data-frequency-owner="SUB"]')).not.toBeNull();
    expect(oldMain.inert).toBe(true); expect(motion.frames).toBe(4);
    const component = components.pop()!; unmount(component);
    expect(motion.frames).toBe(0); expect(motion.listeners).toBe(0); expect(publisher.handlers.size).toBe(0);
  });

  // MOR-2509: the fixture's meters are the v7 VFO face — pixel-locked
  // dash lines whose lit extent is a position, not a count of rects. jsdom
  // supplies neither ResizeObserver nor layout width, so these tests stub
  // both before mounting.
  function installMeterGeometry(): void {
    vi.stubGlobal('ResizeObserver', class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    });
    originalClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
    Object.defineProperty(Element.prototype, 'clientWidth', {
      configurable: true,
      get: () => 606,
    });
  }

  it('uses shared meter continuity for sample, session, source, and unknown transitions', () => {
    installMeterGeometry();
    const publisher = new Publisher(publication({ mainS: 20 })); const root = mountFixture(publisher);
    const meter = () => root.querySelector<HTMLElement>('[data-meter-owner="MAIN"]')!;
    // MOR-2509: the fill is a dash-patterned line; the lit extent is the
    // visible zone lines' end position on the drawn track (fraction 0..1).
    const fills = () => {
      const svg = meter().querySelector('svg')!;
      const track = svg.querySelector('[data-meter-track]')!;
      const x1 = Number(track.getAttribute('x1'));
      const trackW = Number(track.getAttribute('x2')) - x1;
      const end = Math.max(
        ...(('[data-meter-fill],[data-meter-fill-red]')
          .split(',')
          .map((selector) => {
            const line = svg.querySelector(selector)!;
            return line.getAttribute('visibility') === 'hidden'
              ? x1 : Number(line.getAttribute('x2'));
          })),
      );
      return (end - x1) / trackW;
    };
    const frame = meter().querySelector('[data-meter-frame]')?.getAttribute('data-meter-frame');
    const high = fills();
    publisher.emit(publication({ mainS: -48 })); flushSync();
    expect(fills()).toBe(high); expect(meter().textContent).toContain('S1');
    expect(meter().querySelector('[data-meter-frame]')?.getAttribute('data-meter-frame')).toBe(frame);
    publisher.emit(publication({ mainS: -48, epoch: 2 })); flushSync();
    const low = fills(); expect(low).toBeLessThan(high);
    publisher.emit(publication({ mainS: 20, generation: 2 })); flushSync();
    expect(fills()).toBeGreaterThan(low); expect(meter().textContent).toContain('S9+20');
    publisher.emit(publication({ mainS: -48, generation: 1, epoch: 2 })); flushSync();
    expect(fills()).toBe(low);
    publisher.emit(publication({ mainS: 20, generation: 3, epoch: 3 })); flushSync();
    expect(fills()).toBeGreaterThan(low);
    publisher.emit(publication({ mainS: -48, generation: 3, epoch: -1, sessionState: 'disconnected' })); flushSync();
    expect(fills()).toBe(0); expect(meter().textContent).toContain('S1');
    publisher.emit(publication({ meterKnown: false, generation: 3 })); flushSync();
    // MOR-2509: the unknown reading renders the empty unlit face — the
    // state lives in the accessible label, not in a placeholder glyph.
    expect(fills()).toBe(0);
    expect(meter().querySelector('svg')!.getAttribute('aria-label')).toContain('unknown');
    expect(meter().textContent).not.toContain('unknown');
    expect(meter().textContent).not.toContain('?');
    motion.reduced(true); expect(motion.frames).toBe(0);
    motion.reduced(false); expect(motion.frames).toBe(4);
  });

  it('mounts the real external fixture renderer for MAIN and SUB with honest domains', () => {
    expect(createHash('sha256').update(readFileSync(
      'component-kit-api/fixtures/external-kit/src/FixtureSignalMeter.svelte',
    )).digest('hex')).toBe(FIXTURE_SIGNAL_SOURCE_SHA256);
    expect(createHash('sha256').update(readFileSync(
      'component-kit-api/fixtures/external-kit/src/FixtureLevelMeter.svelte',
    )).digest('hex')).toBe(FIXTURE_LEVEL_SOURCE_SHA256);
    expect(FIXTURE_TARBALL_SHA256)
      .toBe('726a85423500204a0ef03c0d84987a54322664560185fd9ebd2c304ec50ddb13');
    selectedMeter.current = fixtureMeterAppearance;
    const publisher = new Publisher(publication()); const root = mountFixture(publisher);
    const meters = () => Array.from(root.querySelectorAll<HTMLElement>('[data-fixture-signal]'));
    expect(meters()).toHaveLength(2);
    expect(meters().map((meter) => [meter.dataset.domain, meter.dataset.value]))
      .toEqual([['engineering:db', '20'], ['engineering:db', '-24']]);

    publisher.emit(publication({ mainS: 53, subS: 54, meterQuality: ['uncalibrated'] })); flushSync();
    expect(meters().map((meter) => [meter.dataset.domain, meter.dataset.value]))
      .toEqual([['raw', '53'], ['raw', '54']]);
    expect(meters().every((meter) => !/dBm|\bS[0-9]/.test(meter.textContent ?? ''))).toBe(true);
    publisher.emit(publication({ mainS: 41, subS: 42, meterQuality: [] })); flushSync();
    expect(meters().map((meter) => [meter.dataset.domain, meter.dataset.value, meter.dataset.scale]))
      .toEqual([['unknown', '41', 'none'], ['unknown', '42', 'none']]);
    publisher.emit(publication({ meterKnown: false, meterQuality: [] })); flushSync();
    expect(meters().every((meter) => meter.dataset.value === undefined)).toBe(true);
  });

  it('preserves dB-relative-to-S9 evidence without inventing geometry or dBm', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const noCalibration = { ...capabilities(), meterCalibrations: {} };
    expect(setCapabilities(noCalibration)).toBe(true);
    const initial = publication({ mainS: -12 });
    const publisher = new Publisher({ ...initial, caps: noCalibration });
    const root = mountFixture(publisher);
    const meter = root.querySelector<HTMLElement>('[data-fixture-signal]')!;
    expect([meter.dataset.domain, meter.dataset.value, meter.dataset.scale])
      .toEqual(['engineering:db', '-12', 'none']);
    expect(meter.textContent).toContain('−12 dB rel S9');
    expect(meter.textContent).not.toContain('dBm');
  });

  it('keeps one motion owner across external-native-external component replacement', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const publisher = new Publisher(publication()); const layout = writable('external-a');
    const live = fromStore(layout);
    const root = mountFixture(publisher, { get layoutKey() { return live.current; } });
    expect(root.querySelectorAll('[data-fixture-signal]')).toHaveLength(2);
    expect(motion.frames).toBe(4);

    selectedMeter.current = undefined; layout.set('native-b'); flushSync();
    expect(root.querySelectorAll('[data-fixture-signal]')).toHaveLength(0);
    expect(root.querySelectorAll('[data-meter-frame]')).toHaveLength(2);
    expect(motion.frames).toBe(4);

    selectedMeter.current = fixtureMeterAppearance; layout.set('external-a-again'); flushSync();
    expect(root.querySelectorAll('[data-fixture-signal]')).toHaveLength(2);
    expect(motion.frames).toBe(4);
  });

  it('resets retained MAIN meter history at a real topology boundary', () => {
    installMeterGeometry();
    const publisher = new Publisher(publication({ mainS: 20, scheme: 'main_sub' }));
    const root = mountFixture(publisher);
    const meter = () => root.querySelector<HTMLElement>('[data-meter-owner="MAIN"]')!;
    // MOR-2509: the fill is a dash-patterned line; the lit extent is the
    // visible zone lines' end position on the drawn track (fraction 0..1).
    const fills = () => {
      const svg = meter().querySelector('svg')!;
      const track = svg.querySelector('[data-meter-track]')!;
      const x1 = Number(track.getAttribute('x1'));
      const trackW = Number(track.getAttribute('x2')) - x1;
      const end = Math.max(
        ...(('[data-meter-fill],[data-meter-fill-red]')
          .split(',')
          .map((selector) => {
            const line = svg.querySelector(selector)!;
            return line.getAttribute('visibility') === 'hidden'
              ? x1 : Number(line.getAttribute('x2'));
          })),
      );
      return (end - x1) / trackW;
    };
    const frame = meter().querySelector('[data-meter-frame]')?.getAttribute('data-meter-frame');
    const high = fills();

    publisher.emit(publication({ mainS: -48, scheme: 'ab_shared' })); flushSync();

    expect(meter().querySelector('[data-meter-frame]')?.getAttribute('data-meter-frame')).toBe(frame);
    expect(fills()).toBeLessThan(high);
    expect(meter().querySelector('[data-meter-peak]')?.getAttribute('visibility')).toBe('hidden');

    publisher.emit(publication({ mainS: 20, scheme: 'ab_shared', epoch: 2 })); flushSync();
    const reseededHigh = fills();
    publisher.emit(publication({
      mainS: -48, scheme: 'ab_shared', epoch: 2, active: 'SUB', mainActiveSlot: 'B',
    })); flushSync();
    expect(fills()).toBe(reseededHigh);

    publisher.emit(publication({ mainS: -48, scheme: 'main_sub', epoch: 2 })); flushSync();
    expect(fills()).toBeLessThan(reseededHigh);
    expect(meter().querySelector('[data-meter-peak]')?.getAttribute('visibility')).toBe('hidden');
    expect(meter().querySelector('[data-meter-frame]')?.getAttribute('data-meter-frame')).toBe(frame);
  });

  it('admits only the receiver field domain to S geometry and calibrated motion', () => {
    installMeterGeometry();
    const publisher = new Publisher(publication({ mainS: 53, meterQuality: ['uncalibrated'] }));
    const root = mountFixture(publisher);
    const meter = () => root.querySelector<HTMLElement>('[data-meter-owner="MAIN"]')!;
    const svg = () => meter().querySelector('svg')!;
    // MOR-2509: the v7 face draws no S scale labels and no peak marker for
    // a raw domain, and no motion at all for an unprojectable one — the
    // unlit track itself stays, as it does for a zero reading.
    expect(svg().getAttribute('aria-label')).toContain('raw, uncalibrated');
    expect(svg().querySelectorAll('[data-scale-label]')).toHaveLength(0);
    expect(svg().querySelector('[data-meter-peak]')?.getAttribute('visibility')).toBe('hidden');

    publisher.emit(publication({ mainS: 53, meterQuality: [] })); flushSync();
    expect(svg().getAttribute('aria-label')).toContain('unit unknown');
    expect(svg().querySelectorAll('[data-scale-label]')).toHaveLength(0);
    expect(svg().querySelector('[data-meter-peak]')?.getAttribute('visibility')).toBe('hidden');
    const unprojectableTrack = svg().querySelector('[data-meter-track]')!;
    expect(Number(svg().querySelector('[data-meter-fill]')!.getAttribute('x2')))
      .toBeCloseTo(Number(unprojectableTrack.getAttribute('x1')), 5);

    motion.reduced(true);
    publisher.emit(publication({ mainS: -12, meterQuality: ['calibrated'] })); flushSync();
    expect(svg().getAttribute('aria-label')).toMatch(/S meter S[0-9]/);
    expect([...svg().querySelectorAll('line')]
      .filter((line) => line.getAttribute('visibility') !== 'hidden').length).toBeGreaterThan(0);
    // The stepped fill is the visible dash line past the track's left end.
    const track = svg().querySelector('[data-meter-track]')!;
    const fill = svg().querySelector('[data-meter-fill]')!;
    expect(fill.getAttribute('visibility')).not.toBe('hidden');
    expect(Number(fill.getAttribute('x2'))).toBeGreaterThan(Number(track.getAttribute('x1')));

    motion.reduced(false);
    publisher.emit(publication({ mainS: 20, meterQuality: ['calibrated'], epoch: 2 })); flushSync();
    const retainedFill = Number(svg().querySelector('[data-meter-fill]')!.getAttribute('x2'));
    publisher.emit(publication({ mainS: -48, meterQuality: ['calibrated'], epoch: 2 })); flushSync();
    // A sample-only update inside one continuity window retains the
    // displayed history — the extent does not step down with the reading.
    expect(Number(svg().querySelector('[data-meter-fill]')!.getAttribute('x2')))
      .toBeCloseTo(retainedFill, 6);
  });

  // MOR-1331: the hosted digit clamp reads the LIVE band envelope. The base
  // caps carry `freqRanges: []`, so the band group is absent and the clamp
  // stays wide open; one emission at 14.250 MHz must still leave as +1 Hz.
  it('keeps the legacy wide clamp while the band group is absent', () => {
    const publisher = new Publisher(publication()); const tune = vi.fn();
    const root = mountFixture(publisher, { onTuneFrequency: tune });
    const readout = root.querySelector<HTMLElement>('[data-frequency-owner="MAIN"] [data-alternate-frequency-readout]')!;
    readout.querySelector<HTMLButtonElement>('[data-multiplier="1"]')!.click();
    readout.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(tune).toHaveBeenCalledExactlyOnceWith('MAIN', 14_250_001);
  });

  it('requires the synchronous publisher and owns no fallback clocks or continuity comparison', () => {
    const addedSources = [
      'src/semantic/meter-renderer-view.ts',
      'src/component-kits/MeterRendererSeat.svelte',
    ].map((file) => readFileSync(file, 'utf8')).join('\n');
    const source = readFileSync('src/semantic/ReceiverInstrumentHost.svelte', 'utf8');
    expect(source).toMatch(/subscribeControlAuthority: SubscribeReceiverAuthority/);
    expect(source).not.toMatch(/subscribeControlAuthority\?|requestAnimationFrame|setInterval|setTimeout|Date\.now/);
    expect(source).toContain('source: meter?.source');
    expect(source).toContain('projectSignalMeter(value, meter?.domain)');
    expect(source).not.toMatch(/LinearSMeter/);
    expect(source).not.toMatch(/providerGeneration === .*source|controlSessionEpoch ===/);
    expect(`${source}\n${addedSources}`)
      .not.toMatch(/requestAnimationFrame|setInterval|setTimeout|Date\.now/);
    expect(addedSources).not.toMatch(
      /calibrat|normaliz|MeterSourceIdentity|MeterContinuitySession|FrequencyInstrumentBinding/i,
    );
  });

  // MOR-2509 owner rule: the Po row exists only where the TX target says —
  // the descriptor is undefined for every other receiver and for an
  // unknown target, so nothing ever renders a scale that is not this
  // receiver's (and no unlit row can read as a measured zero).
  it('builds the Po lower-scale descriptor for the TX-target receiver only', () => {
    const structuralPower = {
      presence: 'present',
      reading: { status: 'known', value: 100 },
      display: { state: 'current', value: 100 },
      availability: { structural: true, operational: true },
      relevant: true,
      domain: { kind: 'engineering', unit: 'w' },
      source: null,
    } as unknown as MetersViewModel['power'];
    const meters = {
      power: structuralPower, rfState: 'transmitting',
    } as unknown as MetersViewModel;
    const knownMain = {
      status: 'known', receiver: 'MAIN',
      slot: { kind: 'slotted', id: 'A' }, frequencyHz: 1,
    } as const;

    expect(buildPowerLowerScale(meters, knownMain, 'transmitting', 'MAIN')).toBeDefined();
    expect(buildPowerLowerScale(meters, knownMain, 'transmitting', 'SUB')).toBeUndefined();
    expect(buildPowerLowerScale(
      meters, { status: 'unknown', reason: 'not-observed' }, 'transmitting', 'MAIN',
    )).toBeUndefined();
    expect(buildPowerLowerScale(undefined, knownMain, 'transmitting', 'MAIN')).toBeUndefined();
  });
});
