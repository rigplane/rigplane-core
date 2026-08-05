/**
 * MOR-1262 decomposition slice 9A (MOR-1296) — `cwKeyer` fact-group adapter
 * derivation. SAFETY-CRITICAL: break-in keys the transmitter.
 *
 * Companion to `rit-xit-adapter.test.ts`/`scan-adapter.test.ts`, which this
 * file does NOT modify. `cwKeyer` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `CwKeyerViewModel` doc comment. The no-key-path
 * proof lives in `cw-keyer-purity.isolated.test.ts`.
 *
 * PARITY — the pins below call the REAL `toCwProps`
 * (`lib/runtime/props/panel-props.ts`) and the REAL `isBreakInActive`
 * (`components-v2/panels/cw-panel-logic.ts`), never a reimplementation, so
 * agreement is against the shipped derivations themselves. Where this adapter
 * deliberately DIVERGES from v2 it is because v2 fabricates a default
 * (600 Hz / 12 WPM / OFF / not-reversed) on an unobserved field, and those
 * divergences are pinned as such rather than papered over.
 *
 * Neither the CW state fields nor the `cw`/`break_in`/`apf`/`twin_peak`
 * capability tags consume a capabilities-STORE-backed helper, so this file
 * never calls the real `setCapabilities` and does not need the isolated pool
 * (MOR-1272).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toCwProps } from '../../props/panel-props';
import { isBreakInActive } from '../../../../components-v2/panels/cw-panel-logic';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

/** A radio whose TX permit is positively `allowed`: 14.195 MHz inside a
 *  declared 20 m TX band, with an observed TX target. Break-in is only
 *  UNGATED under such a permit, so every non-safety pin below uses it. */
function cwCaps(tags: readonly string[] = ['cw', 'break_in', 'apf', 'twin_peak']): Capabilities {
  return caps({
    capabilities: ['tx', ...tags],
    txBands: [{ name: '20m', start: 14000000, end: 14350000 }],
  });
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'CW', filter: 1, dataMode: 0, att: 0, preamp: 0,
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

const reasonFields = (view: RadioViewModel) => view.disabledReasons.map((r) => r.field);

describe('cwKeyer evidence gate (MOR-1296, N3)', () => {
  it('emits no cwKeyer when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no cwKeyer for a baseline radio with no cw capability (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.cwKeyer).toBeUndefined();
    expect(Object.keys(view)).not.toContain('cwKeyer');
  });

  it('emits no cwKeyer for a radio declaring break_in/apf/twin_peak but NOT cw — v2 renders nothing there', () => {
    const view = model(bareState(), cwCaps(['break_in', 'apf', 'twin_peak']));
    expect(view.cwKeyer).toBeUndefined();
  });

  it('emits cwKeyer once the cw capability alone is declared', () => {
    const view = model(bareState(), cwCaps(['cw']));
    expect(view.cwKeyer).toBeDefined();
  });

  it('emits no cwKeyer-derived disabled reason when the group is absent', () => {
    const view = model(bareState(), caps());
    expect(reasonFields(view).filter((f) => f.startsWith('cwKeyer.'))).toEqual([]);
  });
});

describe('cwKeyer per-field structural gates (MOR-1296)', () => {
  it('break-in fields are structurally absent without the break_in capability', () => {
    const view = model(bareState(), cwCaps(['cw']));
    expect(view.cwKeyer!.breakIn.availability.structural).toBe(false);
    expect(view.cwKeyer!.breakInDelay.availability.structural).toBe(false);
    // The cw-tagged fields stay structurally present — one gate per control.
    expect(view.cwKeyer!.keyerSpeed.availability.structural).toBe(true);
    expect(view.cwKeyer!.pitchHz.availability.structural).toBe(true);
    expect(view.cwKeyer!.reversePaddle.availability.structural).toBe(true);
  });

  it('apf is structurally absent without the apf capability, twinPeak without twin_peak', () => {
    const view = model(bareState(), cwCaps(['cw', 'twin_peak']));
    expect(view.cwKeyer!.apf.availability.structural).toBe(false);
    expect(view.cwKeyer!.twinPeak.availability.structural).toBe(true);
  });
});

