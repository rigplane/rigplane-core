import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

export type ManagedTxPhase = 'idle' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
export type ManagedTxIntent = 'momentary' | 'latched' | null;

/** Read-only browser projection of the server ManagedTxAuthority document. */
export interface ManagedTxState {
  phase: ManagedTxPhase;
  intent: ManagedTxIntent;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  fault: string | null;
  faultDetail: null;
  fresh: boolean;
  releaseRequired: boolean;
  configuredSeconds: number | null;
  remainingMs: number | null;
  lastOperation: 'ptt_on' | 'transmit_on' | 'force_receive' | null;
}

const UNKNOWN: ManagedTxState = Object.freeze({
  phase: 'idle', intent: null, radioTx: 'unknown', txRisk: 'none',
  fault: null, faultDetail: null,
  fresh: false, releaseRequired: false, configuredSeconds: null,
  remainingMs: null, lastOperation: null,
});

export function projectManagedTx(
  document: ManagedTransmitDocument | null,
  stale: boolean,
  remainingMs: number | null = null,
): ManagedTxState {
  if (document === null || stale || document.managedTransmit.status !== 'available') return UNKNOWN;
  const managed = document.managedTransmit;
  const radioTx = document.txObservation.observedPtt;
  const intent: ManagedTxIntent = managed.intent.kind === 'transmit'
    ? 'latched'
    : managed.intent.kind === 'ptt' ? 'momentary' : null;
  const txRisk = radioTx === 'on'
    ? 'confirmed-on'
    : managed.releaseRequired || managed.intent.kind !== 'rx' ? 'uncertain' : 'none';
  let phase: ManagedTxPhase;
  if (managed.lastError !== null) phase = 'failed';
  else if (managed.intent.kind === 'rx' && managed.releaseRequired) phase = 'releasing';
  else if (managed.intent.kind === 'rx') phase = 'idle';
  else phase = radioTx === 'on' ? 'active' : 'key-confirm-pending';
  return Object.freeze({
    phase, intent, radioTx, txRisk,
    fault: managed.lastError, faultDetail: null, fresh: true,
    releaseRequired: managed.releaseRequired,
    configuredSeconds: managed.tot.configuredSeconds,
    remainingMs,
    lastOperation: managed.lastActuation?.operation ?? null,
  });
}
