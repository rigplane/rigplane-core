/**
 * MOR-1262 decomposition slice 4A′ (MOR-1284) — `filterPassband` fact-group
 * adapter derivation.
 *
 * Companion to `mode-filter-adapter.test.ts` (MOR-1280), which this file
 * does NOT modify. `filterPassband` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `FilterPassbandViewModel` doc comment.
 *
 * Pool: `isolated` (MOR-1272). The parity-pin block below calls the REAL
 * `setCapabilities` (`$lib/stores/capabilities.svelte`, module-scope global
 * state, no `vi.mock`) to install a non-default PBT control range, because
 * `deriveFilterPassband`'s `pbtInner`/`pbtOuter`/`ifShift` facts consume
 * `$lib/radio/filter-controls`'s `pbtRawToHz`/`deriveIfShift`, which read
 * their scale from that STORE rather than from this file's own `caps`
 * parameter. Under the fast pool's `isolate: false` this mutation would leak
 * into whichever sibling file's tests share the worker afterward — the same
 * shape `rx-audio-purity.isolated.test.ts`, `mod-input-tx-guard.isolated.test.ts`, and
 * `frontend-runtime.isolated.test.ts` are isolated for. See `vite.config.ts`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import type { Capabilities, ControlRange } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import {
  deriveIfShift, measuredPbtDisplayDomain, measuredPbtRawToHz, PBT_MEASURED_STEP_HZ,
} from '$lib/radio/filter-controls';
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

/** No `controls` entry ⇒ `pbtRawToHz`'s own store lookup falls back to the
 *  IC-7610 defaults (rawCenter 128, displayMax 1200) — the neutral baseline
 *  every test starts from, and what every test restores in `afterEach` so no
 *  custom range leaks across this file's own tests (never mind siblings —
 *  isolation handles that half; this handles order-within-file). */
const NEUTRAL_STORE_CAPS = caps();
afterEach(() => setCapabilities(NEUTRAL_STORE_CAPS));

/**
 * MOR-1291: the IC-7610/IC-7300-shaped range a REAL radio profile's caps
 * payload declares in its own `controls.pbt_inner` entry. Since the adapter
 * no longer falls back to the capabilities STORE (or any other default) for
 * `pbtInner`/`pbtOuter`/`ifShift` when `caps` omits its own range, every test
 * below that wants those fields to be structurally present must declare this
 * (or another explicit) range on its `caps` fixture — a bare `capabilities:
 * ['pbt']` with no `controls` entry is now the "unsupported/unavailable"
 * case, not an implicit stand-in for this shape.
 */
const DEFAULT_PBT_RANGE: ControlRange = {
  raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200,
};

/**
 * MOR-2497 (post-#3519): a PBT reading in Hz also needs the per-mode lattice
 * step the server publishes as `filterConfig[mode].pbtStepHz`. Fixtures that
 * expect a KNOWN Hz reading declare it here; a fixture without it is the
 * legacy-payload case pinned in the last describe block. Under the pre-change
 * conversion this key was ignored, so adding it to an existing fixture is
 * value-neutral there and load-bearing only after the change.
 */
const PBT_STEP_50_CONFIG = { USB: { defaults: [2400], fixed: false, pbtStepHz: 50 } };

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

/** The exact shape `radio-view-model-adapter.test.ts`'s own baseline uses. */
function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    // MOR-2497 step 2: `filterWidth` is now load-bearing for every PBT reading
    // in Hz -- the measured lattice has `filterWidth / 50 + 1` positions, so
    // without a width there is no Hz to report and the adapter says `unknown`.
    // 2400 Hz is a width the radio can actually be at (a whole number of 50 Hz
    // steps); the fixture previously omitted the field entirely, which the
    // retired conversion did not care about.
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      filterWidth: 2400,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    sub: {
      freqHz: 7100000, mode: 'LSB', filter: 2, dataMode: 0, att: 0, preamp: 0,
      filterWidth: 2400,
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

describe('filterPassband evidence gate (MOR-1284, N3)', () => {
  it('emits no filterPassband when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no filterPassband for a baseline radio with no filters/pbt/if_shift/data_mode capability (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.filterPassband).toBeUndefined();
    expect(Object.keys(view)).not.toContain('filterPassband');
  });

  it('does not emit filterPassband from filter choices without the filter_width capability', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband).toBeUndefined();
    expect(Object.keys(view)).not.toContain('filterPassband');
  });

  it('emits filterPassband once the pbt capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['pbt'] }));
    expect(view.filterPassband).toBeDefined();
  });

  it('emits filterPassband once the if_shift capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['if_shift'] }));
    expect(view.filterPassband).toBeDefined();
  });

  it('emits filterPassband once the data_mode capability alone is declared', () => {
    const view = model(bareState(), caps({ capabilities: ['data_mode'] }));
    expect(view.filterPassband).toBeDefined();
  });

  it('never emits filterPassband with no capabilities object at all', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });
});

