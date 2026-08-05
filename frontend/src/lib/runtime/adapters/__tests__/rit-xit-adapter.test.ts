/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `ritXit` fact-group adapter
 * derivation.
 *
 * Companion to `rf-front-end-adapter.test.ts`/`band-adapter.test.ts`, which
 * this file does NOT modify. `ritXit` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `RitXitViewModel` doc comment.
 *
 * PARITY — the parity pin below calls the REAL `toRitXitProps`
 * (`lib/runtime/props/panel-props.ts`), never a reimplementation, so
 * agreement is against the shipped derivation itself.
 *
 * Neither `ritOn`/`ritFreq`/`ritTx` nor the `rit`/`xit` capability tags
 * consume a capabilities-STORE-backed helper, so this file never calls the
 * real `setCapabilities` and does not need the isolated pool (MOR-1272).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toRitXitProps } from '../../props/panel-props';

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

describe('ritXit evidence gate (MOR-1295, N3)', () => {
  it('emits no ritXit when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no ritXit for a baseline radio with neither rit nor xit capability (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.ritXit).toBeUndefined();
    expect(Object.keys(view)).not.toContain('ritXit');
  });

  it('emits ritXit once the rit capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['rit'] }));
    expect(view.ritXit).toBeDefined();
  });

  it('emits ritXit once the xit capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['xit'] }));
    expect(view.ritXit).toBeDefined();
  });
});

describe('ritXit per-field structural gates (MOR-1295)', () => {
  it('rit* fields are structurally absent without the rit capability, even with xit present', () => {
    const view = model(bareState(), caps({ capabilities: ['xit'] }));
    expect(view.ritXit!.ritActive.availability.structural).toBe(false);
    expect(view.ritXit!.ritOffset.availability.structural).toBe(false);
    expect(view.ritXit!.xitActive.availability.structural).toBe(true);
  });

  it('xit* fields are structurally absent without the xit capability, even with rit present', () => {
    const view = model(bareState(), caps({ capabilities: ['rit'] }));
    expect(view.ritXit!.xitActive.availability.structural).toBe(false);
    expect(view.ritXit!.xitOffset.availability.structural).toBe(false);
    expect(view.ritXit!.ritActive.availability.structural).toBe(true);
  });
});

describe('ritXit per-field derivation (MOR-1295)', () => {
  const fullCaps = caps({ capabilities: ['rit', 'xit'] });

  it('reports known readings for observed, fresh, capability-backed fields — parity with the real toRitXitProps', () => {
    const state = bareState({
      ritOn: true, ritTx: false, ritFreq: 250,
      fieldStatus: { ...bareState().fieldStatus, ritOn: fresh, ritTx: fresh, ritFreq: fresh },
    });
    const real = toRitXitProps(state, fullCaps);
    const view = model(state, fullCaps);
    const ritXit = view.ritXit!;
    expect(ritXit.ritActive.reading).toEqual({ status: 'known', value: real.ritActive });
    expect(ritXit.ritOffset.reading).toEqual({ status: 'known', value: real.ritOffset });
    expect(ritXit.xitActive.reading).toEqual({ status: 'known', value: real.xitActive });
    expect(ritXit.xitOffset.reading).toEqual({ status: 'known', value: real.xitOffset });
  });

  it('ritOffset and xitOffset read the SAME shared register, in parity with the real derivation', () => {
    const state = bareState({ ritFreq: -400, fieldStatus: { ...bareState().fieldStatus, ritFreq: fresh } });
    const view = model(state, fullCaps);
    expect(view.ritXit!.ritOffset.reading).toEqual({ status: 'known', value: -400 });
    expect(view.ritXit!.xitOffset.reading).toEqual({ status: 'known', value: -400 });
  });

  const STALE_FIELDS: ReadonlyArray<readonly [rawField: 'ritOn' | 'ritTx' | 'ritFreq', viewField: 'ritActive' | 'xitActive' | 'ritOffset' | 'xitOffset']> = [
    ['ritOn', 'ritActive'],
    ['ritTx', 'xitActive'],
    ['ritFreq', 'ritOffset'],
  ];

  it.each(STALE_FIELDS)(
    'degrades a stale %s field to unknown while keeping structural availability true',
    (rawField, viewField) => {
      const state = bareState({
        [rawField]: rawField === 'ritFreq' ? 500 : true,
        fieldStatus: { ...bareState().fieldStatus, [rawField]: stale },
      });
      const view = model(state, fullCaps);
      expect(view.ritXit![viewField]).toEqual({
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      });
    },
  );

  it('marks ritActive structurally absent when the rit capability is missing, never known', () => {
    const state = bareState({ ritOn: true, fieldStatus: { ...bareState().fieldStatus, ritOn: fresh } });
    const view = model(state, caps({ capabilities: ['xit'] }));
    expect(view.ritXit!.ritActive).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('degrades a malformed raw ritFreq (wrong JS type) to unknown rather than coercing', () => {
    const state = bareState({
      ritFreq: '250' as unknown as number,
      fieldStatus: { ...bareState().fieldStatus, ritFreq: fresh },
    });
    const view = model(state, fullCaps);
    expect(view.ritXit!.ritOffset.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the ritXit group (round-trip proof)', () => {
    const state = bareState({
      ritOn: true, ritFreq: 100, fieldStatus: { ...bareState().fieldStatus, ritOn: fresh, ritFreq: fresh },
    });
    const view = model(state, fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * HONESTY GATE — absent raw values never fabricate a `{known, false}`/
 * `{known, 0}` default, mirroring `deriveTxAux`'s own H3-lineage pins.
 */
describe('ritXit honesty gate — absent raw values never fabricate', () => {
  const fullCaps = caps({ capabilities: ['rit', 'xit'] });

  it('ritOffset/xitOffset with no ritFreq reported at all read unknown, not {known, 0}', () => {
    const view = model(bareState(), fullCaps);
    expect(view.ritXit!.ritOffset.reading).toEqual({ status: 'unknown' });
    expect(view.ritXit!.xitOffset.reading).toEqual({ status: 'unknown' });
  });

  it('ritActive with no ritOn reported at all reads unknown, not {known, false}', () => {
    const view = model(bareState(), fullCaps);
    expect(view.ritXit!.ritActive.reading).toEqual({ status: 'unknown' });
  });
});
