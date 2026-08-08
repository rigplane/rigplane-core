/**
 * MOR-1262 decomposition slice 5A (MOR-1290) — `dsp` fact-group adapter
 * derivation.
 *
 * Companion to `filter-passband-adapter.test.ts` (MOR-1284), which this file
 * does NOT modify. `dsp` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `DspViewModel` doc comment.
 *
 * Pool: `isolated` (MOR-1272). The parity-pin block below calls the REAL
 * `setCapabilities` (`$lib/stores/capabilities.svelte`, module-scope global
 * state, no `vi.mock`) to install non-default NR/NB control ranges, because
 * `deriveDsp`'s `nrLevel`/`nbDepth` facts consume `$lib/radio/filter-
 * controls`'s `nrRawToDisplay`/`nbDepthRawToDisplay`, which read their scale
 * from that STORE rather than from this file's own `caps` parameter. Under
 * the fast pool's `isolate: false` this mutation would leak into whichever
 * sibling file's tests share the worker afterward — the same shape
 * `filter-passband-adapter.test.ts` is isolated for. See `vite.config.ts`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import type { Capabilities, ControlRange } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { nrRawToDisplay, nbDepthRawToDisplay, controlRangeFromCapsOrDefault } from '$lib/radio/filter-controls';
import { setCapabilities } from '$lib/stores/capabilities.svelte';

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

/** No `controls` entry ⇒ `nrRawToDisplay`/`nbDepthRawToDisplay`'s own store
 *  lookup falls back to their built-in defaults — the neutral baseline every
 *  test starts from, and what every test restores in `afterEach` so no
 *  custom range leaks across this file's own tests (never mind siblings —
 *  isolation handles that half; this handles order-within-file). */
const NEUTRAL_STORE_CAPS = caps();
afterEach(() => setCapabilities(NEUTRAL_STORE_CAPS));

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

/** The exact shape `filter-passband-adapter.test.ts`'s own baseline uses. */
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

describe('dsp evidence gate (MOR-1290, N3)', () => {
  it('emits no dsp when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no dsp for a baseline radio with no nr/nb/notch/agc capability or nb_depth control (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.dsp).toBeUndefined();
    expect(Object.keys(view)).not.toContain('dsp');
  });

  it('emits dsp once the nr capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['nr'] }));
    expect(view.dsp).toBeDefined();
  });

  it('emits dsp once the nb capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['nb'] }));
    expect(view.dsp).toBeDefined();
  });

  it('emits dsp once the notch capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['notch'] }));
    expect(view.dsp).toBeDefined();
  });

  it('emits dsp once the agc capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['agc'] }));
    expect(view.dsp).toBeDefined();
  });

  it('emits dsp once an nb_depth control range alone is declared, with no other capability', () => {
    const nbDepthRange: ControlRange = { raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10 };
    const view = model(bareState(), caps({ controls: { nb_depth: nbDepthRange } }));
    expect(view.dsp).toBeDefined();
  });

  it('never emits dsp with no capabilities object at all', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });
});

