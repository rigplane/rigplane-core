import type { TxBand } from '$lib/types/capabilities';
import type { TxTargetViewModel } from '../../semantic/radio-view-model';

export type FrequencyPermit =
  | { status: 'allowed'; band: string | null }
  | { status: 'denied'; reason: 'outside-configured-ranges' }
  | { status: 'unknown'; reason: 'ranges-unconfigured' | 'tx-target-unknown' };

/**
 * Evaluate only an explicit TX-target frequency against configured ranges.
 *
 * MOR-4433: the band table is classified BEFORE the missing frequency. An
 * unresolvable target (`null` frequency — unknown, stale, contradictory, or
 * sourceless) must not mask an unconfigured (`null` → unknown/
 * ranges-unconfigured) or explicitly empty (`[]` → denied, deny-all) table:
 * with an unsupported target the sourceless exception downstream suppresses
 * exactly the `tx-target-unknown` unknown, so a frequency-first order would
 * wrongly enable transmit-adjacent controls over an unavailable table.
 * `null` ranges are unknown; an explicit empty list is deny-all.
 */
export function getFrequencyPermit(
  freqHz: number | null,
  txBands: readonly TxBand[] | null,
): FrequencyPermit {
  if (txBands === null) {
    return { status: 'unknown', reason: 'ranges-unconfigured' };
  }
  if (txBands.length === 0) {
    return { status: 'denied', reason: 'outside-configured-ranges' };
  }
  if (freqHz === null || !Number.isFinite(freqHz)) {
    return { status: 'unknown', reason: 'tx-target-unknown' };
  }
  const band = txBands.find(({ start, end }) => freqHz >= start && freqHz <= end);
  return band
    ? { status: 'allowed', band: band.name || null }
    : { status: 'denied', reason: 'outside-configured-ranges' };
}

/**
 * MOR-4433 — the ONE sourceless-target decision, shared by the adapter
 * (`deriveCwKeyerReasons`), the validator (`validateRadioViewModel`), the key
 * gate (`keyBlockedReasons`) and the CW renderer/handler
 * (`CwKeyerSurface.svelte`).
 *
 * True for exactly one shape: the TX target is `unsupported` (no source
 * exists — the sourceless class) AND the permit is unknown FOR that reason
 * (`tx-target-unknown`). Nothing else: a sourced-but-unobserved target, an
 * unconfigured band table (`ranges-unconfigured`) and a denied permit all
 * read false, so every other gate keeps failing closed.
 */
export function isSourcelessPermitUnknown(
  txTarget: TxTargetViewModel,
  txPermit: FrequencyPermit,
): boolean {
  return txTarget.status === 'unknown' && txTarget.reason === 'unsupported'
    && txPermit.status === 'unknown' && txPermit.reason === 'tx-target-unknown';
}

/** Transitional binary view for the pre-v3 mobile caller; unknown fails closed. */
export type TxPermit = 'allowed' | 'denied';
export function getTxPermit(
  freqHz: number,
  txBands?: readonly TxBand[] | null,
): TxPermit {
  return getFrequencyPermit(freqHz, txBands ?? null).status === 'allowed'
    ? 'allowed'
    : 'denied';
}
