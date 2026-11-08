import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import { ScopeStream } from '../../../lib/runtime/scope-stream';
import * as svelteClient from 'svelte/internal/client';
import { createClassComponent } from 'svelte/legacy';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { ReceiverIndicatorViewModel } from '../../../semantic/radio-view-model';
import type { ScopeFrame } from '../../../lib/runtime/scope-frame';
import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { parseCurrentScopeFrame } from '../../../lib/runtime/adapters/scope-adapter';
import { findPlaceholderTokens } from '../../../lib/placeholder-token-rule';

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

/** Builds the typed wire frame the shared stream delivers. */
function wireFrame(pixels: Uint8Array, startFreq = 1, endFreq = 2, receiver = 0): ScopeFrame {
  const buffer = scopeWire({ pixels, viewStartHz: startFreq, viewEndHz: endFreq, receiver });
  const result = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'scope', epoch: 1 });
  if (!result.ok) throw new Error(result.reason);
  return result.frame;
}

const known = <T>(value: T) => ({ reading: { status: 'known' as const, value }, availability: { structural: true, operational: true } });
const unknown = () => ({ reading: { status: 'unknown' as const }, availability: { structural: true, operational: false } });
const canvas = { clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(), strokeStyle: '', lineWidth: 0 };
const dualEffectRuns = new Map<number, number>();
const realUserEffect = svelteClient.user_effect;
beforeEach(() => {
  dualEffectRuns.clear();
  vi.spyOn(svelteClient, 'user_effect').mockImplementation(fn => {
    const caller = new Error().stack?.split('\n').find(line => line.includes('.svelte:')) ?? '';
    if (!/(?:DualSdrFace|ReceiverInstrumentCluster|ReceiverScopeWaterfall)\.svelte/.test(caller)) return realUserEffect(fn);
    const id = dualEffectRuns.size; dualEffectRuns.set(id, 0);
    return realUserEffect(() => { dualEffectRuns.set(id, dualEffectRuns.get(id)! + 1); return fn(); });
  });
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

// MOR-2692 geometry fixture: mirrors the merged `view()` helper but carries
// a real `modeFilter` group so the badge slots mount at all, plus a `band`
// tuning envelope for the frequency-slot reservation. The vfos are
// deliberate no-readings (null frequency/mode/filter), i.e. the fixture's
// unread window. Choice labels include the FTX-1-scale outliers
// ('DATA-FM-N', 'WIDE-F3') so the reserved width is a profile-data pin, not
// a constant.
function modeFilterView(): RadioViewModel {
  const base = view();
  return {
    ...base,
    vfos: base.vfos.map((vfo) => ({ ...vfo, frequencyHz: null, mode: null, filter: null, display: undefined })),
    modeFilter: {
      activeFilterConfiguration: null,
      currentMode: unknown(),
      modeChoices: ['LSB', 'DATA-FM-N'],
      currentFilter: unknown(),
      filterChoices: ['FIL2', 'WIDE-F3'],
      filterWidth: unknown(),
      filterWidthMin: unknown(),
      filterWidthMax: unknown(),
    },
    band: {
      currentBand: { status: 'unknown' },
      receiverBands: { main: { status: 'unknown' }, sub: { status: 'unknown' } },
      bandChoices: [],
      currentBandTx: 'denied',
      tuneMinHz: null,
      tuneMaxHz: 30_000_000,
    },
  } as unknown as RadioViewModel;
}

function view(preamp = 1, preValues = [0, 1, 2], mutex = false, indicators: ReceiverIndicatorViewModel[] = receiverIndicators()): RadioViewModel {
  const result = {
    topologyId: '2/main_sub', vfoScheme: 'main_sub', activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      { receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN', frequencyHz: 7_070_000, mode: 'LSB', filter: 'FIL2', isActive: true, isActiveSlot: true, isTxTarget: false },
      { receiver: 'SUB', slot: { kind: 'unslotted' }, label: 'SUB', frequencyHz: null, mode: null, filter: null, isActive: false, isActiveSlot: true, isTxTarget: false },
    ], split: { status: 'unknown' }, dualWatch: { status: 'unknown' }, txTarget: { status: 'unknown', reason: 'not-observed' }, txPermit: { status: 'unknown', reason: 'target-unknown' },
    scope: { hardwareScope: { structural: true, operational: true }, audioFftScope: { structural: false, operational: false } }, disabledReasons: mutex ? [{ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' }] : [], receiverIndicators: indicators, radioWideIndicators: { rfState: 'unknown', antenna: unknown(), atu: unknown(), ritActive: unknown(), ritOffset: unknown(), xitActive: unknown(), xitOffset: unknown(), actions: { main: { structural: false, operational: false }, sub: { structural: false, operational: false }, equalize: { structural: false, operational: false }, swap: { structural: false, operational: false } } },
    meters: { rfState: 'receiving', signal: known(12), power: unknown(), swr: unknown(), alc: unknown(), compression: unknown(), drainVoltage: unknown(), drainCurrent: unknown() },
    rfFrontEnd: { preamp: known(preamp), preValues, attenuator: known(0), attValues: [0, 20], rfGain: unknown(), squelch: unknown(), digiSel: unknown(), ipPlus: known(false) },
    scopeControls: { mode: known(0), edge: known(1), span: known(0), speed: known(0), hold: known(false), refDb: known(0), refRange: null, dual: known(false), receiver: known(0), duringTx: known(false), centerType: known(0), vbwNarrow: known(false), rbw: known(0) },
    txAux: { atu: unknown(), vox: known(false), voxGain: unknown(), antiVoxGain: unknown(), voxDelay: unknown(), compressor: known(false), compressorLevel: unknown(), monitor: unknown(), monitorLevel: unknown(), rfPower: unknown(), micGain: unknown(), driveGain: unknown() },
  } as unknown as RadioViewModel;
  return result;
}

function realScopeSource() {
    let receive = (_buffer: ArrayBuffer) => {};
    const producer = { subscribe: vi.fn((sink: (buffer: ArrayBuffer) => void) => {
      receive = sink; return () => {};
    }), close() {} };
    const stream = new ScopeStream(producer, { source: 'radio', sourceId: 'scope', epoch: 1 }, 'borrowed');
    const source = { subscribe: stream.subscribe.bind(stream), slotKeys: ['scope:0', 'scope:1'] as const };
    function fire(receiver: number, pixels: number[], outOfRange = false) {
      const buffer = new ArrayBuffer(16 + pixels.length), wire = new DataView(buffer);
      wire.setUint8(0, 1); wire.setUint8(1, receiver);
      wire.setUint32(3, 144_000_000, true); wire.setUint32(7, 144_500_000, true);
      wire.setUint8(13, outOfRange ? 1 : 0); wire.setUint16(14, pixels.length, true);
      new Uint8Array(buffer, 16).set(pixels); receive(buffer); flushSync();
    }
    return { stream, source, fire, producer };
}

describe('DualSdrFace', () => {
  it('consumes real wire frames through live, header-only out-of-range, and live recovery', async () => {
    const { stream, source, fire, producer } = realScopeSource();
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: source } });
    try {
      flushSync(); fire(0, [0, 128, 255]); await tick();
      expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('3');
      expect(() => fire(0, [], true)).not.toThrow(); await tick();
      expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('out-of-range');
      expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('0');
      expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.textContent).toContain('144.000–144.500 MHz');
      fire(0, [3, 17, 254]); await tick();
      expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
      expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('3');
      fire(0, [3, 17, 254], true); await tick();
      expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
      expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('3');
      fire(0, []); await tick();
      expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
      expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('0');
      expect(producer.subscribe).toHaveBeenCalledOnce();
    } finally { await unmount(component); stream.dispose(); }
  });
  it('delivers 100 pixel-only frames without reactive effects in the face, cluster, or waterfall', async () => {
    const { stream, source, fire, producer } = realScopeSource();
    const component = mount(DualSdrFace, { target: document.createElement('div'), props: { view: view(), scopeSource: source } });
    try {
      flushSync(); fire(0, [0, 128, 255]); await tick();
      expect(dualEffectRuns.size).toBeGreaterThan(0);
      const before = new Map(dualEffectRuns), receipt = stream.receipt;
      for (let index = 0; index < 100; index += 1) { fire(0, [index, 128, 255]); await tick(); }
      expect(stream.receipt).toBe(receipt + 100);
      expect(dualEffectRuns).toEqual(before);
      expect(producer.subscribe).toHaveBeenCalledOnce();
    } finally { await unmount(component); stream.dispose(); }
  });
  it('mounts one cluster per receiver, demuxes one scope subscription, and cleans it up', async () => {
    const listeners = new Set<(frame: ScopeFrame, key: string) => void>();
    const deliver = (frame: ScopeFrame, key: string) => { listeners.forEach(next => next(frame, key)); };
    const unsubscribe = vi.fn();
    const source = {
      subscribe: vi.fn((next: (frame: ScopeFrame, slotKey: string) => void) => {
        listeners.add(next); return () => { listeners.delete(next); unsubscribe(); };
      }),
      slotKeys: ['scope:0', 'scope:1'] as const,
    };
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: source } });
    await tick();
    expect(source.subscribe).toHaveBeenCalledTimes(2);
    expect(target.querySelectorAll('[data-receiver-cluster]').length).toBe(2);
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    const pixels = new Uint8Array([1, 2]);
    const parsed = wireFrame(pixels);
    deliver(parsed, 'scope:0');
    parsed.pixels![0] = 255;
    await tick();
    await Promise.resolve();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('unknown');
    expect(target.querySelector('[data-receiver-cluster="0"] canvas')?.getAttribute('data-supplied-pixels')).toBe('2');
    // MOR-4270: the typed pixel view rides the wire buffer with no copy —
    // a producer-side mutation after delivery is visible by design.
    expect(canvas.lineTo).toHaveBeenCalledWith(1, 0);
    deliver(wireFrame(new Uint8Array([3]), 3, 4, 1), 'scope:1');
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-scope-state]')?.getAttribute('data-scope-state')).toBe('frame');
    deliver(wireFrame(new Uint8Array([9]), 9, 10, 9), 'scope:9');
    await tick();
    // MOR-2692: the axis separator is ' · ' — never a dash.
    expect(target.querySelector('[data-receiver-cluster="1"] .axis')?.textContent).toContain('3 · 4');
    unmount(component);
    expect(unsubscribe).toHaveBeenCalledTimes(2);
  });

  it('clears both receiver frames when hardware scope health stops being live', async () => {
    const frameListeners = new Set<(frame: ScopeFrame, slotKey: string) => void>();
    const healthListeners = new Set<(live: boolean) => void>();
    const unsubscribeFrames = vi.fn();
    const unsubscribeHealth = vi.fn();
    const source = {
      subscribe: vi.fn((next: (frame: ScopeFrame, slotKey: string) => void) => {
        frameListeners.add(next);
        return () => { frameListeners.delete(next); unsubscribeFrames(); };
      }),
      subscribeHealth: vi.fn((next: (live: boolean) => void) => {
        healthListeners.add(next);
        return () => { healthListeners.delete(next); unsubscribeHealth(); };
      }),
      slotKeys: ['scope:0', 'scope:1'] as const,
    };
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: source } });
    await tick();
    frameListeners.forEach(next => next(wireFrame(new Uint8Array([1]), 1, 2, 0), 'scope:0'));
    frameListeners.forEach(next => next(wireFrame(new Uint8Array([2]), 3, 4, 1), 'scope:1'));
    await tick();
    expect(target.querySelectorAll('[data-scope-state="frame"]')).toHaveLength(2);
    healthListeners.forEach(next => next(false));
    await tick();
    expect(target.querySelectorAll('[data-scope-state="unknown"]')).toHaveLength(2);
    expect(target.querySelectorAll('[data-scope-state="frame"]')).toHaveLength(0);
    unmount(component);
    expect(unsubscribeFrames).toHaveBeenCalledTimes(2);
    expect(unsubscribeHealth).toHaveBeenCalledTimes(2);
  });

  it('keeps receiver-addressed meter and bandwidth facts with their own cluster', async () => {
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(1, [0, 1, 2], false, receiverIndicators(11, 2_400, 22, 3_100)), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-bandwidth]')?.textContent).toContain('2.4k');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-bandwidth]')?.textContent).toContain('3.1k');
    expect(meter.instances.map((instance) => instance.update.mock.calls[0]?.[0])).toEqual([11, 22]);
    unmount(component);
  });

  // MOR-2692: unread is an empty reserved slot — the old pin expected the
  // literal '—' fallback; its replacement pins the closed form (no '—'
  // anywhere) plus the reserved geometry below.
  it('keeps unknown receiver display unasserted and enables only backed PRE', async () => {
    const onPreChange = vi.fn();
    const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: view(1, [0, 1, 2], false, []), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const }, onPreChange } });
    await tick();
    expect(target.querySelector('[data-receiver-cluster="1"] [data-frequency]')?.textContent).toBe('');
    expect(target.querySelector('[data-receiver-cluster="1"] [data-needle]')).toBeNull();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(false); await pre.click(); await tick(); expect(onPreChange).toHaveBeenCalledWith(2);
    for (const name of ['hold', 'main-sub', 'dual', 'mode', 'edge', 'att', 'ip', 'agc', 'vox', 'comp', 'ant', 'menu1', 'cent-fix', 'expd-set']) expect(target.querySelector<HTMLButtonElement>(`[data-control="${name}"]`)?.disabled).toBe(true);
  });

  // MOR-2688 S3 literal pins: the P.AMP value and the scope MODE status go
  // through `readingText` — the MODE site's own formatter prefixes 'MODE ',
  // so under a `format → String` mutation it prints '0', never 'MODE 0'.
  it('renders the known P.AMP value and the literal MODE … status', async () => {
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(1, [0, 1, 2], false, []), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    expect(target.querySelector<HTMLButtonElement>('[data-control="pre"]')?.textContent).toBe('P.AMP1');
    expect(target.querySelector<HTMLElement>('.status')?.textContent).toBe('SPECTRUM SCOPE · MODE 0');
    unmount(component);
  });

  // MOR-2692 literal pins — no dash/question/placeholder-word token survives
  // anywhere in the rendered subtree of an all-unread face (the same token
  // rule the whole-page guard applies, run here without a browser).
  it('renders no placeholder token on the all-unread view', async () => {
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    const hits: string[] = [];
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      for (const hit of findPlaceholderTokens(n.nodeValue ?? '')) {
        hits.push(`${(n.parentElement?.tagName ?? '?').toLowerCase()}:${JSON.stringify(hit.token)}`);
      }
    }
    unmount(component);
    expect(hits).toEqual([]);
  });

  // MOR-2692 geometry pin: the mode/filter badge slots keep ONE reserved
  // width — derived from the mounted profile's choice sets (here: the
  // fixture's longest label 'DATA-FM-N' at 9ch) — in BOTH the unread state
  // (empty text) and a known state, so a first reading lights the slot
  // without moving its neighbours.
  it('reserves one badge width from the mounted profile choices in any state', async () => {
    const fixtureView = modeFilterView();
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: fixtureView, scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    // unread empty state — the literal literal pin: the longest mode label
    // on the fixture's choice set is 'DATA-FM-N' (9ch); filters top out at
    // 'WIDE-F3' (7ch).
    for (const clusterId of ['0', '1']) {
      for (const item of ['mode', 'filter'] as const) {
        const badge = target.querySelector<HTMLElement>(`[data-receiver-cluster="${clusterId}"] [data-badge="${item}"]`);
        expect(badge?.textContent).toBe('');
        expect(badge?.style.minWidth).toBe(item === 'mode' ? '9ch' : '7ch');
      }
    }
    unmount(component);
    target.remove();
  });

  // MOR-2692 geometry pin (PR-review fix): the frequency slot keeps its box
  // in the UNREAD state too — an empty output must not let the grid row
  // collapse. One-line height plus a width derived from the fixture's
  // tuning envelope ('30,000,000' → 10ch), not from a radio constant.
  it('reserves one frequency slot size in the unread state', async () => {
    const target = document.createElement('div');
    const component = mount(DualSdrFace, { target, props: { view: modeFilterView(), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    for (const clusterId of ['0', '1']) {
      const frequency = target.querySelector<HTMLElement>(`[data-receiver-cluster="${clusterId}"] [data-frequency]`);
      expect(frequency?.textContent).toBe('');
      expect(frequency?.style.minHeight).toBe('1em');
      expect(frequency?.style.minWidth).toBe('10ch');
    }
    unmount(component);
  });

  it.each([
    ['advances a middle declared PRE value', 1, [0, 1, 2], 2],
    ['wraps the last declared PRE value', 2, [0, 1, 2], 0],
  ])('%s', async (_label, current, values, expected) => {
    const onPreChange = vi.fn(); const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: view(current, values), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const }, onPreChange } }); await tick();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(false); pre.click(); expect(onPreChange).toHaveBeenCalledWith(expected);
  });

  it('fails PRE closed for absent current value, mutex, and unknown', async () => {
    const unavailable = view();
    (unavailable.rfFrontEnd!.preamp as { reading: unknown }).reading = unknown().reading;
    for (const candidate of [view(9), view(1, [0, 1, 2], true), unavailable]) {
      const onPreChange = vi.fn(); const target = document.createElement('div');
      mount(DualSdrFace, { target, props: { view: candidate, scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const }, onPreChange } }); await tick();
      const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
      expect(pre.disabled).toBe(true); pre.click(); expect(onPreChange).not.toHaveBeenCalled();
    }
  });

  // MOR-2704 G6: the pre-amp gate is the shared `usable` — a field that is
  // read (known reading) but not operational must stay inert.
  it('keeps PRE disabled for a preamp that is read but not operational', async () => {
    const candidate = view(1, [0, 1, 2]);
    (candidate.rfFrontEnd!.preamp as { availability: { structural: boolean; operational: boolean } }).availability = { structural: true, operational: false };
    const onPreChange = vi.fn(); const target = document.createElement('div');
    mount(DualSdrFace, { target, props: { view: candidate, scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const }, onPreChange } }); await tick();
    const pre = target.querySelector<HTMLButtonElement>('[data-control="pre"]')!;
    expect(pre.disabled).toBe(true); pre.click(); expect(onPreChange).not.toHaveBeenCalled();
  });

  it('uses the shared smoother lifecycle for receiver meter transitions and teardown', async () => {
    const target = document.createElement('div');
    const component = createClassComponent({ component: DualSdrFace, target, props: { view: view(), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
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
    const component = mount(DualSdrFace, { target, props: { view: view(), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
    await tick();
    expect(meter.reduced).toHaveBeenCalled();
    expect(target.querySelector('[data-receiver-cluster="0"] [data-needle]')?.getAttribute('data-reduced-motion')).toBe('true');
    unmount(component);
  });

  // MOR-2688 S4c cluster pins: `observationFixture` puts a vfo display
  // observation into every state the entry point must separate. A
  // `modeFilter` group is declared so the badge slots mount at all.
  function observationFixture(kind?: 'absent' | 'observed' | 'unknown'): RadioViewModel {
    const displayFor = (kind: 'absent' | 'observed' | 'unknown' | undefined) => {
      if (kind === 'observed') {
        return {
          mode: { state: 'stale' as const, value: 'USB' },
          filter: { state: 'current' as const, value: 'FIL1' },
          frequencyHz: { state: 'stale' as const, value: 7_100_250 },
        };
      }
      if (kind === 'unknown') {
        return {
          mode: { state: 'unknown' as const, reason: 'no-projection' },
          filter: { state: 'unknown' as const, reason: 'no-projection' },
          frequencyHz: { state: 'unknown' as const, reason: 'no-projection' },
        };
      }
      return undefined;
    };
    const vfo = (receiver: 'MAIN' | 'SUB', legacy: boolean) => ({
      receiver,
      slot: { kind: 'unslotted' },
      label: receiver,
      frequencyHz: legacy ? 7_070_000 : null,
      mode: legacy ? 'LSB' : null,
      filter: legacy ? 'FIL2' : null,
      display: displayFor(kind),
      isActive: receiver === 'MAIN',
      isActiveSlot: true,
      isTxTarget: false,
    });
    const base = view();
    return {
      ...base,
      vfos: [vfo('MAIN', true), vfo('SUB', false)],
      modeFilter: {
        activeFilterConfiguration: null,
        currentMode: unknown(),
        modeChoices: ['LSB', 'DATA-FM-N'],
        currentFilter: unknown(),
        filterChoices: ['FIL2', 'WIDE-F3'],
        filterWidth: unknown(),
        filterWidthMin: unknown(),
        filterWidthMax: unknown(),
      },
    } as unknown as RadioViewModel;
  }
  describe('ReceiverInstrumentCluster display observation pins (MOR-2688 S4c)', () => {
    it('an absent observation shows the legacy value and asserts current', async () => {
      const target = document.createElement('div');
      const component = mount(DualSdrFace, { target, props: { view: observationFixture('absent'), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
      await tick();
      for (const [badge, text] of [['mode', 'LSB'], ['filter', 'FIL2']] as const) {
        const node = target.querySelector<HTMLElement>(`[data-receiver-cluster="0"] [data-badge="${badge}"]`);
        expect(node?.textContent).toBe(text);
        expect(node?.getAttribute('data-state')).toBe('current');
      }
      const frequency = target.querySelector<HTMLElement>('[data-receiver-cluster="0"] [data-frequency]');
      expect(frequency?.textContent).toBe('7,070,000');
      expect(frequency?.getAttribute('data-state')).toBe('current');
      unmount(component);
    });

    it('a current or stale observation shows the observed value and asserts current', async () => {
      const target = document.createElement('div');
      const component = mount(DualSdrFace, { target, props: { view: observationFixture('observed'), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
      await tick();
      for (const [badge, text] of [['mode', 'USB'], ['filter', 'FIL1']] as const) {
        const node = target.querySelector<HTMLElement>(`[data-receiver-cluster="0"] [data-badge="${badge}"]`);
        expect(node?.textContent).toBe(text);
        expect(node?.getAttribute('data-state')).toBe('current');
      }
      const frequency = target.querySelector<HTMLElement>('[data-receiver-cluster="0"] [data-frequency]');
      expect(frequency?.textContent).toBe('7,100,250');
      expect(frequency?.getAttribute('data-state')).toBe('current');
      unmount(component);
    });

    it('a present unknown observation shows nothing — the legacy value never fires', async () => {
      const target = document.createElement('div');
      const component = mount(DualSdrFace, { target, props: { view: observationFixture('unknown'), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
      await tick();
      for (const badge of ['mode', 'filter'] as const) {
        const node = target.querySelector<HTMLElement>(`[data-receiver-cluster="0"] [data-badge="${badge}"]`);
        expect(node?.textContent).toBe('');
        expect(node?.getAttribute('data-state')).toBe('unknown');
      }
      const frequency = target.querySelector<HTMLElement>('[data-receiver-cluster="0"] [data-frequency]');
      expect(frequency?.textContent).toBe('');
      expect(frequency?.getAttribute('data-state')).toBe('unknown');
      unmount(component);
    });

    // frequencyCh / bandwidthCh: the reserved widths while a value is
    // present — from the legacy/absent path (7,070,000 → 9ch) and the
    // widest of the fixture's filter choices 'WIDE-F3'/BW '2.4k' (9ch).
    it('reserves the frequency and bandwidth widths with literal ch pins', async () => {
      const target = document.createElement('div');
      const component = mount(DualSdrFace, { target, props: { view: observationFixture('absent'), scopeSource: { subscribe: () => () => {}, slotKeys: ['scope:0', 'scope:1'] as const } } });
      await tick();
      const frequency = target.querySelector<HTMLElement>('[data-receiver-cluster="0"] [data-frequency]');
      expect(frequency?.style.minWidth).toBe('9ch');
      const bandwidth = target.querySelector<HTMLElement>('[data-receiver-cluster="0"] [data-bandwidth]');
      expect(bandwidth?.textContent).toBe('BW 2.4k');
      expect(bandwidth?.style.minWidth).toBe('9ch');
      unmount(component);
    });
  });

  it('replaces the scope source reactively and cleans each subscription once', async () => {
    const SLOTS = ['scope:0', 'scope:1'] as const;
    const cleanupA = vi.fn(); const cleanupB = vi.fn();
    const sourceA = { subscribe: vi.fn(() => cleanupA), slotKeys: SLOTS };
    const sourceB = { subscribe: vi.fn(() => cleanupB), slotKeys: SLOTS };
    const target = document.createElement('div');
    const component = createClassComponent({ component: DualSdrFace, target, props: { view: view(), scopeSource: sourceA } });
    await tick();
    expect(sourceA.subscribe).toHaveBeenCalledTimes(2);
    component.$set({ scopeSource: sourceB });
    await tick();
    expect(cleanupA).toHaveBeenCalledTimes(2);
    expect(sourceB.subscribe).toHaveBeenCalledTimes(2);
    component.$destroy();
    expect(cleanupB).toHaveBeenCalledTimes(2);
  });
});
