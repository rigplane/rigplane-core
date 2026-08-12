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
import { deriveIfShift, pbtRawToHz } from '$lib/radio/filter-controls';
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

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

/** The exact shape `radio-view-model-adapter.test.ts`'s own baseline uses. */
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

describe('filterPassband evidence gate (MOR-1284, N3)', () => {
  it('emits no filterPassband when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no filterPassband for a baseline radio with no filters/pbt/if_shift/data_mode capability (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.filterPassband).toBeUndefined();
    expect(Object.keys(view)).not.toContain('filterPassband');
  });

  it('emits filterPassband once filters alone are declared (filterShape structural signal)', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband).toBeDefined();
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
    const view = model(bareState(), caps({ capabilities: ['pbt'] }));
    expect(view.filterPassband!.filterShape.availability.structural).toBe(false);
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(true);
  });

  it('pbtInner/pbtOuter are structurally absent without the pbt capability, even with filters present', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband!.pbtInner.availability.structural).toBe(false);
    expect(view.filterPassband!.pbtOuter.availability.structural).toBe(false);
    expect(view.filterPassband!.filterShape.availability.structural).toBe(true);
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
    }), caps({ filters: ['FIL1'] }));
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

  it('degrades a stale dataMode field to unknown while structural availability stays true', () => {
    const view = model(bareState({
      fieldStatus: { ...bareState().fieldStatus, 'main.dataMode': stale },
    }), dmCaps);
    expect(view.filterPassband!.dataMode).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });
});

/**
 * PARITY PIN (MOR-1284, following the 4A F1 lesson). `pbtInner`/`pbtOuter`/
 * `ifShift` must consume the REAL `$lib/radio/filter-controls` helpers, not
 * a re-derived formula. The discriminating axis a naive re-implementation
 * would miss is the SAME one the X6200 CAT audit flagged for filter-width
 * tables: `pbtRawToHz` reads its raw<->Hz scale from the capabilities
 * STORE's `controls.pbt_inner` (rawCenter/displayMax), not from a constant.
 * A hand-rolled `(raw - 128) * (1200 / 128)` inside the adapter would match
 * every row that leaves the store at its IC-7610-shaped default and silently
 * diverge the instant a radio profile declares a non-default `pbt_inner`
 * control range — exactly the class of bug the 9-row `resolveFilterModeConfig`
 * matrix in `mode-filter-adapter.test.ts` killed for filter width.
 *
 * Each row's "expected" value is computed by calling the SAME shipped
 * `pbtRawToHz`/`deriveIfShift` this test imports directly — this is a
 * regression/mutation-kill pin on the ADAPTER's wiring to those functions,
 * not a re-proof of their own arithmetic.
 */
