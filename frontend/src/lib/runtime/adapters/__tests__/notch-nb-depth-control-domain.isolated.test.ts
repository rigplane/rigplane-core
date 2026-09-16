/**
 * MOR-2475 PR-1 — manual-notch and NB-depth readback through the shared
 * control-domain contract.
 *
 * The FTX-1 publishes an exact `controls.manual_notch_freq` domain
 * (`rigs/ftx1.toml`) and the Yaesu poller writes `main.manualNotchFreq`, so
 * the adapter must decode that field to its display unit and publish the
 * domain. Every Icom profile publishes no such control, so `notchFreq` must
 * keep reading the receiver-scoped `notchFilter` field byte-for-byte. NB
 * depth is decoded through the same `resolveControlContract` path; the
 * IC-7610 legacy band (`rigs/ic7610.toml [controls.nb_depth]`) keeps its
 * shipped raw 0-9 -> display 1-10 conversion.
 *
 * Isolated pool for the same reason as its `dsp-adapter` sibling: it
 * installs capabilities through `toRadioViewModel`'s `caps` argument only,
 * but sits beside store-mutating files in the shared worker pool.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities, ControlDomain, ControlRange } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

const FTX1_MANUAL_NOTCH_FREQ: ControlDomain = {
  mapping: 'linear',
  raw_min: 1, raw_max: 320, raw_step: 1, raw_origin: 1,
  display_min: '10' as never, display_max: '3200' as never,
  display_step: '10' as never, display_origin: '10' as never,
  display_unit: 'Hz', quantization: 'reject', restoration: 'exact',
};
const FTX1_NOTCH_DISPLAY_DOMAIN = { min: 10, max: 3200, step: 10, origin: 10 };
const IC7610_NB_DEPTH: ControlRange = {
  raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10,
};

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false,
    stateContractVersion: 1, providerGeneration: 0, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
};

function state(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    sub: {
      freqHz: 7100000, mode: 'LSB', filter: 2, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    fieldStatus: {
      active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
      'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
    },
    ...overrides,
  } as ServerState;
}

function model(value: ServerState, capabilities: Capabilities): RadioViewModel {
  const view = toRadioViewModel(value, capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('manual notch readback through the published control domain (MOR-2475)', () => {
  const ftx1Caps = caps({
    capabilities: ['notch'],
    controls: { manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ },
  });

  it('decodes raw 160 to 1600 Hz and publishes the domain', () => {
    const view = model(state({
      main: { ...state().main, manualNotchFreq: 160 },
      fieldStatus: { ...state().fieldStatus, 'main.manualNotchFreq': fresh },
    }), ftx1Caps);
    expect(view.dsp!.notchFreq.reading).toEqual({ status: 'known', value: 1600 });
    expect(view.dsp!.notchFreqDomain).toEqual(FTX1_NOTCH_DISPLAY_DOMAIN);
  });

  it.each([[1, 10], [320, 3200]] as const)('decodes endpoint raw %i to %i Hz', (raw, display) => {
    const view = model(state({
      main: { ...state().main, manualNotchFreq: raw },
      fieldStatus: { ...state().fieldStatus, 'main.manualNotchFreq': fresh },
    }), ftx1Caps);
    expect(view.dsp!.notchFreq.reading).toEqual({ status: 'known', value: display });
  });

  it('reads unknown for an out-of-domain manualNotchFreq raw position', () => {
    const view = model(state({
      main: { ...state().main, manualNotchFreq: 321 },
      fieldStatus: { ...state().fieldStatus, 'main.manualNotchFreq': fresh },
    }), ftx1Caps);
    expect(view.dsp!.notchFreq.reading).toEqual({ status: 'unknown' });
  });
});

describe('Icom manual notch readback is unchanged (MOR-2475 regression pin)', () => {
  it('keeps reading the raw notchFilter field and publishes no domain', () => {
    const view = model(state({
      main: { ...state().main, notchFilter: 77, manualNotchFreq: 160 },
      fieldStatus: {
        ...state().fieldStatus,
        'main.notchFilter': fresh, 'main.manualNotchFreq': fresh,
      },
    }), caps({ capabilities: ['notch'] }));
    expect(view.dsp!.notchFreq.reading).toEqual({ status: 'known', value: 77 });
    expect(Object.keys(view.dsp!)).not.toContain('notchFreqDomain');
  });
});

describe('NB depth readback through the shared contract (MOR-2475)', () => {
  const ic7610Caps = caps({ capabilities: ['nb'], controls: { nb_depth: IC7610_NB_DEPTH } });

  it.each([[0, 1], [9, 10]] as const)('decodes IC-7610 raw %i to display %i', (raw, display) => {
    const view = model(state({
      nbDepth: raw,
      fieldStatus: { ...state().fieldStatus, nbDepth: fresh },
    }), ic7610Caps);
    expect(view.dsp!.nbDepth.reading).toEqual({ status: 'known', value: display });
  });

  it('leaves a radio without an nb_depth control structurally absent', () => {
    const view = model(state({
      nbDepth: 4,
      fieldStatus: { ...state().fieldStatus, nbDepth: fresh },
    }), caps({ capabilities: ['nb'] }));
    expect(view.dsp!.nbDepth.availability.structural).toBe(false);
    expect(view.dsp!.nbDepth.reading).toEqual({ status: 'unknown' });
  });
});

describe('notchFreqDomain validator entry (MOR-2475)', () => {
  const ftx1Caps = caps({
    capabilities: ['notch'],
    controls: { manual_notch_freq: FTX1_MANUAL_NOTCH_FREQ },
  });

  it('rejects a domain carrying an extra member', () => {
    const valid = model(state({
      main: { ...state().main, manualNotchFreq: 160 },
      fieldStatus: { ...state().fieldStatus, 'main.manualNotchFreq': fresh },
    }), ftx1Caps);
    const broken = structuredClone(valid) as RadioViewModel;
    (broken.dsp as unknown as Record<string, unknown>).notchFreqDomain = {
      ...FTX1_NOTCH_DISPLAY_DOMAIN, extra: true,
    };
    expect(() => validateRadioViewModel(broken)).toThrow(/\$\.dsp\.notchFreqDomain/);
  });
});
