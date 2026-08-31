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
import { t } from '$lib/i18n';
import type { DisabledReason, RadioViewModel, TxTargetViewModel } from './radio-view-model';

/**
 * The subset of the App TX controller's `TxState` this surface reads, with
 * identical field names and union members so a real snapshot is assignable
 * without adaptation (pinned against the real reducer in
 * `__tests__/rx-tx-authority-parity.test.ts`). `fault` stays `string | null`
 * deliberately: a fault code this surface has never heard of must still show.
 * `faultDetail` (MOR-1792) is `string[]` for the same reason and is OPTIONAL,
 * so a caller that predates it — or a fault that carries no detail — renders
 * exactly as before.
 */
export interface TxAuthoritySnapshot {
  phase: 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
  intent: 'momentary' | 'latched' | null;
  radioTx: 'off' | 'on' | 'unknown';
  txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean;
  fault: string | null;
  faultDetail?: readonly string[] | null;
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
/**
 * MOR-1474: pre-i18n literal English, kept ONLY for `AntennaSurface.svelte`'s
 * `ANTENNA_BLOCKED_LABEL = { ...BLOCKED_LABEL, ... }` type-exhaustiveness
 * spread over `Record<AntennaSwitchBlock, string>`. `antennaSwitchBlocks`
 * (AntennaSurface.svelte) only ever returns `'tx-busy'`,
 * `'radio-transmitting'` or `'rf-state-unknown'` — all THREE of which
 * `ANTENNA_BLOCKED_LABEL` immediately overrides with its own antenna-specific
 * wording — so the other four entries here are structurally unreachable in
 * that surface and are NOT locale-routed. The operator-reachable render
 * paths (`RxTxSurface.svelte`, `TxAuxSurface.svelte`) use `blockedLabel()`
 * below instead, never this object. AntennaSurface's own strings are out of
 * MOR-1474 scope (not named by the ticket; changing them would require
 * touching a fifth surface for zero operator-visible benefit, since none of
 * these four values ever reach its DOM).
 */
export const BLOCKED_LABEL: Record<KeyBlockedReason, string> = {
  'tx-target-unknown': 'TX target not observed',
  'tx-permit-denied': 'frequency outside the configured TX ranges',
  'tx-permit-unknown': 'TX permit unknown',
  'tx-fault': 'unresolved TX fault',
  'tx-busy': 'a TX lease is already in progress',
  'radio-transmitting': 'the radio is already transmitting',
  'rf-state-unknown': 'RF state unknown',
};

/**
 * MOR-1474: the catalog keys behind each `KeyBlockedReason` — operator-
 * legible, i18n-routed text for the REAL render paths (`RxTxSurface`,
 * `TxAuxSurface`). `tx-target-unknown` and `tx-permit-denied` REUSE the
 * exact MOR-1448 `core.band.tx.reason.*` keys: both fire off the identical
 * underlying facts BandSurface's own reasons read
 * (`view.txTarget.status !== 'known'` / `view.txPermit.status === 'denied'`)
 * — same fact, same words, not a re-derivation with its own wording that
 * could drift from BandSurface's.
 */
const BLOCKED_KEY: Record<KeyBlockedReason, string> = {
  'tx-target-unknown': 'core.band.tx.reason.targetUnknown',
  'tx-permit-denied': 'core.band.tx.reason.outOfBand',
  'tx-permit-unknown': 'core.rxTx.blocked.permitUnknown',
  'tx-fault': 'core.rxTx.blocked.fault',
  'tx-busy': 'core.rxTx.blocked.busy',
  'radio-transmitting': 'core.rxTx.blocked.radioTransmitting',
  'rf-state-unknown': 'core.rxTx.blocked.rfStateUnknown',
};
/** Resolves a `KeyBlockedReason` to its operator-legible sentence in the
 *  active locale — called fresh on every render so it stays correct across
 *  a live locale switch (MOR-1448 `reasonLabel` precedent). */
export const blockedLabel = (code: KeyBlockedReason): string => t(BLOCKED_KEY[code]);

export type TxTargetUnknownReason = Extract<TxTargetViewModel, { status: 'unknown' }>['reason'];
/** MOR-1474: `view.txTarget`'s four `status: 'unknown'` reasons
 *  (`radio-view-model.ts`), each backed by ITS OWN catalog key rather than
 *  interpolating the raw enum word into prose (no `{status}`/`{reason}`
 *  placeholder ever receives one of these codes directly) — the same F4
 *  doctrine MOR-1448 established for `txPermit.status`. Per the Python
 *  source (`core/tx_target.py`, `web/radio_poller.py`, `yaesu_cat/
 *  observations.py`): `not-observed`/`stale` are transient poll states with
 *  no operator action that resolves them any faster, `unsupported` is a
 *  PERMANENT fact about this radio model (no CAT emitter for tx_target at
 *  all, or a CI-V VFO scheme the MOR-1496 derivation does not cover), and
 *  `contradiction` is an anomaly in already-observed inputs — none of the
 *  four gets a fabricated remedy (MOR-1448 review F1). */
const TARGET_REASON_KEY: Record<TxTargetUnknownReason, string> = {
  'not-observed': 'core.rxTx.target.reason.notObserved',
  stale: 'core.rxTx.target.reason.stale',
  unsupported: 'core.rxTx.target.reason.unsupported',
  contradiction: 'core.rxTx.target.reason.contradiction',
};
export const targetUnknownReason = (reason: TxTargetUnknownReason): string =>
  t(TARGET_REASON_KEY[reason]);
/** The rendered "TX target unknown" line, assembled through ONE catalog key
 *  so the per-reason sentence above is never interpolated as a bare
 *  fragment without the surrounding verdict. */
export const targetUnknownMessage = (reason: TxTargetUnknownReason): string =>
  t('core.rxTx.target.unknown', { reason: targetUnknownReason(reason) });

/**
 * MOR-1792: the `not-eligible` refusal's per-leg codes, re-declared here for
 * the same reason `TxAuthoritySnapshot` re-declares the TxState subset — ADR
 * invariant 11 forbids this zone importing the TX reducer. Member parity with
 * the reducer's own `TxIneligibility` is pinned in
 * `__tests__/rx-tx-authority-parity.test.ts`.
 */
export const FAULT_REASON_CODES = [
  'cat-ptt-unavailable', 'browser-tx-audio-unavailable', 'control-not-live',
  'tx-permit-not-allowed', 'tx-target-unknown', 'ptt-not-off',
  'ptt-not-authoritative', 'no-confirmed-ptt-off', 'authority-epoch-mismatch',
] as const;
export type TxIneligibilityReason = (typeof FAULT_REASON_CODES)[number];
/**
 * Per-leg catalog keys. Same F4 doctrine as `TARGET_REASON_KEY` above: each
 * code gets its OWN sentence rather than having the raw enum word
 * interpolated into prose, and none of them fabricates a remedy the operator
 * cannot perform. `ptt-not-authoritative` and `no-confirmed-ptt-off` are the
 * two the MOR-1792 bench session needed and never got.
 */
const FAULT_REASON_KEY: Record<TxIneligibilityReason, string> = {
  'cat-ptt-unavailable': 'core.rxTx.fault.reason.catPttUnavailable',
  'browser-tx-audio-unavailable': 'core.rxTx.fault.reason.browserTxAudioUnavailable',
  'control-not-live': 'core.rxTx.fault.reason.controlNotLive',
  'tx-permit-not-allowed': 'core.rxTx.fault.reason.permitNotAllowed',
  'tx-target-unknown': 'core.rxTx.fault.reason.targetUnknown',
  'ptt-not-off': 'core.rxTx.fault.reason.pttNotOff',
  'ptt-not-authoritative': 'core.rxTx.fault.reason.pttNotAuthoritative',
  'no-confirmed-ptt-off': 'core.rxTx.fault.reason.noConfirmedPttOff',
  'authority-epoch-mismatch': 'core.rxTx.fault.reason.authorityEpochMismatch',
};
const isFaultReason = (code: string): code is TxIneligibilityReason =>
  Object.prototype.hasOwnProperty.call(FAULT_REASON_KEY, code);
/** One failing eligibility leg, as the operator's own sentence. */
export const faultReasonLabel = (code: TxIneligibilityReason): string => t(FAULT_REASON_KEY[code]);
/** The legs this surface can put into words, in the order the authority reported them. */
export const faultReasons = (tx: TxAuthoritySnapshot): readonly TxIneligibilityReason[] =>
  (tx.faultDetail ?? []).filter(isFaultReason);
/**
 * The operator-legible fault line (MOR-1792, per the MOR-1783 owner decision
 * that fault text names the CAUSE). A fault the authority annotated with legs
 * reads as the causes; anything else keeps showing its raw code, because a
 * code this surface has never heard of must still reach the operator rather
 * than being swallowed by a friendly generic sentence.
 */
export function faultMessage(tx: TxAuthoritySnapshot): string {
  const reasons = faultReasons(tx);
  return reasons.length > 0
    ? t('core.rxTx.fault.causes', { reasons: reasons.map(faultReasonLabel).join('; ') })
    : t('core.rxTx.fault.code', { code: tx.fault ?? '' });
}

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
  /** Two conditions can reach the same verdict (a confirmed-on risk and an
   *  observed `radioTx: 'on'` are one fact seen twice). The list is rendered
   *  as a KEYED each, so a repeat is not merely noise — it is a duplicate key. */
  const add = (reason: KeyBlockedReason): void => {
    if (!reasons.includes(reason)) reasons.push(reason);
  };
  if (view.txTarget.status !== 'known') add('tx-target-unknown');
  if (view.txPermit.status === 'denied') add('tx-permit-denied');
  if (view.txPermit.status === 'unknown') add('tx-permit-unknown');
  // MOR-1906. A 'failed' phase is a FAULT, not a session — and it blocks on the
  // phase itself, not on `fault` being non-null, so the verdict cannot go
  // missing should a future state ever reach 'failed' without a code.
  if (tx.fault !== null || tx.phase === 'failed') add('tx-fault');
  if (tx.mayOwnKey || (tx.phase !== 'idle' && tx.phase !== 'failed')) add('tx-busy');
  /**
   * MOR-1906. `txRisk` used to collapse into `tx-busy`, which renders as "a TX
   * session is already in progress" — told to an operator on a demonstrably
   * idle radio whose first PTT reading had simply not arrived yet. RF doubt is
   * not a lease. These two lines are `rfState()` above, term for term, so the
   * reason the key gives and the RF label the same surface prints can never
   * disagree; the antenna gate's "not provably idle" set is unchanged, because
   * both members were already in it.
   */
  if (tx.radioTx === 'on' || tx.txRisk === 'confirmed-on') add('radio-transmitting');
  if (tx.radioTx === 'unknown' || tx.txRisk === 'uncertain') add('rf-state-unknown');
  return reasons;
}

/** The view model's own disabled reasons that concern TX — scope/VFO reasons are not TX gates. */
export const txDisabledReasons = (view: RadioViewModel): readonly DisabledReason[] =>
  view.disabledReasons.filter((reason) => reason.field.startsWith('tx'));

let sequence = 0;
/** Per-instance DOM id, so several mounted surfaces keep distinct aria targets. */
export const nextSurfaceId = (): string => `rx-tx-${++sequence}`;