describe('pbtInner/pbtOuter/ifShift parity with the real filter-controls helpers (MOR-1284)', () => {
  const customPbtRange: ControlRange = {
    raw_min: 0, raw_max: 200, raw_center: 100, display_min: -900, display_max: 900,
  };

  const MATRIX: ReadonlyArray<{
    name: string; controls?: Record<string, ControlRange>; pbtInner: number; pbtOuter: number;
  }> = [
    { name: 'default IC-7610 range, both centered (128/128)', pbtInner: 128, pbtOuter: 128 },
    { name: 'default range, odd raw values that force rounding', pbtInner: 191, pbtOuter: 64 },
    {
      name: 'custom capabilities-store PBT range (rawCenter 100, displayMax 900), asymmetric',
      controls: { pbt_inner: customPbtRange }, pbtInner: 50, pbtOuter: 175,
    },
    {
      name: 'custom range at its raw extremes (0 / 200) — the X6200-lesson discriminator',
      controls: { pbt_inner: customPbtRange }, pbtInner: 0, pbtOuter: 200,
    },
  ];

  it.each(MATRIX)('$name', ({ controls, pbtInner, pbtOuter }) => {
    // The SAME caps object drives both the global store (what `pbtRawToHz`
    // reads) and the adapter's `caps` parameter (what `hasCap` reads) —
    // mirroring how `frontend-runtime.ts` wires `setCapabilities` and
    // `runtime.caps` from one shared object in production.
    const parityCaps = caps({ capabilities: ['pbt'], ...(controls ? { controls } : {}) });
    setCapabilities(parityCaps);

    // Independently-derived expectation from the REAL imported functions —
    // NOT a copy of the adapter's inputs; the adapter's OUTPUT is compared
    // against what the shipped helpers themselves say.
    const expectedInnerHz = pbtRawToHz(pbtInner);
    const expectedOuterHz = pbtRawToHz(pbtOuter);
    const expectedIfShift = deriveIfShift(expectedInnerHz, expectedOuterHz);

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

  it('the custom-range rows actually produce a different scale than the default (sanity on the discriminator itself)', () => {
    setCapabilities(NEUTRAL_STORE_CAPS);
    const defaultHz = pbtRawToHz(50);
    setCapabilities(caps({ controls: { pbt_inner: customPbtRange } }));
    const customHz = pbtRawToHz(50);
    expect(customHz).not.toBe(defaultHz);
  });

  it('ifShift clamps to ±1200 via the real deriveIfShift even when the custom PBT scale would exceed it, while pbtInner/pbtOuter stay UNCLAMPED', () => {
    // displayMax 2000 pushes raw 255 well past ±1200 Hz before clamping —
    // `pbtRawToHz` itself never clamps (only `deriveIfShift` does), so a
    // re-implementation that clamped the wrong function (or neither) would
    // diverge on exactly one side of this assertion.
    const wideRange: ControlRange = { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -2000, display_max: 2000 };
    const parityCaps = caps({ capabilities: ['pbt'], controls: { pbt_inner: wideRange } });
    setCapabilities(parityCaps);

    const expectedInnerHz = pbtRawToHz(255);
    const expectedIfShift = deriveIfShift(expectedInnerHz, expectedInnerHz);
    expect(Math.abs(expectedInnerHz)).toBeGreaterThan(1200);
    expect(Math.abs(expectedIfShift)).toBe(1200);

    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 255, pbtOuter: 255 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), parityCaps);

    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: expectedInnerHz });
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: 1200 });
  });
});

/**
 * F1 (BLOCKER, verify round 1). `pbtInner`/`pbtOuter`/`ifShift` must be a
 * PURE function of `(state, caps)` — never of the capabilities STORE
 * singleton `pbtRawToHz` otherwise falls back to. Before the fix,
 * `deriveFilterPassband` called `pbtRawToHz(raw)` with no range argument, so
 * identical `(state, caps)` produced DIFFERENT facts depending on whatever
 * the store happened to hold, and — worse — a radio whose OWN `caps`
 * declared a non-default `controls.pbt_inner` range read a confidently wrong
 * `{status:'known'}` value sourced from an unrelated (e.g. still-empty)
 * store, exactly the fabrication class MOR-1280's F2 fix closed for
 * filterWidthMin/Max. Fix: `pbtRangeFromCaps(caps)` derives the range
 * explicitly from THIS call's own `caps` argument and is passed into
 * `pbtRawToHz` — the store lookup is now used ONLY when `caps` carries no
 * usable range of its own (the honest "we don't know this radio's scale"
 * case), never as an override of a range `caps` already declares.
 */
