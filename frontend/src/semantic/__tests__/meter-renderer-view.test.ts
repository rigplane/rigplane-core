import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { SignalMeterFrame } from '../../components-v2/meters/signal-meter-motion.svelte';
import type { SignalMeterProjection } from '../../components-v2/meters/smeter-scale';
import { SUPPLY_VOLTAGE_WINDOW } from '../../components-v2/panels/meter-utils';
import type { LevelMeterKey } from '../bar-meter-projector';
import type { StationLevelMeterFrame } from '../StationMeterInstrumentHost.svelte';
import { toLevelMeterRendererView, toSignalMeterRendererView } from '../meter-renderer-view';

function capabilities(): Capabilities {
  return {
    model: 'public-meter-test', scope: false, audio: false, tx: false,
    capabilities: [], receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: 0,
    meterCalibrations: { s_meter: [
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 130, actual: 0, label: 'S9' },
      { raw: 240, actual: 40, label: 'S9+40' },
    ] },
  };
}

beforeEach(() => setCapabilities(capabilities()));
afterEach(() => clearCapabilities());

function frame(overrides: Partial<SignalMeterProjection> = {}): SignalMeterFrame {
  return {
    projection: {
      scaleMode: 's', motionFraction: 0.4,
      primaryText: 'S7', secondaryText: '−85 dBm',
      accessibleDescription: 'S meter S7, −85 dBm', crossoverFraction: 0.55,
      marks: [{ actual: 0, fraction: 0.55, text: '9', color: 'red' }],
      ticks: [{ fraction: 0.1, kind: 'mid', color: 'white' }],
      ...overrides,
    },
    smoothedFraction: 0.35,
    peakFraction: 0.6,
  };
}

function levelFrame(
  key: LevelMeterKey = 'power',
  projection: Record<string, unknown> = {},
  motion: Record<string, unknown> = {},
): StationLevelMeterFrame {
  return {
    projection: {
      key, label: key === 'power' ? 'Po' : key, evidence: { state: 'current', value: 50 },
      relevant: true, observed: true, state: 'current',
      domain: { kind: 'engineering', unit: key === 'swr' ? 'ratio' : 'w' },
      motionFraction: 0.25, scale: null, displayText: '50W', stateText: '',
      accessibleDescription: 'Po: Current observation. 50W', fault: false,
      showPeak: true, gauge: true,
      ...(key === 'swr' ? { ratioScale: true } : {}),
      ...projection,
    },
    motion: { smoothedFraction: 0.2, peakFraction: 0.4, ...motion },
  } as StationLevelMeterFrame;
}

function expectFrozenDataGraph(value: object): void {
  const seen = new Set<object>();
  const visit = (item: unknown): void => {
    expect(typeof item).not.toBe('symbol');
    expect(typeof item).not.toBe('function');
    if (item === null || typeof item !== 'object' || seen.has(item)) return;
    seen.add(item);
    expect(Object.isFrozen(item)).toBe(true);
    for (const key of Reflect.ownKeys(item)) {
      expect(typeof key).toBe('string');
      expect(String(key)).not.toMatch(/source|session|binding|frame|callback|getter|owner|resetPeak/i);
      const descriptor = Object.getOwnPropertyDescriptor(item, key)!;
      expect('value' in descriptor).toBe(true);
      if ('value' in descriptor) visit(descriptor.value);
    }
  };
  visit(value);
}

