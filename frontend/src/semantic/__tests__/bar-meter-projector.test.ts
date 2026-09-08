import { afterEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { topologyFixtures, withMeters, withTxAux } from '../fixtures/topologies';
import type {
  DisplayObservation,
  MeterRfState,
  MeterSourceIdentity,
  MetersViewModel,
  RadioViewModel,
} from '../radio-view-model';
import {
  projectBarMeters,
  projectSwrMeter,
  projectTxMeterPresentation,
  type BarMeterKey,
} from '../bar-meter-projector';

function base(rfState: MeterRfState = 'transmitting'): RadioViewModel {
  const view = withMeters(withTxAux(topologyFixtures['1/single']), rfState);
  view.txAux!.compressor = {
    reading: { status: 'known', value: true },
    availability: { structural: true, operational: true },
  };
  return view;
}

function setMeter(
  view: RadioViewModel,
  key: BarMeterKey,
  changes: Partial<MetersViewModel[BarMeterKey]>,
): void {
  view.meters![key] = { ...view.meters![key], ...changes };
}

function setDisplay(
  view: RadioViewModel,
  key: 'power' | 'alc',
  display: DisplayObservation<number>,
): void {
  setMeter(view, key, { display });
}

function calibratedCaps(): Capabilities {
  return {
    model: 'bar-projector-test',
    scope: false,
    audio: false,
    tx: true,
    capabilities: ['tx'],
    receivers: 1,
    vfoScheme: 'single',
    freqRanges: [],
    modes: [],
    filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: {
      power: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 255, actual: 200, label: '200' },
      ],
      alc: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 120, actual: 1, label: '1' },
      ],
    },
  };
}

afterEach(() => clearCapabilities());

