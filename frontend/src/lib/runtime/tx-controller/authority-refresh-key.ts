import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

// MOR-2607: the inputs of the managed-transmit authority refresh. The
// document (`GET /api/v1/managed-transmit`, built by
// `web/managed_tx_view.py: build_managed_tx_view`) carries the authority
// projection plus `txObservation.observedPtt`, which is projected from the
// radio state's ptt field and its freshness. `refreshAuthority` also
// invalidates across provider generations, and the page's TX controller
// behaviour additionally follows the listed capability fields.
export interface ManagedTxAuthorityInputs {
  readonly ptt: boolean | null | undefined;
  readonly observationSeq: number | null | undefined;
  readonly pttFieldStatus: unknown;
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
    observationSeq: state?.observationSeq,
    pttFieldStatus: state?.fieldStatus?.['ptt'],
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
 * Serialize the TX inputs so an `$effect` re-runs exactly when one of them
 * changes. Observation counters (`stateRevision`, `freshnessRevision`,
 * `observationSeq`) and unrelated state/caps fields are excluded: an idle
 * page must not refetch on every radio observation (MOR-2607).
 */
export function managedTxAuthorityKey(
  state: ServerState | null | undefined,
  caps: Capabilities | null | undefined,
): string {
  return JSON.stringify(managedTxAuthorityInputs(state, caps));
}
