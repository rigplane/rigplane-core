/**
 * MOR-1262 decomposition slice 6A (MOR-1292) — `rfFrontEnd` fact-group
 * adapter derivation. Extended by slice 6A′ (MOR-1293) with `digiSel`/
 * `ipPlus` and the PREAMP/DIGI-SEL hardware mutex (MOR-479).
 *
 * Companion to `dsp-adapter.isolated.test.ts` (MOR-1290), which this file does NOT
 * modify. `rfFrontEnd` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `RfFrontEndViewModel` doc comment.
 *
 * Unlike `dsp`'s `nrLevel`/`nbDepth`, none of this group's six facts
 * (preamp/attenuator/rfGain/squelch/digiSel/ipPlus) consume a
 * capabilities-STORE-backed scale conversion — they are plain pass-through
 * readings (see `deriveRfFrontEnd`'s doc comment) — so this file never calls
 * the real `setCapabilities` and does not need the isolated pool (MOR-1272;
 * contrast `dsp-adapter.isolated.test.ts`'s own file-level doc comment on that
 * point).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toRfFrontEndProps } from '../../props/panel-props';

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

/** The exact shape `dsp-adapter.isolated.test.ts`'s own baseline uses. */
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

describe('rfFrontEnd evidence gate (MOR-1292, N3)', () => {
  it('emits no rfFrontEnd when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no rfFrontEnd for a baseline radio with no preamp/attenuator/rf_gain/squelch capability (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.rfFrontEnd).toBeUndefined();
    expect(Object.keys(view)).not.toContain('rfFrontEnd');
  });

  it('emits rfFrontEnd once the preamp capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['preamp'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });

  it('emits rfFrontEnd once the attenuator capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['attenuator'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });

  it('emits rfFrontEnd once the rf_gain capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['rf_gain'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });

  it('emits rfFrontEnd once the squelch capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['squelch'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });
});

describe('rfFrontEnd per-field structural gates (MOR-1292)', () => {
  it('preamp is structurally absent without the preamp capability, even with squelch present', () => {
    const view = model(bareState(), caps({ capabilities: ['squelch'] }));
    expect(view.rfFrontEnd!.preamp.availability.structural).toBe(false);
    expect(view.rfFrontEnd!.squelch.availability.structural).toBe(true);
  });

  it('attenuator is structurally absent without the attenuator capability, even with preamp present', () => {
    const view = model(bareState(), caps({ capabilities: ['preamp'] }));
    expect(view.rfFrontEnd!.attenuator.availability.structural).toBe(false);
  });

  it('rfGain is structurally absent without the rf_gain capability, even with attenuator present', () => {
    const view = model(bareState(), caps({ capabilities: ['attenuator'] }));
    expect(view.rfFrontEnd!.rfGain.availability.structural).toBe(false);
  });

  it('squelch is structurally absent without the squelch capability, even with rf_gain present', () => {
    const view = model(bareState(), caps({ capabilities: ['rf_gain'] }));
    expect(view.rfFrontEnd!.squelch.availability.structural).toBe(false);
  });

  it('follows the SUB receiver once it is the active one', () => {
    const view = model(bareState({
      active: 'SUB',
      sub: { ...bareState().sub, preamp: 2 },
      fieldStatus: { ...bareState().fieldStatus, 'sub.preamp': fresh },
    }), caps({ capabilities: ['preamp'] }));
    expect(view.rfFrontEnd!.preamp.reading).toEqual({ status: 'known', value: 2 });
  });
});

describe('rfFrontEnd per-field derivation (MOR-1292)', () => {
  const fullCaps = caps({
    capabilities: ['preamp', 'attenuator', 'rf_gain', 'squelch'],
    preValues: [0, 1, 2], attValues: [0, 6, 12, 18],
  });

  it('reports known readings for observed, fresh, capability-backed fields', () => {
    const view = model(bareState({
      main: { ...bareState().main, preamp: 2, att: 12, rfGain: 0.8, squelch: 0.25 },
      fieldStatus: {
        ...bareState().fieldStatus,
        'main.preamp': fresh, 'main.att': fresh, 'main.rfGain': fresh, 'main.squelch': fresh,
      },
    }), fullCaps);
    const rfFrontEnd = view.rfFrontEnd!;
    expect(rfFrontEnd.preamp).toEqual(
      { reading: { status: 'known', value: 2 }, availability: { structural: true, operational: true } },
    );
    expect(rfFrontEnd.attenuator.reading).toEqual({ status: 'known', value: 12 });
    expect(rfFrontEnd.rfGain.reading).toEqual({ status: 'known', value: 0.8 });
    expect(rfFrontEnd.squelch.reading).toEqual({ status: 'known', value: 0.25 });
  });

  // Verifier finding (MOR-1292 re-verify): the single-field version of this
  // pin (`att` only) let mutant G1 (loosening the `rfGain` freshness gate)
  // survive — 35/35 green. One row per field closes that: each of the four
  // controls has its OWN `topFieldAvailable(...)` call site in `deriveRfFrontEnd`,
  // and only a table covering all four proves every one of them is wired to
  // the strict gate, not just `att`'s.
  const STALE_FIELDS: ReadonlyArray<readonly [
    rawField: 'preamp' | 'att' | 'rfGain' | 'squelch',
    viewField: 'preamp' | 'attenuator' | 'rfGain' | 'squelch',
    value: number,
  ]> = [
    ['preamp', 'preamp', 2],
    ['att', 'attenuator', 18],
    ['rfGain', 'rfGain', 0.5],
    ['squelch', 'squelch', 0.5],
  ];

  it.each(STALE_FIELDS)(
    'degrades a stale %s field to unknown while keeping structural availability true',
    (rawField, viewField, value) => {
      const main = { ...bareState().main, [rawField]: value } as unknown as ServerState['main'];
      const view = model(bareState({
        main,
        fieldStatus: { ...bareState().fieldStatus, [`main.${rawField}`]: stale },
      }), fullCaps);
      expect(view.rfFrontEnd![viewField]).toEqual({
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      });
    },
  );

  it('marks a control structurally absent when its capability tag is missing, never known', () => {
    const view = model(bareState({
      main: { ...bareState().main, preamp: 2 },
      fieldStatus: { ...bareState().fieldStatus, 'main.preamp': fresh },
    }), caps({ capabilities: ['squelch'] }));
    // preamp capability absent — must never surface a "known" reading even
    // though the (irrelevant) field status looks fresh and a value exists.
    expect(view.rfFrontEnd!.preamp).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, rfGain: 'high' as unknown as number },
      fieldStatus: { ...bareState().fieldStatus, 'main.rfGain': fresh },
    }), fullCaps);
    expect(view.rfFrontEnd!.rfGain.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the rfFrontEnd group (round-trip proof)', () => {
    const view = model(bareState({
      main: { ...bareState().main, preamp: 1, att: 6 },
      fieldStatus: { ...bareState().fieldStatus, 'main.preamp': fresh, 'main.att': fresh },
    }), fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

describe('preValues/attValues choice sets (MOR-1292)', () => {
  it('reports the capability-declared choice sets verbatim', () => {
    const view = model(bareState(), caps({ capabilities: ['preamp', 'attenuator'], preValues: [0, 1, 2], attValues: [0, 20] }));
    expect(view.rfFrontEnd!.preValues).toEqual([0, 1, 2]);
    expect(view.rfFrontEnd!.attValues).toEqual([0, 20]);
  });

  it('defaults to an empty array — never the shipped panel\'s [0,1,2]/[0,6,12,18] fallback — when caps declares none', () => {
    const view = model(bareState(), caps({ capabilities: ['preamp', 'attenuator'] }));
    expect(view.rfFrontEnd!.preValues).toEqual([]);
    expect(view.rfFrontEnd!.attValues).toEqual([]);
  });
});

/**
 * HONESTY GATE (MOR-1292, mirroring the DSP F2/mutant-H3 lesson,
 * `dsp-adapter.isolated.test.ts`). Each of the four numeric fields is read via
 * `numOrUndef(raw)` with no `?? 0` — pin that a mutant seeding one WOULD be
 * caught: the field absent from the receiver object entirely, no
 * `fieldStatus` entry either, so the ONLY thing standing between an honest
 * `unknown` and a fabricated `{status: 'known', value: 0}` is `numOrUndef`
 * gating on the raw value itself.
 */
describe('rfFrontEnd honesty gate — absent raw values never fabricate (MOR-1292, mutant H3 lineage)', () => {
  const allCaps = caps({ capabilities: ['preamp', 'attenuator', 'rf_gain', 'squelch'] });
  const RECEIVER_SCOPED_FIELDS = ['preamp', 'att', 'rfGain', 'squelch'] as const;
  const VIEW_FIELD_NAME = { preamp: 'preamp', att: 'attenuator', rfGain: 'rfGain', squelch: 'squelch' } as const;

  it.each(RECEIVER_SCOPED_FIELDS)(
    '%s: absent from the receiver object (no fieldStatus entry) reads unknown, not {known, 0}',
    (field) => {
      const main = { ...bareState().main } as Record<string, unknown>;
      delete main[field];
      const view = model(bareState({ main: main as unknown as ServerState['main'] }), allCaps);
      const viewField = VIEW_FIELD_NAME[field];
      expect(view.rfFrontEnd![viewField].reading).toEqual({ status: 'unknown' });
    },
  );
});

/**
 * MOR-1293 (slice 6A′) — `digiSel`/`ipPlus`, the two family-11 facts the
 * MOR-1292 re-verify found enumerated nowhere in the decomposition despite
 * belonging to the same panel/adapter as `preamp`/`attenuator`/`rfGain`/
 * `squelch`. Same evidence-gate, structural-gate, freshness and honesty
 * disciplines as the original four — mirrored here rather than folded into
 * the existing tables above so the MOR-1292 test file stays untouched
 * (A-queue: this slice extends, never edits, prior-family assertions).
 */
describe('rfFrontEnd evidence gate — digiSel/ipPlus (MOR-1293, N3)', () => {
  it('emits rfFrontEnd once the digisel capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['digisel'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });

  it('emits rfFrontEnd once the ip_plus capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['ip_plus'] }));
    expect(view.rfFrontEnd).toBeDefined();
  });
});

describe('rfFrontEnd per-field structural gates — digiSel/ipPlus (MOR-1293)', () => {
  it('digiSel is structurally absent without the digisel capability, even with preamp present', () => {
    const view = model(bareState(), caps({ capabilities: ['preamp'] }));
    expect(view.rfFrontEnd!.digiSel.availability.structural).toBe(false);
    expect(view.rfFrontEnd!.preamp.availability.structural).toBe(true);
  });

  it('ipPlus is structurally absent without the ip_plus capability, even with digisel present', () => {
    const view = model(bareState(), caps({ capabilities: ['digisel'] }));
    expect(view.rfFrontEnd!.ipPlus.availability.structural).toBe(false);
    expect(view.rfFrontEnd!.digiSel.availability.structural).toBe(true);
  });
});

describe('rfFrontEnd per-field derivation — digiSel/ipPlus (MOR-1293)', () => {
  const boolCaps = caps({ capabilities: ['digisel', 'ip_plus'] });

  it('reports known boolean readings for observed, fresh, capability-backed fields', () => {
    const view = model(bareState({
      main: { ...bareState().main, digisel: true, ipplus: false },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh, 'main.ipplus': fresh },
    }), boolCaps);
    expect(view.rfFrontEnd!.digiSel).toEqual(
      { reading: { status: 'known', value: true }, availability: { structural: true, operational: true } },
    );
    expect(view.rfFrontEnd!.ipPlus.reading).toEqual({ status: 'known', value: false });
  });

  // Same STALE_FIELDS lesson as the base four (MOR-1292 re-verify F1): one
  // row per field proves each has its OWN `topFieldAvailable` call, not a
  // shared one that only one mutation-tested field happens to exercise.
  const STALE_BOOL_FIELDS: ReadonlyArray<readonly [
    rawField: 'digisel' | 'ipplus', viewField: 'digiSel' | 'ipPlus',
  ]> = [
    ['digisel', 'digiSel'],
    ['ipplus', 'ipPlus'],
  ];

  it.each(STALE_BOOL_FIELDS)(
    'degrades a stale %s field to unknown while keeping structural availability true',
    (rawField, viewField) => {
      const main = { ...bareState().main, [rawField]: true } as unknown as ServerState['main'];
      const view = model(bareState({
        main,
        fieldStatus: { ...bareState().fieldStatus, [`main.${rawField}`]: stale },
      }), boolCaps);
      expect(view.rfFrontEnd![viewField]).toEqual({
        reading: { status: 'unknown' }, availability: { structural: true, operational: false },
      });
    },
  );

  it('marks digiSel structurally absent when its capability tag is missing, never known', () => {
    const view = model(bareState({
      main: { ...bareState().main, digisel: true },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh },
    }), caps({ capabilities: ['squelch'] }));
    expect(view.rfFrontEnd!.digiSel).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, digisel: 'on' as unknown as boolean },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh },
    }), boolCaps);
    expect(view.rfFrontEnd!.digiSel.reading).toEqual({ status: 'unknown' });
  });
});

