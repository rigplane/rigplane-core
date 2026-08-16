import { describe, expect, it } from 'vitest';

import type { Capabilities, ControlDomain, ControlRange } from '$lib/types/capabilities';
import type {
  FieldAvailability, FieldStatus, ReceiverState, ServerState,
} from '$lib/types/state';
import {
  validateRadioViewModel, type DspViewModel, type RadioViewModel,
} from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

const EXACT_NR_DOMAIN: ControlDomain = {
  mapping: 'identity', raw_min: 0, raw_max: 10, raw_step: 1, raw_origin: 0,
  display_min: '0' as never, display_max: '10' as never,
  display_step: '1' as never, display_origin: '0' as never,
  display_unit: 'level',
  quantization: 'reject', restoration: 'exact',
};

const EXACT_DISPLAY_DOMAIN = { min: 0, max: 10, step: 1, origin: 0 };
const LEGACY_DISPLAY_DOMAIN = { min: 0, max: 15, step: 1, origin: 0 };
const OMIT_NR_METADATA = Symbol('omit-nr-metadata');

const fresh: FieldStatus = {
  storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
};

function fieldStatus(availability: FieldAvailability): FieldStatus {
  return {
    storePath: 'receiver.nr_level',
    observed: availability !== 'missing',
    freshness: availability === 'available'
      ? 'fresh'
      : availability === 'stale' ? 'stale' : 'unknown',
    availability,
  };
}

function receiver(overrides: Partial<ReceiverState> = {}): ReceiverState {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0,
    att: 0, preamp: 0, nb: true, nbLevel: 7, nr: true,
    afLevel: 128, rfGain: 255, squelch: 0, sMeter: 0,
    autoNotch: false, manualNotch: true, notchFilter: 77,
    manualNotchWidth: 2, agc: 2, agcTimeConstant: 9,
    ...overrides,
  };
}

function state(
  raw: number | undefined,
  availability: FieldAvailability = 'available',
): ServerState {
  const main = receiver({ nrLevel: raw });
  if (raw === undefined) delete main.nrLevel;
  return {
    revision: 1,
    stateRevision: 1,
    freshnessRevision: 1,
    observationSeq: 1,
    updatedAt: '2026-08-15T00:00:00Z',
    active: 'MAIN',
    split: false,
    dualWatch: false,
    ptt: false,
    tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14_074_000 },
    main,
    sub: receiver(),
    nbDepth: 4,
    nbWidth: 3,
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus: {
      active: fresh,
      split: fresh,
      dualWatch: fresh,
      txTarget: fresh,
      'main.freqHz': fresh,
      'main.mode': fresh,
      'main.filter': fresh,
      'main.nr': fresh,
      'main.nrLevel': fieldStatus(availability),
      'main.nb': fresh,
      'main.nbLevel': fresh,
      nbDepth: fresh,
      nbWidth: fresh,
      'main.autoNotch': fresh,
      'main.manualNotch': fresh,
      'main.notchFilter': fresh,
      'main.manualNotchWidth': fresh,
      'main.agc': fresh,
      'main.agcTimeConstant': fresh,
    },
  };
}

