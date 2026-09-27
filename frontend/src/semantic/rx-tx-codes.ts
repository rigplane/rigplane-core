/**
 * MOR-2718 — the RX/TX surface's code lists, as runtime constants.
 *
 * Pure: no DOM, no Svelte, no i18n import chain, so the Node-side
 * Playwright page scan (`tests/e2e/placeholder-guard/page-scan.spec.ts`)
 * and the internal-identifier rule can load it. `rx-tx-surface.ts`
 * derives its `KeyBlockedReason` and `TxIneligibilityReason` unions from
 * these lists, so each code is written exactly once.
 */
export const KEY_BLOCKED_REASON_CODES = [
  'tx-target-unknown', 'tx-permit-denied', 'tx-permit-unknown',
  'tx-fault', 'tx-busy', 'radio-transmitting', 'rf-state-unknown',
] as const;
export type KeyBlockedReasonCode = (typeof KEY_BLOCKED_REASON_CODES)[number];

/**
 * MOR-1792: the `not-eligible` refusal's per-leg codes. Re-exported by
 * `rx-tx-surface.ts`, which keeps the ADR-invariant seam (no TX reducer
 * import).
 */
export const FAULT_REASON_CODES = [
  'cat-ptt-unavailable', 'browser-tx-audio-unavailable', 'control-not-live',
  'tx-permit-not-allowed', 'tx-target-unknown', 'ptt-not-off',
  'ptt-not-authoritative', 'no-confirmed-ptt-off', 'authority-epoch-mismatch',
] as const;
export type FaultReasonCode = (typeof FAULT_REASON_CODES)[number];