describe(
  'rfFrontEnd honesty gate — digiSel/ipPlus absent raw values never fabricate (MOR-1293, mutant H3 lineage)',
  () => {
    const allCaps = caps({ capabilities: ['digisel', 'ip_plus'] });
    const RECEIVER_SCOPED_BOOL_FIELDS = ['digisel', 'ipplus'] as const;
    const VIEW_FIELD_NAME = { digisel: 'digiSel', ipplus: 'ipPlus' } as const;

    it.each(RECEIVER_SCOPED_BOOL_FIELDS)(
      '%s: absent from the receiver object (no fieldStatus entry) reads unknown, not {known, false}',
      (field) => {
        const main = { ...bareState().main } as Record<string, unknown>;
        delete main[field];
        const view = model(bareState({ main: main as unknown as ServerState['main'] }), allCaps);
        const viewField = VIEW_FIELD_NAME[field];
        expect(view.rfFrontEnd![viewField].reading).toEqual({ status: 'unknown' });
      },
    );
  },
);

/**
 * THE MUTEX (MOR-479, MOR-1293) — `deriveRfFrontEndMutex`. The shipped
 * `toRfFrontEndProps` (`panel-props.ts`) computes `preDisabled = rx?.digisel
 * ?? false` and disables the PRE control when it's true. This contract
 * expresses the same disable as a `disabledReasons` entry
 * (`{field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control'}`),
 * derived from the group's OWN `digiSel` fact rather than a second raw read.
 *
 * The parity pins below call the REAL `toRfFrontEndProps` (never a
 * reimplementation) so the known-value agreement is against the shipped
 * function itself, not an assumption about it — "consume, never re-derive"
 * per the MOR-1292 re-verify's gap-ticket wording. The `unknown` pin is the
 * one place this contract is REQUIRED to diverge from that real function's
 * own `?? false` (fail-closed, not the panel's fail-open) — asserted
 * explicitly rather than merely implied by the on/off pins.
 */
