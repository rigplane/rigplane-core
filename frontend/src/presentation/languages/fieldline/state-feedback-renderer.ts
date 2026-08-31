/**
 * `fieldline` TX renderer (MOR-1074, TX slice) — MOR-977 §2.2's "takeover"
 * state feedback: a left rail that floods, a full-width band above the
 * frequency, and an action slab that inverts to a solid fill with knocked-out
 * black text.
 *
 * SAFETY INVARIANT R9. This renderer DISPLAYS the App TX authority's
 * conclusions; it never forms its own. The only fields it reads are `rf` and
 * `session` — the outputs of `semantic/rx-tx-surface`'s `rfState()` /
 * `txSessionState()`, themselves derived from the App-owned
 * `TxAuthoritySnapshot` — plus `fault` and `keyBlocked`. It never reads a raw
 * `ptt`. The fail-closed DECISION over those four fields is
 * `resolveTxFeedbackState` (MOR-2031, `presentation/languages/tx-feedback-
 * state.ts`): it takes a named struct of exactly those four primitives,
 * never a whole `RendererViewModel`, so a fifth field cannot be smuggled in
 * without changing that signature. Unrecognised values fail CLOSED to the
 * doubt rail, never to the quiet RX one: "nothing is happening" is the
 * dangerous claim.
 *
 * Colour is never the sole channel. Every state carries a distinct rail WIDTH,
 * a distinct band TREATMENT (absent / outlined / filled) and a distinct text
 * label, so the state survives forced-colors and colour-vision deficiency.
 * The carrier is the LEFT RAIL rather than studioline's top rail: it is the one
 * element that never scrolls out of a stacked layout (MOR-977 §2.2).
 *
 * MOR-2031: the WIDTH/BAND/LABEL table below, keyed by
 * `resolveTxFeedbackState`'s resolved `rail`, is fieldline's own — the same
 * six buckets rank differently on studioline's rail thickness (see that
 * function's doc comment), so the ranking itself cannot be shared, only the
 * bucket name.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';
import {
  resolveTxFeedbackState, stringField, type TxFeedbackRail, type TxKeyTreatment,
} from '../tx-feedback-state';
import { FIELDLINE_KNOCKOUT, FIELDLINE_PALETTE } from './tokens';

export type BandTreatment = 'absent' | 'outlined' | 'filled';

export interface FieldlineRail {
  /** 8px baseline, flooding to 16 and 24 — width IS the non-colour channel. */
  readonly widthPx: 8 | 16 | 24;
  readonly tone: string;
  /** Non-negotiable: the rail runs the full height and cannot be cropped away. */
  readonly edge: 'inline-start';
  readonly fullBleed: true;
}

export interface FieldlineBand {
  readonly treatment: BandTreatment;
  readonly text: string | null;
  readonly tone: string;
  /** Knocked-out black on a filled band; the label tone otherwise. */
  readonly textTone: string | null;
}

export interface FieldlineSlab {
  readonly treatment: TxKeyTreatment;
  /** Denied/unknown permit renders inert-but-present — a hidden PTT reads as "no TX capability". */
  readonly present: true;
  readonly filled: boolean;
  readonly fill: string;
  readonly tone: string;
  readonly edgeStyle: 'solid' | 'dashed' | 'dotted';
  /** Full-width bottom slab, above the 44px floor: gloves, one-handed (MOR-977 §2.2). */
  readonly fullWidth: true;
  readonly minHeightPx: 48;
  readonly focusRing: string;
}

export interface FieldlineStateFeedback {
  readonly kind: 'fieldline-state-feedback';
  readonly rail: FieldlineRail;
  readonly band: FieldlineBand;
  readonly numeralTone: 'primary' | 'tx-target';
  readonly meterScaleLabel: 'S' | 'PO';
  readonly slab: FieldlineSlab;
}

/**
 * fieldline's own width/band/label per rail bucket — the bucket itself comes
 * from `resolveTxFeedbackState`; only this table, and its RANKING, belongs
 * to fieldline. `absent`/`null` at `idle` is the RX baseline — the only
 * silent state.
 */
const RAIL_TABLE: Record<TxFeedbackRail, { width: 8 | 16 | 24; band: BandTreatment; label: string | null }> = {
  idle: { width: 8, band: 'absent', label: null },
  pending: { width: 16, band: 'outlined', label: 'KEYING' },
  keyed: { width: 24, band: 'filled', label: 'ON AIR' },
  releasing: { width: 16, band: 'outlined', label: 'UNKEYING' },
  failed: { width: 24, band: 'filled', label: 'TX FAULT' },
  doubt: { width: 16, band: 'outlined', label: 'TX?' },
};

const SLAB_EDGE: Record<TxKeyTreatment, FieldlineSlab['edgeStyle']> = {
  idle: 'solid', pending: 'solid', keyed: 'solid', fault: 'dashed', blocked: 'dotted',
};

export function renderStateFeedback(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): FieldlineStateFeedback {
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
    kind: 'fieldline-state-feedback',
    rail: { widthPx: rail.width, tone, edge: 'inline-start', fullBleed: true },
    band: {
      treatment: rail.band,
      // The fault code rides IN the band rather than in a separate micro-label:
      // takeover means the operator does not have to look anywhere else.
      text: faultText ?? rail.label,
      tone,
      textTone: rail.band === 'absent' ? null
        : rail.band === 'filled' ? FIELDLINE_KNOCKOUT : tone,
    },
    numeralTone: keyed ? 'tx-target' : 'primary',
    // The meter re-zones with the transmitter, not with the request to key.
    meterScaleLabel: keyed ? 'PO' : 'S',
    slab: {
      treatment,
      present: true,
      filled: treatment === 'keyed',
      fill: treatment === 'keyed' ? tokens.tx.active : 'transparent',
      tone: treatment === 'blocked' ? FIELDLINE_PALETTE.inert
        : treatment === 'keyed' ? FIELDLINE_KNOCKOUT : tone,
      edgeStyle: SLAB_EDGE[treatment],
      fullWidth: true,
      minHeightPx: 48,
      focusRing: tokens.focusRing,
    },
  };
}