describe('toSignalMeterRendererView', () => {
  it('copies only the public graph and freezes it recursively', () => {
    const source = frame();
    const view = toSignalMeterRendererView(
      source, { status: 'known', value: -12 }, { kind: 'engineering', unit: 'db' },
    );

    expect(Reflect.ownKeys(view)).toEqual([
      'kind', 'evidence', 'scaleMode', 'displayedFraction', 'peakFraction',
      'primaryText', 'secondaryText', 'accessibleDescription', 'crossoverFraction',
      'marks', 'ticks',
    ]);
    expect(view).toMatchObject({
      evidence: { state: 'current', value: -12, domain: { kind: 'engineering', unit: 'db' } },
      displayedFraction: 0.35, peakFraction: 0.6,
    });
    expect(view.marks).toEqual([{ actual: 0, fraction: 0.55, text: '9' }]);
    expect(view.ticks).toEqual([{ fraction: 0.1, kind: 'mid' }]);
    expect(view.marks).not.toBe(source.projection.marks);
    expect(view.ticks).not.toBe(source.projection.ticks);
    expect(JSON.stringify(view)).not.toMatch(/color|source|session|binding/);
    expect([
      view, view.evidence, view.evidence.domain, view.marks, view.marks[0],
      view.ticks, view.ticks[0],
    ].every(Object.isFrozen)).toBe(true);
    expect([view, view.evidence, view.evidence.domain, view.marks[0], view.ticks[0]]
      .flatMap((item) => Object.values(Object.getOwnPropertyDescriptors(item)))
      .every((descriptor) => 'value' in descriptor)).toBe(true);
    expect(() => (view.marks as unknown as Array<SignalMeterProjection['marks'][number]>).push({
      actual: 20, fraction: 0.7, text: '+20', color: 'orange',
    })).toThrow();
    expect(source.projection.marks).toEqual([
      { actual: 0, fraction: 0.55, text: '9', color: 'red' },
    ]);
  });

  it.each([
    ['raw', { kind: 'raw' } as const, 'raw'],
    ['unknown', { kind: 'unknown' } as const, 'unknown'],
  ])('preserves an explicit %s domain without engineering relabeling', (_name, domain, kind) => {
    const view = toSignalMeterRendererView(
      frame({ scaleMode: kind === 'raw' ? 'raw' : 'none' }),
      { status: 'known', value: 53 }, domain,
    );
    expect(view.evidence).toEqual({ state: 'current', value: 53, domain });
  });

  it.each(['s', 'raw'] as const)('downgrades the omitted legacy %s domain without inference',
    (scaleMode) => {
      const view = toSignalMeterRendererView(
        frame({ scaleMode }), { status: 'known', value: 12 }, undefined,
      );
      expect(view.evidence).toEqual({ state: 'current', value: 12, domain: { kind: 'unknown' } });
      expect(view).toMatchObject({
        scaleMode: 'none', displayedFraction: null, peakFraction: null,
        crossoverFraction: null, marks: [], ticks: [], primaryText: '12',
        secondaryText: 'unit unknown',
      });
      expect(`${view.primaryText} ${view.secondaryText}`).not.toMatch(/dBm|rel S9|uncalibrated/);
    });

  it('keeps qualified dB-relative-to-S9 evidence when calibrated geometry is unavailable', () => {
    const view = toSignalMeterRendererView(frame({
      scaleMode: 'none', motionFraction: null, primaryText: '−12 dB rel S9',
      secondaryText: 'scale unavailable', accessibleDescription: 'minus 12 dB relative to S9',
      crossoverFraction: null, marks: [], ticks: [],
    }), { status: 'known', value: -12 }, { kind: 'engineering', unit: 'db' });

    expect(view.evidence).toEqual({
      state: 'current', value: -12, domain: { kind: 'engineering', unit: 'db' },
    });
    expect(view).toMatchObject({ displayedFraction: null, peakFraction: null });
    expect(`${view.primaryText} ${view.secondaryText}`).not.toContain('dBm');
  });

  it('uses canonical unknown text and no live geometry for a stale valid frame', () => {
    const view = toSignalMeterRendererView(frame(),
      { status: 'unknown' }, { kind: 'engineering', unit: 'db' });
    expect(view.evidence).toEqual({
      state: 'unknown', domain: { kind: 'engineering', unit: 'db' },
    });
    expect(view.scaleMode).toBe('s');
    expect(view.marks.length).toBeGreaterThan(0);
    expect(view.ticks.length).toBeGreaterThan(0);
    expect(view.displayedFraction).toBeNull();
    expect(view.peakFraction).toBeNull();
    expect(`${view.primaryText} ${view.secondaryText}`).not.toContain('dBm');
  });

  it('turns a non-finite internal reading into unknown evidence', () => {
    const view = toSignalMeterRendererView(
      frame(),
      { status: 'known', value: Number.NaN },
      { kind: 'engineering', unit: 'db' },
    );
    expect(view.evidence).toEqual({
      state: 'unknown', domain: { kind: 'engineering', unit: 'db' },
    });
    expect(view.displayedFraction).toBeNull();
  });

  it.each([
    ['motion', () => frame({ motionFraction: 1.1 })],
    ['displayed', () => ({ ...frame(), smoothedFraction: Number.NaN })],
    ['peak', () => ({ ...frame(), peakFraction: Infinity })],
  ])('drops invalid %s live geometry without erasing the valid static scale', (_name, makeFrame) => {
    expect(toSignalMeterRendererView(
      makeFrame(), { status: 'known', value: 0 }, { kind: 'engineering', unit: 'db' },
    )).toMatchObject({
      scaleMode: 's', displayedFraction: null, peakFraction: null,
    });
  });

  it.each([
    ['crossover', () => frame({ crossoverFraction: Number.NaN })],
    ['mark', () => frame({ marks: [{ actual: 0, fraction: -0.1, text: '9', color: '' }] })],
    ['mark actual', () => frame({ marks: [{ actual: Number.NaN, fraction: 0.1, text: '9', color: '' }] })],
    ['tick', () => frame({ ticks: [{ fraction: Infinity, kind: 'minor', color: '' }] })],
  ])('drops invalid %s static geometry instead of clamping or normalizing', (_name, makeFrame) => {
    expect(toSignalMeterRendererView(
      makeFrame(), { status: 'known', value: 0 }, { kind: 'engineering', unit: 'db' },
    )).toMatchObject({
      scaleMode: 'none', displayedFraction: null, peakFraction: null,
      crossoverFraction: null, marks: [], ticks: [],
    });
  });

  it('copies explicit false station relevance without changing receiver keys', () => {
    const receiver = toSignalMeterRendererView(
      frame(), { status: 'known', value: -12 }, { kind: 'engineering', unit: 'db' },
    );
    const station = toSignalMeterRendererView(
      frame(), { status: 'known', value: -12 }, { kind: 'engineering', unit: 'db' }, false,
    );
    expect(Object.hasOwn(receiver, 'relevant')).toBe(false);
    expect(Object.hasOwn(station, 'relevant')).toBe(true);
    expect(station.relevant).toBe(false);
  });
});