describe('dsp per-field structural gates (MOR-1290)', () => {
  it('nrActive/nrLevel are structurally absent without the nr capability, even with agc present', () => {
    const view = model(bareState(), caps({ capabilities: ['agc'] }));
    expect(view.dsp!.nrActive.availability.structural).toBe(false);
    expect(view.dsp!.nrLevel.availability.structural).toBe(false);
    expect(view.dsp!.agcMode.availability.structural).toBe(true);
  });

  it('nbActive/nbLevel are structurally absent without the nb capability, even with nr present', () => {
    const view = model(bareState(), caps({ capabilities: ['nr'] }));
    expect(view.dsp!.nbActive.availability.structural).toBe(false);
    expect(view.dsp!.nbLevel.availability.structural).toBe(false);
  });

  it('nbDepth/nbWidth are structurally absent without a declared nb_depth control range, even with nb present', () => {
    const view = model(bareState(), caps({ capabilities: ['nb'] }));
    expect(view.dsp!.nbDepth.availability.structural).toBe(false);
    expect(view.dsp!.nbWidth.availability.structural).toBe(false);
    expect(view.dsp!.nbActive.availability.structural).toBe(true);
  });

  it('nbDepth/nbWidth are structurally present once an nb_depth control range is declared, independent of the nb capability', () => {
    const nbDepthRange: ControlRange = { raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10 };
    const view = model(bareState(), caps({ controls: { nb_depth: nbDepthRange } }));
    expect(view.dsp!.nbDepth.availability.structural).toBe(true);
    expect(view.dsp!.nbWidth.availability.structural).toBe(true);
    expect(view.dsp!.nbActive.availability.structural).toBe(false);
  });

  it('notchMode/notchFreq/manualNotchWidth are structurally absent without the notch capability', () => {
    const view = model(bareState(), caps({ capabilities: ['agc'] }));
    expect(view.dsp!.notchMode.availability.structural).toBe(false);
    expect(view.dsp!.notchFreq.availability.structural).toBe(false);
    expect(view.dsp!.manualNotchWidth.availability.structural).toBe(false);
  });

  it('agcMode/agcTimeConstant are structurally absent without the agc capability', () => {
    const view = model(bareState(), caps({ capabilities: ['nr'] }));
    expect(view.dsp!.agcMode.availability.structural).toBe(false);
    expect(view.dsp!.agcTimeConstant.availability.structural).toBe(false);
  });

  it('follows the SUB receiver once it is the active one', () => {
    const view = model(bareState({
      active: 'SUB',
      sub: { ...bareState().sub, nr: true },
      fieldStatus: { ...bareState().fieldStatus, 'sub.nr': fresh },
    }), caps({ capabilities: ['nr'] }));
    expect(view.dsp!.nrActive.reading).toEqual({ status: 'known', value: true });
  });
});

describe('agcModes choice set (MOR-1290)', () => {
  it('reports the capability-declared choice set verbatim', () => {
    const view = model(bareState(), caps({ capabilities: ['agc'], agcModes: [1, 2, 3] }));
    expect(view.dsp!.agcModes).toEqual([1, 2, 3]);
  });

  it('defaults to an empty array — never a fabricated FAST/MID/SLOW default — when caps declares none', () => {
    const view = model(bareState(), caps({ capabilities: ['agc'] }));
    expect(view.dsp!.agcModes).toEqual([]);
  });
});

/**
 * PARITY PIN (MOR-1290, following the 4A′ F1 lesson). `nrLevel`/`nbDepth`
 * must consume the REAL `$lib/radio/filter-controls` helpers, not a
 * re-derived formula. The discriminating axis a naive re-implementation
 * would miss is the SAME one the X6200 CAT audit flagged for filter-width
 * tables and `filterPassband`'s PBT scale: `nrRawToDisplay`/
 * `nbDepthRawToDisplay` read their raw<->display scale from the capabilities
 * STORE's `controls.nr_level`/`controls.nb_depth` (rawMin/rawMax/displayMin/
 * displayMax), not from a constant. A hand-rolled linear-interpolation
 * inside the adapter would match every row that leaves the store at its
 * built-in default and silently diverge the instant a radio profile
 * declares a non-default control range.
 *
 * Each row's "expected" value is computed by calling the SAME shipped
 * `nrRawToDisplay`/`nbDepthRawToDisplay` this test imports directly — this
 * is a regression/mutation-kill pin on the ADAPTER's wiring to those
 * functions, not a re-proof of their own arithmetic.
 */
