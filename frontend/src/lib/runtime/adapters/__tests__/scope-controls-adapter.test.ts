/**
 * MOR-1262 decomposition slice 11A (MOR-1298), extended by slice 11A′
 * (MOR-1299) — `scopeControls` fact-group adapter derivation.
 *
 * Companion to `cw-keyer-adapter.test.ts`/`scan-adapter.test.ts`, which this
 * file does NOT modify. `scopeControls` is a SEPARATE optional group — see
 * `radio-view-model.ts`'s `ScopeControlsViewModel` doc comment.
 *
 * PARITY — this group has no extractable pure `toXProps` function to call
 * (the real derivation is inline in `SpectrumToolbar.svelte`'s `$derived`
 * blocks), so the pins below call the SAME `isFieldAvailable` predicate the
 * toolbar calls for its own `scopeModeAvailable`/`scopeEdgeAvailable`/
 * `scopeSpanAvailable`/`scopeSpeedAvailable`/`scopeHoldAvailable`/
 * `scopeRefAvailable`/`scopeDualAvailable`/`scopeReceiverAvailable`
 * booleans, rather than reimplementing it. The toolbar's
 * `scopeControls?.span ?? 3` (etc.) fallbacks are pinned as fabrication this
 * contract deliberately diverges from — see the honesty-gate describe
 * block.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { isFieldAvailable } from '$lib/state/field-status';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

/** Scope-bearing, single-RX by default (mirrors IC-7300/IC-9700: `scope`
 *  without `dual_rx`). Pass `['scope', 'dual_rx']` for the IC-7610 case. */
function scopeCaps(tags: readonly string[] = ['scope']): Capabilities {
  return caps({ capabilities: [...tags] });
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    fieldStatus: { active: fresh, split: fresh, dualWatch: fresh },
    ...overrides,
  } as ServerState;
}

