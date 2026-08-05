/**
 * MOR-1262 decomposition slice 4A (MOR-1280) — `modeFilter` fact-group
 * adapter derivation.
 *
 * Companion to `radio-view-model-adapter.test.ts` (MOR-1065), which this
 * file does NOT modify. That file's `TOPOLOGY_CAPS` fixtures all declare
 * `modes: [], filters: []` and its "carries only contract data" test hard-
 * codes the exact key list a view model may have — under a naive "emit
 * whenever a receiver exists" gate (mode/filter are REQUIRED fields on
 * `ReceiverStatePublic`, so they are never actually absent), `modeFilter`
 * would appear on every model that file builds. `deriveModeFilter`'s
 * evidence gate requires a non-empty capability-declared choice set instead
 * — this file separately pins that a radio with real modes/filters DOES get
 * the group, and that the empty-choice-set baseline still gets none.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities, FilterModeConfig } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { resolveFilterModeConfig } from '$lib/runtime/props/panel-props';

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

describe('modeFilter evidence gate (MOR-1280, N3)', () => {
  it('emits no modeFilter when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  // The exact scenario `radio-view-model-adapter.test.ts`'s fixtures hit:
  // a receiver always carries `mode`/`filter` (required fields), no choice
  // sets declared. Regression pin — this must stay group-absent.
  it('emits no modeFilter for empty modes AND empty filters, even with a fully-populated receiver (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.modeFilter).toBeUndefined();
    expect(Object.keys(view)).not.toContain('modeFilter');
  });

  it('emits modeFilter once modes alone are declared', () => {
    const view = model(bareState(), caps({ modes: ['USB', 'LSB'] }));
    expect(view.modeFilter).toBeDefined();
  });

  it('emits modeFilter once filters alone are declared', () => {
    const view = model(bareState(), caps({ filters: ['FIL1', 'FIL2'] }));
    expect(view.modeFilter).toBeDefined();
  });

  it('never emits modeFilter with no capabilities object at all', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });
});

describe('modeFilter per-field derivation (MOR-1280)', () => {
  const fullCaps = caps({ modes: ['USB', 'LSB', 'CW'], filters: ['FIL1', 'FIL2', 'FIL3'] });

  it('reports the capability-declared choice sets verbatim', () => {
    const view = model(bareState(), fullCaps);
    expect(view.modeFilter!.modeChoices).toEqual(['USB', 'LSB', 'CW']);
    expect(view.modeFilter!.filterChoices).toEqual(['FIL1', 'FIL2', 'FIL3']);
  });

  it('reports known current mode/filter/width readings for the active (MAIN) receiver', () => {
    const view = model(bareState({
      main: { freqHz: 14195000, mode: 'USB', filter: 2, dataMode: 0, filterWidth: 2400, att: 0, preamp: 0, nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0 },
      fieldStatus: { ...bareState().fieldStatus, 'main.filterWidth': fresh },
    }), fullCaps);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'known', value: 'USB' });
    expect(view.modeFilter!.currentFilter.reading).toEqual({ status: 'known', value: 2 });
    expect(view.modeFilter!.filterWidth.reading).toEqual({ status: 'known', value: 2400 });
  });

  it('follows the SUB receiver once it is the active one', () => {
    const view = model(bareState({
      active: 'SUB',
      fieldStatus: { ...bareState().fieldStatus, 'sub.mode': fresh, 'sub.filter': fresh },
    }), fullCaps);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'known', value: 'LSB' });
    expect(view.modeFilter!.currentFilter.reading).toEqual({ status: 'known', value: 2 });
  });

  it('marks currentMode structurally absent when no modes are declared, even with filters present', () => {
    const view = model(bareState(), caps({ filters: ['FIL1'] }));
    expect(view.modeFilter!.currentMode.availability.structural).toBe(false);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'unknown' });
    expect(view.modeFilter!.currentFilter.availability.structural).toBe(true);
  });

  it('marks currentFilter/filterWidth structurally absent when no filters are declared, even with modes present', () => {
    const view = model(bareState(), caps({ modes: ['USB'] }));
    expect(view.modeFilter!.currentFilter.availability.structural).toBe(false);
    expect(view.modeFilter!.filterWidth.availability.structural).toBe(false);
    expect(view.modeFilter!.currentMode.availability.structural).toBe(true);
  });

  it('degrades a stale mode field to unknown while keeping structural availability true', () => {
    const view = model(bareState({
      fieldStatus: { ...bareState().fieldStatus, 'main.mode': stale },
    }), fullCaps);
    expect(view.modeFilter!.currentMode).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('resolves filterWidthMin/Max from the mode-keyed filterConfig table when one matches', () => {
    const filterConfig: Record<string, FilterModeConfig> = {
      USB: { defaults: [2400], fixed: false, minHz: 50, maxHz: 3600 },
    };
    const view = model(
      bareState({ main: { ...bareState().main, mode: 'USB' } }),
      caps({ ...fullCaps, filterConfig, filterWidthMin: 10, filterWidthMax: 9999 }),
    );
    expect(view.modeFilter!.filterWidthMin.reading).toEqual({ status: 'known', value: 50 });
    expect(view.modeFilter!.filterWidthMax.reading).toEqual({ status: 'known', value: 3600 });
  });

  it('falls back to the capability-level filterWidthMin/Max when no per-mode config matches', () => {
    const view = model(
      bareState({ main: { ...bareState().main, mode: 'FM' } }),
      caps({ ...fullCaps, modes: ['FM'], filterWidthMin: 10, filterWidthMax: 9999 }),
    );
    expect(view.modeFilter!.filterWidthMin.reading).toEqual({ status: 'known', value: 10 });
    expect(view.modeFilter!.filterWidthMax.reading).toEqual({ status: 'known', value: 9999 });
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than throwing or coercing', () => {
    const view = model(bareState({
      main: { ...bareState().main, filter: 'wide' as unknown as number },
    }), fullCaps);
    expect(view.modeFilter!.currentFilter.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the modeFilter group (round-trip proof)', () => {
    const view = model(bareState(), fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * SAFETY CONSTRAINT (verify round 1, F1). The doc comment on `deriveModeFilter`
 * claims `filterWidthMin`/`filterWidthMax` read the shipped
 * `resolveFilterModeConfig`'s own fallback chain rather than re-deriving it —
 * same doctrine as `rxAudio.modInputReadiness` reading `deriveTxCapabilities`
 * verbatim (`rx-audio.test.ts`'s readiness matrix) and `meters.rfState` reading
 * the real reducer (`meters.test.ts`'s 9-combo pin). Before this block, no test
 * actually drove a DATA-mode receiver or the CW-R/RTTY-R/SSB fallback branches
 * — two independent regressions (dropping `dataMode` from the resolver call;
 * forking a hand-rolled `caps.filterConfig[mode]` lookup that skips the whole
 * fallback chain) passed the entire suite untouched. This matrix drives the
 * REAL `resolveFilterModeConfig` across every branch of that chain and asserts
 * the adapter's bounds equal its own `minHz ?? table[0] ?? caps.filterWidthMin`
 * / `maxHz ?? table[last] ?? caps.filterWidthMax` — drift in either direction
 * (dropped argument, forked lookup) reddens this file.
 */
