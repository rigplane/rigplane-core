import { beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { ReceiverIndicatorViewModel } from '../../../semantic/radio-view-model';
import type { ScopeFrame } from '../../../lib/runtime/adapters/scope-adapter';

const meter = vi.hoisted(() => ({
  instances: [] as Array<{ value: number; update: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }>,
  create: vi.fn(),
  reduced: vi.fn(() => false),
}));

vi.mock('$lib/utils/smoothing.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/utils/smoothing.svelte')>();
  return {
    ...actual,
    createSmoother: meter.create.mockImplementation(() => {
      const instance = { value: 0, update: vi.fn(), start: vi.fn(), stop: vi.fn() };
      meter.instances.push(instance);
      return instance;
    }),
    prefersReducedMotion: meter.reduced,
  };
});

vi.mock('../../../components-v2/meters/smeter-scale', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../components-v2/meters/smeter-scale')>();
  return { ...actual, isSmeterCalibrated: vi.fn(() => true), calibratedToSegments: vi.fn((value: number) => value) };
});

import DualSdrFace from '../DualSdrFace.svelte';

const known = <T>(value: T) => ({ reading: { status: 'known' as const, value }, availability: { structural: true, operational: true } });
const unknown = () => ({ reading: { status: 'unknown' as const }, availability: { structural: true, operational: false } });
const canvas = { clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), strokeStyle: '', lineWidth: 0 };
beforeEach(() => {
  meter.instances.length = 0; meter.create.mockClear(); meter.reduced.mockReset(); meter.reduced.mockReturnValue(false);
  for (const call of Object.values(canvas)) if (typeof call === 'function') call.mockClear();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(canvas as unknown as CanvasRenderingContext2D);
  Object.defineProperty(HTMLCanvasElement.prototype, 'clientWidth', { configurable: true, get: () => 10 });
  Object.defineProperty(HTMLCanvasElement.prototype, 'clientHeight', { configurable: true, get: () => 10 });
});

const receiverIndicators = (main = 11, mainBandwidth = 2_400, sub = 22, subBandwidth = 3_100): ReceiverIndicatorViewModel[] => [
  { receiver: 'MAIN', availability: { structural: true, operational: true }, sMeter: known(main), bandwidthHz: known(mainBandwidth), agcMode: unknown(), nbActive: unknown(), nrActive: unknown(), notchMode: unknown(), attenuator: unknown(), preamp: unknown(), rfGain: unknown(), digiSel: unknown(), ipPlus: unknown() },
  { receiver: 'SUB', availability: { structural: true, operational: true }, sMeter: known(sub), bandwidthHz: known(subBandwidth), agcMode: unknown(), nbActive: unknown(), nrActive: unknown(), notchMode: unknown(), attenuator: unknown(), preamp: unknown(), rfGain: unknown(), digiSel: unknown(), ipPlus: unknown() },
];