describe('projectBarMeters', () => {
  it('retains TX evidence and the live value for both current and stale (R29/R32)', () => {
    const current = base();
    setDisplay(current, 'power', { state: 'current', value: 75 });
    expect(projectTxMeterPresentation(current.meters!.power, 'transmitting')).toMatchObject({
      state: 'current', evidence: { state: 'current', value: 75 }, value: 75,
    });

    setDisplay(current, 'power', { state: 'stale', value: 75 });
    expect(projectTxMeterPresentation(current.meters!.power, 'transmitting')).toMatchObject({
      state: 'stale', evidence: { state: 'stale', value: 75 }, value: 75,
    });
    expect(projectTxMeterPresentation({
      ...current.meters!.power,
      availability: { structural: false, operational: false },
    }, 'transmitting')).toMatchObject({
      state: 'unsupported', evidence: { state: 'unsupported' }, value: null,
    });
  });

  it('projects the five real rows in priority order with only consumed fields', () => {
    const view = base();
    const source = {
      providerGeneration: 7,
      scope: 'radio',
      receiver: null,
      path: 'powerMeter',
    } as const satisfies MeterSourceIdentity;
    setMeter(view, 'power', { reading: { status: 'known', value: 128 }, source });
    setMeter(view, 'alc', { source: null });

    const projected = projectBarMeters(view);
    expect(projected.map(({ key, label }) => ({ key, label }))).toEqual([
      { key: 'power', label: 'Po' },
      { key: 'alc', label: 'ALC' },
      { key: 'drainCurrent', label: 'Id' },
      { key: 'drainVoltage', label: 'Vd' },
      { key: 'compression', label: 'COMP' },
    ]);
    expect(Object.keys(projected[0]).sort()).toEqual([
      'accessibleDescription', 'displayText', 'domain', 'evidence', 'fault', 'gauge', 'key',
      'label', 'motionFraction', 'observed', 'relevant', 'showPeak', 'source',
      'state', 'stateText',
    ]);
    expect(projected[0]).toMatchObject({
      motionFraction: 128 / 255,
      displayText: '128 raw',
      accessibleDescription: 'Po: Observed. 128 raw',
      observed: true,
      gauge: true,
      showPeak: true,
    });
    expect(projected[0].source).toBe(source);
    expect(projected[1].source).toBeNull();
    expect(projected[2].source).toBeUndefined();
  });

  it('uses calibrated power and ALC values once and preserves a current zero', () => {
    setCapabilities(calibratedCaps());
    const view = base();
    setMeter(view, 'power', { reading: { status: 'unknown' } });
    setDisplay(view, 'power', { state: 'current', value: 50 });
    setDisplay(view, 'alc', { state: 'current', value: 0.95 });

    const projected = projectBarMeters(view);
    expect(projected.find(({ key }) => key === 'power')).toMatchObject({
      evidence: { state: 'current', value: 50 },
      motionFraction: 0.25,
      displayText: '50W',
      observed: true,
      gauge: true,
    });
    expect(projected.find(({ key }) => key === 'alc')).toMatchObject({
      motionFraction: 0.95,
      displayText: '95%',
      fault: true,
    });

    setDisplay(view, 'power', { state: 'current', value: 0 });
    expect(projectBarMeters(view).find(({ key }) => key === 'power')).toMatchObject({
      motionFraction: 0,
      displayText: '0W',
      observed: true,
      gauge: true,
    });
  });

  // MOR-2425/R41: the description says 'Observed' for BOTH stale and current.
  it('keeps stale, unknown, idle, and indeterminate TX observations distinct (R29/R32: stale keeps its value; idle/unknown are an empty scale)', () => {
    const view = base();
    setDisplay(view, 'power', { state: 'stale', value: 170 });
    setMeter(view, 'power', { domain: { kind: 'raw' } });
    expect(projectBarMeters(view)[0]).toMatchObject({
      evidence: { state: 'stale', value: 170 },
      state: 'stale',
      domain: { kind: 'raw' },
      motionFraction: 170 / 255,
      displayText: '170 raw',
      accessibleDescription: 'Po: Observed. 170 raw',
      observed: true,
      gauge: true,
      showPeak: false,
    });

    setDisplay(view, 'power', { state: 'unknown', reason: 'not-observed' });
    expect(projectBarMeters(view)[0]).toMatchObject({
      evidence: { state: 'unknown' },
      state: 'unknown',
      motionFraction: null,
      displayText: '',
      accessibleDescription: 'Po: No reading',
      observed: false,
      gauge: true,
    });

    view.meters!.rfState = 'receiving';
    setMeter(view, 'power', { relevant: false });
    setDisplay(view, 'power', { state: 'current', value: 170 });
    expect(projectBarMeters(view)[0]).toMatchObject({
      evidence: { state: 'idle' },
      state: 'idle',
      motionFraction: null,
      displayText: '',
      accessibleDescription: 'Po: Not measuring in receive',
      observed: false,
      gauge: true,
    });

    view.meters!.rfState = 'unknown';
    setMeter(view, 'power', { relevant: true });
    expect(projectBarMeters(view)[0]).toMatchObject({
      state: 'current',
      motionFraction: 170 / 255,
      displayText: '170 raw ?',
      accessibleDescription: 'Po: RF relevance indeterminate. Observed. 170 raw',
      observed: true,
      gauge: true,
    });
  });

  it('keeps TX and plain rows both mounted with an empty scale when unobserved (MOR-2425)', () => {
    const view = base();
    for (const key of ['power', 'drainCurrent'] as const) {
      setMeter(view, key, {
        reading: { status: 'unknown' },
        availability: { structural: true, operational: false },
      });
    }

    expect(projectBarMeters(view).find(({ key }) => key === 'power')).toMatchObject({
      gauge: true,
      observed: false,
      motionFraction: null,
      displayText: '',
    });
    expect(projectBarMeters(view).find(({ key }) => key === 'drainCurrent')).toMatchObject({
      evidence: { state: 'unknown' },
      gauge: true,
      observed: false,
      motionFraction: null,
      displayText: '',
    });
  });

  it('does not retain a known non-TX sample when operational availability is false', () => {
    const view = base();
    setMeter(view, 'drainVoltage', {
      reading: { status: 'known', value: 13.8 },
      availability: { structural: true, operational: false },
    });
    expect(projectBarMeters(view).find(({ key }) => key === 'drainVoltage')).toMatchObject({
      evidence: { state: 'unknown' }, state: 'unknown', observed: false,
      motionFraction: null, displayText: '',
    });
  });

  it('expresses absence by list membership and fails the COMP gate closed', () => {
    const view = base();
    setMeter(view, 'drainVoltage', {
      availability: { structural: false, operational: false },
    });
    view.txAux!.compressor = {
      reading: { status: 'unknown' },
      availability: { structural: true, operational: true },
    };

    expect(projectBarMeters(view).map(({ key }) => key)).toEqual([
      'power', 'alc', 'drainCurrent',
    ]);
    view.txAux!.compressor = {
      reading: { status: 'known', value: false },
      availability: { structural: true, operational: true },
    };
    expect(projectBarMeters(view).some(({ key }) => key === 'compression')).toBe(false);
  });

  it('asserts an ALC fault for an observed relevant calibrated reading, current or stale (R29)', () => {
    setCapabilities(calibratedCaps());
    const view = base();
    setDisplay(view, 'alc', { state: 'current', value: 0.95 });
    expect(projectBarMeters(view).find(({ key }) => key === 'alc')?.fault).toBe(true);

    setMeter(view, 'alc', { relevant: false });
    expect(projectBarMeters(view).find(({ key }) => key === 'alc')?.fault).toBe(false);

    setMeter(view, 'alc', { relevant: true });
    setDisplay(view, 'alc', { state: 'stale', value: 0.95 });
    expect(projectBarMeters(view).find(({ key }) => key === 'alc')?.fault).toBe(true);
  });

  it('arbitrates identical current values by each field\'s explicit domain', () => {
    setCapabilities(calibratedCaps());
    const raw = base();
    setDisplay(raw, 'power', { state: 'current', value: 50 });
    setMeter(raw, 'power', { domain: { kind: 'raw' } });
    expect(projectBarMeters(raw)[0]).toMatchObject({
      state: 'current',
      domain: { kind: 'raw' },
      motionFraction: 50 / 255,
      displayText: '50 raw',
      fault: false,
      showPeak: false,
    });

    const engineering = base();
    setDisplay(engineering, 'power', { state: 'current', value: 50 });
    setMeter(engineering, 'power', { domain: { kind: 'engineering', unit: 'w' } });
    expect(projectBarMeters(engineering)[0]).toMatchObject({
      state: 'current',
      domain: { kind: 'engineering', unit: 'w' },
      motionFraction: 0.25,
      displayText: '50W',
      showPeak: true,
    });

    const unknown = base();
    setDisplay(unknown, 'power', { state: 'current', value: 50 });
    setMeter(unknown, 'power', { domain: { kind: 'unknown' } });
    expect(projectBarMeters(unknown)[0]).toMatchObject({
      state: 'current',
      domain: { kind: 'unknown' },
      motionFraction: null,
      displayText: '50 unit unknown',
      fault: false,
      showPeak: false,
    });
  });

  it('projects SWR once with explicit state/domain and no raw-derived fault', () => {
    setCapabilities(calibratedCaps());
    const view = base();
    view.meters!.swr = {
      ...view.meters!.swr,
      domain: { kind: 'raw' },
      display: { state: 'current', value: 120 },
    };
    expect(projectSwrMeter(view)).toMatchObject({
      key: 'swr',
      state: 'current',
      domain: { kind: 'raw' },
      motionFraction: 120 / 255,
      displayText: '120 raw',
      ratioScale: false,
      fault: false,
      showPeak: false,
    });

    view.meters!.swr = {
      ...view.meters!.swr,
      domain: { kind: 'unknown' },
    };
    expect(projectSwrMeter(view)).toMatchObject({
      state: 'current',
      domain: { kind: 'unknown' },
      motionFraction: null,
      displayText: '120 unit unknown',
      ratioScale: false,
      fault: false,
    });

    view.meters!.swr = {
      ...view.meters!.swr,
      domain: { kind: 'engineering', unit: 'ratio' },
      display: { state: 'current', value: 2.25 },
    };
    expect(projectSwrMeter(view)).toMatchObject({
      state: 'current',
      motionFraction: null,
      displayText: '2.3',
      ratioScale: false,
      fault: true,
    });
  });
});
