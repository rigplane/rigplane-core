import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';

// MOR-2607: the inputs of the managed-transmit authority refresh. The
// document (`GET /api/v1/managed-transmit`, built by
// `web/managed_tx_view.py: build_managed_tx_view`) carries the authority
// projection plus `txObservation.observedPtt`, which is projected from the
// radio state's ptt field and its freshness. `refreshAuthority` also
// invalidates across provider generations, and the page's TX controller
// behaviour additionally follows the listed capability fields.
export interface ManagedTxAuthorityInputs {
  readonly ptt: boolean | null | undefined;
  readonly pttObserved: boolean | undefined;
  readonly pttFreshness: FieldStatus['freshness'] | undefined;
  readonly pttAvailability: FieldStatus['availability'] | undefined;
  readonly providerGeneration: number | null | undefined;
  readonly caps: Pick<
    Capabilities,
    'tx' | 'audioTx' | 'audioTxRequiredModInputSource' | 'capabilities' | 'vfoScheme' | 'txBands'
  > | null | undefined;
}

/** Project the refresh-relevant TX inputs out of the live radio state. */
export function managedTxAuthorityInputs(
  state: ServerState | null | undefined,
  caps: Capabilities | null | undefined,
): ManagedTxAuthorityInputs {
  const pttStatus = state?.fieldStatus?.['ptt'];
  return {
    ptt: state?.ptt,
    // Only the stable ptt status parts: `lastObservedMonotonic` advances on
    // every ptt poll (~0.3 s) and must not move the key on an idle page.
    pttObserved: pttStatus?.observed,
    pttFreshness: pttStatus?.freshness,
    pttAvailability: pttStatus?.availability,
    providerGeneration: state?.providerGeneration,
    caps: caps == null
      ? caps ?? null
      : {
        tx: caps.tx,
        audioTx: caps.audioTx,
        audioTxRequiredModInputSource: caps.audioTxRequiredModInputSource,
        capabilities: caps.capabilities,
        vfoScheme: caps.vfoScheme,
        txBands: caps.txBands,
      },
  };
}

/**
 * Serialize the TX inputs so a refresh gate can tell a TX-relevant change
 * from an observation-only frame. Observation counters (`stateRevision`,
 * `freshnessRevision`, `observationSeq`) and unrelated state/caps fields are
 * excluded: an idle page must not refetch on every radio observation
 * (MOR-2607).
 */
export function managedTxAuthorityKey(
  state: ServerState | null | undefined,
  caps: Capabilities | null | undefined,
): string {
  return JSON.stringify(managedTxAuthorityInputs(state, caps));
}

/**
 * Remembers the last refresh key and reports only genuine TX-input changes.
 * The App.svelte effect re-runs on every radio-state replacement (the store
 * swaps the whole state object per frame), so it must compare keys instead
 * of refreshing unconditionally. Plain non-reactive state: the effect holds
 * one instance in a plain `let` and refreshes only when this returns true.
 */
export class ManagedTxAuthorityRefreshGate {
  #lastKey: string | null = null;
  shouldRefresh(nextKey: string): boolean {
    if (nextKey === this.#lastKey) return false;
    this.#lastKey = nextKey;
    return true;
  }
}
