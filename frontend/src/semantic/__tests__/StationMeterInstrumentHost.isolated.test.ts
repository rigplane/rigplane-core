import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { MeterAppearance, SignalMeterRendererView } from '../../../component-kit-api/src/index';
const selectedMeter = vi.hoisted(() => ({ current: undefined as MeterAppearance | undefined }));
const rendererViews = vi.hoisted(() => ({ signals: [] as SignalMeterRendererView[] }));
vi.mock('../../component-kits/activation', () => ({
  getSelectedMeterAppearance: () => selectedMeter.current,
}));
vi.mock('../meter-renderer-view', async (original) => {
  const actual = await original<typeof import('../meter-renderer-view')>();
  return { ...actual,
    toSignalMeterRendererView(...args: Parameters<typeof actual.toSignalMeterRendererView>) {
      const projected = actual.toSignalMeterRendererView(...args);
      rendererViews.signals.push(projected);
      return projected;
    },
  };
});
const motion = vi.hoisted(() => ({ bars: [] as any[], signals: [] as any[] }));
vi.mock('../../components-v2/meters/bar-meter-motion.svelte', async (original) => {
  const actual = await original<typeof import('../../components-v2/meters/bar-meter-motion.svelte')>();
  return { ...actual, createBarMeterMotion(input: Parameters<typeof actual.createBarMeterMotion>[0]) {
    const binding = actual.createBarMeterMotion(input);
    const wrapped = { initial: input, frame: binding.frame, sync: vi.fn(binding.sync), start: vi.fn(binding.start),
      stop: vi.fn(binding.stop), resetPeak: vi.fn(binding.resetPeak) };
    motion.bars.push(wrapped); return wrapped;
  } };
});
vi.mock('../../components-v2/meters/signal-meter-motion.svelte', async (original) => {
  const actual = await original<typeof import('../../components-v2/meters/signal-meter-motion.svelte')>();
  return { ...actual, createSignalMeterMotion(input: Parameters<typeof actual.createSignalMeterMotion>[0]) {
    const binding = actual.createSignalMeterMotion(input);
    const wrapped = { frame: binding.frame, sync: vi.fn(binding.sync), start: vi.fn(binding.start),
      stop: vi.fn(binding.stop) };
    motion.signals.push(wrapped); return wrapped;
  } };
});
import Fixture, {
  StationMeterTestPublisher, fixtureMeterAppearance, stationMeterProbe,
} from './fixtures/StationMeterInstrumentHostFixture.svelte';
import { topologyFixtures, withMeters, withTxAux } from '../fixtures/topologies';
import type { MeterSourceIdentity, MeterValueDomain, RadioViewModel } from '../radio-view-model';
import type { StationMeterAuthorityPublication, SubscribeStationMeterAuthority } from '../StationMeterInstrumentHost.svelte';
import { getDesignLanguage, registerDesignLanguage } from '../../presentation/languages/contract';
import { dimColor } from '../../components-v2/meters/bar-gauge-utils';
const paths = { signal: 'main.sMeter', power: 'powerMeter', swr: 'swrMeter', alc: 'alcMeter',
  compression: 'compMeter', drainVoltage: 'vdMeter', drainCurrent: 'idMeter' } as const;
type Key = keyof typeof paths;
const source = (path: MeterSourceIdentity['path']): MeterSourceIdentity =>
  ({ providerGeneration: 1, scope: 'radio', receiver: null, path });
function view(rfState: 'receiving' | 'transmitting' = 'transmitting'): RadioViewModel {
  const base = withMeters(withTxAux(topologyFixtures['1/single']), rfState);
  const meters = { ...base.meters! };
  for (const key of Object.keys(paths) as Key[]) meters[key] = { ...meters[key], source: source(paths[key]) };
  return { ...base, meters, txAux: { ...base.txAux!, compressor: {
    reading: { status: 'known', value: true }, availability: { structural: true, operational: true },
  } } };
}
function field(base: RadioViewModel, key: Key, over: Record<string, unknown>): RadioViewModel {
  return { ...base, meters: { ...base.meters!, [key]: { ...base.meters![key], ...over } } };
}
const domain = (base: RadioViewModel, value: MeterValueDomain | undefined): RadioViewModel =>
  field(base, 'swr', { domain: value });
