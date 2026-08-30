/**
 * `studioline` TX renderer (MOR-1073, TX slice) — MOR-977 §2.3's
 * "quiet but total" state feedback: a full-bleed top rail that steps 1→2→3px
 * and re-tones, plus the intentional treatment for the TX key (N2).
 *
 * SAFETY INVARIANT R9. This renderer DISPLAYS the App TX authority's
 * conclusions; it never forms its own. The only fields it reads are `rf` and
 * `session` — the outputs of `semantic/rx-tx-surface`'s `rfState()` /
 * `txSessionState()`, which are themselves derived from the App-owned
 * `TxAuthoritySnapshot` — plus `fault` and `keyBlocked`. It never reads a raw
 * `ptt`. The fail-closed DECISION over those four fields is
 * `semantic/rx-tx-surface`'s `resolveTxFeedbackState` (MOR-2031): it takes a
 * named struct of exactly those four primitives, never a whole
 * `RendererViewModel`, so a fifth field cannot be smuggled in without
 * changing that signature. Unrecognised values fail CLOSED to the doubt
 * rail, never to the quiet RX one: "nothing is happening" is the dangerous
 * claim.
 *
 * Colour is never the sole channel — every state carries a distinct rail
 * THICKNESS and a distinct text label, so the state survives forced-colors
 * and colour-vision deficiency (MOR-977 §4.4 required mitigation).
 *
 * MOR-2031: the THICKNESS/LABEL table below, keyed by
 * `resolveTxFeedbackState`'s resolved `rail`, is studioline's own — the same
 * six buckets rank differently on fieldline's rail width (see that
 * function's doc comment), so the ranking itself cannot be shared, only the
 * bucket name.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';
import {
  resolveTxFeedbackState, stringField, type TxFeedbackRail, type TxKeyTreatment,
} from '../../../semantic/rx-tx-surface';
import { STUDIOLINE_PALETTE } from './tokens';

export interface StudiolineRail {
  readonly thicknessPx: 1 | 2 | 3;
  readonly tone: string;
  readonly label: string | null;
  /** Non-negotiable: a full-bleed rail cannot be scrolled, cropped, or collapsed out of any layout. */
  readonly fullBleed: true;
}

export interface StudiolineKey {
  readonly treatment: TxKeyTreatment;
  /** Denied/unknown permit renders inert-but-present — a hidden PTT reads as "no TX capability". */
  readonly present: true;
  readonly filled: boolean;
  readonly fill: string;
  readonly tone: string;
  /** The one radius studioline allows anywhere (MOR-977 §2.3 geometry). */
  readonly radius: '999px';
  readonly focusRing: string;
}

export interface StudiolineStateFeedback {
  readonly kind: 'studioline-state-feedback';
  readonly rail: StudiolineRail;
  readonly numeralTone: 'primary' | 'tx-target';
  readonly meterScaleLabel: 'S' | 'PO';
  readonly micro: string | null;
  readonly key: StudiolineKey;
}

/**
 * studioline's own thickness/label per rail bucket — the bucket itself comes
 * from `resolveTxFeedbackState`; only this table, and its RANKING, belongs
 * to studioline. `null` label at `idle` is the RX baseline — the only
 * silent state.
 */
const RAIL_TABLE: Record<TxFeedbackRail, { thickness: 1 | 2 | 3; label: string | null }> = {
  idle: { thickness: 1, label: null },
  pending: { thickness: 3, label: 'KEYING' },
  keyed: { thickness: 3, label: 'TX' },
  releasing: { thickness: 2, label: 'UNKEYING' },
  failed: { thickness: 3, label: 'TX FAULT' },
  doubt: { thickness: 3, label: 'TX?' },
};

export function renderStateFeedback(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): StudiolineStateFeedback {
  const resolved = resolveTxFeedbackState({
    rf: stringField(viewModel.fields, 'rf'),
    session: stringField(viewModel.fields, 'session'),
    fault: stringField(viewModel.fields, 'fault'),
    keyBlocked: viewModel.fields.keyBlocked === true,
  });
  const { treatment, keyed, faultText } = resolved;
  const rail = RAIL_TABLE[resolved.rail];
  const tone = resolved.tone === 'rx-idle' ? tokens.rx.idle
    : resolved.tone === 'tx-active' ? tokens.tx.active
      : tokens.tx.tuning;

  return {
    kind: 'studioline-state-feedback',
    rail: { thicknessPx: rail.thickness, tone, label: rail.label, fullBleed: true },
    numeralTone: keyed ? 'tx-target' : 'primary',
    // The meter re-zones with the transmitter, not with the request to key.
    meterScaleLabel: keyed ? 'PO' : 'S',
    micro: faultText,
    key: {
      treatment,
      present: true,
      filled: treatment === 'keyed',
      fill: treatment === 'keyed' ? tokens.tx.active : 'transparent',
      tone: treatment === 'blocked' ? STUDIOLINE_PALETTE.inert : tone,
      radius: '999px',
      focusRing: tokens.focusRing,
    },
  };
}