describe('cwKeyer per-field derivation (MOR-1296)', () => {
  const fullCaps = cwCaps();

  it('reports known readings for observed, fresh, capability-backed fields — parity with the real toCwProps', () => {
    const state = bareState({
      breakIn: 2, breakInDelay: 128, keySpeed: 24, cwPitch: 700, dashRatio: -1,
      main: { ...bareState().main, apfTypeLevel: 1, twinPeakFilter: true },
      fieldStatus: {
        ...bareState().fieldStatus,
        breakIn: fresh, breakInDelay: fresh, keySpeed: fresh, cwPitch: fresh, dashRatio: fresh,
        'main.apfTypeLevel': fresh, 'main.twinPeakFilter': fresh,
      },
    } as Partial<ServerState>);
    const real = toCwProps(state, fullCaps);
    const cw = model(state, fullCaps).cwKeyer!;
    expect(cw.breakInDelay.reading).toEqual({ status: 'known', value: real.breakInDelay });
    expect(cw.keyerSpeed.reading).toEqual({ status: 'known', value: real.keySpeed });
    expect(cw.pitchHz.reading).toEqual({ status: 'known', value: real.cwPitch });
    expect(cw.reversePaddle.reading).toEqual({ status: 'known', value: real.reversePaddle });
    expect(cw.apf.reading).toEqual({ status: 'known', value: real.apfMode });
    expect(cw.twinPeak.reading).toEqual({ status: 'known', value: real.twinPeak });
    // `wpm` and `sidetonePitch` are v2 duplicates of the same two registers —
    // one fact each here, in agreement with both shipped props.
    expect(cw.keyerSpeed.reading).toEqual({ status: 'known', value: real.wpm });
    expect(cw.pitchHz.reading).toEqual({ status: 'known', value: real.sidetonePitch });
  });

  const BREAK_IN_CASES = [[0, 'off'], [1, 'semi'], [2, 'full']] as const;

  it.each(BREAK_IN_CASES)(
    'decodes the raw break-in int %i to %s, agreeing with the real isBreakInActive predicate',
    (raw, mode) => {
      const state = bareState({
        breakIn: raw, fieldStatus: { ...bareState().fieldStatus, breakIn: fresh },
      } as Partial<ServerState>);
      const cw = model(state, fullCaps).cwKeyer!;
      expect(cw.breakIn.reading).toEqual({ status: 'known', value: mode });
      expect(cw.breakIn.reading.status === 'known' && cw.breakIn.reading.value !== 'off')
        .toBe(isBreakInActive(toCwProps(state, fullCaps).breakIn));
    },
  );

  it('reads apf/twinPeak off the ACTIVE receiver, not always MAIN — value AND field-status path', () => {
    // MAIN's own entries are STALE on purpose: without them a MAIN-path
    // field-status lookup would find no entry and default to available, so a
    // receiver-path bug would still read SUB's values and stay invisible.
    const state = bareState({
      active: 'SUB',
      main: { ...bareState().main, apfTypeLevel: 1, twinPeakFilter: true },
      sub: { ...bareState().sub, apfTypeLevel: 3, twinPeakFilter: false },
      fieldStatus: {
        ...bareState().fieldStatus,
        active: fresh, 'sub.apfTypeLevel': fresh, 'sub.twinPeakFilter': fresh,
        'main.apfTypeLevel': stale, 'main.twinPeakFilter': stale,
      },
    } as Partial<ServerState>);
    const cw = model(state, cwCaps()).cwKeyer!;
    expect(cw.apf.reading).toEqual({ status: 'known', value: 3 });
    expect(cw.twinPeak.reading).toEqual({ status: 'known', value: false });
    expect(cw.apf.availability.operational).toBe(true);
  });

  const STALE_FIELDS = [
    ['breakIn', 'breakIn', 1],
    ['breakInDelay', 'breakInDelay', 100],
    ['keySpeed', 'keyerSpeed', 30],
    ['cwPitch', 'pitchHz', 750],
    ['dashRatio', 'reversePaddle', -1],
  ] as const;

  it.each(STALE_FIELDS)(
    'degrades a stale %s field to unknown while keeping structural availability true',
    (rawField, viewField, rawValue) => {
      const state = bareState({
        [rawField]: rawValue, fieldStatus: { ...bareState().fieldStatus, [rawField]: stale },
      } as Partial<ServerState>);
      expect(model(state, fullCaps).cwKeyer![viewField]).toEqual({
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      });
    },
  );

  it('marks breakIn structurally absent when the break_in capability is missing, never known', () => {
    const state = bareState({
      breakIn: 2, fieldStatus: { ...bareState().fieldStatus, breakIn: fresh },
    } as Partial<ServerState>);
    expect(model(state, cwCaps(['cw'])).cwKeyer!.breakIn).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('degrades a malformed raw cwPitch (wrong JS type) to unknown rather than coercing', () => {
    const state = bareState({
      cwPitch: '700' as unknown as number, fieldStatus: { ...bareState().fieldStatus, cwPitch: fresh },
    } as Partial<ServerState>);
    expect(model(state, fullCaps).cwKeyer!.pitchHz.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the cwKeyer group (round-trip proof)', () => {
    const state = bareState({
      breakIn: 1, keySpeed: 20, fieldStatus: { ...bareState().fieldStatus, breakIn: fresh, keySpeed: fresh },
    } as Partial<ServerState>);
    const view = model(state, fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * HONESTY / FAIL-CLOSED GATE — absent raw values never fabricate v2's
 * defaults, and an unrecognised break-in encoding never decodes to `off`.
 */
describe('cwKeyer honesty gate — absent raw values never fabricate (MOR-1296)', () => {
  const fullCaps = cwCaps();

  const ABSENT_CASES = [
    ['breakIn', 'off'],
    ['breakInDelay', 0],
    ['keyerSpeed', 12],
    ['pitchHz', 600],
    ['reversePaddle', false],
    ['apf', 0],
    ['twinPeak', false],
  ] as const;

  it.each(ABSENT_CASES)(
    '%s with nothing reported at all reads unknown, not v2\'s fabricated %s',
    (viewField) => {
      expect(model(bareState(), fullCaps).cwKeyer![viewField].reading).toEqual({ status: 'unknown' });
    },
  );

  it("v2's own toCwProps DOES fabricate those defaults — the divergence is real, not vacuous", () => {
    const real = toCwProps(bareState(), fullCaps);
    expect(real.breakIn).toBe(0);
    expect(real.keySpeed).toBe(12);
    expect(real.cwPitch).toBe(600);
    expect(real.reversePaddle).toBe(false);
  });

  it('an unrecognised break-in int reads unknown, where the real formatBreakIn falls back to OFF', () => {
    const state = bareState({
      breakIn: 7, fieldStatus: { ...bareState().fieldStatus, breakIn: fresh },
    } as Partial<ServerState>);
    expect(model(state, cwCaps()).cwKeyer!.breakIn.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * THE APF/TPF MUTEX (MOR-479 lineage, MOR-1293 precedent) — expressed as
 * `disabledReasons` with the generic `'mutually-exclusive-control'` code,
 * never as bespoke `apfDisabled`/`tpfDisabled` booleans, and fail-closed on
 * an unknown mode.
 */
describe('cwKeyer APF/TPF mode mutex (MOR-1296)', () => {
  const stateInMode = (mode: string): ServerState => bareState({
    main: { ...bareState().main, mode },
    fieldStatus: { ...bareState().fieldStatus, 'main.mode': fresh },
  } as Partial<ServerState>);

  /** The mutex reads `modeFilter.currentMode`, which needs a declared mode
   *  set to exist at all — hence the `modes` override here and its absence
   *  in the "modeFilter group absent" pin below. */
  function viewFor(mode: string, tags?: readonly string[]): RadioViewModel {
    const capabilities = { ...cwCaps(tags), modes: ['USB', 'CW', 'CW-R', 'RTTY', 'RTTY-R'] } as Capabilities;
    return model(stateInMode(mode), capabilities);
  }

  it('carries no bespoke apfDisabled/tpfDisabled key on the group', () => {
    expect(Object.keys(viewFor('CW').cwKeyer!).sort()).toEqual([
      'apf', 'breakIn', 'breakInDelay', 'keyerSpeed', 'pitchHz', 'reversePaddle', 'twinPeak',
    ]);
  });

  it.each(['CW', 'CW-R'])('leaves APF enabled in %s, and disables TPF there — parity with toCwProps', (mode) => {
    const view = viewFor(mode);
    const real = toCwProps(stateInMode(mode), cwCaps());
    expect(real.apfDisabled).toBe(false);
    expect(real.tpfDisabled).toBe(true);
    expect(reasonFields(view)).not.toContain('cwKeyer.apf');
    expect(view.disabledReasons).toContainEqual({ field: 'cwKeyer.twinPeak', code: 'mutually-exclusive-control' });
  });

  it.each(['RTTY', 'RTTY-R'])('leaves TPF enabled in %s and disables APF there', (mode) => {
    const view = viewFor(mode);
    expect(reasonFields(view)).not.toContain('cwKeyer.twinPeak');
    expect(view.disabledReasons).toContainEqual({ field: 'cwKeyer.apf', code: 'mutually-exclusive-control' });
  });

  it('disables BOTH in an unrelated mode', () => {
    const fields = reasonFields(viewFor('USB'));
    expect(fields).toContain('cwKeyer.apf');
    expect(fields).toContain('cwKeyer.twinPeak');
  });

  it('FAILS CLOSED on an unobserved mode: both disabled, no fabricated USB/CW guess', () => {
    const state = bareState({
      fieldStatus: { ...bareState().fieldStatus, 'main.mode': stale },
    } as Partial<ServerState>);
    const capabilities = { ...cwCaps(), modes: ['USB', 'CW'] } as Capabilities;
    const view = model(state, capabilities);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'unknown' });
    expect(reasonFields(view)).toContain('cwKeyer.apf');
    expect(reasonFields(view)).toContain('cwKeyer.twinPeak');
  });

  it('FAILS CLOSED when the modeFilter group is absent entirely — no mode fact, no enablement', () => {
    const view = model(stateInMode('CW'), cwCaps());
    expect(view.modeFilter).toBeUndefined();
    expect(reasonFields(view)).toContain('cwKeyer.apf');
    expect(reasonFields(view)).toContain('cwKeyer.twinPeak');
  });

  it('emits no mutex reason for a control the radio does not have', () => {
    const view = viewFor('USB', ['cw']);
    expect(reasonFields(view)).not.toContain('cwKeyer.apf');
    expect(reasonFields(view)).not.toContain('cwKeyer.twinPeak');
  });
});

/**
 * SAFETY CONSTRAINT 2 — the break-in TX gate consumes the model's ONE
 * `txPermit`. There is no second permit and no `state.ptt` read (R9).
 */
describe('cwKeyer break-in TX gate (MOR-1296)', () => {
  const inBand = cwCaps();

  it('leaves break-in ungated under a positively allowed txPermit', () => {
    const view = model(bareState(), inBand);
    expect(view.txPermit.status).toBe('allowed');
    expect(reasonFields(view)).not.toContain('cwKeyer.breakIn');
  });

  it('disables break-in out of band, with the same out-of-band code the model-level permit uses', () => {
    const state = bareState({
      main: { ...bareState().main, freqHz: 14400000 },
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14400000 },
    } as Partial<ServerState>);
    const view = model(state, inBand);
    expect(view.txPermit.status).toBe('denied');
    expect(view.disabledReasons).toContainEqual({ field: 'cwKeyer.breakIn', code: 'out-of-band' });
  });

  it('disables break-in when the TX target is unobserved (fail-closed, not fail-open)', () => {
    const state = bareState({
      txTarget: { status: 'unknown', reason: 'not-observed' },
      fieldStatus: { ...bareState().fieldStatus, txTarget: stale },
    } as Partial<ServerState>);
    const view = model(state, inBand);
    expect(view.txPermit.status).toBe('unknown');
    expect(view.disabledReasons).toContainEqual({ field: 'cwKeyer.breakIn', code: 'tx-target-unknown' });
  });

  it('disables break-in when the radio declares no TX ranges at all', () => {
    const unconfigured = caps({
      capabilities: ['tx', 'cw', 'break_in'], txBands: null,
    } as unknown as Partial<Capabilities>);
    const view = model(bareState(), unconfigured);
    expect(view.txPermit).toEqual({ status: 'unknown', reason: 'ranges-unconfigured' });
    expect(view.disabledReasons)
      .toContainEqual({ field: 'cwKeyer.breakIn', code: 'capability-unavailable' });
  });

  it('emits no break-in gate for a radio without the break_in capability — nothing to key with', () => {
    const state = bareState({
      main: { ...bareState().main, freqHz: 14400000 },
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14400000 },
    } as Partial<ServerState>);
    const view = model(state, cwCaps(['cw']));
    expect(view.txPermit.status).toBe('denied');
    expect(reasonFields(view)).not.toContain('cwKeyer.breakIn');
  });

  it('gates on the permit even while the radio reports ptt=false and break-in reads off (R9)', () => {
    const state = bareState({
      ptt: false, breakIn: 0,
      main: { ...bareState().main, freqHz: 14400000 },
      txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14400000 },
      fieldStatus: { ...bareState().fieldStatus, breakIn: fresh },
    } as Partial<ServerState>);
    const view = model(state, inBand);
    expect(view.cwKeyer!.breakIn.reading).toEqual({ status: 'known', value: 'off' });
    expect(reasonFields(view)).toContain('cwKeyer.breakIn');
  });
});

/**
 * DETERMINISM — a fact-layer value is a pure function of `(state, caps)`.
 * Both directions: same inputs ⇒ same output, and a changed input ⇒ changed
 * output (so the first half cannot pass by returning a constant).
 */
describe('cwKeyer determinism in (state, caps) (MOR-1296)', () => {
  it('is stable across repeated derivations of the same inputs', () => {
    const state = bareState({
      breakIn: 1, keySpeed: 18, cwPitch: 650,
      fieldStatus: { ...bareState().fieldStatus, breakIn: fresh, keySpeed: fresh, cwPitch: fresh },
    } as Partial<ServerState>);
    expect(model(state, cwCaps()).cwKeyer).toEqual(model(state, cwCaps()).cwKeyer);
  });

  it('changes with state, and with caps, independently', () => {
    const withState = (speed: number) => bareState({
      keySpeed: speed, fieldStatus: { ...bareState().fieldStatus, keySpeed: fresh },
    } as Partial<ServerState>);
    expect(model(withState(18), cwCaps()).cwKeyer!.keyerSpeed.reading)
      .not.toEqual(model(withState(30), cwCaps()).cwKeyer!.keyerSpeed.reading);
    expect(model(withState(18), cwCaps()).cwKeyer!.apf.availability)
      .not.toEqual(model(withState(18), cwCaps(['cw'])).cwKeyer!.apf.availability);
  });
});