describe('toLevelMeterRendererView', () => {
  it('copies only the public level graph and freezes fresh evidence/domain objects', () => {
    const source = levelFrame();
    const view = toLevelMeterRendererView(source);
    expect(Reflect.ownKeys(view)).toEqual([
      'kind', 'key', 'label', 'evidence', 'relevant', 'observed',
      'displayedFraction', 'peakFraction', 'scale', 'displayText', 'stateText',
      'accessibleDescription', 'gauge', 'fault', 'peakEnabled',
    ]);
    expect(view).toMatchObject({
      kind: 'level', key: 'power',
      evidence: { state: 'current', value: 50, domain: { kind: 'engineering', unit: 'w' } },
      displayedFraction: 0.2, peakFraction: 0.4, peakEnabled: true,
    });
    expect(view.evidence).not.toBe(source.projection.evidence);
    expect(view.evidence.domain).not.toBe(source.projection.domain);
    expect([view, view.evidence, view.evidence.domain].every(Object.isFrozen)).toBe(true);
    expectFrozenDataGraph(view);
  });

  it.each([
    'power', 'swr', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
  ] as const)('keeps the exact public key set for %s and maps an omitted domain to unknown', (key) => {
    const view = toLevelMeterRendererView(levelFrame(key, { domain: undefined }));
    const expected = [
      'accessibleDescription', 'displayText', 'displayedFraction', 'evidence', 'fault',
      'gauge', 'key', 'kind', 'label', 'observed', 'peakEnabled', 'peakFraction',
      'relevant', 'scale', 'stateText', ...(key === 'swr' ? ['ratioScale'] : []),
    ].sort();
    expect(Reflect.ownKeys(view).sort()).toEqual(expected);
    expect(view.evidence.domain).toEqual({ kind: 'unknown' });
    expectFrozenDataGraph(view);
  });

  it('retains stale numeric evidence while suppressing live geometry', () => {
    const view = toLevelMeterRendererView(levelFrame('power', {
      evidence: { state: 'stale', value: 170 }, observed: false, state: 'stale',
      domain: { kind: 'raw' }, motionFraction: null, displayText: 'STALE',
      stateText: 'STALE', accessibleDescription: 'Po: Stale observation', showPeak: false,
    }, { smoothedFraction: 0.8, peakFraction: 0.9 }));

    expect(view).toMatchObject({
      kind: 'level', key: 'power',
      evidence: { state: 'stale', value: 170, domain: { kind: 'raw' } },
      displayedFraction: null, peakFraction: null, peakEnabled: false,
    });
    expect(Object.isFrozen(view)).toBe(true);
    expect(Object.isFrozen(view.evidence)).toBe(true);
    expect(Object.isFrozen(view.evidence.domain)).toBe(true);
  });

  // MUTATION KILLED: `toLevelMeterRendererView`'s `live` gate keying only on
  // `evidence.state === 'current'` — a stale reading with otherwise-valid
  // geometry then loses its fill/peak while an identical current one keeps
  // it. R29: stale renders identically to current, fill included.
  it('keeps displayedFraction/peakFraction for a stale reading identical to current (R29)', () => {
    const frameFor = (state: 'current' | 'stale') => levelFrame('power', {
      evidence: { state, value: 50 }, state,
    }, { smoothedFraction: 0.6666666666666666, peakFraction: 0.8 });
    const current = toLevelMeterRendererView(frameFor('current'));
    const stale = toLevelMeterRendererView(frameFor('stale'));
    expect(current.displayedFraction).toBe(0.6666666666666666);
    expect(current.peakFraction).toBe(0.8);
    expect(stale.displayedFraction).toBe(current.displayedFraction);
    expect(stale.peakFraction).toBe(current.peakFraction);
  });

  it.each([
    ['idle', 'idle'], ['unknown', 'unknown'], ['unsupported', 'unsupported'],
  ] as const)('keeps %s evidence nonnumeric and live geometry absent', (_name, state) => {
    const view = toLevelMeterRendererView(levelFrame('power', {
      evidence: { state }, state, observed: false, motionFraction: 0.25,
    }));
    expect(view.evidence).toEqual({ state, domain: { kind: 'engineering', unit: 'w' } });
    expect(view.displayedFraction).toBeNull();
    expect(view.peakFraction).toBeNull();
    expect(Object.hasOwn(view.evidence, 'value')).toBe(false);
  });

  it('fails non-finite numeric evidence closed without rewriting passive text', () => {
    const view = toLevelMeterRendererView(levelFrame('power', {
      evidence: { state: 'current', value: Number.NaN }, displayText: 'bad sample',
    }));
    expect(view.evidence).toEqual({
      state: 'unknown', domain: { kind: 'engineering', unit: 'w' },
    });
    expect(view.displayedFraction).toBeNull();
    expect(view.peakFraction).toBeNull();
    expect(view.displayText).toBe('bad sample');
  });

  it.each([
    ['projected target', { motionFraction: 1.1 }, {}, null, null],
    ['smoothed fill', {}, { smoothedFraction: Number.NaN }, null, null],
    ['peak only', {}, { peakFraction: Infinity }, 0.2, null],
    ['peak disabled', { showPeak: false }, {}, 0.2, null],
  ] as const)('validates %s independently', (_name, projection, motion, fill, peak) => {
    expect(toLevelMeterRendererView(levelFrame('power', projection, motion))).toMatchObject({
      displayedFraction: fill, peakFraction: peak,
    });
  });

  // T171: `MeterRendererSeat.svelte` passes an external level renderer this
  // view plus a reset-peak lease, and nothing else of the frame — so a scale
  // left out of the copy is unreachable from a face however faithfully
  // `LevelMeterProjection.scale` is derived.
  it.each([
    ['drainVoltage', { kind: 'engineering', unit: 'v' }, SUPPLY_VOLTAGE_WINDOW],
    ['drainCurrent', { kind: 'engineering', unit: 'a' }, { min: 0, max: 1.0 }],
    ['power', { kind: 'raw' }, null],
  ] as const)('carries the %s projection scale into the public view', (key, domain, scale) => {
    const view = toLevelMeterRendererView(levelFrame(key, { domain, scale }));
    expect(view.scale).toEqual(scale);
    expectFrozenDataGraph(view);
  });

  it('copies the scale rather than publishing the projection object itself', () => {
    const view = toLevelMeterRendererView(levelFrame('drainVoltage', {
      domain: { kind: 'engineering', unit: 'v' }, scale: SUPPLY_VOLTAGE_WINDOW,
    }));
    expect(view.scale).not.toBe(SUPPLY_VOLTAGE_WINDOW);
    expect(Object.isFrozen(view.scale)).toBe(true);
  });

  it('emits ratioScale only for the SWR discriminant', () => {
    const swr = toLevelMeterRendererView(levelFrame('swr', {
      label: 'SWR', ratioScale: false, showPeak: false,
      domain: { kind: 'raw' }, displayText: '120 raw',
    }));
    const power = toLevelMeterRendererView(levelFrame());
    expect(swr).toMatchObject({ key: 'swr', ratioScale: false });
    expect(Object.hasOwn(power, 'ratioScale')).toBe(false);
  });
});
