import { describe, expect, it } from 'vitest';

import { projectNrLevel } from '$lib/radio/filter-controls';
import type { Capabilities, ControlDomain } from '$lib/types/capabilities';
import type { FieldAvailability, FieldStatus, ReceiverState, ServerState } from '$lib/types/state';
import { toDspProps } from '../panel-props';

const EXACT_NR_DOMAIN: ControlDomain = {
  mapping: 'identity',
  raw_min: 0,
  raw_max: 10,
  raw_step: 1,
  raw_origin: 0,
  display_min: '0' as never,
  display_max: '10' as never,
  display_step: '1' as never,
  display_origin: '0' as never,
  display_unit: 'level',
  quantization: 'reject',
  restoration: 'exact',
};

const DISPLAY_DOMAIN = { min: 0, max: 10, step: 1, origin: 0 };
const LEGACY_DOMAIN = { min: 0, max: 15, step: 1, origin: 0 };

function receiver(overrides: Partial<ReceiverState> = {}): ReceiverState {
  return {
    freqHz: 14_074_000,
    mode: 'USB',
    filter: 1,
    dataMode: 0,
    att: 0,
    preamp: 0,
    nb: false,
    nr: false,
    afLevel: 128,
    rfGain: 255,
    squelch: 0,
    sMeter: 0,
    ...overrides,
  };
}

function status(availability: FieldAvailability): FieldStatus {
  return {
    storePath: 'receiver.nr_level',
    observed: availability !== 'missing',
    freshness: availability === 'available'
      ? 'fresh'
      : availability === 'stale' ? 'stale' : 'unknown',
    availability,
  };
}

function state(
  raw: number | undefined,
  availability: FieldAvailability = 'available',
  overrides: Partial<ReceiverState> = {},
): ServerState {
  const main = receiver({ nrLevel: raw, ...overrides });
  if (raw === undefined) delete main.nrLevel;
  return {
    revision: 1,
    stateRevision: 1,
    freshnessRevision: 1,
    observationSeq: 1,
    updatedAt: '2026-08-15T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main,
    sub: receiver(),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus: {
      'main.nr': status('available'),
      'main.nrLevel': status(availability),
      'main.nb': status('available'),
      'main.manualNotch': status('available'),
      'main.autoNotch': status('available'),
    },
  };
}

function caps(
  controls: Record<string, unknown> = {},
  capabilities: string[] = ['nr'],
): Capabilities {
  return {
    model: 'Test Radio',
    scope: false,
    audio: false,
    tx: false,
    capabilities,
    receivers: 1,
    vfoScheme: 'ab',
    freqRanges: [],
    modes: [],
    filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
    webrtc: { available: false, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    controls: controls as Capabilities['controls'],
  };
}

describe('exact NR-level fallback-props projection (MOR-1734)', () => {
  it.each([0, 1, 4, 10])('projects exact FTX raw %i unchanged', (raw) => {
    const capabilities = caps({ nr_level: EXACT_NR_DOMAIN });
    const expected = { value: raw, domain: DISPLAY_DOMAIN, adjustable: true };

    expect(projectNrLevel(capabilities, raw, true)).toEqual(expected);
    expect(toDspProps(state(raw), capabilities).nrLevelProjection).toEqual(expected);
  });

  it.each([
    ['missing raw state', undefined, 'available'],
    ['missing field status', 4, 'missing'],
    ['stale field status', 4, 'stale'],
    ['raw below the domain', -1, 'available'],
    ['raw above the domain', 11, 'available'],
  ] as const)('fails closed for %s', (_name, raw, availability) => {
    expect(toDspProps(state(raw, availability), caps({ nr_level: EXACT_NR_DOMAIN })).nrLevelProjection)
      .toEqual({ value: null, domain: DISPLAY_DOMAIN, adjustable: false });
  });

  it.each([
    ['malformed', { ...EXACT_NR_DOMAIN, display_max: '9' }],
    ['trapping', new Proxy({ ...EXACT_NR_DOMAIN }, {
      get: () => { throw new Error('nr_level metadata trap'); },
    })],
  ])('never throws and rejects %s present controls.nr_level metadata', (_name, nrLevel) => {
    const project = () => toDspProps(state(4), caps({ nr_level: nrLevel })).nrLevelProjection;
    expect(project).not.toThrow();
    expect(project()).toEqual({ value: null, domain: null, adjustable: false });
  });

  it.each([
    [0, 0],
    [128, 8],
    [255, 15],
  ])('preserves safely absent legacy raw %i as display %i', (raw, display) => {
    expect(toDspProps(state(raw), caps()).nrLevelProjection).toEqual({
      value: display,
      domain: LEGACY_DOMAIN,
      adjustable: true,
    });
  });

  it('requires the NR capability before the projection is adjustable', () => {
    expect(toDspProps(state(128), caps({}, [])).nrLevelProjection).toEqual({
      value: 8,
      domain: LEGACY_DOMAIN,
      adjustable: false,
    });
  });

  it('leaves the existing NR, NB, and notch props unchanged', () => {
    const props = toDspProps(
      state(4, 'available', {
        nr: true,
        nb: true,
        nbLevel: 7,
        manualNotch: true,
        notchFilter: 77,
        manualNotchWidth: 2,
      }),
      caps({
        nr_level: EXACT_NR_DOMAIN,
        nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 },
      }, ['nr', 'nb', 'notch']),
    );

    expect({
      nrMode: props.nrMode,
      nrLevel: props.nrLevel,
      hasNr: props.hasNr,
      nbActive: props.nbActive,
      nbLevel: props.nbLevel,
      notchMode: props.notchMode,
      notchFreq: props.notchFreq,
      manualNotchWidth: props.manualNotchWidth,
      hasNb: props.hasNb,
      hasNotch: props.hasNotch,
      hasAutoNotch: props.hasAutoNotch,
    }).toEqual({
      nrMode: 1,
      nrLevel: 0,
      hasNr: true,
      nbActive: true,
      nbLevel: 7,
      notchMode: 'manual',
      notchFreq: 77,
      manualNotchWidth: 2,
      hasNb: true,
      hasNotch: true,
      hasAutoNotch: true,
    });
  });
});