describe('nrLevel/nbDepth parity with the real filter-controls helpers (MOR-1290)', () => {
  const customNrRange: ControlRange = { raw_min: 0, raw_max: 200, raw_center: 0, display_min: 0, display_max: 20 };
  const customNbDepthRange: ControlRange = {
    raw_min: 0, raw_max: 20, raw_center: 0, display_min: 1, display_max: 20,
  };

  const NR_MATRIX: ReadonlyArray<{ name: string; controls?: Record<string, ControlRange>; raw: number }> = [
    { name: 'default 0-255/0-15 range, mid value', raw: 128 },
    { name: 'default range, odd raw value that forces rounding', raw: 191 },
    {
      name: 'custom capabilities-store nr_level range (0-200 -> 0-20)',
      controls: { nr_level: customNrRange }, raw: 100,
    },
    {
      name: 'custom range at its raw extreme (200) — the X6200-lesson discriminator',
      controls: { nr_level: customNrRange }, raw: 200,
    },
  ];

  it.each(NR_MATRIX)('nrLevel: $name', ({ controls, raw }) => {
    const parityCaps = caps({ capabilities: ['nr'], ...(controls ? { controls } : {}) });
    setCapabilities(parityCaps);
    const expectedDisplay = nrRawToDisplay(raw);

    const view = model(bareState({
      main: { ...bareState().main, nrLevel: raw },
      fieldStatus: { ...bareState().fieldStatus, 'main.nrLevel': fresh },
    }), parityCaps);

    expect(view.dsp!.nrLevel.reading).toEqual({ status: 'known', value: expectedDisplay });
  });

  // `nbDepth`'s STRUCTURAL gate itself requires a declared `nb_depth` control
  // entry to exist (see `deriveDsp`'s `hasNbDepth`), so every row below
  // declares one explicitly — including the "IC-7610-shaped" row, which
  // declares the same bounds `CONTROL_DEFAULTS.nb_depth` uses rather than
  // omitting `controls.nb_depth` (that would fail the structural gate, not
  // exercise the store-lookup fallback branch).
  const ic7610ShapedRange: ControlRange = { raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10 };

  const NB_MATRIX: ReadonlyArray<{ name: string; range: ControlRange; raw: number }> = [
    { name: 'declared IC-7610-shaped range (0-9 -> 1-10), mid value', range: ic7610ShapedRange, raw: 5 },
    { name: 'custom capabilities-store nb_depth range (0-20 -> 1-20)', range: customNbDepthRange, raw: 10 },
    {
      name: 'custom range at its raw extreme (20) — the X6200-lesson discriminator',
      range: customNbDepthRange, raw: 20,
    },
  ];

  it.each(NB_MATRIX)('nbDepth: $name', ({ range, raw }) => {
    const parityCaps = caps({ controls: { nb_depth: range } });
    setCapabilities(parityCaps);
    const expectedDisplay = nbDepthRawToDisplay(raw);

    const view = model(bareState({
      nbDepth: raw,
      fieldStatus: { ...bareState().fieldStatus, nbDepth: fresh },
    }), parityCaps);

    expect(view.dsp!.nbDepth.reading).toEqual({ status: 'known', value: expectedDisplay });
  });

  it('the custom-range rows actually produce a different scale than the default (sanity on the discriminator itself)', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const defaultHz = nrRawToDisplay(100);
    setCapabilities(caps({ controls: { nr_level: customNrRange } }));
    const customHz = nrRawToDisplay(100);
    expect(customHz).not.toBe(defaultHz);
  });
});

/**
 * F1 determinism pin (MOR-1290, following `filterPassband`'s MOR-1284 F1
 * lesson — CLOSED at verify round 1). `nrLevel`/`nbDepth` must be a PURE
 * function of `(state, caps)` in BOTH branches: when `caps` declares its own
 * `controls.nr_level`/`nb_depth` range (CASE A) AND when it declares none at
 * all (CASE B). CASE A was already deterministic pre-fix (an explicit range
 * always won). CASE B was NOT: `controlRangeFromCaps` alone returns
 * `undefined` when caps carries no range, and passing that straight through
 * let `nrRawToDisplay` fall through to ITS OWN capabilities-STORE lookup —
 * the round-1 verifier's live probe showed `nrLevel` moving 8 / 50 / 8 across
 * three store states for the SAME `(state, caps)` pair. The fix routes
 * `deriveDsp` through `controlRangeFromCapsOrDefault` (`$lib/radio/filter-
 * controls`, MOR-1290 F1), which always returns a CONCRETE range — this
 * module's own `CONTROL_DEFAULTS` when caps has none — so the store is never
 * consulted in either branch.
 *
 * The round-1 verifier's OWN finding on the prior version of this block: its
 * expectation was computed via the store-reading `nrRawToDisplay(100)`
 * no-arg call, so it moved WITH whatever store the test happened to set and
 * could only prove "the adapter agrees with the store fallback" — never "the
 * fact is independent of the store". Every expectation below is computed via
 * `controlRangeFromCapsOrDefault(key, caps)` — a pure function of `caps`
 * ALONE, with zero store read — so it cannot silently track a store mutation
 * the way the old computation did.
 */