function view(preamp = 1, preValues = [0, 1, 2], mutex = false, indicators: ReceiverIndicatorViewModel[] = receiverIndicators()): RadioViewModel {
  const result = {
    topologyId: '2/main_sub', vfoScheme: 'main_sub', activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      { receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 7_070_000, mode: 'LSB', filter: 'FIL2', isActive: true, isActiveSlot: true, isTxTarget: false },
      { receiver: 'SUB', slot: { kind: 'unslotted' }, label: 'SUB', frequencyHz: null, mode: null, filter: null, isActive: false, isActiveSlot: true, isTxTarget: false },
    ], split: { status: 'unknown' }, dualWatch: { status: 'unknown' }, txTarget: { status: 'unknown', reason: 'not-observed' }, txPermit: { status: 'unknown', reason: 'target-unknown' },
    scope: { hardwareScope: { structural: true, operational: true }, audioFftScope: { structural: false, operational: false } }, disabledReasons: mutex ? [{ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' }] : [], receiverIndicators: indicators, radioWideIndicators: { rfState: 'unknown', antenna: unknown(), atu: unknown(), ritActive: unknown(), ritOffset: unknown(), xitActive: unknown(), xitOffset: unknown(), actions: { main: { structural: false, operational: false }, sub: { structural: false, operational: false }, equalize: { structural: false, operational: false }, swap: { structural: false, operational: false }, quickSplit: { structural: false, operational: false }, quickDualWatch: { structural: false, operational: false }, speak: { structural: false, operational: false } } },
    meters: { rfState: 'receiving', signal: known(12), power: unknown(), swr: unknown(), alc: unknown(), compression: unknown(), drainVoltage: unknown(), drainCurrent: unknown() },
    rfFrontEnd: { preamp: known(preamp), preValues, attenuator: known(0), attValues: [0, 20], rfGain: unknown(), squelch: unknown(), digiSel: unknown(), ipPlus: known(false) },
    scopeControls: { mode: known(0), edge: known(1), span: known(0), speed: known(0), hold: known(false), refDb: known(0), dual: known(false), receiver: known(0), duringTx: known(false), centerType: known(0), vbwNarrow: known(false), rbw: known(0) },
    txAux: { atu: unknown(), vox: known(false), voxGain: unknown(), antiVoxGain: unknown(), voxDelay: unknown(), compressor: known(false), compressorLevel: unknown(), monitor: unknown(), monitorLevel: unknown(), rfPower: unknown(), micGain: unknown(), driveGain: unknown() },
  } as unknown as RadioViewModel;
  return result;
}

describe('DualSdrFace', () => {
  it('mounts one cluster per receiver, demuxes one scope subscription, and cleans it up', async () => {
    let listener: ((frame: ScopeFrame) => void) | undefined;
    const unsubscribe = vi.fn();
    const source = { subscribe: vi.fn((next: (frame: ScopeFrame) => void) => { listener = next; return unsubscribe; }) };
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: source } });
    await tick();
    expect(source.subscribe).toHaveBeenCalledOnce();
    expect(target.querySelectorAll('[data-receiver-cluster]').length).toBe(2);
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    const pixels = new Uint8Array([1, 2]);
    listener?.({ receiver: 0, mode: 0, startFreq: 1, endFreq: 2, pixels });
    pixels[0] = 255;
    await tick();
    await Promise.resolve();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('2');
    expect(canvas.lineTo).toHaveBeenCalledWith(1, expect.closeTo(8.96, 2));
    listener?.({ receiver: 1, mode: 0, startFreq: 3, endFreq: 4, pixels: new Uint8Array([3]) });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    listener?.({ receiver: 9, mode: 0, startFreq: 9, endFreq: 9, pixels: new Uint8Array([9]) });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] .axis')?.textContent).toContain('3 — 4');
    unmount(component);
    expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it('keeps receiver-addressed meter and bandwidth facts with their own cluster', async () => {
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(1, [0, 1, 2], false, receiverIndicators(11, 2_400, 22, 3_100)), scopeSource: { subscribe: () => () => {} } } });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-bandwidth]')?.textContent).toContain('2400');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-bandwidth]')?.textContent).toContain('3100');
    expect(meter.instances.map((instance) => instance.update.mock.calls[0]?.[0])).toEqual([11, 22]);
    unmount(component);
  });

  it('keeps unknown receiver display unasserted and enables only backed PRE', async () => {
    const onPreChange = vi.fn();
    const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: view(1, [0, 1, 2], false, []), scopeSource: { subscribe: () => () => {} }, onPreChange } });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-frequency]')?.textContent).toContain('—');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-needle]')).toBeNull();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(false); await pre.click(); await tick(); expect(onPreChange).toHaveBeenCalledWith(2);
    for (const name of ['hold', 'main-sub', 'dual', 'mode', 'edge', 'att', 'ip', 'agc', 'vox', 'comp', 'ant', 'menu1', 'cent-fix', 'expd-set']) expect(target.querySelector<HTMLButtonElement>(`[data-control="${name}"]`)?.disabled).toBe(true);
  });

  it.each([
    ['advances a middle declared PRE value', 1, [0, 1, 2], 2],
    ['wraps the last declared PRE value', 2, [0, 1, 2], 0],
  ])('%s', async (_label, current, values, expected) => {
    const onPreChange = vi.fn(); const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: view(current, values), scopeSource: { subscribe: () => () => {} }, onPreChange } }); await tick();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(false); pre.click(); expect(onPreChange).toHaveBeenCalledWith(expected);
  });

  it('fails PRE closed for absent current value, mutex, and unknown', async () => {
    const unavailable = view();
    (unavailable.rfFrontEnd!.preamp as { reading: unknown }).reading = unknown().reading;
    for (const candidate of [view(9), view(1, [0, 1, 2], true), unavailable]) {
      const onPreChange = vi.fn(); const target = document.createElement('div');
      mount(DualSdrFace, { target, props: { view: candidate, scopeSource: { subscribe: () => () => {} }, onPreChange } }); await tick();
      const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
      expect(pre.disabled).toBe(true); pre.click(); expect(onPreChange).not.toHaveBeenCalled();
    }
  });

  it('uses the shared smoother lifecycle for receiver meter transitions and teardown', async () => {
    const target = document.createElement('div');
    const component = createClassComponent({ component: DualSdrFace, target, props: { view: view(), scopeSource: { subscribe: () => () => {} } } });
    await tick();
    expect(meter.create).toHaveBeenCalledTimes(2);
    expect(meter.instances.map((instance) => instance.start)).toSatisfy((starts: ReturnType<typeof vi.fn>[]) => starts.every((start) => start.mock.calls.length === 1));
    expect(meter.instances.map((instance) => instance.update.mock.calls[0]?.[0])).toEqual([11, 22]);
    component.$set({ view: view(1, [0, 1, 2], false, receiverIndicators(33, 2_400, 44, 3_100)) });
    await tick();
    expect(meter.instances.map((instance) => instance.update.mock.calls.at(-1)?.[0])).toEqual([33, 44]);
    component.$destroy();
    expect(meter.instances.map((instance) => instance.stop)).toSatisfy((stops: ReturnType<typeof vi.fn>[]) => stops.every((stop) => stop.mock.calls.length === 1));
  });

  it('passes reduced-motion observation through the shared meter helper', async () => {
    meter.reduced.mockReturnValue(true);
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: { subscribe: () => () => {} } } });
    await tick();
    expect(meter.reduced).toHaveBeenCalled();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-needle]')?.getAttribute('data-reduced-motion')).toBe('true');
    unmount(component);
  });

  it('replaces the scope source reactively and cleans each subscription once', async () => {
    const cleanupA = vi.fn(); const cleanupB = vi.fn();
    const sourceA = { subscribe: vi.fn(() => cleanupA) }; const sourceB = { subscribe: vi.fn(() => cleanupB) };
    const target = document.createElement('div');
    const component = createClassComponent({ component: DualSdrFace, target, props: { view: view(), scopeSource: sourceA } });
    await tick();
    expect(sourceA.subscribe).toHaveBeenCalledOnce();
    component.$set({ scopeSource: sourceB });
    await tick();
    expect(cleanupA).toHaveBeenCalledOnce();
    expect(sourceB.subscribe).toHaveBeenCalledOnce();
    component.$destroy();
    expect(cleanupB).toHaveBeenCalledOnce();
  });
});
