import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

// MOR-2607: the inputs of the managed-transmit authority refresh. The
// managed-transmit document changes only through the authority state (whose
// mutations the server already announces with `managed_transmit_changed`)
// and through `txObservation.observedPtt`. `observedPtt` is projected from
// the separate `global.tx_state.observed_ptt` store path — not from anything
// in this key — and its changes arrive through the same server
// `managed_transmit_changed` event, so the page refetches exactly then.
// `refreshAuthority` also invalidates across provider generations, and the
// page's TX controller behaviour additionally follows the listed capability
// fields.
export interface ManagedTxAuthorityInputs {
  // Temporary mutation: the public ptt leaf back in the key.
  readonly ptt: boolean | null | undefined;
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
  return {
    ptt: state?.ptt,
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
