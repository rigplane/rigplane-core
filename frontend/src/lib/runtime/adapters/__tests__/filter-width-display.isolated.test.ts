/**
 * MOR-3677 — the `FilterWidthDisplay` truth table (design §1), derived on
 * the REAL FTX-1 bench capture fixtures (`fixtures/ftx1-profile.ts`):
 * `FTX1_CAPABILITIES` + `FTX1_STATE` with `fieldStatus` overrides.
 *
 * The rows pin the operator-facing contract the width row and the spectrum
 * chip render from:
 *  - no `filter_width` capability → `unsupported`;
 *  - NARROW confirmed on while the width leaf is withheld (the declared-
 *    absent discard of MOR-2803) → `narrow`, never a Hz value;
 *  - the `filterWidthRadioDefault` flag confirmed true → `radio-default`
 *    (the radio's own mode default, code 000 — no Hz exists);
 *  - a positive-integer width reading → `known {hz, stale}`;
 *  - an observed width leaf with no Hz (C4FM, out-of-table code, or the
 *    flag leaf still unpublished by a pre-PR1 backend) → `not-reported`;
 *  - anything else → `not-observed`.
 *
 * Row 2's `availability !== 'available'` conjunct is the NARROW-over-SH
 * boundary itself: bench 09:17:11 read `NA01` while `SH0` still carried
 * 3000 Hz, and SH does not follow NARROW — so Hz may only be shown again
 * once the freshness tick withholds the width leaf. The one-tick window
 * where the stale 3000 Hz is still `known` is the owner-accepted default
 * (design Q8) and is pinned as such below.
 *
 * PR2 must merge alone: with the flag leaf absent (PR1 not merged), the
 * flag observations resolve not-observed and the derivation degrades to
 * `not-reported`/`not-observed` — never a fabricated Hz.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ReceiverState, ServerState } from '$lib/types/state';
import { FTX1_CAPABILITIES, FTX1_STATE } from './fixtures/ftx1-profile';
import { deriveFilterWidthDisplay } from '../filter-width-display';

/** Aligned on both sides so `validIdentity` holds (the capture carries 1/0). */
const GENERATION = 31;

const FRESH = (path: string): FieldStatus => ({
  storePath: path, observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 670_561.5,
});
const STALE = (path: string): FieldStatus => ({
  storePath: path, observed: true, freshness: 'stale', availability: 'stale',
  lastObservedMonotonic: 670_561.5,
});

function caps(extra: { capabilities?: string[]; providerGeneration?: number } = {}): Capabilities {
  return {
    ...FTX1_CAPABILITIES,
    providerGeneration: extra.providerGeneration ?? GENERATION,
    capabilities: extra.capabilities ?? FTX1_CAPABILITIES.capabilities,
  };
}

interface MainOverrides {
  filterWidth?: number | null;
  narrow?: boolean | null;
  /** PR1's leaf — absent from the generated type until PR1 regenerates it,
   *  so it rides in through the same defensive cast the derivation uses. */
  filterWidthRadioDefault?: boolean | null;
}

function state(overrides: {
  main?: MainOverrides;
  fieldStatus?: Record<string, FieldStatus>;
  providerGeneration?: number;
} = {}): ServerState {
  const base = structuredClone(FTX1_STATE);
  return {
    ...base,
    providerGeneration: overrides.providerGeneration ?? GENERATION,
    main: { ...base.main!, ...overrides.main } as ReceiverState,
    fieldStatus: {
      ...base.fieldStatus,
      ...(overrides.main?.filterWidth !== undefined
        ? { 'main.filterWidth': FRESH('main.filterWidth') } : {}),
      ...(overrides.main?.narrow !== undefined
        ? { 'main.narrow': FRESH('main.narrow') } : {}),
      ...(overrides.main?.filterWidthRadioDefault !== undefined
        ? { 'main.filterWidthRadioDefault': FRESH('main.filterWidthRadioDefault') } : {}),
      ...overrides.fieldStatus,
    },
  } as ServerState;
}