describe('pbtInner/pbtOuter/ifShift are deterministic in (state, caps) — MOR-1284 F1', () => {
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
  ] as const)('identical (state, caps) ⇒ identical facts, regardless of the store — %s', (_label, storeCaps) => {
    setCapabilities(storeCaps);
    const view = model(stateWithPbt, capsWithNoOwnRange);
    // `capsWithNoOwnRange` has no `controls.pbt_inner` of its own, so
    // `pbtRangeFromCaps` returns `undefined` and the adapter's own
    // documented fallback (store lookup) applies HERE — this block's point
    // is that a `caps` argument WITHOUT its own range still degrades
    // predictably to "whatever pbtRange()'s try/catch default is" rather
    // than reading three different values for three different stores when
    // nothing about the request changed. Assert against the one real
    // conversion function, not a hand-copied constant.
    const expected = pbtRawToHz(200);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: expected });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'known', value: expected });
  });

  it('caps-declared range WINS over a conflicting store — the pre-capabilities-landed boot window (verifier Probe 2)', () => {
    // The store sits at its neutral/empty default (the ordinary "capabilities
    // have not arrived yet" state) while THIS call's `caps` already declares
    // a distinct range. The fact must reflect `caps`, not the empty store.
    setCapabilities(NEUTRAL_STORE_CAPS);
    const capsWithOwnRange = caps({ capabilities: ['pbt'], controls: { pbt_inner: rangeB } });
    const view = model(stateWithPbt, capsWithOwnRange);
    const expectedFromCaps = pbtRawToHz(200, { rawCenter: 100, displayMin: -900, displayMax: 900 });
    const expectedFromEmptyStore = pbtRawToHz(200); // what the OLD store-only code would have said
    expect(expectedFromCaps).not.toBe(expectedFromEmptyStore);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: expectedFromCaps });
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
    const pbtOnlyCaps = caps({ capabilities: ['pbt'] });
    const view = model(bareState({
      main: { ...bareState().main, ifShift: 900, pbtInner: 128, pbtOuter: 128 },
      fieldStatus: {
        ...bareState().fieldStatus, 'main.ifShift': fresh,
        'main.pbtInner': fresh, 'main.pbtOuter': fresh,
      },
    }), pbtOnlyCaps);
    const expected = deriveIfShift(pbtRawToHz(128), pbtRawToHz(128));
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'known', value: expected });
    expect(view.filterPassband!.ifShift.reading).not.toEqual({ status: 'known', value: 900 });
  });

  it('with neither if_shift nor pbt, ifShift is structurally absent', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband!.ifShift.availability.structural).toBe(false);
    expect(view.filterPassband!.ifShift.reading).toEqual({ status: 'unknown' });
  });
});

/**
 * `ifShiftControlStructural` (MOR-1494 review round) — a SEPARATE,
 * presentation-only flag `FilterSurface.svelte` uses to decide whether to
 * show the IF-shift ROW, deliberately independent of `ifShift.availability.
 * structural` above (which stays `hasIfShiftCap || hasPbtCap`, unchanged,
 * because `scope-adapter.ts`'s passband-center overlay still needs the
 * derived reading for a PBT-only radio). `ifShiftControlStructural` answers
 * the narrower question: does the radio have a REAL `if_shift` command.
 */
describe('ifShiftControlStructural — the presentation-only IF-shift control gate (MOR-1494)', () => {
  it('IC-7300-shaped (pbt, no if_shift): false, even though the derived fact stays structural', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 60 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': fresh },
    }), caps({ capabilities: ['pbt'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(false);
    // The trap: a naive fix that reused `ifShiftStructural` for this flag
    // too would silently break `scope-adapter.ts`'s derived reading for
    // exactly this radio shape. It must stay untouched.
    expect(view.filterPassband!.ifShift.availability.structural).toBe(true);
    expect(view.filterPassband!.ifShift.reading.status).toBe('known');
  });

  it('FTX-1-shaped (if_shift, no pbt): true', () => {
    const view = model(bareState({
      main: { ...bareState().main, ifShift: 300 },
      fieldStatus: { ...bareState().fieldStatus, 'main.ifShift': fresh },
    }), caps({ capabilities: ['if_shift'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(true);
  });

  it('neither if_shift nor pbt: false', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(false);
  });

  it('both if_shift and pbt (hypothetical radio): true — a real if_shift command always wins the presentation gate', () => {
    const view = model(bareState(), caps({ capabilities: ['if_shift', 'pbt'] }));
    expect(view.filterPassband!.ifShiftControlStructural).toBe(true);
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
  const pbtCaps = caps({ capabilities: ['pbt'] });

  it('pbtInner observed, pbtOuter UNOBSERVED — ifShift must NOT derive from a fabricated pbtOuter default', () => {
    const view = model(bareState({
      main: { ...bareState().main, pbtInner: 200, pbtOuter: 128 },
      fieldStatus: { ...bareState().fieldStatus, 'main.pbtInner': fresh, 'main.pbtOuter': stale },
    }), pbtCaps);
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: pbtRawToHz(200) });
    expect(view.filterPassband!.pbtOuter.reading).toEqual({ status: 'unknown' });
    expect(view.filterPassband!.ifShift).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
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
    expect(view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: pbtRawToHz(200) });
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
    }), caps({ filters: ['FIL1'] }));
    expect(view.filterPassband!.filterShape.reading).toEqual({ status: 'unknown' });
  });
});