function model(state: ServerState | null, capabilities: Capabilities | null): RadioViewModel {
  const view = toRadioViewModel(state, capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('scopeControls evidence gate (MOR-1298, N3)', () => {
  it('emits no scopeControls when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no scopeControls for a radio without the scope capability (regression pin)', () => {
    const view = model(bareState(), caps({ capabilities: ['audio', 'tx'] }));
    expect(view.scopeControls).toBeUndefined();
    expect(Object.keys(view)).not.toContain('scopeControls');
  });

  it('emits scopeControls once the scope capability alone is declared', () => {
    const view = model(bareState(), scopeCaps());
    expect(view.scopeControls).toBeDefined();
    expect(Object.keys(view.scopeControls!).sort()).toEqual(
      ['dual', 'edge', 'hold', 'mode', 'receiver', 'refDb', 'span', 'speed'],
    );
  });
});

describe('scopeControls per-field structural gates (MOR-1298/MOR-1299, X6200 lesson)', () => {
  it('mode/edge/span/speed/hold/refDb stay structurally present on a scope-only, single-RX radio (IC-7300/IC-9700 shape)', () => {
    const view = model(bareState(), scopeCaps(['scope']));
    expect(view.scopeControls!.mode.availability.structural).toBe(true);
    expect(view.scopeControls!.edge.availability.structural).toBe(true);
    expect(view.scopeControls!.span.availability.structural).toBe(true);
    expect(view.scopeControls!.speed.availability.structural).toBe(true);
    expect(view.scopeControls!.hold.availability.structural).toBe(true);
    expect(view.scopeControls!.refDb.availability.structural).toBe(true);
  });

  it('dual/receiver are structurally absent without dual_rx — no radio-specific table, just the cap tag', () => {
    const view = model(bareState(), scopeCaps(['scope']));
    expect(view.scopeControls!.dual.availability.structural).toBe(false);
    expect(view.scopeControls!.receiver.availability.structural).toBe(false);
  });

  it('dual/receiver become structurally present once dual_rx is declared alongside scope (IC-7610 shape)', () => {
    const view = model(bareState(), scopeCaps(['scope', 'dual_rx']));
    expect(view.scopeControls!.dual.availability.structural).toBe(true);
    expect(view.scopeControls!.receiver.availability.structural).toBe(true);
  });

  it('no scopeControls-derived structural availability at all when the scope capability itself is missing', () => {
    const view = model(bareState(), caps({ capabilities: ['audio', 'tx', 'dual_rx'] }));
    expect(view.scopeControls).toBeUndefined();
  });
});

describe('scopeControls per-field derivation (MOR-1298/MOR-1299)', () => {
  const fullCaps = scopeCaps(['scope', 'dual_rx']);

  it('reports known readings for observed, fresh fields — parity with isFieldAvailable, the same predicate the real toolbar uses', () => {
    const state = bareState({
      scopeControls: {
        receiver: 1, dual: true, mode: 1, span: 5, edge: 2, hold: true, refDb: -5, speed: 2,
        duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: {
        ...bareState().fieldStatus,
        'scopeControls.mode': fresh, 'scopeControls.edge': fresh,
        'scopeControls.span': fresh, 'scopeControls.speed': fresh,
        'scopeControls.hold': fresh, 'scopeControls.refDb': fresh,
        'scopeControls.dual': fresh, 'scopeControls.receiver': fresh,
      },
    } as Partial<ServerState>);
    const sc = model(state, fullCaps).scopeControls!;
    expect(sc.mode.reading).toEqual({ status: 'known', value: 1 });
    expect(sc.edge.reading).toEqual({ status: 'known', value: 2 });
    expect(sc.span.reading).toEqual({ status: 'known', value: 5 });
    expect(sc.speed.reading).toEqual({ status: 'known', value: 2 });
    expect(sc.hold.reading).toEqual({ status: 'known', value: true });
    expect(sc.refDb.reading).toEqual({ status: 'known', value: -5 });
    expect(sc.dual.reading).toEqual({ status: 'known', value: true });
    expect(sc.receiver.reading).toEqual({ status: 'known', value: 1 });
    for (const leaf of ['mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver'] as const) {
      expect(sc[leaf].availability.operational).toBe(isFieldAvailable(state, `scopeControls.${leaf}`));
    }
  });

  const STALE_FIELDS = ['mode', 'edge', 'span', 'speed', 'hold', 'refDb', 'dual', 'receiver'] as const;

  it.each(STALE_FIELDS)(
    'degrades a stale scopeControls.%s to unknown while keeping structural availability true',
    (leaf) => {
      const state = bareState({
        scopeControls: {
          receiver: 1, dual: true, mode: 0, span: 4, edge: 0, hold: false, refDb: 0, speed: 1,
          duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
          fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
        },
        fieldStatus: { ...bareState().fieldStatus, [`scopeControls.${leaf}`]: stale },
      } as Partial<ServerState>);
      const field = model(state, fullCaps).scopeControls![leaf];
      expect(field.reading).toEqual({ status: 'unknown' });
      expect(field.availability).toEqual({ structural: true, operational: false });
    },
  );

  it('marks dual/receiver structurally absent (never known) when dual_rx is missing, even with fresh raw data', () => {
    const state = bareState({
      scopeControls: {
        receiver: 1, dual: true, mode: 0, span: 3, edge: 0, hold: false, refDb: 0, speed: 1,
        duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: {
        ...bareState().fieldStatus, 'scopeControls.dual': fresh, 'scopeControls.receiver': fresh,
      },
    } as Partial<ServerState>);
    const sc = model(state, scopeCaps(['scope'])).scopeControls!;
    expect(sc.dual).toEqual({ reading: { status: 'unknown' }, availability: { structural: false, operational: false } });
    expect(sc.receiver).toEqual({ reading: { status: 'unknown' }, availability: { structural: false, operational: false } });
  });

  it('degrades a malformed raw span (wrong JS type) to unknown rather than coercing', () => {
    const state = bareState({
      scopeControls: {
        receiver: 0, dual: false, mode: 0, span: '5' as unknown as number, edge: 0, hold: false,
        refDb: 0, speed: 1, duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: { ...bareState().fieldStatus, 'scopeControls.span': fresh },
    } as Partial<ServerState>);
    expect(model(state, fullCaps).scopeControls!.span.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the scopeControls group (round-trip proof)', () => {
    const state = bareState({
      scopeControls: {
        receiver: 0, dual: false, mode: 0, span: 3, edge: 0, hold: false, refDb: 0, speed: 1,
        duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: { ...bareState().fieldStatus, 'scopeControls.span': fresh, 'scopeControls.speed': fresh },
    } as Partial<ServerState>);
    const view = model(state, fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * HONESTY / FAIL-CLOSED GATE — absent raw values never fabricate the
 * SpectrumToolbar's own `?? 3` / `?? 1` / `?? false` / `?? 0` defaults
 * (`SpectrumToolbar.svelte` lines around its `SPAN_LABELS[scopeControls?.
 * span ?? 3]`-style reads).
 */
describe('scopeControls honesty gate — absent raw values never fabricate (MOR-1298/MOR-1299)', () => {
  const fullCaps = scopeCaps(['scope', 'dual_rx']);

  const NO_TOOLBAR_DEFAULT_CASES = [
    ['mode', 0],
    ['edge', 1],
  ] as const;

  const TOOLBAR_DEFAULT_CASES = [
    ['span', 3],
    ['speed', 1],
    ['hold', false],
    ['refDb', 0],
    ['dual', false],
    ['receiver', 0],
  ] as const;

  it.each(NO_TOOLBAR_DEFAULT_CASES)(
    '%s with nothing reported at all reads unknown, no toolbar default at all — the row is hidden',
    (viewField) => {
      expect(model(bareState(), fullCaps).scopeControls![viewField].reading).toEqual({ status: 'unknown' });
    },
  );

  it.each(TOOLBAR_DEFAULT_CASES)(
    '%s with nothing reported at all reads unknown, not the toolbar\'s fabricated %s default',
    (viewField) => {
      expect(model(bareState(), fullCaps).scopeControls![viewField].reading).toEqual({ status: 'unknown' });
    },
  );

  it('emits no false "available" reading before scope is ever enabled (no scopeControls block observed at all)', () => {
    const state = bareState();
    expect(state.scopeControls).toBeUndefined();
    const sc = model(state, fullCaps).scopeControls!;
    expect(sc.span.reading.status).toBe('unknown');
    expect(sc.dual.reading.status).toBe('unknown');
  });
});

/**
 * DETERMINISM — a fact-layer value is a pure function of `(state, caps)`.
 * Both directions: same inputs ⇒ same output, and a changed input ⇒ changed
 * output (so the first half cannot pass by returning a constant).
 */
describe('scopeControls determinism in (state, caps) (MOR-1298)', () => {
  const fullCaps = scopeCaps(['scope', 'dual_rx']);

  it('is stable across repeated derivations of the same inputs', () => {
    const state = bareState({
      scopeControls: {
        receiver: 1, dual: true, mode: 0, span: 6, edge: 0, hold: false, refDb: 0, speed: 0,
        duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: { ...bareState().fieldStatus, 'scopeControls.span': fresh },
    } as Partial<ServerState>);
    expect(model(state, fullCaps).scopeControls).toEqual(model(state, fullCaps).scopeControls);
  });

  it('changes with state, and with caps, independently', () => {
    const withSpan = (span: number) => bareState({
      scopeControls: {
        receiver: 0, dual: false, mode: 0, span, edge: 0, hold: false, refDb: 0, speed: 1,
        duringTx: false, centerType: 0, vbwNarrow: false, rbw: 0,
        fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
      },
      fieldStatus: { ...bareState().fieldStatus, 'scopeControls.span': fresh },
    } as Partial<ServerState>);
    expect(model(withSpan(2), fullCaps).scopeControls!.span.reading)
      .not.toEqual(model(withSpan(6), fullCaps).scopeControls!.span.reading);
    expect(model(withSpan(2), fullCaps).scopeControls!.dual.availability)
      .not.toEqual(model(withSpan(2), scopeCaps(['scope'])).scopeControls!.dual.availability);
  });
});
