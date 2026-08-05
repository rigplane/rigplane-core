/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `scan` fact-group adapter
 * derivation.
 *
 * Companion to `rit-xit-adapter.test.ts`/`antenna-adapter.test.ts`, which
 * this file does NOT modify. `scan` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `ScanViewModel` doc comment.
 *
 * PARITY — the parity pin below calls the REAL `toScanProps`
 * (`lib/runtime/props/panel-props.ts`), never a reimplementation, so the
 * `& 0x0F` resume-mode mask is proven consumed, not re-derived.
 *
 * No `scan` capability tag exists anywhere in v2, so this group's evidence
 * gate is state-only (mirrors `deriveMeters`); this file never calls the
 * real `setCapabilities` and does not need the isolated pool (MOR-1272).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toScanProps } from '../../props/panel-props';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

function bareState(overrides: Partial<ServerState> = {}): ServerState {
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

function model(state: ServerState | null, capabilities: Capabilities | null): RadioViewModel {
  const view = toRadioViewModel(state, capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('scan evidence gate (MOR-1295, N3)', () => {
  it('emits no scan when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no scan for a baseline radio that has never reported any scan field (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.scan).toBeUndefined();
    expect(Object.keys(view)).not.toContain('scan');
  });

  it('emits scan once scanning alone has ever been reported', () => {
    const view = model(bareState({ scanning: false }), caps());
    expect(view.scan).toBeDefined();
  });

  it('emits scan once scanType alone has ever been reported', () => {
    const view = model(bareState({ scanType: 0x01 }), caps());
    expect(view.scan).toBeDefined();
  });

  it('emits scan once scanResumeMode alone has ever been reported', () => {
    const view = model(bareState({ scanResumeMode: 0 }), caps());
    expect(view.scan).toBeDefined();
  });
});

describe('scan per-field structural gates (MOR-1295, mirrors deriveMeters\' "!== undefined" discipline)', () => {
  it('scanType is structurally absent when never reported, even though scanning is present', () => {
    const view = model(bareState({ scanning: true }), caps());
    expect(view.scan!.scanning.availability.structural).toBe(true);
    expect(view.scan!.scanType.availability.structural).toBe(false);
  });

  it('scanResumeMode is structurally present once it has ever been reported, independent of the others', () => {
    const view = model(bareState({ scanResumeMode: 0xd1 }), caps());
    expect(view.scan!.scanResumeMode.availability.structural).toBe(true);
    expect(view.scan!.scanning.availability.structural).toBe(false);
  });
});

describe('scan per-field derivation (MOR-1295)', () => {
  it('reports known readings for observed, fresh fields — parity with the real toScanProps', () => {
    const state = bareState({
      scanning: true, scanType: 0x22, scanResumeMode: 0xd2,
      fieldStatus: { ...bareState().fieldStatus, scanning: fresh, scanType: fresh, scanResumeMode: fresh },
    });
    const real = toScanProps(state);
    const view = model(state, caps());
    expect(view.scan!.scanning.reading).toEqual({ status: 'known', value: real.scanning });
    expect(view.scan!.scanType.reading).toEqual({ status: 'known', value: real.scanType });
    expect(view.scan!.scanResumeMode.reading).toEqual({ status: 'known', value: real.scanResumeMode });
  });

  it('applies the shipped 0x0F resume-mode mask verbatim — parity with the real toScanProps', () => {
    // 0xD2 (10s resume) carries bits outside the low nibble; the mask must
    // survive through this contract exactly as `toScanProps` applies it.
    const state = bareState({
      scanResumeMode: 0xd2, fieldStatus: { ...bareState().fieldStatus, scanResumeMode: fresh },
    });
    const real = toScanProps(state);
    const view = model(state, caps());
    expect(real.scanResumeMode).toBe(0x02);
    expect(view.scan!.scanResumeMode.reading).toEqual({ status: 'known', value: 0x02 });
  });

  const STALE_FIELDS: ReadonlyArray<readonly [rawField: 'scanning' | 'scanType' | 'scanResumeMode', value: boolean | number]> = [
    ['scanning', true],
    ['scanType', 0x01],
    ['scanResumeMode', 0xd0],
  ];

  it.each(STALE_FIELDS)(
    'degrades a stale %s field to unknown while keeping structural availability true',
    (rawField, value) => {
      const state = bareState({
        [rawField]: value, fieldStatus: { ...bareState().fieldStatus, [rawField]: stale },
      });
      const view = model(state, caps());
      expect(view.scan![rawField]).toEqual({
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      });
    },
  );

  it('degrades a malformed raw scanType (wrong JS type) to unknown rather than coercing', () => {
    const state = bareState({
      scanType: 'PROG' as unknown as number,
      fieldStatus: { ...bareState().fieldStatus, scanType: fresh },
    });
    const view = model(state, caps());
    expect(view.scan!.scanType.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the scan group (round-trip proof)', () => {
    const state = bareState({
      scanning: true, scanType: 0x01, fieldStatus: { ...bareState().fieldStatus, scanning: fresh, scanType: fresh },
    });
    const view = model(state, caps());
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/** HONESTY GATE — absent raw values never fabricate. */
describe('scan honesty gate — absent raw values never fabricate', () => {
  it('scanning read via the group triggered by scanType alone still reads unknown, not {known, false}', () => {
    const view = model(bareState({ scanType: 0x01 }), caps());
    expect(view.scan!.scanning.reading).toEqual({ status: 'unknown' });
  });

  it('scanResumeMode read via the group triggered by scanning alone still reads unknown, not {known, 0}', () => {
    const view = model(bareState({ scanning: false }), caps());
    expect(view.scan!.scanResumeMode.reading).toEqual({ status: 'unknown' });
  });
});