describe('filterPassband per-field structural gates (MOR-1284)', () => {
  it('filterShape is structurally absent with no declared filters, even with pbt present', () => {
    const view = model(bareState(), caps({
      capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE },
    }));
    expect(view.filterPassband!.filterShape.availability.structural).toBe(false);
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(true);
  });

  it('pbtInner/pbtOuter are structurally absent without pbt, even with filter_width present', () => {
    const view = model(bareState(), caps({
      filters: ['FIL1'], capabilities: ['filter_width'],
    }));
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(false);
    expect(view.filterPassband!.pbtOuter.availability.structural).toBe(false);
    expect(view.filterPassband!.filterShape.availability.structural).toBe(true);
  });

  /**
   * MOR-1291: the `pbt` capability tag ALONE is no longer enough —
   * `pbtInner`/`pbtOuter` also require a usable `pbt_inner` range declared by
   * THIS caps object. Without it there is no fabricated IC-7610-shaped
   * substitute; the fields are structurally absent, same as if the `pbt`
   * capability were missing entirely.
   */
  it('pbtInner/pbtOuter are structurally absent when pbt is declared but caps carries no usable pbt_inner range', () => {
    const view = model(bareState(), caps({ capabilities: ['pbt'] }));
    expect(view.filterPassband!.pbtInner).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false }, display: { state: 'unsupported' },
    });
    expect(view.filterPassband!.pbtOuter).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false }, display: { state: 'unsupported' },
    });
  });

  it.each([
    ['raw_center missing', { raw_min: 0, raw_max: 255, display_min: -1200, display_max: 1200 }],
    ['display_min missing', { raw_min: 0, raw_max: 255, raw_center: 128, display_max: 1200 }],
    ['display_max missing', { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200 }],
    ['display_max is zero (division-by-zero guard)', { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 0 }],
    ['raw_center is zero (division-by-zero guard)', { raw_min: 0, raw_max: 255, raw_center: 0, display_min: -1200, display_max: 1200 }],
    ['display_max is NaN', { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: Number.NaN }],
  ] as const)('pbtInner/pbtOuter are structurally absent for an invalid pbt_inner range — %s', (_label, badRange) => {
    const view = model(bareState(), caps({
      capabilities: ['pbt'], controls: { pbt_inner: badRange as unknown as ControlRange },
    }));
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(false);
    expect(view.filterPassband!.pbtOuter.availability.structural).toBe(false);
  });

  it('dataMode is structurally absent without the data_mode capability, even with filters+pbt present', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'], capabilities: ['pbt'] }));
    expect(view.filterPassband!.dataMode.availability.structural).toBe(false);
  });

  it('follows the SUB receiver once it is the active one', () => {
    const view = model(bareState({
      active: 'SUB',
      sub: { ...bareState().sub, filterShape: 3 },
      fieldStatus: { ...bareState().fieldStatus, 'sub.filterShape': fresh },
    }), caps({ filters: ['FIL1'], capabilities: ['filter_width'] }));
    expect(view.filterPassband!.filterShape.reading).toEqual({ status: 'known', value: 3 });
  });
});