function caps(
  nrLevel: unknown | typeof OMIT_NR_METADATA = OMIT_NR_METADATA,
): Capabilities {
  const nbDepth: ControlRange = {
    raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10,
  };
  const controls = nrLevel === OMIT_NR_METADATA
    ? { nb_depth: nbDepth }
    : { nr_level: nrLevel, nb_depth: nbDepth };
  return {
    model: 'FTX-1 fixture',
    scope: false,
    audio: false,
    tx: false,
    capabilities: ['nr', 'nb', 'notch', 'agc'],
    receivers: 1,
    vfoScheme: 'single',
    freqRanges: [],
    modes: [],
    filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
    webrtc: { available: false, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    agcModes: [1, 2, 3],
    controls: controls as Capabilities['controls'],
  };
}

function model(
  raw: number | undefined,
  capabilities: Capabilities,
  availability: FieldAvailability = 'available',
): RadioViewModel {
  const view = toRadioViewModel(state(raw, availability), capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

function projectionOf(dsp: DspViewModel): unknown {
  return (dsp as DspViewModel & { nrLevelProjection?: unknown }).nrLevelProjection;
}

function payloadWithProjection(projection: unknown): unknown {
  const payload = structuredClone(model(4, caps(EXACT_NR_DOMAIN))) as unknown as {
    dsp: Record<string, unknown>;
  };
  payload.dsp.nrLevelProjection = projection;
  return payload;
}

describe('NR-level semantic projection (MOR-1736)', () => {
  it.each([0, 1, 4, 10])('carries exact FTX-1 raw/display %i through adapter and validator', (raw) => {
    const dsp = model(raw, caps(EXACT_NR_DOMAIN)).dsp!;
    expect(projectionOf(dsp)).toEqual({
      value: raw,
      domain: EXACT_DISPLAY_DOMAIN,
      adjustable: true,
    });
    expect(dsp.nrLevel.reading).toEqual({ status: 'known', value: raw });
  });

  it.each([
    ['stale readback', 4, 'stale'],
    ['unread value', undefined, 'missing'],
    ['below-domain value', -1, 'available'],
    ['above-domain value', 11, 'available'],
  ] as const)('fails closed for %s', (_label, raw, availability) => {
    const dsp = model(raw, caps(EXACT_NR_DOMAIN), availability).dsp!;
    expect(projectionOf(dsp)).toEqual({
      value: null,
      domain: EXACT_DISPLAY_DOMAIN,
      adjustable: false,
    });
    expect(dsp.nrLevel.reading).toEqual({ status: 'unknown' });
  });

  it.each([
    ['malformed metadata', () => ({ ...EXACT_NR_DOMAIN, display_max: '9' })],
    ['trapping metadata', () => new Proxy({ ...EXACT_NR_DOMAIN }, {
      get: () => { throw new Error('nr_level metadata trap'); },
    })],
  ] as const)('does not throw and fails closed for %s', (_label, metadata) => {
    let view: RadioViewModel | undefined;
    expect(() => { view = model(4, caps(metadata())); }).not.toThrow();
    expect(projectionOf(view!.dsp!)).toEqual({ value: null, domain: null, adjustable: false });
    expect(view!.dsp!.nrLevel.reading).toEqual({ status: 'unknown' });
  });

  it.each([
    [0, 0],
    [128, 8],
    [255, 15],
  ])('preserves absent-metadata legacy raw %i as display %i', (raw, display) => {
    const dsp = model(raw, caps()).dsp!;
    expect(projectionOf(dsp)).toEqual({
      value: display,
      domain: LEGACY_DISPLAY_DOMAIN,
      adjustable: true,
    });
    expect(dsp.nrLevel.reading).toEqual({ status: 'known', value: display });
  });

  it('leaves NR toggle, NB, notch, and AGC facts unchanged', () => {
    const dsp = model(4, caps(EXACT_NR_DOMAIN)).dsp!;
    const facts = [dsp.nrActive, dsp.nbActive, dsp.nbLevel, dsp.nbDepth, dsp.nbWidth,
      dsp.notchMode, dsp.notchFreq, dsp.manualNotchWidth, dsp.agcMode, dsp.agcTimeConstant];
    expect(facts.map((fact) => fact.reading)).toEqual([
      { status: 'known', value: true }, { status: 'known', value: true },
      { status: 'known', value: 7 }, { status: 'known', value: 5 },
      { status: 'known', value: 3 }, { status: 'known', value: 'manual' },
      { status: 'known', value: 77 }, { status: 'known', value: 2 },
      { status: 'known', value: 2 }, { status: 'known', value: 9 },
    ]);
    expect(facts.every((fact) => fact.availability.structural && fact.availability.operational)).toBe(true);
    expect(dsp.agcModes).toEqual([1, 2, 3]);
  });

  it('keeps the projection optional for old semantic payloads', () => {
    const legacy = structuredClone(model(4, caps(EXACT_NR_DOMAIN))) as RadioViewModel;
    delete (legacy.dsp as DspViewModel & { nrLevelProjection?: unknown }).nrLevelProjection;
    const validated = validateRadioViewModel(legacy);
    expect(projectionOf(validated.dsp!)).toBeUndefined();
  });

  it.each([
    ['malformed value', { value: '4', domain: EXACT_DISPLAY_DOMAIN, adjustable: true }],
    ['extra projection member', { value: 4, domain: EXACT_DISPLAY_DOMAIN, adjustable: true, extra: true }],
    ['extra domain member', { value: 4, domain: { ...EXACT_DISPLAY_DOMAIN, extra: true }, adjustable: true }],
  ])('strictly rejects %s whenever the projection is present', (_label, projection) => {
    expect(() => validateRadioViewModel(payloadWithProjection(projection)))
      .toThrow(/\$\.dsp\.nrLevelProjection/);
  });

  it.each([
    ['projection', Object.create({ value: 4, domain: EXACT_DISPLAY_DOMAIN, adjustable: true, extra: true })],
    ['domain', { value: 4, domain: Object.create(EXACT_DISPLAY_DOMAIN), adjustable: true }],
  ])('rejects prototype-inherited %s fields', (_label, projection) => {
    expect(() => validateRadioViewModel(payloadWithProjection(projection)))
      .toThrow(/\$\.dsp\.nrLevelProjection/);
  });

  it('rejects accessors without invoking them', () => {
    let calls = 0;
    const projection = Object.defineProperty(
      { domain: EXACT_DISPLAY_DOMAIN, adjustable: true }, 'value',
      { enumerable: true, get: () => { calls += 1; throw new Error('getter trap'); } },
    );
    expect(() => validateRadioViewModel(payloadWithProjection(projection)))
      .toThrow(/\$\.dsp\.nrLevelProjection/);
    const setterDomain = Object.defineProperty(
      { ...EXACT_DISPLAY_DOMAIN }, 'step', { enumerable: true, set: () => { calls += 1; } },
    );
    expect(() => validateRadioViewModel(payloadWithProjection(
      { value: 4, domain: setterDomain, adjustable: true },
    ))).toThrow(/\$\.dsp\.nrLevelProjection/);
    expect(calls).toBe(0);
  });

  it.each([
    ['symbol extra', () => ({ value: 4, domain: EXACT_DISPLAY_DOMAIN, adjustable: true, [Symbol('extra')]: true })],
    ['hidden extra', () => Object.defineProperty(
      { value: 4, domain: EXACT_DISPLAY_DOMAIN, adjustable: true }, 'extra', { value: true },
    )],
    ['hidden required field', () => Object.defineProperty(
      { domain: EXACT_DISPLAY_DOMAIN, adjustable: true }, 'value', { value: 4 },
    )],
  ])('rejects %s', (_label, projection) => {
    expect(() => validateRadioViewModel(payloadWithProjection(projection())))
      .toThrow(/\$\.dsp\.nrLevelProjection/);
  });

  it('converts a proxy reflection trap into normal validator rejection', () => {
    const projection = new Proxy(
      { value: 4, domain: EXACT_DISPLAY_DOMAIN, adjustable: true },
      { ownKeys: () => { throw new Error('proxy ownKeys trap'); } },
    );
    expect(() => validateRadioViewModel(payloadWithProjection(projection)))
      .toThrow(/\$\.dsp\.nrLevelProjection/);
  });
});
