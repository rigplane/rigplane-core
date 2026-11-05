/**
 * MOR-4213 — the radio view model is derived once per input snapshot.
 *
 * On every state delivery about seventeen call sites (the canonical surface
 * view, control-feedback projections, instrument hosts, the status bar's
 * active frequency, the media session) asked `toRadioViewModel` for the same
 * `(state, caps)` pair and each re-derived the whole model. These tests pin
 * the contract that replaces it: one derivation per distinct input set, and
 * a new derivation hands back the previous objects for every part whose
 * facts did not change.
 *
 * Derivations are counted through `deriveTxCapabilities`, which the adapter
 * calls exactly once per derivation. Isolated file: it wraps that module.
 * The memo is a production-build property (DEV re-derives so fixtures that
 * edit a snapshot in place keep working), so these tests run with DEV off.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { fixtureById } from '../../../../../fixtures/catalog';
import { IC7300_CAPABILITIES, IC7300_STATE } from './fixtures/ic7300-profile';

const derivations = vi.hoisted(() => ({ count: 0 }));
vi.mock('../tx-capabilities', async (importOriginal) => {
  const real = await importOriginal<typeof import('../tx-capabilities')>();
  return {
    ...real,
    deriveTxCapabilities: (...args: Parameters<typeof real.deriveTxCapabilities>) => {
      derivations.count += 1;
      return real.deriveTxCapabilities(...args);
    },
  };
});

const { toRadioViewModel } = await import('../radio-view-model-adapter');

type Radio = { name: string; state: () => ServerState; caps: () => Capabilities };
const RADIOS: Radio[] = [
  {
    name: 'IC-7300 (1/ab, live capture)',
    state: () => structuredClone(IC7300_STATE),
    caps: () => structuredClone(IC7300_CAPABILITIES),
  },
  {
    name: 'dual receiver (2/main_sub)',
    state: () => structuredClone(fixtureById('topology-2-main-sub')!.state()!),
    caps: () => structuredClone(fixtureById('topology-2-main-sub')!.caps()!),
  },
];

/** A delivery that only advances transport metadata, as the leaf-delta applier builds it. */
function metadataOnlyDelivery(state: ServerState): ServerState {
  return {
    ...state,
    revision: (state.revision ?? 0) + 1,
    stateRevision: (state.stateRevision ?? 0) + 1,
    observationSeq: (state.observationSeq ?? 0) + 1,
    updatedAt: '2026-11-05T12:00:01Z',
  };
}

beforeEach(() => {
  vi.stubEnv('DEV', false);
  derivations.count = 0;
});
afterEach(() => { vi.unstubAllEnvs(); });

describe.each(RADIOS)('toRadioViewModel memo — $name', ({ state: makeState, caps: makeCaps }) => {
  it('serves every caller of one (state, caps) pair from a single derivation', () => {
    const state = makeState();
    const caps = makeCaps();
    const first = toRadioViewModel(state, caps);
    expect(first).not.toBeNull();
    for (let i = 0; i < 16; i += 1) expect(toRadioViewModel(state, caps)).toBe(first);
    expect(derivations.count).toBe(1);
  });

  it('keeps the canonical (snapshot) lane and the bare lane apart', () => {
    const state = makeState();
    const caps = makeCaps();
    const tx = { radioTx: 'receiving', txRisk: 'none' } as never;
    const bare = toRadioViewModel(state, caps);
    const full = toRadioViewModel(state, caps, tx);
    expect(toRadioViewModel(state, caps)).toBe(bare);
    expect(toRadioViewModel(state, caps, tx)).toBe(full);
    expect(derivations.count).toBe(2);
  });

  it('re-derives for a new state snapshot and returns the previous view when no fact changed', () => {
    const state = makeState();
    const caps = makeCaps();
    const first = toRadioViewModel(state, caps);
    const next = toRadioViewModel(metadataOnlyDelivery(state), caps);
    expect(derivations.count).toBe(2);
    expect(next).toBe(first);
  });

  it('a changed receiver re-derives; every group that did not change keeps its identity', () => {
    const state = makeState();
    const caps = makeCaps();
    const first = toRadioViewModel(state, caps)!;
    const tuned = metadataOnlyDelivery(state);
    tuned.main = { ...state.main, freqHz: (state.main.freqHz ?? 14_000_000) + 1_000 };
    const next = toRadioViewModel(tuned, caps)!;
    expect(next).not.toBe(first);
    expect(next.vfos).not.toBe(first.vfos);
    // Parts that do not read the main receiver frequency stay the same objects.
    expect(next.split).toBe(first.split);
    expect(next.dualWatch).toBe(first.dualWatch);
    expect(next.scope).toBe(first.scope);
    expect(next.receiverIndicators).toBe(first.receiverIndicators);
    if (first.dsp !== undefined) expect(next.dsp).toBe(first.dsp);
    if (first.rfFrontEnd !== undefined) expect(next.rfFrontEnd).toBe(first.rfFrontEnd);
    // Sharing keeps the model's own alias: the current band IS a receiver entry.
    if (next.band !== undefined) {
      expect(Object.values(next.band.receiverBands)).toContain(next.band.currentBand);
    }
  });

  it('new capabilities re-derive', () => {
    const state = makeState();
    toRadioViewModel(state, makeCaps());
    toRadioViewModel(state, makeCaps());
    expect(derivations.count).toBe(2);
  });

  it('a shared result equals an unmemoized derivation of the same input', () => {
    const state = makeState();
    const caps = makeCaps();
    const tuned = metadataOnlyDelivery(state);
    tuned.main = { ...state.main, freqHz: (state.main.freqHz ?? 14_000_000) + 2_500 };
    toRadioViewModel(state, caps);
    const shared = toRadioViewModel(tuned, caps);
    // Cloned inputs miss the memo, so this is a full derivation of the same facts.
    const fresh = toRadioViewModel(structuredClone(tuned), structuredClone(caps));
    expect(shared).toEqual(fresh);
  });
});

describe('toRadioViewModel in DEV', () => {
  it('re-derives on every call so an in-place fixture edit is seen', () => {
    vi.stubEnv('DEV', true);
    const state = structuredClone(IC7300_STATE);
    const caps = structuredClone(IC7300_CAPABILITIES);
    const first = toRadioViewModel(state, caps)!;
    state.main.freqHz = (state.main.freqHz ?? 14_000_000) + 5_000;
    const next = toRadioViewModel(state, caps)!;
    expect(derivations.count).toBe(2);
    expect(next.vfos).not.toEqual(first.vfos);
  });
});
