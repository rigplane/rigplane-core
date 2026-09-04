import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

export type TxPhase = 'idle' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
export type TxIntent = 'momentary' | 'latched' | null;

/** Read-only projection of the server ManagedTxAuthority document. */
export interface TxState {
  phase: TxPhase;
  intent: TxIntent;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  fault: string | null;
  faultDetail: null;
}

const UNKNOWN: TxState = Object.freeze({
  phase: 'idle', intent: null, radioTx: 'unknown', txRisk: 'none',
  fault: null, faultDetail: null,
});

export function projectManagedTransmit(
  document: ManagedTransmitDocument | null,
  stale: boolean,
): TxState {
  if (document === null || stale || document.managedTransmit.status !== 'available') return UNKNOWN;
  const managed = document.managedTransmit;
  const radioTx = document.txObservation.observedPtt;
  const intent: TxIntent = managed.intent.kind === 'transmit'
    ? 'latched'
    : managed.intent.kind === 'ptt' ? 'momentary' : null;
  const txRisk = radioTx === 'on'
    ? 'confirmed-on'
    : managed.releaseRequired || managed.intent.kind !== 'rx' ? 'uncertain' : 'none';
  let phase: TxPhase;
  if (managed.intent.kind === 'rx' && managed.releaseRequired) phase = 'releasing';
  else if (managed.lastError !== null) phase = 'failed';
  else if (managed.intent.kind === 'rx') phase = 'idle';
  else phase = radioTx === 'on' ? 'active' : 'key-confirm-pending';
  return Object.freeze({
    phase, intent, radioTx, txRisk,
    fault: managed.lastError, faultDetail: null,
  });
}