describe('dataMode derivation (MOR-1284)', () => {
  // dataMode is a REQUIRED field (`ReceiverStatePublic.dataMode: number`),
  // same "raw presence carries no signal" story 4A's mode/filter have — the
  // structural gate is the `data_mode` capability, not field observation.
  const dmCaps = caps({ capabilities: ['data_mode'] });

  it('reports a known dataMode reading once observed', () => {
    const view = model(bareState({
      main: { ...bareState().main, dataMode: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.dataMode': fresh },
    }), dmCaps);
    expect(view.filterPassband!.dataMode.reading).toEqual({ status: 'known', value: 1 });
  });

  it('holds a stale dataMode field at its last observed value (MOR-2425/R40)', () => {
    const view = model(bareState({
      fieldStatus: { ...bareState().fieldStatus, 'main.dataMode': stale },
    }), dmCaps);
    expect(view.filterPassband!.dataMode).toEqual({
      reading: { status: 'known', value: 0 },
      availability: { structural: true, operational: true },
    });
  });
});

/**
 * PARITY PIN (MOR-1284, following the 4A F1 lesson; MOR-1291 update). Every
 * row now declares its OWN explicit `controls.pbt_inner` range — including
 * the "default IC-7610-shaped" rows, which used to rely on `caps` omitting
 * the range and `pbtRawToHz` falling through to the capabilities STORE.
 * MOR-1291 removed that fallback from the fact layer: a `caps` object must
 * declare its own range, explicitly, the same way a real IC-7610/IC-7300
 * profile's capabilities payload does. `pbtInner`/`pbtOuter`/`ifShift` must
 * consume the REAL `$lib/radio/filter-controls` helpers, not a re-derived
 * formula — the discriminating axis a naive re-implementation would miss is
 * the SAME one the X6200 CAT audit flagged for filter-width tables:
 * `pbtRawToHz` reads its raw<->Hz scale from its `range` ARGUMENT, not from a
 * constant. A hand-rolled `(raw - 128) * (1200 / 128)` inside the adapter
 * would match every row that leaves the range at its IC-7610-shaped default
 * and silently diverge the instant a radio profile declares a non-default
 * `pbt_inner` control range — exactly the class of bug the 9-row
 * `resolveFilterModeConfig` matrix in `mode-filter-adapter.test.ts` killed
 * for filter width.
 *
 * Each row's "expected" value is computed by calling the SAME shipped
 * `pbtRawToHz`/`deriveIfShift` this test imports directly, with the SAME
 * explicit range the row's `caps` fixture declares — this is a
 * regression/mutation-kill pin on the ADAPTER's wiring to those functions,
 * not a re-proof of their own arithmetic.
 */
/** MOR-2497 step 2: Hz of a PBT raw on the measured lattice. The width is the
 *  scale now, so every expectation below names the width it is taken at; the
 *  declared range is a gate and no longer enters the number. */
const hz = (raw: number, widthHz = 2400): number => {
  const value = measuredPbtRawToHz(raw, widthHz, PBT_MEASURED_STEP_HZ);
  if (value === null) throw new Error(`no lattice at width ${widthHz}`);
  return value;
};

describe('pbtInner/pbtOuter/ifShift parity with the real filter-controls helpers (MOR-1284, MOR-1291)', () => {
  const customPbtRange: ControlRange = {
    raw_min: 0, raw_max: 200, raw_center: 100, display_min: -900, display_max: 900,
  };

  const MATRIX: ReadonlyArray<{
    name: string; controls: Record<string, ControlRange>; pbtInner: number; pbtOuter: number;
  }> = [
    {
      name: 'default IC-7610 range, both centered (128/128)',
      controls: { pbt_inner: DEFAULT_PBT_RANGE }, pbtInner: 128, pbtOuter: 128,
    },
    {
      name: 'default range, odd raw values that force rounding',
      controls: { pbt_inner: DEFAULT_PBT_RANGE }, pbtInner: 191, pbtOuter: 64,
    },
    {
      name: 'custom capabilities-declared PBT range (rawCenter 100, displayMax 900), asymmetric',
      controls: { pbt_inner: customPbtRange }, pbtInner: 50, pbtOuter: 175,
    },
    {
      name: 'custom range at its raw extremes (0 / 200) — the X6200-lesson discriminator',
      controls: { pbt_inner: customPbtRange }, pbtInner: 0, pbtOuter: 200,
    },
  ];

  it.each(MATRIX)('$name', ({ controls, pbtInner, pbtOuter }) => {
    const parityCaps = caps({ capabilities: ['pbt'], controls, filterConfig: PBT_STEP_50_CONFIG });
    // The store is deliberately left at an UNRELATED shape (the neutral
    // default) for every row — MOR-1291 proof-of-independence: the caps
    // object's OWN range must drive the result, never the store.
    setCapabilities(NEUTRAL_STORE_CAPS);

    // Independently-derived expectation from the REAL imported functions,
    // called with the SAME explicit range the row's `caps` fixture
    // declares — NOT a copy of the adapter's inputs; the adapter's OUTPUT is
    // compared against what the shipped helpers themselves say.
    // MOR-2497 step 2: the declared range is no longer the SCALE. The Hz comes
    // off the lattice the radio snaps to, whose spacing is the measured 50 Hz
    // step and whose span is the fixture's 2400 Hz filter width. The declared
    // range survives as the GATE -- it still decides whether this radio has a
    // usable PBT at all -- which is why the rows still vary it and why the two
    // custom-range rows below must now agree with the default-range rows.
    const width = bareState().main!.filterWidth!;
    const expectedInnerHz = measuredPbtRawToHz(pbtInner, width, PBT_MEASURED_STEP_HZ);
    const expectedOuterHz = measuredPbtRawToHz(pbtOuter, width, PBT_MEASURED_STEP_HZ);
    expect(expectedInnerHz).not.toBeNull();
    expect(expectedOuterHz).not.toBeNull();
    const expectedIfShift = deriveIfShift(expectedInnerHz!, expectedOuterHz!);

    const view = model(bareState({
      main: { ...bareState().main, pbtInner, pbtOuter },
      fieldStatus: {
        ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh,
      },
    }), parityCaps);

    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: expectedInnerHz });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'known', value: expectedOuterHz });
    // No if_shift capability declared ⇒ this exercises the `deriveIfShift`
    // fallback branch, not the raw-field branch (see the next describe block).
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: expectedIfShift });
  });

  // MOR-2497 step 2 inverts this discriminator, and that inversion IS the
  // change. The declared range used to set the scale, so two different ranges
  // had to read one raw differently -- that is what the retired assertion
  // checked. On the measured lattice the scale comes from the filter width, so
  // two different declared ranges must now read the same raw IDENTICALLY, and
  // it is the WIDTH that has to separate them. Both halves are asserted, so a
  // regression to a range-driven scale fails the first half and a conversion
  // that ignores the width fails the second.
  it('the declared range no longer sets the scale, and the filter width does', () => {
    // Asserted through the ADAPTER, not through the conversion function. The
    // range is no longer an argument to that function, so calling it twice
    // could only compare a value with itself -- which an earlier revision of
    // this test did, and review caught. The claim worth pinning is about the
    // adapter: given one state, two unrelated declared ranges must produce the
    // same reading, and two filter widths must not.
    setCapabilities(NEUTRAL_STORE_CAPS);
    const stateAt = (widthHz: number) => bareState({
      main: { ...bareState().main, filterWidth: widthHz, pbtInner: 50, pbtOuter: 128 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    });
    const readingWith = (controls: Record<string, ControlRange>, widthHz: number) =>
      model(stateAt(widthHz), caps({ capabilities: ['pbt'], controls, filterConfig: PBT_STEP_50_CONFIG })).filterPassband!.pbtInner.reading;

    const underDefault = readingWith({ pbt_inner: DEFAULT_PBT_RANGE }, 2400);
    expect(underDefault).toEqual({ status: 'known', value: hz(50) });
    // Same state and width, a range with a different centre and span: the
    // reading must not move. Under the retired conversion it would have.
    expect(readingWith({ pbt_inner: customPbtRange }, 2400)).toEqual(underDefault);
    // Same state and range, a different legal width: the reading must move.
    expect(readingWith({ pbt_inner: DEFAULT_PBT_RANGE }, 1800)).not.toEqual(underDefault);
  });

  // MOR-2497 step 2 replaces what this test used to assert. It pinned a
  // +/-1200 Hz clamp inside `deriveIfShift`; that bound was measured on the
  // IC-7610 on 2026-09-17 to truncate a state the radio reaches, so it is gone
  // and the adapter must now carry the larger shift through. The fixture's own
  // 2400 Hz width cannot show this -- half of 2400 is exactly 1200, so the
  // retired clamp and the truth agree there -- which is why this test sets a
  // 3600 Hz filter, where an edge reaches 1800 Hz.
  it('carries an IF shift past the retired ±1200 bound at a filter wide enough to reach it', () => {
    const parityCaps = caps({ capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE }, filterConfig: PBT_STEP_50_CONFIG });
    setCapabilities(NEUTRAL_STORE_CAPS); // store deliberately unrelated

    // Raw 254 is the top reachable position at 3600 Hz: the radio read both
    // edges back as 254 when both were driven to the top, and that is +1800 Hz
    // on the lattice.
    const edgeHz = measuredPbtRawToHz(254, 3600, PBT_MEASURED_STEP_HZ);
    expect(edgeHz).toBe(1800);

    const view = model(bareState({
      main: { ...bareState().main, filterWidth: 3600, pbtInner: 254, pbtOuter: 254 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), parityCaps);

    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: 1800 });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'known', value: 1800 });
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: 1800 });
  });
});

