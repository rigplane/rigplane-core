/**
 * MOR-2475 PR-2 — `toDspProps` carries the published manual-notch display
 * domain and decodes the FTX-1's `manualNotchFreq` reading through the same
 * `resolveControlContract` the view-model adapter uses (see
 * `radio-view-model-adapter.ts` and its `notch-nb-depth-control-domain`
 * test). A radio publishing no `manual_notch_freq` control (every Icom
 * profile) keeps the raw `notchFilter` props byte-for-byte and gets no
 * `notchFreqDomain` key at all.
 */
import { describe, expect, it } from 'vitest';

import type { Capabilities, ControlDomain } from '$lib/types/capabilities';
import type {
  FieldAvailability, FieldStatus, ReceiverState, ServerState,
} from '$lib/types/state';
import { toAudioSpectrumProps, toDspProps } from '../panel-props';

const FTX1_MANUAL_NOTCH_FREQ: ControlDomain = {
  mapping: 'linear',
  raw_min: 1, raw_max: 320, raw_step: 1, raw_origin: 1,
  display_min: '10' as never, display_max: '3200' as never,
  display_step: '10' as never, display_origin: '10' as never,
  display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
};
const FTX1_NOTCH_DISPLAY_DOMAIN = { min: 10, max: 3200, step: 10, origin: 10 };

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
    storePath: 'receiver.manual_notch_freq',
    observed: availability !== 'missing',
    freshness: availability === 'available'
      ? 'fresh'
      : availability === 'stale' ? 'stale' : 'unknown',
    availability,
  };
}

function state(
  receiverOverrides: Partial<ReceiverState>,
  fields: Record<string, FieldAvailability>,
): ServerState {
  const fieldStatus: Record<string, FieldStatus> = {};
  for (const [path, availability] of Object.entries(fields)) {
    fieldStatus[path] = status(availability);
  }
  return {
    revision: 1,
    stateRevision: 1,
    freshnessRevision: 1,
    observationSeq: 1,
    updatedAt: '2026-09-15T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: receiver(receiverOverrides),
    sub: receiver(),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus,
  };
}

function caps(
  controls: Record<string, unknown> = {},
  capabilities: string[] = ['notch'],
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

const NOTCH_FIELDS = {
  'main.manualNotch': 'available',
  'main.autoNotch': 'available',
} as const;

describe('toDspProps manual-notch domain from the published control (MOR-2475)', () => {
  const ftx1Caps = () => caps({ manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ });

  it.each([[1, 10], [160, 1600], [320, 3200]] as const)(
    'decodes FTX-1 manualNotchFreq raw %i to %i Hz and carries the domain',
    (raw, hz) => {
      const props = toDspProps(
        state({ manualNotchFreq: raw, manualNotch: true }, {
          ...NOTCH_FIELDS, 'main.manualNotchFreq': 'available',
        }),
        ftx1Caps(),
      );
      expect(props.notchFreq).toBe(hz);
      expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
    },
  );

  it('keeps the domain but reads notchFilter when the manualNotchFreq field is unusable', () => {
    const props = toDspProps(
      state({ manualNotchFreq: 160, notchFilter: 77, manualNotch: true }, {
        ...NOTCH_FIELDS,
        'main.manualNotchFreq': 'unavailable',
        'main.notchFilter': 'available',
      }),
      ftx1Caps(),
    );
    expect(props.notchFreq).toBe(77);
    expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
  });

  it('reads an out-of-domain raw position as 0, never a rescaled stand-in', () => {
    const props = toDspProps(
      state({ manualNotchFreq: 321, manualNotch: true }, {
        ...NOTCH_FIELDS, 'main.manualNotchFreq': 'available',
      }),
      ftx1Caps(),
    );
    expect(props.notchFreq).toBe(0);
    expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
  });
});

describe('toDspProps manual notch without a published domain (Icom regression pin)', () => {
  it('keeps the raw notchFilter value and emits no notchFreqDomain key', () => {
    const props = toDspProps(
      state({ notchFilter: 77, manualNotch: true }, {
        ...NOTCH_FIELDS, 'main.notchFilter': 'available',
      }),
      caps(),
    );
    expect(props.notchFreq).toBe(77);
    expect(props).not.toHaveProperty('notchFreqDomain');
  });
});

// PR-3: `toAudioSpectrumProps` feeds the same reading to the scope notch
// markers through the one shared `manualNotchReading` helper, so its source
// selection must match `toDspProps` case for case.
describe('toAudioSpectrumProps manual-notch domain from the published control (MOR-2475 PR-3)', () => {
  const ftx1Caps = () => caps({ manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ });

  it.each([[1, 10], [160, 1600], [320, 3200]] as const)(
    'decodes FTX-1 manualNotchFreq raw %i to %i Hz and carries the domain',
    (raw, hz) => {
      const props = toAudioSpectrumProps(
        state({ manualNotchFreq: raw, manualNotch: true }, {
          ...NOTCH_FIELDS, 'main.manualNotchFreq': 'available',
        }),
        ftx1Caps(),
      );
      expect(props.notchFreq).toBe(hz);
      expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
    },
  );

  it('keeps the domain but reads notchFilter when the manualNotchFreq field is unusable', () => {
    const props = toAudioSpectrumProps(
      state({ manualNotchFreq: 160, notchFilter: 77, manualNotch: true }, {
        ...NOTCH_FIELDS,
        'main.manualNotchFreq': 'unavailable',
        'main.notchFilter': 'available',
      }),
      ftx1Caps(),
    );
    expect(props.notchFreq).toBe(77);
    expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
  });

  it('reads an out-of-domain raw position as 0, never a rescaled stand-in', () => {
    const props = toAudioSpectrumProps(
      state({ manualNotchFreq: 321, manualNotch: true }, {
        ...NOTCH_FIELDS, 'main.manualNotchFreq': 'available',
      }),
      ftx1Caps(),
    );
    expect(props.notchFreq).toBe(0);
    expect(props.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
  });
});

describe('toAudioSpectrumProps manual notch without a published domain (Icom regression pin)', () => {
  it('keeps the raw notchFilter value and emits no notchFreqDomain key', () => {
    const props = toAudioSpectrumProps(
      state({ notchFilter: 77, manualNotch: true }, {
        ...NOTCH_FIELDS, 'main.notchFilter': 'available',
      }),
      caps(),
    );
    expect(props.notchFreq).toBe(77);
    expect(props).not.toHaveProperty('notchFreqDomain');
  });
});