let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null;
beforeEach(() => {
  target = document.createElement('div'); document.body.appendChild(target);
  component = null; motion.bars = []; motion.signals = []; stationMeterProbe.clear();
  rendererViews.signals = [];
  selectedMeter.current = undefined;
  window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => { if (component) unmount(component); target.remove(); vi.restoreAllMocks();
  selectedMeter.current = undefined; });
function render(initial: StationMeterAuthorityPublication = { view: view(), session: { controlSessionEpoch: 1 } }) {
  const publisher = new StationMeterTestPublisher(initial);
  const props = proxy({ subscribeStationMeterAuthority: publisher.subscribe, presentation: 'native', probe: true });
  component = mount(Fixture, { target, props }); flushSync();
  return { publisher, props };
}
const expectStopped = () => { expect(motion.signals[0].stop).toHaveBeenCalledOnce(); for (const binding of motion.bars) expect(binding.stop).toHaveBeenCalledOnce(); };
describe('StationMeterInstrumentHost', () => {
  it.each([
    ['native', true, true], ['native', true, false], ['native', false, true], ['native', false, false],
    ['external', true, true], ['external', true, false],
    ['external', false, true], ['external', false, false],
  ] as const)('%s selection keeps station siblings live for signal=%s SWR=%s',
    (selection, signalPresent, swrPresent) => {
      selectedMeter.current = selection === 'external' ? fixtureMeterAppearance : undefined;
      let current = view();
      current = field(current, 'signal', {
        availability: { structural: signalPresent, operational: signalPresent },
      });
      current = field(current, 'swr', {
        availability: { structural: swrPresent, operational: swrPresent },
      });
      render({ view: current, session: { controlSessionEpoch: 1 } });

      const externalSignal = target.querySelectorAll('[data-fixture-signal]');
      const externalLevels = [...target.querySelectorAll<HTMLElement>('[data-fixture-level]')]
        .map((node) => node.dataset.fixtureLevel);
      if (selection === 'external') {
        expect(externalSignal).toHaveLength(signalPresent ? 1 : 0);
        expect(externalLevels).toEqual([
          'power', ...(swrPresent ? ['swr'] : []), 'alc',
          'drainCurrent', 'drainVoltage', 'compression',
        ]);
        expect(target.querySelector('[data-meter-tile]')).toBeNull();
      } else {
        expect(externalSignal).toHaveLength(0);
        expect(externalLevels).toEqual([]);
        expect(target.querySelector('[data-testid="meter-power"]')).not.toBeNull();
        expect(target.querySelector('[data-testid="meter-alc"]')).not.toBeNull();
        expect(target.querySelector('[data-testid="meter-drainCurrent"]')).not.toBeNull();
        expect(target.querySelector('[data-testid="meter-drainVoltage"]')).not.toBeNull();
        expect(target.querySelector('[data-testid="meter-compression"]')).not.toBeNull();
        expect(target.querySelector('[data-testid="meter-signal"]') !== null).toBe(signalPresent);
        expect(target.querySelector('[data-testid="meter-swr"]') !== null)
          .toBe(!signalPresent && swrPresent);
      }
    });

  it('mounts all seven real external meter renderers without adding motion owners', () => {
    selectedMeter.current = fixtureMeterAppearance;
    render();
    expect(target.querySelectorAll('[data-fixture-signal]')).toHaveLength(1);
    expect([...target.querySelectorAll<HTMLElement>('[data-fixture-level]')]
      .map((node) => node.dataset.fixtureLevel)).toEqual([
        'power', 'swr', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
      ]);
    expect([motion.signals.length, motion.bars.length]).toEqual([1, 6]);
    expect([...target.querySelectorAll<HTMLElement>('[data-fixture-level] button')]
      .map((button) => button.parentElement?.dataset.fixtureLevel)).toEqual([
        'power', 'alc', 'drainCurrent',
    ]);
  });

  it('shows exact engineering, raw, and unknown evidence through the real external fixture', () => {
    selectedMeter.current = fixtureMeterAppearance;
    let current = field(view(), 'power', { domain: { kind: 'engineering', unit: 'w' } });
    current = field(current, 'alc', { domain: { kind: 'raw' } });
    current = field(current, 'drainVoltage', { domain: undefined });
    render({ view: current, session: { controlSessionEpoch: 1 } });
    const meter = (key: string) => target.querySelector<HTMLElement>(`[data-fixture-level="${key}"]`)!;
    expect(meter('power').dataset).toMatchObject({ state: 'current', domain: 'engineering:w', value: '0.6' });
    expect(meter('alc').dataset).toMatchObject({ state: 'current', domain: 'raw', value: '40' });
    expect(meter('drainVoltage').dataset).toMatchObject({ state: 'current', domain: 'unknown', value: '200' });
  });

  it('keeps a structurally present unavailable-known signal external but fail-closed', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const unavailable = field(view('receiving'), 'signal', {
      reading: { status: 'known', value: 120 },
      availability: { structural: true, operational: false },
    });
    render({ view: unavailable, session: { controlSessionEpoch: 1 } });
    const signal = target.querySelector<HTMLElement>('[data-fixture-signal]')!;
    const publicView = rendererViews.signals.at(-1)!;
    expect(signal).not.toBeNull();
    expect(signal.dataset.state).toBe('unknown');
    expect(signal.hasAttribute('data-value')).toBe(false);
    expect(publicView.evidence).toEqual({ state: 'unknown', domain: { kind: 'unknown' } });
    expect(publicView.displayedFraction).toBeNull();
    expect(publicView.peakFraction).toBeNull();
  });

  it('routes real external reset leases and revokes a retained A-B-A button', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const { publisher } = render();
    const button = (key: string) => target.querySelector<HTMLButtonElement>(
      `[data-fixture-level="${key}"] button`,
    );
    button('power')!.click(); button('alc')!.click(); button('drainCurrent')!.click();
    expect(motion.bars[0].resetPeak).toHaveBeenCalledOnce();
    expect(motion.bars[1].resetPeak).toHaveBeenCalledOnce();
    expect(motion.bars[2].resetPeak).toHaveBeenCalledOnce();
    expect(button('swr')).toBeNull(); expect(button('drainVoltage')).toBeNull();
    expect(button('compression')).toBeNull();

    const retained = button('power')!;
    publisher.emit({ view: field(view(), 'power', {
      source: { ...source('powerMeter'), providerGeneration: 2 },
    }), session: { controlSessionEpoch: 2 } });
    publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } });
    flushSync();
    retained.click();
    expect(motion.bars[0].resetPeak).toHaveBeenCalledOnce();
    button('power')!.click();
    expect(motion.bars[0].resetPeak).toHaveBeenCalledTimes(2);

    publisher.emit({ view: view('receiving'), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(button('power')).toBeNull();
    publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(button('power')).not.toBeNull();
  });

  it('keeps owners stable across external-native-external surface replacement', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const { props } = render();
    expect(target.querySelectorAll('[data-fixture-signal], [data-fixture-level]')).toHaveLength(7);
    selectedMeter.current = undefined; props.presentation = 'native-b'; flushSync();
    expect(target.querySelectorAll('[data-fixture-signal], [data-fixture-level]')).toHaveLength(0);
    expect(target.querySelector('[data-testid="meter-signal"]')).not.toBeNull();
    selectedMeter.current = fixtureMeterAppearance; props.presentation = 'external-again'; flushSync();
    expect(target.querySelectorAll('[data-fixture-signal], [data-fixture-level]')).toHaveLength(7);
    expect([motion.signals.length, motion.bars.length]).toEqual([1, 6]);
  });

  it('owns seven live bindings, stable passive frames, and tears them down once', () => {
    let id = 0; const add = vi.fn(); const remove = vi.fn();
    window.matchMedia = vi.fn().mockReturnValue({ matches: false, addEventListener: add, removeEventListener: remove });
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => ++id);
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const interval = vi.spyOn(window, 'setInterval').mockImplementation(() => ++id as any);
    const clear = vi.spyOn(window, 'clearInterval').mockImplementation(() => {});
    const { publisher, props } = render();
    expect([motion.signals.length, motion.bars.length]).toEqual([1, 6]);
    expect(raf).toHaveBeenCalledTimes(8); expect(interval).toHaveBeenCalledTimes(3); expect(add.mock.calls.length).toBeGreaterThanOrEqual(7);
    expect([...stationMeterProbe.frames.keys()].sort()).toEqual(Object.keys(paths).sort());
    const initial = new Map(stationMeterProbe.frames);
    for (const [key, frame] of initial) {
      expect(Object.keys(frame).sort()).toEqual(key === 'signal' ? ['field', 'motion'] : ['motion', 'projection']);
      expect('source' in frame || 'session' in frame || 'resetPeak' in frame || 'binding' in frame).toBe(false);
      if (key === 'signal') expect(Object.hasOwn((frame as any).field, 'source')).toBe(false);
      if (key !== 'signal') expect(Object.hasOwn((frame as any).projection, 'source')).toBe(false);
    }
    const detached = target.querySelector<SVGSVGElement>('[data-meter="power"] svg')!;
    props.presentation = 'alternate'; flushSync(); detached.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
    for (const [key, frame] of initial) expect(stationMeterProbe.frames.get(key)).toBe(frame);
    const stale = (stationMeterProbe.seats.get('power') as any).attachRenderer();
    expect(publisher.handlers.size).toBe(1); unmount(component!); component = null; stale.invoke();
    expect(publisher.handlers.size).toBe(0);
    expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
    expectStopped();
    expect(cancel.mock.calls.length).toBeGreaterThanOrEqual(8); expect(clear).toHaveBeenCalledTimes(3); expect(remove).toHaveBeenCalled();
  });
  it('cleans mounted owners while preserving subscription and unsubscribe failures', async () => {
    const initial = { view: view(), session: { controlSessionEpoch: 1 } };
    const setupFailure = new Error('setup failed');
    const cleanupFailure = new Error('cleanup failed');
    const setup: SubscribeStationMeterAuthority = (handler) => {
      handler(initial);
      motion.bars[0].stop.mockImplementationOnce(() => { throw cleanupFailure; });
      throw setupFailure;
    };
    let caught: unknown;
    try {
      mount(Fixture, { target, props: { subscribeStationMeterAuthority: setup, probe: true } });
    } catch (error) {
      caught = error;
    }
    expect(caught).toBe(setupFailure);
    expectStopped();
    motion.bars = [];
    motion.signals = [];
    const stopFailure = new Error('stop failed');
    let publish!: (next: StationMeterAuthorityPublication) => void;
    const subscribe: SubscribeStationMeterAuthority = (handler) => {
      publish = handler;
      handler(initial);
      return () => { throw stopFailure; };
    };
    component = mount(Fixture, { target, props: { subscribeStationMeterAuthority: subscribe, probe: true } }); flushSync();
    const frame = stationMeterProbe.frames.get('power');
    const lease = (stationMeterProbe.seats.get('power') as any).attachRenderer();
    motion.bars[0].stop.mockImplementationOnce(() => { throw cleanupFailure; });
    let removal: unknown;
    try {
      publish({ view: field(view(), 'power', { availability: { structural: false, operational: false } }), session: initial.session });
    } catch (error) {
      removal = error;
    }
    expect(removal).toBe(cleanupFailure); publish(initial); flushSync();
    expect(stationMeterProbe.frames.get('power')).not.toBe(frame);
    expect(lease.active).toBe(false);
    motion.bars.at(-1).stop.mockImplementationOnce(() => { throw cleanupFailure; });
    const mounted = component; component = null;
    await expect(unmount(mounted)).rejects.toBe(stopFailure);
    lease.invoke();
    expectStopped();
    expect(lease.active).toBe(false);
    expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
  });
  it('preserves both SWR ratio-scale projections without exposing source', () => {
    selectedMeter.current = fixtureMeterAppearance;
    const { publisher } = render({ view: domain(view(), undefined), session: { controlSessionEpoch: 1 } });
    const frame = () => stationMeterProbe.frames.get('swr') as any;
    const external = () => target.querySelector<HTMLElement>('[data-fixture-level="swr"]')!;
    expect(frame().projection.ratioScale).toBe(true);
    expect(external().dataset.ratioScale).toBe('true');
    publisher.emit({ view: domain(view(), { kind: 'raw' }), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(frame().projection.ratioScale).toBe(false); expect(external().dataset.ratioScale).toBe('false');
    expect(Object.hasOwn(frame().projection, 'source')).toBe(false);
  });
  it('keeps custom level palettes without admitting a signal owner or gauge', () => {
    const original = getDesignLanguage('fieldline')!; const zones = [{ end: 1, color: '#123456' }] as const;
    registerDesignLanguage({ ...original, renderers: { ...original.renderers, meters: () => ({ kind: 'host-projection', segmentCount: 10, segmentGapPx: 2, toneBelowS9: '#111', toneAboveS9: '#222', zones, unknown: false }) } });
    document.documentElement.dataset.designLanguage = 'fieldline';
    try {
      const absent = field(field(view(), 'signal', { availability: { structural: false, operational: false } }), 'swr', { availability: { structural: false, operational: false } });
      render({ view: absent, session: { controlSessionEpoch: 1 } });
      expect(motion.signals).toHaveLength(0); expect(target.querySelector('[data-testid="meter-signal"]')).toBeNull();
      const fills = [...target.querySelectorAll('[data-testid="meter-power"] rect')].map((node) => node.getAttribute('fill'));
      expect(fills).toContain(dimColor(zones[0].color));
    } finally { registerDesignLanguage(original); delete document.documentElement.dataset.designLanguage; }
  });
  it('rotates reset authority for every qualified source/session field across pre-flush A-B-A', () => {
    const { publisher } = render();
    const mutate = [
      (s: MeterSourceIdentity) => ({ ...s, providerGeneration: 2 }),
      (s: MeterSourceIdentity) => ({ ...s, scope: 'receiver' as const }),
      (s: MeterSourceIdentity) => ({ ...s, receiver: 'MAIN' as const }),
      (s: MeterSourceIdentity) => ({ ...s, path: 'alcMeter' as const }),
    ];
    for (const change of mutate) {
      const oldSeat = stationMeterProbe.seats.get('power') as any; const stale = oldSeat.attachRenderer();
      publisher.emit({ view: field(view(), 'power', { source: change(source('powerMeter')) }), session: { controlSessionEpoch: 1 } });
      publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } }); flushSync();
      stale.invoke(); expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
    }
    const oldSeat = stationMeterProbe.seats.get('power') as any; const stale = oldSeat.attachRenderer();
    publisher.emit({ view: view(), session: { controlSessionEpoch: 2 } });
    publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } }); flushSync();
    stale.invoke(); expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
    target.querySelector<SVGSVGElement>('[data-meter="power"] svg')!
      .dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(motion.bars[0].resetPeak).toHaveBeenCalledOnce();
    const unqualified = field(view(), 'power', { source: undefined });
    publisher.emit({ view: unqualified, session: { controlSessionEpoch: 1 } }); flushSync();
    const legacy = (stationMeterProbe.seats.get('power') as any).attachRenderer();
    publisher.emit({ view: unqualified, session: { controlSessionEpoch: 1 } }); flushSync(); expect(legacy.active).toBe(true);
    publisher.emit({ view: field(unqualified, 'power', { source: null }), session: null });
    publisher.emit({ view: unqualified, session: { controlSessionEpoch: 1 } }); flushSync(); legacy.invoke();
    expect(motion.bars[0].resetPeak).toHaveBeenCalledOnce();
  });
  it('keeps same-source reset authority through null samples, then re-admits a fresh owner', () => {
    const { publisher } = render();
    const seat = stationMeterProbe.seats.get('power') as any; const lease = seat.attachRenderer();
    const idle = view('receiving');
    publisher.emit({ view: idle, session: { controlSessionEpoch: 1 } }); flushSync();
    expect(stationMeterProbe.seats.get('power')).toBe(seat); expect(lease.view.available).toBe(false);
    expect(motion.bars[0].sync.mock.lastCall[0].value).toBeNull();
    expect(motion.bars[0].sync.mock.lastCall[0].source).toEqual(source('powerMeter'));
    lease.invoke(); expect(motion.bars[0].resetPeak).not.toHaveBeenCalled();
    publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(stationMeterProbe.seats.get('power')).toBe(seat); lease.invoke();
    expect(motion.bars[0].resetPeak).toHaveBeenCalledOnce();
    publisher.emit({ view: field(idle, 'power', { source: null }), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(stationMeterProbe.seats.get('power')).not.toBe(seat);
    expect(lease.active).toBe(false);
    const oldFrame = stationMeterProbe.frames.get('compression');
    const comp = motion.bars[4];
    const off = { ...view(), txAux: { ...view().txAux!, compressor: { reading: { status: 'known' as const, value: false }, availability: { structural: true, operational: true } } } };
    publisher.emit({ view: off, session: { controlSessionEpoch: 1 } }); flushSync(); expect(comp.stop).toHaveBeenCalledOnce();
    publisher.emit({ view: { ...off, txAux: { ...off.txAux!, compressor: { ...off.txAux!.compressor, reading: { status: 'unknown' } } } }, session: { controlSessionEpoch: 1 } }); flushSync(); expect(motion.bars).toHaveLength(6);
    publisher.emit({ view: view(), session: { controlSessionEpoch: 1 } }); flushSync();
    expect(stationMeterProbe.frames.get('compression')).not.toBe(oldFrame);
  });
});
