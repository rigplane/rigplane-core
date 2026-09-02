/**
 * `segmentline` TX renderer (MOR-2149, TX slice) — how the glass says
 * RX / TX / TUNE: the PERIMETER, not a field wash or a carrier rail. A hot
 * bezel tone plus an inward glow; the data area stays untouched, which is
 * what distinguishes this family's carrier from studioline's top rail and
 * fieldline's flooding left rail.
 *
 * SAFETY INVARIANT R9, identical to the two sibling renderers. This renderer
 * DISPLAYS the App TX authority's conclusions; it never forms its own. The
 * only fields it reads are `rf` and `session` — the outputs of
 * `semantic/rx-tx-surface.ts`'s `rfState()`/`txSessionState()`, themselves
 * derived from the App-owned `TxAuthoritySnapshot` — plus `fault` and
 * `keyBlocked`. It never reads a raw `ptt`. The fail-closed DECISION over
 * those four fields is `resolveTxFeedbackState` (MOR-2031,
 * `presentation/languages/tx-feedback-state.ts`), shared with studioline and
 * fieldline; only the RAIL_TABLE below — segmentline's own lit/label per
 * bucket — belongs to this family.
 *
 * INPUT CORRECTION. The real call site, `semantic/RxTxSurface.svelte`'s
 * `stateFeedback` derived value, calls exactly
 * `renderSlot('stateFeedback', { rf, session, fault: tx.fault, keyBlocked:
 * blocked.length > 0 })` — the same four fields studioline/fieldline read.
 * An earlier draft of this file instead read `channel`/`phase`/`treatment`
 * fields directly off `viewModel.fields`; `RxTxSurface.svelte` never supplies
 * any of the three, so that version would have rendered every state as the
 * `channel==='rx'`/`phase==='idle'` default regardless of the real TX state —
 * exactly the "nothing is happening" failure R9 exists to prevent. This
 * version reads the same canonical fields the two sibling renderers do.
 *
 * WHAT ACTUALLY REACHES THE DOM. `renderSlot` annotates only TOP-LEVEL
 * PRIMITIVES; `kind`, `numeralTone`, `meterScaleLabel` and `micro` (when
 * non-null) are that surface. `micro` mirrors studioline's own top-level
 * field, not fieldline's (whose fault text lives in nested `band.text`) —
 * but the resulting three-vs-four attribute count differs from fieldline in
 * only 1 of the 6 rail buckets (`failed` with a fault code), not generally.
 * `perimeter` and `cell` are private structure, the same role `groups`
 * plays for the frequency renderer — no stylesheet rule in this repo reads
 * either through `renderSlot`'s `data-dl-*` mechanism.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';
import {
  resolveTxFeedbackState, stringField, toneFor, type TxFeedbackRail, type TxKeyTreatment,
} from '../tx-feedback-state';
import { SEGMENTLINE_INK, SEGMENTLINE_PALETTE } from './tokens';

/** Matches `.dl-glass[data-tx='active']::after`'s box-shadow in `segmentline.css` exactly. */
const FRAME_GLOW = 'inset 0 0 38px 2px rgba(214, 28, 8, 0.46), inset 0 0 11px rgba(255, 80, 40, 0.55)';

export interface SegmentlinePerimeter {
  /** Every non-idle rail bucket lights the perimeter; idle stays quiet. */
  readonly lit: boolean;
  readonly label: string | null;
  readonly tone: string;
  /** Empty string when not lit — never a partial glow. */
  readonly insetShadow: string;
  readonly transition: string;
}

export interface SegmentlineCell {
  readonly treatment: TxKeyTreatment;
  /** Denied/unknown permit renders inert-but-present — a hidden PTT reads as "no TX capability". */
  readonly present: true;
  /** Segmentline never fills a control (`tokens.ts`: "every control is an
   *  outlined cell, never a filled button") — engagement is carried by ink
   *  strength alone, via `segmentline.css`'s `[data-active='true']`. */
  readonly active: boolean;
  readonly tone: string;
  readonly focusRing: string;
}

export interface SegmentlineStateFeedback {
  readonly kind: 'segmentline-state-feedback';
  readonly numeralTone: 'primary' | 'tx-target';
  readonly meterScaleLabel: 'S' | 'PO';
  readonly micro: string | null;
  readonly perimeter: SegmentlinePerimeter;
  readonly cell: SegmentlineCell;
}

/**
 * segmentline's own lit/label per rail bucket — the bucket itself comes from
 * `resolveTxFeedbackState`; only this table, and the choice to carry state on
 * the perimeter rather than a rail or a band, belongs to segmentline. `null`
 * label at `idle` is the RX baseline — the only silent state.
 */
const RAIL_TABLE: Record<TxFeedbackRail, { readonly lit: boolean; readonly label: string | null }> = {
  idle: { lit: false, label: null },
  pending: { lit: true, label: 'KEYING' },
  keyed: { lit: true, label: 'TX' },
  releasing: { lit: true, label: 'UNKEYING' },
  failed: { lit: true, label: 'TX FAULT' },
  doubt: { lit: true, label: 'TX?' },
};

export function renderStateFeedback(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): SegmentlineStateFeedback {
  const resolved = resolveTxFeedbackState({
    rf: stringField(viewModel.fields, 'rf'),
    session: stringField(viewModel.fields, 'session'),
    fault: stringField(viewModel.fields, 'fault'),
    keyBlocked: viewModel.fields.keyBlocked === true,
  });
  const { treatment, keyed, faultText } = resolved;
  const rail = RAIL_TABLE[resolved.rail];
  const tone = toneFor(resolved.tone, tokens);

  return {
    kind: 'segmentline-state-feedback',
    numeralTone: keyed ? 'tx-target' : 'primary',
    // The meter re-zones with the transmitter, not with the request to key.
    meterScaleLabel: keyed ? 'PO' : 'S',
    micro: faultText,
    perimeter: {
      lit: rail.lit,
      label: rail.label,
      tone,
      insetShadow: rail.lit ? FRAME_GLOW : '',
      transition: `border-color ${tokens.motion.durationMs}ms linear, box-shadow ${tokens.motion.durationMs}ms linear`,
    },
    cell: {
      treatment,
      present: true,
      active: treatment === 'keyed',
      // Same hot+active CONCEPT as segmentline.css's `.dl-cell[data-tone=
      // 'hot'][data-active='true']` rule — but `tone` holds a colour value
      // here, not the keyword 'hot'; no rule currently reads this field.
      tone: treatment === 'blocked' ? SEGMENTLINE_INK.ghost
        : treatment === 'keyed' ? SEGMENTLINE_PALETTE.txMark
          : SEGMENTLINE_INK.soft,
      focusRing: tokens.focusRing,
    },
  };
}
