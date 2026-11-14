/**
 * MOR-4433 follow-up: an unobservable TX target must not block
 * transmit-adjacent controls.
 *
 * Radios with one transceiver and no readable TX target (FT-991A, Xiegu
 * G7250) project `txTarget: { status: 'unknown', reason: 'unsupported' }` —
 * "no source exists", not "a source exists but is unobserved". The shared
 * gates must read that reason: `keyBlockedReasons`, the break-in
 * `disabledReasons` entry, and the `txPermit` entry stop blocking, while
 * `not-observed` / `stale` / `contradiction` (a source exists) still fail
 * closed — as does a genuinely unconfigured band table.
 *
 * Fixtures are built from the producer types (`UnknownTxTargetPublic`'s own
 * `reason` union, the real `topologyFixtures` view models, the real
 * `validateRadioViewModel`), never from re-typed copies: the `1/ab`
 * fixture's permit is already `unknown`/`tx-target-unknown` with configured
 * bands beside it, exactly the shape `deriveTxCapabilities` produces for an
 * unsupported target.
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel,
  type RadioViewModel,
} from '../radio-view-model';
import {
  keyBlockedReasons,
  type TxAuthoritySnapshot,
} from '../rx-tx-surface';
import { antennaSwitchBlocks } from '../AntennaInstrumentHost.svelte';
import {
  topologyFixtures,
  withAntenna,
  withCwKeyer,
} from '../fixtures/topologies';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';

const IDLE_RX: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
};

/** The `1/ab` view as a sourceless radio projects it: target `unsupported`,
 *  permit unknown for that reason, bands configured. */
function unsupportedTargetView(): RadioViewModel {
  return {
    ...topologyFixtures['1/ab'],
    txTarget: { status: 'unknown', reason: 'unsupported' },
  };
}

describe('unsupported TX target does not block', () => {
  it('keyBlockedReasons is empty for unsupported target on an idle RX radio', () => {
    // Kill-mutation: `view.txTarget.status !== 'known'` without the
    // `unsupported` exception — the prohibition this change removes.
    expect(keyBlockedReasons(unsupportedTargetView(), IDLE_RX)).toEqual([]);
  });

  it('keyBlockedReasons still blocks not-observed / stale / contradiction', () => {
    const base = topologyFixtures['1/ab'];
    expect(keyBlockedReasons(base, IDLE_RX)).toContain('tx-target-unknown');
    for (const reason of ['not-observed', 'stale', 'contradiction'] as const) {
      const view: RadioViewModel = {
        ...base, txTarget: { status: 'unknown', reason },
      };
      expect(keyBlockedReasons(view, IDLE_RX)).toContain('tx-target-unknown');
    }
  });

  it('keyBlockedReasons still blocks an unconfigured band table under unsupported target', () => {
    // The exception is scoped to "permit unknown because the target is
    // sourceless" — `ranges-unconfigured` (no TX bands at all) still blocks.
    const view: RadioViewModel = {
      ...unsupportedTargetView(),
      txPermit: { status: 'unknown', reason: 'ranges-unconfigured' },
    };
    const reasons = keyBlockedReasons(view, IDLE_RX);
    expect(reasons).not.toContain('tx-target-unknown');
    expect(reasons).toContain('tx-permit-unknown');
  });

  it('antenna switching is not gated on the target, with or without a source', () => {
    // `antennaSwitchBlocks` only keeps the RF-idle subset; the target
    // reasons never reached the relay. Pinned both ways so a future
    // widening of the filter cannot silently re-block sourceless radios.
    expect(antennaSwitchBlocks(withAntenna(unsupportedTargetView()), IDLE_RX)).toEqual([]);
    expect(antennaSwitchBlocks(withAntenna(topologyFixtures['1/ab']), IDLE_RX)).toEqual([]);
  });

  it('a validator-accepted model may arm break-in under an unsupported target', () => {
    // The adapter stops recording the `cwKeyer.breakIn` disable for a
    // sourceless target (break-in stays usable); the validator — the
    // fail-closed half of "no second permit" — accepts the model without
    // that entry. Kill-mutation: drop the `unsupported` exception in either
    // `deriveCwKeyerReasons` or `validateRadioViewModel`.
    const keyed = withCwKeyer(unsupportedTargetView());
    const enabled: RadioViewModel = {
      ...keyed,
      disabledReasons: keyed.disabledReasons.filter((r) => r.field !== 'cwKeyer.breakIn'),
    };
    expect(() => validateRadioViewModel(enabled)).not.toThrow();
    expect(
      enabled.disabledReasons.some((r) => r.field === 'cwKeyer.breakIn'),
    ).toBe(false);
  });

  it('the validator still rejects an enabled break-in for not-observed target', () => {
    // The exception above is scoped to `unsupported`: a source that exists
    // but has not been read still fails closed.
    const keyed = withCwKeyer(topologyFixtures['1/ab']);
    const enabled: RadioViewModel = {
      ...keyed,
      disabledReasons: keyed.disabledReasons.filter((r) => r.field !== 'cwKeyer.breakIn'),
    };
    expect(() => validateRadioViewModel(enabled)).toThrow();
  });
});

describe('adapter projects the unsupported target without blocking', () => {
  /** Minimal `ab` capabilities with TX bands configured (FT-991A shape). */
  const caps: Capabilities = {
    model: 'FT-991A',
    scope: false,
    audio: true,
    tx: true,
    capabilities: ['tx', 'audio', 'cw', 'break_in'],
    receivers: 1,
    vfoScheme: 'ab',
    freqRanges: [],
    txBands: [{ name: '20m', start: 14_000_000, end: 14_350_000 }],
    modes: ['USB'],
    filters: ['NARROW'],
  };

  /** Server state carrying the backend's `unsupported` projection, observed. */
  const state: ServerState = {
    txTarget: { status: 'unknown', reason: 'unsupported' },
    main: { freqHz: 14_074_000, mode: 'USB' },
    connection: { state: 'connected' },
    fieldStatus: {
      txTarget: { observed: true, freshness: 'fresh', availability: 'available' },
    },
  } as unknown as ServerState;

  it('toRadioViewModel keeps the reason and records no break-in disable', () => {
    // End to end through the real adapter: the view model carries the
    // backend's `unsupported` reason, the permit stays honestly unknown,
    // and no `cwKeyer.breakIn` disabled reason blocks the keyer — and the
    // whole model validates.
    const view = toRadioViewModel(state, caps);
    if (view === null) throw new Error('fixture precondition: model is null');
    expect(view.txTarget).toEqual({ status: 'unknown', reason: 'unsupported' });
    expect(view.txPermit).toEqual({ status: 'unknown', reason: 'tx-target-unknown' });
    expect(
      view.disabledReasons.some((r) => r.field === 'cwKeyer.breakIn'),
    ).toBe(false);
    expect(() => validateRadioViewModel(view)).not.toThrow();
  });

  it('toRadioViewModel still records the break-in disable for not-observed target', () => {
    const unobserved = {
      ...state,
      txTarget: { status: 'unknown', reason: 'not-observed' },
    } as unknown as ServerState;
    const view = toRadioViewModel(unobserved, caps);
    if (view === null) throw new Error('fixture precondition: model is null');
    expect(
      view.disabledReasons.some((r) => r.field === 'cwKeyer.breakIn'),
    ).toBe(true);
  });
});
