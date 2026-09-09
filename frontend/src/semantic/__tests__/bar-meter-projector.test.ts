import { afterEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { topologyFixtures, withMeters, withTxAux } from '../fixtures/topologies';
import type {
  DisplayObservation,
  MeterRfState,
  MeterSourceIdentity,
  MeterValueDomain,
  MetersViewModel,
  RadioViewModel,
} from '../radio-view-model';
import {
  projectBarMeters,
  projectSwrMeter,
  projectTxMeterPresentation,
  type BarMeterKey,
  type BarMeterProjection,
  type LevelMeterKey,
  type SwrMeterProjection,
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
      'label', 'motionFraction', 'observed', 'presence', 'relevant', 'scale', 'showPeak', 'source',
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

    // MOR-2425/R41: the ' ?' indeterminate glyph is symmetric — a retained
    // stale reading gets the same text as current, not just current.
    setDisplay(view, 'power', { state: 'stale', value: 170 });
    expect(projectBarMeters(view)[0]).toMatchObject({
      state: 'stale',
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

// ---------------------------------------------------------------------------
// Bar scale domains (T168). The product owns value-to-position, so it must
// also publish the two numbers the position was computed from — otherwise a
// face labelling the scale from anywhere else prints ends the fill does not
// agree with. Wherever a case has both a scale and a reading it asserts the
// scale AND that `motionFraction` is that scale's own position of the
// reading; a scale that diverged from the level function fails the second
// assertion even when the first still passes.
// ---------------------------------------------------------------------------

const FTX1_DRAIN_CALS = {
  vd: [
    { raw: 0, actual: 0.0, label: '0' },
    { raw: 210, actual: 13.8, label: '13.8' },
  ],
  id: [
    { raw: 0, actual: 0.0, label: '0' },
    { raw: 29, actual: 1.0, label: '1.0' },
  ],
};

function drainCaps(): Capabilities {
  const base = calibratedCaps();
  return { ...base, meterCalibrations: { ...base.meterCalibrations, ...FTX1_DRAIN_CALS } };
}

function positionOn(scale: { min: number; max: number } | null, value: number): number {
  if (!scale) throw new Error('no scale to position against');
  return (value - scale.min) / (scale.max - scale.min);
}

describe('projectBarMeters — scale domains (T168)', () => {
  it('draws the supply-voltage bar against the fixed 11-15 V window, fill included', () => {
    setCapabilities(drainCaps());
    const view = base();
    setMeter(view, 'drainVoltage', {
      reading: { status: 'known', value: 12.0 },
      domain: { kind: 'engineering', unit: 'v' },
    });

    const vd = projectBarMeters(view).find(({ key }) => key === 'drainVoltage')!;
    expect(vd.scale).toEqual({ min: 11, max: 15 });
    expect(vd.motionFraction).toBeCloseTo(positionOn(vd.scale, 12.0));
    expect(vd.motionFraction).toBeCloseTo(0.25);
  });

  it('publishes the window even with nothing to show on it, so an empty bar can still be labelled', () => {
    setCapabilities(drainCaps());
    const view = base();
    setMeter(view, 'drainVoltage', {
      reading: { status: 'unknown' },
      availability: { structural: true, operational: false },
      domain: { kind: 'engineering', unit: 'v' },
    });

    const vd = projectBarMeters(view).find(({ key }) => key === 'drainVoltage')!;
    expect(vd.motionFraction).toBeNull();
    expect(vd.scale).toEqual({ min: 11, max: 15 });
  });

  it('leaves the drain-current bar unwindowed: zero to the profile table top', () => {
    setCapabilities(drainCaps());
    const view = base();
    setMeter(view, 'drainCurrent', {
      reading: { status: 'known', value: 0.5 },
      relevant: true,
      domain: { kind: 'engineering', unit: 'a' },
    });

    const id = projectBarMeters(view).find(({ key }) => key === 'drainCurrent')!;
    expect(id.scale).toEqual({ min: 0, max: 1.0 });
    expect(id.motionFraction).toBeCloseTo(positionOn(id.scale, 0.5));
    expect(id.motionFraction).toBeCloseTo(0.5);
  });

  it('gives the power bar zero to its own table top', () => {
    setCapabilities(drainCaps());
    const view = base();
    setDisplay(view, 'power', { state: 'current', value: 50 });
    setMeter(view, 'power', { domain: { kind: 'engineering', unit: 'w' } });

    const power = projectBarMeters(view)[0];
    expect(power.scale).toEqual({ min: 0, max: 200 });
    expect(power.motionFraction).toBeCloseTo(positionOn(power.scale, 50));
  });

  it('carries no scale for a raw-domain meter — raw/255 is bar geometry, not a scale', () => {
    setCapabilities(drainCaps());
    const view = base();
    setDisplay(view, 'power', { state: 'current', value: 50 });
    setMeter(view, 'power', { domain: { kind: 'raw' } });

    const power = projectBarMeters(view)[0];
    expect(power.scale).toBeNull();
    expect(power.motionFraction).toBeCloseTo(50 / 255);
  });

  it('carries no scale for an unknown domain, nor when the profile declares no table', () => {
    setCapabilities(drainCaps());
    const unknown = base();
    setDisplay(unknown, 'power', { state: 'current', value: 50 });
    setMeter(unknown, 'power', { domain: { kind: 'unknown' } });
    expect(projectBarMeters(unknown)[0].scale).toBeNull();

    clearCapabilities();
    const uncalibrated = base();
    setMeter(uncalibrated, 'drainVoltage', {
      reading: { status: 'known', value: 12.0 },
      domain: { kind: 'engineering', unit: 'v' },
    });
    const vd = projectBarMeters(uncalibrated).find(({ key }) => key === 'drainVoltage')!;
    expect(vd.scale).toBeNull();
    expect(vd.motionFraction).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Level/scale pairing per definition row (T172). `BAR_DEFINITIONS` and
// `SWR_DEFINITION` pair a level function with a scale function on one row.
// Each `*Scale` returns null for a unit it does not serve, so a row wired to
// another row's scale function publishes a null scale while its level function
// still returns a position. Every row is projected with a reading inside its
// own domain and asserted against its own published scale, so such a pairing
// fails here.
// ---------------------------------------------------------------------------

const PAIRING_CALS = {
  power: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 255, actual: 200, label: '200' },
  ],
  alc: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 120, actual: 1, label: '1' },
  ],
  vd: [
    { raw: 0, actual: 0.0, label: '0' },
    { raw: 210, actual: 13.8, label: '13.8' },
  ],
  id: [
    { raw: 0, actual: 0.0, label: '0' },
    { raw: 29, actual: 1.0, label: '1.0' },
  ],
  comp: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 255, actual: 20, label: '20' },
  ],
  swr: [
    { raw: 0, actual: 1.0, label: '1.0' },
    { raw: 255, actual: 6.0, label: '6.0+' },
  ],
};

type PairingCase = {
  readonly domain: MeterValueDomain;
  readonly value: number;
  readonly scale: { min: number; max: number };
  readonly position: number;
};

const PAIRING_CASES: Readonly<Record<LevelMeterKey, PairingCase>> = {
  power: {
    domain: { kind: 'engineering', unit: 'w' },
    value: 50, scale: { min: 0, max: 200 }, position: 0.25,
  },
  alc: {
    domain: { kind: 'engineering', unit: 'normalized' },
    value: 0.4, scale: { min: 0, max: 1 }, position: 0.4,
  },
  drainCurrent: {
    domain: { kind: 'engineering', unit: 'a' },
    value: 0.5, scale: { min: 0, max: 1.0 }, position: 0.5,
  },
  drainVoltage: {
    domain: { kind: 'engineering', unit: 'v' },
    value: 12.0, scale: { min: 11, max: 15 }, position: 0.25,
  },
  compression: {
    domain: { kind: 'engineering', unit: 'db' },
    value: 5, scale: { min: 0, max: 20 }, position: 0.25,
  },
  swr: {
    domain: { kind: 'engineering', unit: 'ratio' },
    value: 2.0, scale: { min: 0, max: 6.0 }, position: 2.0 / 6.0,
  },
};

function pairingCaps(): Capabilities {
  return { ...calibratedCaps(), meterCalibrations: PAIRING_CALS };
}

/** Every row projected at once, each with a reading inside its own domain. */
function pairingRows(domainOf: (key: LevelMeterKey) => MeterValueDomain): Map<
  LevelMeterKey, BarMeterProjection | SwrMeterProjection
> {
  const view = base();
  for (const [key, { value }] of Object.entries(PAIRING_CASES) as [
    LevelMeterKey, PairingCase,
  ][]) {
    view.meters![key] = {
      ...view.meters![key],
      reading: { status: 'known', value },
      relevant: true,
      domain: domainOf(key),
    };
  }
  const rows = new Map<LevelMeterKey, BarMeterProjection | SwrMeterProjection>(
    projectBarMeters(view).map((row) => [row.key, row]),
  );
  rows.set('swr', projectSwrMeter(view)!);
  return rows;
}

describe('projectBarMeters/projectSwrMeter — level and scale come from one row (T172)', () => {
  afterEach(() => clearCapabilities());

  it('covers every projected row', () => {
    setCapabilities(pairingCaps());
    expect([...pairingRows((key) => PAIRING_CASES[key].domain).keys()].sort())
      .toEqual(Object.keys(PAIRING_CASES).sort());
  });

  it.each(Object.keys(PAIRING_CASES) as LevelMeterKey[])(
    'positions %s on the scale that row publishes',
    (key) => {
      setCapabilities(pairingCaps());
      const { value, scale, position } = PAIRING_CASES[key];
      const row = pairingRows((rowKey) => PAIRING_CASES[rowKey].domain).get(key)!;

      expect(row.scale).toEqual(scale);
      expect(row.motionFraction).toBeCloseTo(positionOn(row.scale, value), 10);
      expect(row.motionFraction).toBeCloseTo(position, 10);
    },
  );

  it.each(Object.keys(PAIRING_CASES) as LevelMeterKey[])(
    'leaves %s with neither a scale nor a position in a domain its row cannot serve',
    (key) => {
      setCapabilities(pairingCaps());
      const row = pairingRows(() => ({ kind: 'unknown' })).get(key)!;

      expect(row.scale).toBeNull();
      expect(row.motionFraction).toBeNull();
    },
  );
});


it('retains unavailable declared projections for other zones', () => {
  const view = base();
  for (const key of ['power', 'swr', 'alc', 'compression', 'drainVoltage', 'drainCurrent'] as const) {
    view.meters![key] = { ...view.meters![key], presence: 'unavailable',
      availability: { structural: true, operational: false }, reading: { status: 'unknown' } };
  }
  const projections = [...projectBarMeters(view), projectSwrMeter(view)!];
  expect(projections).toHaveLength(6);
  for (const projection of projections) expect(projection).toMatchObject({
    presence: 'unavailable', gauge: true, observed: false, motionFraction: null, displayText: '',
  });
});