describe('nrLevel/nbDepth are deterministic in (state, caps) — MOR-1290 (F1, verify round 1)', () => {
  const rangeA: ControlRange = { raw_min: 0, raw_max: 255, raw_center: 0, display_min: 0, display_max: 15 };
  const rangeB: ControlRange = { raw_min: 0, raw_max: 200, raw_center: 0, display_min: 0, display_max: 20 };
  // The `caps` argument itself declares NO `controls.nr_level` — this is the
  // property under test: the STORE varies (A / B / EMPTY) but the
  // `(state, caps)` ARGUMENTS to `toRadioViewModel` never change.
  const capsWithNoOwnRange = caps({ capabilities: ['nr'] });
  const stateWithNr = bareState({
    main: { ...bareState().main, nrLevel: 100 },
    fieldStatus: { ...bareState().fieldStatus, 'main.nrLevel': fresh },
  });
  // Pure — no `setCapabilities` call anywhere in this expression. This is
  // THE literal value `deriveDsp` must produce for `(stateWithNr,
  // capsWithNoOwnRange)`, computed once, outside any store mutation.
  const pureExpected = nrRawToDisplay(100, controlRangeFromCapsOrDefault('nr_level', capsWithNoOwnRange));

  it.each([
    ['store shaped like rangeA (0-255 -> 0-15, same shape CONTROL_DEFAULTS uses)', rangeA],
    ['store shaped like rangeB (0-200 -> 0-20, genuinely different scale)', rangeB],
  ] as const)(
    'identical (state, caps-without-range) ⇒ the SAME store-independent literal fact — %s',
    (_label, storeRange) => {
      setCapabilities(caps({ controls: { nr_level: storeRange } }));
      const view = model(stateWithNr, capsWithNoOwnRange);
      expect(view.dsp!.nrLevel.reading).toEqual({ status: 'known', value: pureExpected });
    },
  );

  it('identical (state, caps-without-range) ⇒ the SAME store-independent literal fact — store EMPTY', () => {
    setCapabilities(caps());
    const view = model(stateWithNr, capsWithNoOwnRange);
    expect(view.dsp!.nrLevel.reading).toEqual({ status: 'known', value: pureExpected });
  });

  it('sanity: rangeA and rangeB really do produce different raw-100 outputs when actually applied (the discriminator the rows above depend on)', () => {
    const underA = nrRawToDisplay(100, { rawMin: 0, rawMax: 255, displayMin: 0, displayMax: 15 });
    const underB = nrRawToDisplay(100, { rawMin: 0, rawMax: 200, displayMin: 0, displayMax: 20 });
    expect(underA).not.toBe(underB);
  });

  // Mirrors the round-1 verifier's own live probe (raw 128, 0..15 vs 0..100)
  // as a direct, easy-to-re-run kill-test for a regression of this exact fix.
  it("reproduces the verifier's own probe: raw 128 reads the SAME literal value under three genuinely different stores, not 8/50/8", () => {
    const raw = 128;
    const stateWith128 = bareState({
      main: { ...bareState().main, nrLevel: raw },
      fieldStatus: { ...bareState().fieldStatus, 'main.nrLevel': fresh },
    });
    const literalExpected = nrRawToDisplay(raw, controlRangeFromCapsOrDefault('nr_level', capsWithNoOwnRange));

    setCapabilities(caps({ controls: { nr_level: { raw_min: 0, raw_max: 255, raw_center: 0, display_min: 0, display_max: 15 } } }));
    const underDefaultShapedStore = model(stateWith128, capsWithNoOwnRange).dsp!.nrLevel.reading;

    setCapabilities(caps({ controls: { nr_level: { raw_min: 0, raw_max: 100, raw_center: 0, display_min: 0, display_max: 100 } } }));
    const under0to100Store = model(stateWith128, capsWithNoOwnRange).dsp!.nrLevel.reading;

    setCapabilities(caps());
    const underEmptyStore = model(stateWith128, capsWithNoOwnRange).dsp!.nrLevel.reading;

    expect(underDefaultShapedStore).toEqual({ status: 'known', value: literalExpected });
    expect(under0to100Store).toEqual({ status: 'known', value: literalExpected });
    expect(underEmptyStore).toEqual({ status: 'known', value: literalExpected });
  });

  it('CASE A — caps-declared range WINS over a conflicting store — the pre-capabilities-landed boot window', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const capsWithOwnRange = caps({ capabilities: ['nr'], controls: { nr_level: rangeB } });
    const view = model(stateWithNr, capsWithOwnRange);
    const expectedFromCaps = nrRawToDisplay(100, { rawMin: 0, rawMax: 200, displayMin: 0, displayMax: 20 });
    const expectedFromEmptyStore = nrRawToDisplay(100); // what the OLD store-only code would have said
    expect(expectedFromCaps).not.toBe(expectedFromEmptyStore);
    expect(view.dsp!.nrLevel.reading).toEqual({ status: 'known', value: expectedFromCaps });
  });

  it('CASE A — identical (state, caps) ⇒ identical facts even when the store is left at a THIRD, unrelated range mid-test', () => {
    const capsOwnRange = caps({ capabilities: ['nr'], controls: { nr_level: rangeB } });
    setCapabilities(caps({ controls: { nr_level: rangeA } }));
    const viewUnderStoreA = model(stateWithNr, capsOwnRange);
    setCapabilities(caps()); // store now EMPTY — still must not move the fact
    const viewUnderEmptyStore = model(stateWithNr, capsOwnRange);
    expect(viewUnderStoreA.dsp!.nrLevel.reading).toEqual(viewUnderEmptyStore.dsp!.nrLevel.reading);
  });
});

