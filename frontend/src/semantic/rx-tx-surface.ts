/**
 * MOR-1064 — the semantic RX/TX status and action vocabulary.
 *
 * Pure. It maps (a) the MOR-1062 `RadioViewModel` and (b) a snapshot of the
 * App-owned TX authority onto the words the operator reads and the gate the key
 * action obeys. It never computes TX truth, never keys anything, and never
 * imports the TX controller: v3 ADR invariant 11 keeps TX authority in
 * `lib/runtime/tx-controller` (App-root host, MOR-1059); this module renders
 * that authority's conclusions rather than becoming a second one.
 *
 * Every mapping fails CLOSED: an unrecognised phase reads as "keying in
 * progress", an unobserved RF state reads as "unknown" rather than RX, and any
 * doubt blocks the key intent. Only the unkey intent is ungated — stopping
 * transmission must never depend on this surface agreeing that it is happening.
 */
import type { DisabledReason, RadioViewModel } from './radio-view-model';

/**
 * The subset of the App TX controller's `TxState` this surface reads, with
 * identical field names and union members so a real snapshot is assignable
 * without adaptation (pinned against the real reducer in
 * `__tests__/rx-tx-authority-parity.test.ts`). `fault` stays `string | null`
 * deliberately: a fault code this surface has never heard of must still show.
 */
export interface TxAuthoritySnapshot {
  phase: 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
  intent: 'momentary' | 'latched' | null;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean;
  fault: string | null;
}

export type RfState = 'receiving' | 'transmitting' | 'uncertain' | 'unknown';
export type TxSessionState = 'idle' | 'pending' | 'keyed' | 'releasing' | 'failed';
export type TxOrigin = 'local' | 'external';
export type KeyBlockedReason =
  | 'tx-target-unknown' | 'tx-permit-denied' | 'tx-permit-unknown'
  | 'tx-fault' | 'tx-busy' | 'radio-transmitting' | 'rf-state-unknown';

/** Text AND shape — never colour alone, so the state survives forced-colors (MOR-977). */
export const RF_LABEL: Record<RfState, string> = { receiving: 'RX', transmitting: 'TX', uncertain: 'TX?', unknown: 'RF ?' };
export const RF_MARK: Record<RfState, string> = { receiving: '▼', transmitting: '▲', uncertain: '△', unknown: '◇' };
export const SESSION_LABEL: Record<TxSessionState, string> = { idle: 'ready', pending: 'keying', keyed: 'key down', releasing: 'releasing', failed: 'fault' };
export const BLOCKED_LABEL: Record<KeyBlockedReason, string> = {
  'tx-target-unknown': 'TX target not observed',
  'tx-permit-denied': 'frequency outside the configured TX ranges',
  'tx-permit-unknown': 'TX permit unknown',
  'tx-fault': 'unresolved TX fault',
  'tx-busy': 'a TX lease is already in progress',
  'radio-transmitting': 'the radio is already transmitting',
  'rf-state-unknown': 'RF state unknown',
};

const SESSION_BY_PHASE: Record<TxAuthoritySnapshot['phase'], TxSessionState> = {
  idle: 'idle', 'audio-start-pending': 'pending', 'key-confirm-pending': 'pending', active: 'keyed', releasing: 'releasing', failed: 'failed',
};

/** 'receiving' requires a positively observed OFF and zero TX risk; anything else is doubt. */
export function rfState(tx: TxAuthoritySnapshot): RfState {
  if (tx.radioTx === 'on' || tx.txRisk === 'confirmed-on') return 'transmitting';
  if (tx.txRisk === 'uncertain') return 'uncertain';
  return tx.radioTx === 'off' && tx.txRisk === 'none' ? 'receiving' : 'unknown';
}

/** An unrecognised phase reads as 'pending' — never as 'idle' ("nothing is happening"). */
export const txSessionState = (tx: TxAuthoritySnapshot): TxSessionState =>
  SESSION_BY_PHASE[tx.phase] ?? 'pending';

/** 'external' only when the authority is provably uninvolved — "not you" is the dangerous claim. */
export const txOrigin = (tx: TxAuthoritySnapshot): TxOrigin =>
  tx.mayOwnKey || tx.phase !== 'idle' ? 'local' : 'external';

/**
 * Both halves must agree before a key intent may leave this surface: the
 * permit says the FREQUENCY is legal, the authority says the TRANSMITTER is
 * free. A 'unknown' permit is not a permit.
 */
export function keyBlockedReasons(
  view: RadioViewModel, tx: TxAuthoritySnapshot,
): readonly KeyBlockedReason[] {
  const reasons: KeyBlockedReason[] = [];
  if (view.txTarget.status !== 'known') reasons.push('tx-target-unknown');
  if (view.txPermit.status === 'denied') reasons.push('tx-permit-denied');
  if (view.txPermit.status === 'unknown') reasons.push('tx-permit-unknown');
  if (tx.fault !== null) reasons.push('tx-fault');
  if (tx.phase !== 'idle' || tx.mayOwnKey || tx.txRisk !== 'none') reasons.push('tx-busy');
  if (tx.radioTx === 'on') reasons.push('radio-transmitting');
  if (tx.radioTx === 'unknown') reasons.push('rf-state-unknown');
  return reasons;
}

/** The view model's own disabled reasons that concern TX — scope/VFO reasons are not TX gates. */
export const txDisabledReasons = (view: RadioViewModel): readonly DisabledReason[] =>
  view.disabledReasons.filter((reason) => reason.field.startsWith('tx'));

let sequence = 0;
/** Per-instance DOM id, so several mounted surfaces keep distinct aria targets. */
export const nextSurfaceId = (): string => `rx-tx-${++sequence}`;