describe('rfFrontEnd PREAMP/DIGI-SEL mutex (MOR-479, MOR-1293)', () => {
  const mutexCaps = caps({ capabilities: ['preamp', 'digisel'] });

  function preampMutexReason(view: RadioViewModel) {
    return view.disabledReasons.find(
      (r) => r.field === 'rfFrontEnd.preamp' && r.code === 'mutually-exclusive-control',
    );
  }

  it('DIGI-SEL on: the real toRfFrontEndProps disables PRE, and this contract agrees', () => {
    const state = bareState({
      main: { ...bareState().main, digisel: true, preamp: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh, 'main.preamp': fresh },
    });
    expect(toRfFrontEndProps(state, mutexCaps).preDisabled).toBe(true);

    const view = model(state, mutexCaps);
    expect(preampMutexReason(view)).toEqual({ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' });
  });

  it('DIGI-SEL off: the real toRfFrontEndProps leaves PRE enabled, and this contract agrees', () => {
    const state = bareState({
      main: { ...bareState().main, digisel: false, preamp: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh, 'main.preamp': fresh },
    });
    expect(toRfFrontEndProps(state, mutexCaps).preDisabled).toBe(false);

    const view = model(state, mutexCaps);
    expect(preampMutexReason(view)).toBeUndefined();
  });

  it(
    'DIGI-SEL unknown (never observed): FAILS CLOSED — disables PRE — deliberately diverging from ' +
    'the real toRfFrontEndProps, which would fail OPEN',
    () => {
      const state = bareState({
        main: { ...bareState().main, preamp: 1 },
        // No 'main.digisel' fieldStatus entry AND no fieldStatus for the group
        // parent either — an honestly never-observed control.
        fieldStatus: { ...bareState().fieldStatus, 'main.preamp': fresh },
      });
      // The shipped panel's own naive `rx?.digisel ?? false` reads this as
      // "off" — PRE stays enabled. This is the exact fabricated-default this
      // contract exists to refuse to repeat.
      expect(toRfFrontEndProps(state, mutexCaps).preDisabled).toBe(false);

      const view = model(state, mutexCaps);
      expect(preampMutexReason(view)).toEqual({ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' });
      expect(view.rfFrontEnd!.digiSel.reading).toEqual({ status: 'unknown' });
    },
  );

  it('DIGI-SEL stale: FAILS CLOSED the same way as never-observed', () => {
    const state = bareState({
      main: { ...bareState().main, digisel: false, preamp: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': stale, 'main.preamp': fresh },
    });
    const view = model(state, mutexCaps);
    expect(view.rfFrontEnd!.digiSel.reading).toEqual({ status: 'unknown' });
    expect(preampMutexReason(view)).toEqual({ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' });
  });

  it('no digisel capability at all: no mutex entry regardless of raw digisel value', () => {
    const state = bareState({
      main: { ...bareState().main, digisel: true, preamp: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh, 'main.preamp': fresh },
    });
    const view = model(state, caps({ capabilities: ['preamp'] }));
    expect(view.rfFrontEnd!.digiSel.availability.structural).toBe(false);
    expect(preampMutexReason(view)).toBeUndefined();
  });

  it('no preamp capability at all: no mutex entry regardless of DIGI-SEL state', () => {
    const state = bareState({
      main: { ...bareState().main, digisel: true },
      fieldStatus: { ...bareState().fieldStatus, 'main.digisel': fresh },
    });
    const view = model(state, caps({ capabilities: ['digisel'] }));
    expect(view.rfFrontEnd!.preamp.availability.structural).toBe(false);
    expect(preampMutexReason(view)).toBeUndefined();
  });

  it('follows the SUB receiver once it is the active one', () => {
    const state = bareState({
      active: 'SUB',
      sub: { ...bareState().sub, digisel: true, preamp: 2 },
      fieldStatus: { ...bareState().fieldStatus, 'sub.digisel': fresh, 'sub.preamp': fresh },
    });
    const view = model(state, mutexCaps);
    expect(preampMutexReason(view)).toEqual({ field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' });
  });
});