describe('filterWidthMin/Max parity with the real resolveFilterModeConfig (MOR-1280, F1)', () => {
  const filterConfig: Record<string, FilterModeConfig> = {
    'USB-D': { defaults: [1500], fixed: false, minHz: 200, maxHz: 2800 },
    SSB: { defaults: [2400], fixed: false, minHz: 50, maxHz: 3000 },
    CW: { defaults: [500], fixed: false, minHz: 50, maxHz: 1200 },
    RTTY: { defaults: [300], fixed: false, minHz: 50, maxHz: 900 },
  };
  const parityCaps = caps({
    modes: ['USB', 'LSB', 'CW', 'CW-R', 'RTTY', 'RTTY-R', 'FM'],
    filters: ['FIL1'],
    filterConfig,
    filterWidthMin: 10,
    filterWidthMax: 9999,
  });

  // Each row exercises a distinct branch of `resolveFilterModeConfig`'s
  // candidate chain — direct hit, `-D` data-mode suffix, the USB/LSB→SSB
  // alias (voice AND data), the CW-R→CW and RTTY-R→RTTY voice fallbacks, and
  // an unmapped mode that falls all the way through to the capability-level
  // bounds. `dataMode` is the exact argument mutation D1 dropped.
  const MATRIX: ReadonlyArray<{ name: string; mode: string; dataMode: number }> = [
    { name: 'USB, voice (dataMode 0) — falls through to the SSB alias', mode: 'USB', dataMode: 0 },
    { name: 'USB, data (dataMode 1) — direct USB-D hit, distinct from the voice table', mode: 'USB', dataMode: 1 },
    { name: 'LSB, voice — falls through to the SSB alias', mode: 'LSB', dataMode: 0 },
    { name: 'LSB, data — SSB-D absent, falls through to SSB (not USB-D)', mode: 'LSB', dataMode: 1 },
    { name: 'CW — direct hit', mode: 'CW', dataMode: 0 },
    { name: 'CW-R — falls back to the CW table', mode: 'CW-R', dataMode: 0 },
    { name: 'RTTY — direct hit', mode: 'RTTY', dataMode: 0 },
    { name: 'RTTY-R — falls back to the RTTY table', mode: 'RTTY-R', dataMode: 0 },
    { name: 'FM — unmapped, falls through to the capability-level bounds', mode: 'FM', dataMode: 0 },
  ];

  it.each(MATRIX)('$name', ({ mode, dataMode }) => {
    // The independent, real-resolver-derived expectation — NOT a copy of the
    // adapter's own formula's inputs, the adapter's OUTPUT is compared
    // against what the shipped resolver itself says for this mode/dataMode.
    const expected = resolveFilterModeConfig(parityCaps, mode, dataMode);
    const expectedMin = expected?.minHz ?? expected?.table?.[0] ?? parityCaps.filterWidthMin;
    const expectedMax = expected?.maxHz
      ?? (expected?.table?.length ? expected.table[expected.table.length - 1] : undefined)
      ?? parityCaps.filterWidthMax;

    const view = model(bareState({ main: { ...bareState().main, mode, dataMode } }), parityCaps);

    expect(view.modeFilter!.filterWidthMin.reading).toEqual({ status: 'known', value: expectedMin });
    expect(view.modeFilter!.filterWidthMax.reading).toEqual({ status: 'known', value: expectedMax });
  });

  it('the matrix actually exercises a direct hit, a -D hit, an alias fallback and a full fall-through (no silent coverage gap)', () => {
    const resolved = MATRIX.map(({ mode, dataMode }) => resolveFilterModeConfig(parityCaps, mode, dataMode));
    expect(resolved.some((c) => c === filterConfig.CW)).toBe(true); // direct hit
    expect(resolved.some((c) => c === filterConfig['USB-D'])).toBe(true); // -D hit
    expect(resolved.some((c) => c === filterConfig.SSB)).toBe(true); // alias fallback
    expect(resolved.some((c) => c === null)).toBe(true); // full fall-through (FM)
  });
});