/**
 * F1 (BLOCKER, verify round 1; MOR-1291 supersedes the store-fallback half
 * of this pin). `pbtInner`/`pbtOuter`/`ifShift` must be a PURE function of
 * `(state, caps)` — never of the capabilities STORE singleton. Before the
 * MOR-1284 F1 fix, `deriveFilterPassband` called `pbtRawToHz(raw)` with no
 * range argument, so identical `(state, caps)` produced DIFFERENT facts
 * depending on whatever the store happened to hold, and — worse — a radio
 * whose OWN `caps` declared a non-default `controls.pbt_inner` range read a
 * confidently wrong `{status:'known'}` value sourced from an unrelated
 * (e.g. still-empty) store, exactly the fabrication class MOR-1280's F2 fix
 * closed for filterWidthMin/Max. F1 fixed the "caps declares its own range"
 * half via `pbtRangeFromCaps(caps)`; MOR-1291 closes the REMAINING half —
 * `caps` declaring NO range of its own no longer falls through to the store
 * for a plausible substitute either. It is now structurally absent, full
 * stop, so the store's content can never leak into the fact at all —
 * "true independence" below, not merely "self-consistent with whatever the
 * store happened to hold at read time" (the old block's weaker property).
 */
describe('pbtInner/pbtOuter/ifShift are deterministic in (state, caps) — MOR-1284 F1, MOR-1291', () => {
  const rangeA: ControlRange = { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 };
  const rangeB: ControlRange = { raw_min: 0, raw_max: 200, raw_center: 100, display_min: -900, display_max: 900 };
  // The `caps` argument itself declares NO `controls.pbt_inner` — this is
  // the property under test: the STORE varies (A / B / EMPTY) but the
  // `(state, caps)` ARGUMENTS to `toRadioViewModel` never change.
  const capsWithNoOwnRange = caps({ capabilities: ['pbt'] });
  const stateWithPbt = bareState({
    main: { ...bareState().main, pbtInner: 200, pbtOuter: 200 },
    fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
  });

  it.each([
    ['store A (default-shaped)', caps({ controls: { pbt_inner: rangeA } })],
    ['store B (non-default)', caps({ controls: { pbt_inner: rangeB } })],
    ['store EMPTY (no controls at all)', caps()],
  ] as const)(
    'TRUE INDEPENDENCE PIN (MOR-1291): caps without its own range obtains NOTHING from the store — structurally absent regardless of store A / B / EMPTY — %s',
    (_label, storeCaps) => {
      setCapabilities(storeCaps);
      const view = model(stateWithPbt, capsWithNoOwnRange);
      // `capsWithNoOwnRange` has no `controls.pbt_inner` of its own, so
      // `pbtRangeFromCaps` returns `undefined` — MOR-1291: the adapter no
      // longer falls through to the store for ANY substitute value here, so
      // the fact is identically structurally-absent across all three store
      // shapes, never merely "the same known value the store happens to
      // agree with itself on".
      const absent = {
        reading: { status: 'unknown' as const }, availability: { structural: false, operational: false },
      };
      expect(view.filterPassband!.pbtInner).toEqual({ ...absent, display: { state: 'unsupported' } });
      expect(view.filterPassband!.pbtOuter).toEqual({ ...absent, display: { state: 'unsupported' } });
      expect(view.filterPassband!.ifShift).toEqual(absent);
    },
  );

  it('caps-declared range WINS over a conflicting store — the pre-capabilities-landed boot window (verifier Probe 2)', () => {
    // The store sits at its neutral/empty default (the ordinary "capabilities
    // have not arrived yet" state) while THIS call's `caps` already declares
    // a distinct range. The fact must reflect `caps`, not the empty store.
    setCapabilities(NEUTRAL_STORE_CAPS);
    const capsWithOwnRange = caps({ capabilities: ['pbt'], controls: { pbt_inner: rangeB }, filterConfig: PBT_STEP_50_CONFIG });
    const view = model(stateWithPbt, capsWithOwnRange);
    // MOR-2497 step 2: the value no longer comes from either range -- it comes
    // from the filter width -- so the original discriminator (caps range vs
    // store range give different Hz) cannot be written any more. What this test
    // still guards is intact and is asserted directly: `caps` declaring a
    // usable range makes the reading KNOWN while the store is empty, and the
    // number is the lattice value at the state's own width.
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: hz(200) });
  });

  it('identical (state, caps) ⇒ identical facts even when the store is left at a THIRD, unrelated range mid-test (determinism, not accidental agreement)', () => {
    const capsOwnRange = caps({ capabilities: ['pbt'], controls: { pbt_inner: rangeB } });
    setCapabilities(caps({ controls: { pbt_inner: rangeA } }));
    const viewUnderStoreA = model(stateWithPbt, capsOwnRange);
    setCapabilities(caps()); // store now EMPTY — still must not move the fact
    const viewUnderEmptyStore = model(stateWithPbt, capsOwnRange);
    expect(viewUnderStoreA.filterPassband!.pbtInner.reading).toEqual(
      viewUnderEmptyStore.filterPassband!.pbtInner.reading,
    );
    expect(viewUnderStoreA.filterPassband!.ifShift.reading).toEqual(
      viewUnderEmptyStore.filterPassband!.ifShift.reading,
    );
  });
});

