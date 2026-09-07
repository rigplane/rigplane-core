import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { SignalMeterFrame } from '../../components-v2/meters/signal-meter-motion.svelte';
import type { SignalMeterProjection } from '../../components-v2/meters/smeter-scale';
import { toSignalMeterRendererView } from '../meter-renderer-view';

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
});