/**
 * SAFETY CONSTRAINT (verify round 1, F2). `filterWidthMin`/`filterWidthMax`
 * are VALUES OF (caps, mode) — never of `filterWidth` — so their operational
 * gate must be `modeObserved`, not `widthObserved`. Before the fix these two
 * probes were the demonstrated fabrication/false-negative pair.
 */
describe('filterWidthMin/Max observation gate (MOR-1280, F2)', () => {
  // filterWidthMin/Max set at the capability level (not just via a per-mode
  // filterConfig) so "the bounds ARE derivable" is unambiguous in the second
  // probe below — the whole point is that a mode-independent fallback is
  // still knowable the instant the mode is observed, with no filterConfig at all.
  const fullCaps = caps({
    modes: ['USB', 'LSB', 'CW'], filters: ['FIL1', 'FIL2', 'FIL3'],
    filterWidthMin: 50, filterWidthMax: 9999,
  });

  it('mode UNOBSERVED, filterWidth observed — bounds must NOT fabricate the mode-derived table', () => {
    const view = model(bareState({
      fieldStatus: {
        ...bareState().fieldStatus, 'main.mode': stale, 'main.filterWidth': fresh,
      },
    }), fullCaps);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'unknown' });
    // The bounds derive from the SAME unobserved mode as currentMode — they
    // must degrade with it, not publish a confidently-wrong table.
    expect(view.modeFilter!.filterWidthMin).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    expect(view.modeFilter!.filterWidthMax).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('mode OBSERVED, filterWidth UNOBSERVED — bounds ARE derivable and must read known', () => {
    const view = model(bareState({
      main: { ...bareState().main, mode: 'USB', filterWidth: undefined },
      fieldStatus: { ...bareState().fieldStatus, 'main.mode': fresh },
    }), fullCaps);
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'known', value: 'USB' });
    // filterWidth itself may legitimately still be unknown — it is gated on
    // its OWN observation, unaffected by this fix.
    expect(view.modeFilter!.filterWidthMin.reading.status).toBe('known');
    expect(view.modeFilter!.filterWidthMax.reading.status).toBe('known');
  });
});