describe('deriveFilterWidthDisplay truth table (MOR-3677)', () => {
  it('a radio without the filter_width capability is unsupported', () => {
    const withoutWidth = FTX1_CAPABILITIES.capabilities.filter((name) => name !== 'filter_width');
    expect(deriveFilterWidthDisplay(state(), caps({ capabilities: withoutWidth }), 'MAIN'))
      .toEqual({ state: 'unsupported' });
  });

  it('a fresh positive width reading is known and not stale', () => {
    expect(deriveFilterWidthDisplay(state(), caps(), 'MAIN'))
      .toEqual({ state: 'known', hz: 3000, stale: false });
  });

  it('a held (stale) width reading stays known with the stale marker (R40)', () => {
    expect(deriveFilterWidthDisplay(
      state({ fieldStatus: { 'main.filterWidth': STALE('main.filterWidth') } }),
      caps(),
      'MAIN',
    )).toEqual({ state: 'known', hz: 3000, stale: true });
  });

  it('the radio-default flag true (code 000, no Hz) wins over a null width value', () => {
    expect(deriveFilterWidthDisplay(
      state({ main: { filterWidth: null, filterWidthRadioDefault: true } }),
      caps(),
      'MAIN',
    )).toEqual({ state: 'radio-default' });
  });

  it('a held (stale) radio-default flag still wins — a stale true cannot be outlived', () => {
    expect(deriveFilterWidthDisplay(
      state({
        main: { filterWidth: null, filterWidthRadioDefault: true },
        fieldStatus: { 'main.filterWidthRadioDefault': STALE('main.filterWidthRadioDefault') },
      }),
      caps(),
      'MAIN',
    )).toEqual({ state: 'radio-default' });
  });

  it('the flag false with a null width degrades to not-reported, never a Hz', () => {
    expect(deriveFilterWidthDisplay(
      state({ main: { filterWidth: null, filterWidthRadioDefault: false } }),
      caps(),
      'MAIN',
    )).toEqual({ state: 'unknown', reason: 'not-reported' });
  });

  it('PR2 stands alone: an observed null width with NO flag leaf (pre-PR1 backend) is not-reported', () => {
    expect(deriveFilterWidthDisplay(state({ main: { filterWidth: null } }), caps(), 'MAIN'))
      .toEqual({ state: 'unknown', reason: 'not-reported' });
  });

  it('NARROW confirmed on with the width leaf withheld (MOR-2803 discard) is narrow', () => {
    const withheld: FieldStatus = {
      storePath: 'main.filterWidth', observed: false, freshness: 'unknown', availability: 'missing',
    };
    expect(deriveFilterWidthDisplay(
      state({
        main: { filterWidth: null, narrow: true },
        fieldStatus: {
          'main.narrow': FRESH('main.narrow'),
          'main.filterWidth': withheld,
        },
      }),
      caps({ capabilities: [...FTX1_CAPABILITIES.capabilities, 'narrow'] }),
      'MAIN',
    )).toEqual({ state: 'narrow' });
  });

  it('NARROW-over-SH: while the width leaf is STILL available with 3000 Hz, the reading stays known (design Q8 one-tick window)', () => {
    // Bench 09:17:11 — NA01 answered while SH0 still said 3000. The
    // declared-absent discard happens on the freshness tick, so for that
    // tick the honest derivation is the (stale-but-held) 3000 Hz reading.
    expect(deriveFilterWidthDisplay(
      state({
        main: { filterWidth: 3000, narrow: true },
        fieldStatus: { 'main.narrow': FRESH('main.narrow') },
      }),
      caps({ capabilities: [...FTX1_CAPABILITIES.capabilities, 'narrow'] }),
      'MAIN',
    )).toEqual({ state: 'known', hz: 3000, stale: false });
  });

  it('an unobserved width leaf is not-observed', () => {
    const neverRead: FieldStatus = {
      storePath: 'main.filterWidth', observed: false, freshness: 'unknown', availability: 'available',
    };
    expect(deriveFilterWidthDisplay(
      state({ main: { filterWidth: null }, fieldStatus: { 'main.filterWidth': neverRead } }),
      caps(),
      'MAIN',
    )).toEqual({ state: 'unknown', reason: 'not-observed' });
  });

  it('an unresolved identity fails closed to not-observed even with a positive value on the leaf', () => {
    expect(deriveFilterWidthDisplay(state(), caps({ providerGeneration: 999 }), 'MAIN'))
      .toEqual({ state: 'unknown', reason: 'not-observed' });
  });

  it('derives the SUB receiver from its own leaves', () => {
    const withheld: FieldStatus = {
      storePath: 'sub.filterWidth', observed: false, freshness: 'unknown', availability: 'missing',
    };
    const base = state({
      main: { filterWidth: null, narrow: true },
      fieldStatus: {
        'main.narrow': FRESH('main.narrow'),
        'main.filterWidth': withheld,
      },
    });
    const subState = {
      ...base,
      active: 'SUB',
      sub: { ...base.sub!, filterWidth: null, narrow: true },
      fieldStatus: {
        ...base.fieldStatus,
        'sub.narrow': FRESH('sub.narrow'),
        'sub.filterWidth': withheld,
      },
    } as ServerState;
    expect(deriveFilterWidthDisplay(
      subState,
      caps({ capabilities: [...FTX1_CAPABILITIES.capabilities, 'narrow'] }),
      'SUB',
    )).toEqual({ state: 'narrow' });
  });
});