/**
 * ifShift's two-path conditional (MOR-1284), byte-identical to
 * `toFilterProps`'s own `hasCap(caps, 'if_shift') ? rx.ifShift :
 * deriveIfShift(pbtInner, pbtOuter)`. A naive re-implementation that always
 * derives from PBT (ignoring the capability) would pass every PBT-only row
 * above but diverge the moment a radio reports BOTH capabilities and its own
 * raw ifShift disagrees with the PBT-derived value — exactly what this block
 * pins.
 */
describe('ifShift raw-field vs PBT-derived branch selection (MOR-1284)', () => {
  it('with if_shift capability, reports the raw field even when it disagrees with the PBT-derived value', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const bothCaps = caps({ capabilities: ['if_shift', 'pbt'] });
    // pbtInner/pbtOuter (default range) would derive to a small offset from
    // center; ifShift is set to something that value could never equal, so
    // an accidental fall-through to the derive branch is unmistakable.
    const view = model(bareState({
      main: { ...bareState().main, ifShift: 900, pbtInner: 130, pbtOuter: 130 },
      fieldStatus: {
        ...bareState().fieldStatus, 'main.ifShift': fresh,
        'main.pbtInner': fresh, 'main.pbtOuter': fresh,
      },
    }), bothCaps);
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: 900 });
  });

  it('without if_shift capability but with pbt, derives from PBT even when a stray raw ifShift field is present', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const pbtOnlyCaps = caps({ capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE }, filterConfig: PBT_STEP_50_CONFIG });
    const view = model(bareState({
      main: { ...bareState().main, ifShift: 900, pbtInner: 128, pbtOuter: 128 },
      fieldStatus: {
        ...bareState().fieldStatus, 'main.ifShift': fresh,
        'main.pbtInner': fresh, 'main.pbtOuter': fresh,
      },
    }), pbtOnlyCaps);
    const expected = deriveIfShift(hz(128), hz(128));
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: expected });
    expect(view.filterPassband!.ifShift.reading).not.toEqual({ status: 'known', value: 900 });
  });

  it('with neither if_shift nor pbt, ifShift is structurally absent', () => {
    const view = model(bareState(), caps({
      filters: ['FIL1'], capabilities: ['filter_width'],
    }));
    expect(view.filterPassband!.ifShift.availability.structural).toBe(false);
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * `ifShiftControlStructural` (MOR-1494 review round) — a SEPARATE,
 * presentation-only flag `FilterSurface.svelte` uses to decide whether to
 * show the IF-shift ROW, deliberately independent of `ifShift.availability.
 * structural` above (which is `hasIfShiftCap || (hasPbtCap && hasPbtRange)`
 * as of MOR-1291 — see that block's own header comment — because
 * `scope-adapter.ts`'s passband-center overlay still needs the derived
 * reading for a PBT-only radio THAT DECLARES A USABLE RANGE).
 * `ifShiftControlStructural` answers the narrower question: does the radio
 * have a REAL `if_shift` command.
 */
describe('ifShiftControlStructural — the presentation-only IF-shift control gate (MOR-1494)', () => {
  it('IC-7300-shaped (pbt + declared range, no if_shift): false, even though the derived fact stays structural', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 60 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), caps({ capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE }, filterConfig: PBT_STEP_50_CONFIG }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(false);
    // The trap: a naive fix that reused `ifShiftStructural` for this flag
    // too would silently break `scope-adapter.ts`'s derived reading for
    // exactly this radio shape. It must stay untouched.
    expect(view.filterPassband!.ifShift.availability.structural).toBe(true);
    expect(view.filterPassband!.ifShift.reading.status).toBe('known');
  });

  /**
   * MOR-1291: the NEW degrade case — `pbt` declared but NO usable range.
   * `ifShiftControlStructural` stays `false` (still no real `if_shift`
   * command; unaffected by the range), but now the underlying derived
   * `ifShift` FACT also becomes structurally absent, since there is no scale
   * to derive an Hz value with. This is the fabrication path MOR-1291
   * closes — a caps-declared-but-rangeless PBT radio no longer gets an
   * IC-7610-shaped stand-in reading via the store fallback.
   */
  it('IC-7300-shaped WITHOUT a declared PBT range: ifShiftControlStructural stays false, AND the derived ifShift fact becomes structurally absent too', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 60 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), caps({ capabilities: ['pbt'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(false);
    expect(view.filterPassband!.ifShift).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('FTX-1-shaped (if_shift, no pbt): true', () => {
    const view = model(bareState({
      main: { ...bareState().main, ifShift: 300 },
      fieldStatus: { ...bareState().fieldStatus, 'main.ifShift': fresh },
    }), caps({ capabilities: ['if_shift'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(true);
  });

  it('neither if_shift nor pbt: false', () => {
    const view = model(bareState(), caps({
      filters: ['FIL1'], capabilities: ['filter_width'],
    }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(false);
  });

  it('both if_shift and pbt (hypothetical radio): true — a real if_shift command always wins the presentation gate', () => {
    const view = model(bareState(), caps({ capabilities: ['if_shift', 'pbt'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(true);
  });
});

/**
 * `filterShapeControlStructural` (MOR-1502) — a SEPARATE, presentation-only
 * flag `FilterInstrumentHost.svelte` uses to decide whether to show the SHARP/SOFT
 * shape ROW, deliberately independent of `filterShape.availability.
 * structural` above (which is `hasCap(caps, 'filter_width')` — see the
 * "per-field structural gates" block — because `scope-adapter.ts` still needs
 * the derived reading for a width-capable radio, filter_shape-capable or not).
 * `filterShapeControlStructural` answers the narrower
 * question: does the radio have a REAL `filter_shape` command
 * (`hasCap(caps, 'filter_shape')`).
 */
describe('filterShapeControlStructural — the presentation-only filter-shape control gate (MOR-1502)', () => {
  it('FTX-1-shaped (filter_width, no filter_shape): false, even though the derived fact stays structural', () => {
    const view = model(bareState({
      main: { ...bareState().main, filterShape: 1 },
      fieldStatus: { ...bareState().fieldStatus, 'main.filterShape': fresh },
    }), caps({
      filters: ['FIL1', 'FIL2', 'FIL3'], capabilities: ['filter_width'],
    }));
    expect(view.filterPassband!.filterShapeControlStructural).toBe(false);
    // The trap: a naive fix that reused `hasWidth` for this flag too
    // would silently break `scope-adapter.ts`'s derived reading for exactly
    // this radio shape (the FTX-1). It must stay untouched.
    expect(view.filterPassband!.filterShape.availability.structural).toBe(true);
    expect(view.filterPassband!.filterShape.reading).toEqual({ status: 'known', value: 1 });
  });

  it('IC-7300-shaped (filter_width + filter_shape): true', () => {
    const view = model(bareState(), caps({
      filters: ['FIL1', 'FIL2', 'FIL3'], capabilities: ['filter_width', 'filter_shape'],
    }));
    expect(view.filterPassband!.filterShapeControlStructural).toBe(true);
  });

  it('neither filters nor filter_shape: false', () => {
    const view = model(bareState(), caps({ capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE } }));
    expect(view.filterPassband!.filterShapeControlStructural).toBe(false);
  });
});

/**
 * HONESTY GATE (MOR-1284, following the 4A F2 lesson). `ifShift`'s derived
 * branch must never fabricate a reading from ONE observed PBT field and the
 * other's silently-defaulted value — the same "never emit a known value
 * derived from an unobserved input" discipline `deriveModeFilter`'s F2 fix
 * enforces for filterWidthMin/Max vs `modeObserved`.
 */
describe('filterPassband honesty gate — no derivation from a half-observed input (MOR-1284, F2 lesson)', () => {
  const pbtCaps = caps({ capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE }, filterConfig: PBT_STEP_50_CONFIG });

  it('pbtInner fresh, pbtOuter stale-but-observed — ifShift derives from its last value (MOR-2425/R29)', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 128 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': stale },
    }), pbtCaps);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: hz(200) });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'known', value: hz(128) });
    expect(view.filterPassband!.ifShift).toEqual({
      reading: { status: 'known', value: deriveIfShift(hz(200), hz(128)) },
      availability: { structural: true, operational: true },
    });
  });

  it('both pbtInner and pbtOuter observed — ifShift derives and reports known', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 60 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), pbtCaps);
    expect(view.filterPassband!.ifShift.reading.status).toBe('known');
  });

  // MOR-1284 F2 (verify round 1): PA4 mutant — seeding an ABSENT raw value
  // with `?? 128` — survived 26/26 because the tests above only vary
  // `fieldStatus` (unobserved) while the raw field is still PRESENT on the
  // state object. This pins the other direction: `main.pbtOuter` missing
  // from the receiver object entirely, no `fieldStatus` entry for it either
  // (so the loose `topFieldAvailable` gate would default it to "available"
  // were the reading not independently gated on the raw value itself). A
  // `?? 128` stand-in here would publish `pbtOuter {known, 0}` and an
  // `ifShift` derived from `pbtInner` + a fabricated center value — this
  // must instead read `unknown` for both, exactly like the fieldStatus-based
  // case above.
  it('pbtOuter ABSENT from the receiver object (not merely unobserved) — pbtOuter and ifShift must read unknown, not fabricate from a ?? 128 stand-in', () => {
    const mainWithoutPbtOuter = { ...bareState().main, pbtInner: 200 };
    delete (mainWithoutPbtOuter as { pbtOuter?: number }).pbtOuter;
    const view = model(bareState({ main: mainWithoutPbtOuter }), pbtCaps);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: hz(200) });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'unknown' });
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'unknown' });
  });
});