/**
 * HONESTY GATE (MOR-1290, following the 4A′ F2 lesson). `notchMode`'s
 * derivation from TWO raw booleans (`autoNotch`/`manualNotch`) must never
 * fabricate a reading from ONE observed field and the other's silently-
 * defaulted-to-false value — the same "never emit a known value derived
 * from an unobserved input" discipline `filterPassband`'s `ifShift` F2 fix
 * enforces for `pbtInner`/`pbtOuter`.
 */
describe('dsp honesty gate — no notchMode derivation from a half-observed input (MOR-1290, F2 lesson)', () => {
  const notchCaps = caps({ capabilities: ['notch'] });

  it('autoNotch observed, manualNotch UNOBSERVED (stale) — notchMode must NOT derive from a fabricated manualNotch default', () => {
    const view = model(bareState({
      main: { ...bareState().main, autoNotch: true, manualNotch: false },
      fieldStatus: { ...bareState().fieldStatus, 'main.autoNotch': fresh, 'main.manualNotch': stale },
    }), notchCaps);
    expect(view.dsp!.notchMode).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('both autoNotch and manualNotch observed — notchMode derives and reports known', () => {
    const view = model(bareState({
      main: { ...bareState().main, autoNotch: false, manualNotch: true },
      fieldStatus: { ...bareState().fieldStatus, 'main.autoNotch': fresh, 'main.manualNotch': fresh },
    }), notchCaps);
    expect(view.dsp!.notchMode.reading).toEqual({ status: 'known', value: 'manual' });
  });

  it('both false and both observed ⇒ notchMode reports the honest "off", not withheld as unknown', () => {
    const view = model(bareState({
      main: { ...bareState().main, autoNotch: false, manualNotch: false },
      fieldStatus: { ...bareState().fieldStatus, 'main.autoNotch': fresh, 'main.manualNotch': fresh },
    }), notchCaps);
    expect(view.dsp!.notchMode.reading).toEqual({ status: 'known', value: 'off' });
  });

  // MOR-1290 (mirroring MOR-1284 F2's mutant pin): a `?? false` stand-in for
  // an ABSENT raw value must not seed a fabricated 'off'. `main.manualNotch`
  // missing from the receiver object entirely, no `fieldStatus` entry for it
  // either (so the loose `topFieldAvailable` gate defaults it to "available"
  // were the reading not independently gated on the raw value itself).
  it('manualNotch ABSENT from the receiver object (not merely unobserved) — notchMode must read unknown, not fabricate "auto"/"off"', () => {
    const mainWithoutManualNotch = { ...bareState().main, autoNotch: false };
    delete (mainWithoutManualNotch as { manualNotch?: boolean }).manualNotch;
    const view = model(bareState({
      main: mainWithoutManualNotch,
      fieldStatus: { ...bareState().fieldStatus, 'main.autoNotch': fresh },
    }), notchCaps);
    expect(view.dsp!.notchMode.reading).toEqual({ status: 'unknown' });
  });

  it('with no notch capability, notchMode is structurally absent even when both booleans are observed', () => {
    const view = model(bareState({
      main: { ...bareState().main, autoNotch: false, manualNotch: false },
      fieldStatus: { ...bareState().fieldStatus, 'main.autoNotch': fresh, 'main.manualNotch': fresh },
    }), caps({ capabilities: ['agc'] }));
    expect(view.dsp!.notchMode.availability.structural).toBe(false);
    expect(view.dsp!.notchMode.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * HONESTY GATE (MOR-1290 F2, verify round 1). `notchMode`'s absent-raw pin
 * above does NOT cover the six plain numeric fields — each is read via
 * `numOrUndef(raw)` with no `?? 0` today, but nothing pinned that a mutant
 * seeding one WOULD be caught. The round-1 verifier's mutation H3
 * (`numOrUndef(rx?.nrLevel) ?? 0`) survived all 51 pre-fix tests, turning an
 * entirely-absent `nrLevel` into a fabricated `{status:'known', value:0}` —
 * "NR level is 0" — with nothing to notice. One table-driven case per field,
 * mirroring `notchMode`'s own "ABSENT from the receiver object (not merely
 * unobserved), no `fieldStatus` entry either" construction, so the loose
 * `topFieldAvailable` gate defaults to "available" and the ONLY thing that
 * can stop a fabricated reading is `numOrUndef` gating on the raw value
 * itself — exactly what a `?? 0` mutant would defeat.
 */
describe('dsp honesty gate — absent raw values on numeric fields never fabricate (MOR-1290 F2, mutant H3)', () => {
  const allCaps = caps({
    capabilities: ['nr', 'nb', 'notch', 'agc'],
    controls: { nb_depth: { raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10 } },
  });

  const RECEIVER_SCOPED_FIELDS = ['nrLevel', 'nbLevel', 'manualNotchWidth', 'agcTimeConstant'] as const;

  it.each(RECEIVER_SCOPED_FIELDS)(
    '%s: absent from the receiver object (no fieldStatus entry) reads unknown, not {known, 0}',
    (field) => {
      const main = { ...bareState().main } as Record<string, unknown>;
      delete main[field];
      const view = model(bareState({ main: main as unknown as ServerState['main'] }), allCaps);
      expect(view.dsp![field].reading).toEqual({ status: 'unknown' });
    },
  );

  // `nbDepth`/`notchFreq` are TOP-LEVEL `ServerState` fields (not on `main`),
  // and `bareState()` already omits both by default — no explicit `delete`
  // needed, the baseline fixture itself is the absent-raw case.
  const TOP_LEVEL_FIELDS = ['nbDepth', 'notchFreq'] as const;

  it.each(TOP_LEVEL_FIELDS)(
    '%s: absent from top-level state (no fieldStatus entry) reads unknown, not {known, 0}',
    (field) => {
      const view = model(bareState(), allCaps);
      expect(view.dsp![field].reading).toEqual({ status: 'unknown' });
    },
  );
});

describe('dsp validator round-trip (MOR-1290)', () => {
  it('emits a validator-clean model carrying the dsp group', () => {
    const view = model(bareState(), caps({ capabilities: ['nr', 'nb', 'notch', 'agc'] }));
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, agc: 'fast' as unknown as number },
    }), caps({ capabilities: ['agc'] }));
    expect(view.dsp!.agcMode.reading).toEqual({ status: 'unknown' });
  });
});
