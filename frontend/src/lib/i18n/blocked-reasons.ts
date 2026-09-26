/**
 * MOR-1890 — a stored refusal code mapped to its operator-legible sentence.
 *
 * A TX-interlock refusal stores its reason as a kebab-case semantic code
 * (`ws-client` maps the snake_case wire code at the transport edge). This
 * module resolves that code, via the shared catalog table below, to the
 * sentence every renderer reads (adapters' outcome builder, semantic
 * surfaces).
 *
 * Lives in `$lib/i18n` — with `faceplate-invariant-keys.ts` — and NOT in
 * `semantic/rx-tx-surface.ts`: the v3-ADR adapters layer may import only
 * semantic CONTRACT types, so the value resolver must sit on the lib side
 * of the seam for `projectControlFeedback` to reach it without a runtime
 * dependency on semantic/.
 *
 * `tx-target-unknown` and `tx-permit-denied` REUSE the exact MOR-1448
 * `core.band.tx.reason.*` keys — same underlying facts, same words (see the
 * rationale in `semantic/rx-tx-surface.ts`, which shares this table).
 */
import { t } from './index';

/** Kebab-case blocked-reason code → catalog key. */
export const BLOCKED_REASON_KEY: Record<string, string> = {
  'tx-target-unknown': 'core.band.tx.reason.targetUnknown',
  'tx-permit-denied': 'core.band.tx.reason.outOfBand',
  'tx-permit-unknown': 'core.rxTx.blocked.permitUnknown',
  'tx-fault': 'core.rxTx.blocked.fault',
  'tx-busy': 'core.rxTx.blocked.busy',
  'radio-transmitting': 'core.rxTx.blocked.radioTransmitting',
  'rf-state-unknown': 'core.rxTx.blocked.rfStateUnknown',
};

/**
 * One call from a stored refusal code to its operator-legible sentence —
 * `undefined` for anything not in the catalog, so an unrecognised raw
 * server message keeps its verbatim fallback, and prototype-junk strings
 * like `'constructor'` never resolve either (guarded hasOwnProperty
 * lookup, same as the semantic blockedKey guard).
 */
export const blockedReasonLabel = (code: string): string | undefined =>
  Object.prototype.hasOwnProperty.call(BLOCKED_REASON_KEY, code)
    ? t(BLOCKED_REASON_KEY[code]) : undefined;