describe('filterPassband validator round-trip (MOR-1284)', () => {
  it('emits a validator-clean model carrying the filterPassband group', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'], capabilities: ['pbt', 'data_mode'] }));
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, filterShape: 'sharp' as unknown as number },
    }), caps({ filters: ['FIL1'], capabilities: ['filter_width'] }));
    expect(view.filterPassband!.filterShape.reading).toEqual({ status: 'unknown' });
  });
});


describe('PBT display observations (MOR-1692)', () => {
  // MOR-2497 step 2: the expected Hz below are lattice values at the fixture's
  // 2400 Hz filter width, written through `hz()` rather than as literals so the
  // width they belong to is visible. Under the retired conversion the declared
  // range set the scale and raw 150 read 450 Hz; the radio has no position
  // there. The declared range is still varied here because it still gates
  // whether the reading exists at all.
  const range: ControlRange = { raw_min: 0, raw_max: 200, raw_center: 100, display_min: -900, display_max: 900 };
  const ownCaps = caps({ capabilities: ['pbt'], controls: { pbt_inner: range }, filterConfig: PBT_STEP_50_CONFIG });
  const observed = { ...fresh, lastObservedMonotonic: 310658.42975425 };
  const source = (status: FieldStatus | undefined = observed, raw = 150): ServerState => bareState({
    stateContractVersion: 1, providerGeneration: 0,
    main: { ...bareState().main, pbtInner: raw, pbtOuter: 100 },
    fieldStatus: { ...bareState().fieldStatus, ...(status ? { 'main.pbtInner': status } : {}), 'main.pbtOuter': observed },
  });

  it.each(['fresh', 'stale'] as const)('adds %s scaled display; a stale-but-observed reading is available too (MOR-2425/R29)', (freshness) => {
    const input = source({ ...observed, freshness, availability: freshness === 'fresh' ? 'available' : 'stale' });
    const result = model(input, ownCaps).filterPassband!;
    const { display, ...strict } = result.pbtInner;
    expect(display).toEqual({ state: freshness === 'fresh' ? 'current' : 'stale', value: hz(150) });
    // A stale-but-observed reading still carries its last value and is
    // `available` (R29) — freshness no longer distinguishes the strict facts,
    // only the `display` cue above does.
    expect(strict).toEqual({ reading: { status: 'known', value: hz(150) }, availability: { structural: true, operational: true } });
    expect(result.pbtOuter.display).toEqual({ state: 'current', value: hz(100) });
    const { display: outerDisplay, ...strictOuter } = result.pbtOuter;
    const absent = { reading: { status: 'unknown' }, availability: { structural: false, operational: false } };
    expect({ ...result, pbtInner: strict, pbtOuter: strictOuter }).toEqual({
      filterShape: absent, filterShapeControlStructural: false, ifShiftControlStructural: false, dataMode: absent, dataModeChoices: [],
      ifShift: { reading: { status: 'known', value: deriveIfShift(hz(150), hz(100)) }, availability: { structural: true, operational: true } },
      pbtDomain: measuredPbtDisplayDomain(2400, PBT_MEASURED_STEP_HZ),
      pbtInner: strict,
      pbtOuter: { reading: { status: 'known', value: hz(100) }, availability: { structural: true, operational: true } },
    });
    expect(outerDisplay).toEqual({ state: 'current', value: hz(100) });
    expect(validateRadioViewModel(model(input, ownCaps))).toEqual(model(input, ownCaps));
  });

  it.each([
    ['missing marker', { ...fresh }, 'invalid-evidence'],
    ['unobserved', { ...observed, observed: false }, 'not-observed'],
    ['negative marker', { ...observed, lastObservedMonotonic: -0.5 }, 'invalid-evidence'],
  ] as const)('rejects %s without fabricating a measurement', (_name, status, reason) => {
    expect(model(source(status), ownCaps).filterPassband!.pbtInner.display).toEqual({ state: 'unknown', reason });
  });

  it('rejects missing metadata, invalid values, caps mismatch and absent scale', () => {
    const missing = source(); delete missing.fieldStatus!['main.pbtInner'];
    expect(model(missing, ownCaps).filterPassband!.pbtInner.display).toEqual({ state: 'unknown', reason: 'not-observed' });
    expect(model(source(observed, NaN), ownCaps).filterPassband!.pbtInner.display).toEqual({ state: 'unknown', reason: 'invalid-value' });
    expect(model(source(), { ...ownCaps, providerGeneration: 2 }).filterPassband!.pbtInner.display).toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    expect(model(source(), { ...ownCaps, controls: {} }).filterPassband!.pbtInner.display).toEqual({ state: 'unsupported' });
  });

  it.each(['stale', 'unobserved'] as const)('honors a %s ancestor independently of a fresh leaf', (parent) => {
    const input = source(); input.fieldStatus!.main = parent === 'stale'
      ? { ...observed, freshness: 'stale', availability: 'stale' } : { ...observed, observed: false };
    expect(model(input, ownCaps).filterPassband!.pbtInner.display).toEqual(parent === 'stale'
      ? { state: 'stale', value: hz(150) } : { state: 'unknown', reason: 'not-observed' });
  });
});

/**
 * Per-mode PBT lattice step (MOR-2497, on top of #3519's `pbtStepHz`). The
 * step comes from the CURRENT mode's `filterConfig` entry — 50 Hz in
 * SSB/CW/RTTY, 200 Hz in AM, absent where the mode has no twin PBT (FM).
 * Structure and reading are separate questions: `modeHasTwinPbt` decides
 * whether the PBT fields exist at all (radio-wide for a legacy payload that
 * declares no step in ANY mode), while the Hz reading needs the resolved
 * mode's own step either way.
 */
describe('per-mode PBT lattice step (MOR-2497)', () => {
  const pbtState = (mode: string, pbtInner: number, pbtOuter: number) => bareState({
    main: { ...bareState().main, mode, pbtInner, pbtOuter },
    fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
  });

  it('AM converts at its declared 200 Hz step, not the 50 Hz SSB step', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const amCaps = caps({
      capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE },
      filterConfig: {
        AM: { defaults: [2400], fixed: false, pbtStepHz: 200 },
        USB: { defaults: [2400], fixed: false, pbtStepHz: 50 },
      },
    });
    // Width 2400, raw 200: position 10 of the 13-position AM lattice is
    // +800 Hz; the same raw reads +700 Hz at the 50 Hz SSB step.
    const view = model(pbtState('AM', 200, 128), amCaps);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: 800 });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'known', value: 0 });
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: deriveIfShift(800, 0) });
  });

  it('FM (config present, no pbtStepHz, another mode declares one): no reading AND non-structural PBT fields', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const fmCaps = caps({
      capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE },
      filterConfig: {
        USB: { defaults: [2400], fixed: false, pbtStepHz: 50 },
        FM: { defaults: [15000], fixed: true },
      },
    });
    // filterWidth 2400 is a width that WOULD form a lattice at 50 Hz, so the
    // refusal can only be attributed to FM declaring no twin-PBT step.
    const view = model(pbtState('FM', 200, 56), fmCaps);
    const absent = {
      reading: { status: 'unknown' as const }, availability: { structural: false, operational: false },
    };
    expect(view.filterPassband!.pbtInner).toEqual({ ...absent, display: { state: 'unsupported' } });
    expect(view.filterPassband!.pbtOuter).toEqual({ ...absent, display: { state: 'unsupported' } });
    // The derived IF-shift path depends on PBT, so it goes non-structural
    // with it; a REAL if_shift command (not this fixture) is unaffected.
    expect(view.filterPassband!.ifShift).toEqual(absent);
  });

  it('legacy payload (no mode declares pbtStepHz): radio-wide structure, but no Hz reading', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const legacyCaps = caps({
      capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE },
      filterConfig: { USB: { defaults: [2400], fixed: false } },
    });
    const view = model(pbtState('USB', 200, 56), legacyCaps);
    // Structure is still the radio-wide capability + range decision (the
    // payload predates the field, so nothing can conclude FM-style absence);
    // the READING is absent because there is no step to convert with. A
    // 50 Hz fallback would read +700 here and fail this pin.
    expect(view.filterPassband!.pbtInner.availability).toEqual({ structural: true, operational: true });
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'unknown' });
    expect(view.filterPassband!.pbtOuter.availability).toEqual({ structural: true, operational: true });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'unknown' });
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * The group's PBT slider domain (MOR-2497 W2b): the adapter carries the SAME
 * `measuredPbtDisplayDomain` derivation `toFilterProps` already put the v2
 * `FilterPanel` on (#3527) — one derivation, no second copy; the semantic
 * surface computes nothing. No domain key (FM, a legacy payload with no
 * declared step, a width that forms no lattice) leaves the surface on its
 * own row constants, the same fallback contract `FilterPanel` kept in W2a.
 */
describe('filterPassband.pbtDomain — the measured slider domain (MOR-2497)', () => {
  const latticeCaps = (filterConfig: Capabilities['filterConfig']): Capabilities => caps({
    capabilities: ['pbt'], controls: { pbt_inner: DEFAULT_PBT_RANGE }, filterConfig,
  });
  const stepCaps = latticeCaps({
    USB: { defaults: [2400], fixed: false, pbtStepHz: 50 },
    CW: { defaults: [250], fixed: false, pbtStepHz: 50 },
    AM: { defaults: [6000], fixed: false, pbtStepHz: 200 },
  });
  const widthState = (mode: string, filterWidth: number): ServerState => bareState({
    main: { ...bareState().main, mode, filterWidth, pbtInner: 128, pbtOuter: 128 },
    fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
  });

  it.each([
    ['USB', 3600, 50, 1800],
    ['USB', 500, 50, 250],
    ['CW', 250, 50, 100],
    ['AM', 6000, 200, 3000],
  ])('%s at width %s / step %s spans +/- %s Hz', (mode, width, step, span) => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const view = model(widthState(mode, width), stepCaps);
    expect(view.filterPassband!.pbtDomain).toEqual({ min: -span, max: span, step, origin: 0 });
  });

  it('takes the width of the ACTIVE receiver (SUB at 500 while MAIN sits at 3600)', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const state = bareState({
      active: 'SUB',
      sub: { ...bareState().sub, mode: 'USB', filterWidth: 500, pbtInner: 128, pbtOuter: 128 },
      fieldStatus: { ...bareState().fieldStatus, 'sub.pbtInner': fresh, 'sub.pbtOuter': fresh },
    });
    const view = model(state, stepCaps);
    expect(view.filterPassband!.pbtDomain).toEqual({ min: -250, max: 250, step: 50, origin: 0 });
  });

  it('FM (mode with no step): no domain key — the PBT controls do not exist there', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const fmCaps = latticeCaps({
      USB: { defaults: [2400], fixed: false, pbtStepHz: 50 },
      FM: { defaults: [15000], fixed: true },
    });
    const view = model(widthState('FM', 15000), fmCaps);
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(false);
    expect(Object.keys(view.filterPassband!)).not.toContain('pbtDomain');
  });

  it('legacy payload (no mode declares a step): structural PBT fields but no domain key', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const legacyCaps = latticeCaps({ USB: { defaults: [2400], fixed: false } });
    const view = model(widthState('USB', 3600), legacyCaps);
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(true);
    expect(Object.keys(view.filterPassband!)).not.toContain('pbtDomain');
  });

  it('a width that forms no lattice (125 is not a whole multiple of 50): no domain key', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const view = model(widthState('USB', 125), stepCaps);
    expect(Object.keys(view.filterPassband!)).not.toContain('pbtDomain');
  });
});
