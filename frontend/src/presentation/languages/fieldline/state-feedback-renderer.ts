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
 * `ptt`, and because it names every field it consumes, one cannot be smuggled
 * in. Unrecognised values fail CLOSED to the doubt rail, never to the quiet RX
 * one: "nothing is happening" is the dangerous claim.
 *
 * Colour is never the sole channel. Every state carries a distinct rail WIDTH,
 * a distinct band TREATMENT (absent / outlined / filled) and a distinct text
 * label, so the state survives forced-colors and colour-vision deficiency.
 * The carrier is the LEFT RAIL rather than studioline's top rail: it is the one
 * element that never scrolls out of a stacked layout (MOR-977 §2.2).
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';
import { FIELDLINE_KNOCKOUT, FIELDLINE_PALETTE } from './tokens';

export type KeyTreatment = 'idle' | 'pending' | 'keyed' | 'fault' | 'blocked';
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
  readonly treatment: KeyTreatment;
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

interface RailState {
  width: 8 | 16 | 24;
  band: BandTreatment;
  label: string | null;
  keyed: boolean;
}

/** Rail flood + band per session state. `absent` band is the RX baseline — the only silent state. */
const SESSION_RAIL: Record<string, RailState> = {
  idle: { width: 8, band: 'absent', label: null, keyed: false },
  pending: { width: 16, band: 'outlined', label: 'KEYING', keyed: false },
  keyed: { width: 24, band: 'filled', label: 'ON AIR', keyed: true },
  releasing: { width: 16, band: 'outlined', label: 'UNKEYING', keyed: true },
  failed: { width: 24, band: 'filled', label: 'TX FAULT', keyed: false },
};

/** The fail-closed rail: doubt about RF outranks a tidy silhouette. */
const DOUBT_RAIL: RailState = { width: 16, band: 'outlined', label: 'TX?', keyed: false };

const SLAB_EDGE: Record<KeyTreatment, FieldlineSlab['edgeStyle']> = {
  idle: 'solid', pending: 'solid', keyed: 'solid', fault: 'dashed', blocked: 'dotted',
};
const KEY_TREATMENT: Record<string, KeyTreatment> = {
  idle: 'idle', pending: 'pending', keyed: 'keyed', releasing: 'keyed', failed: 'fault',
};

const stringField = (fields: RendererViewModel['fields'], key: string): string => {
  const value = fields[key];
  return typeof value === 'string' ? value : '';
};

export function renderStateFeedback(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): FieldlineStateFeedback {
  const rf = stringField(viewModel.fields, 'rf');
  const session = stringField(viewModel.fields, 'session');
  const fault = stringField(viewModel.fields, 'fault');
  const blocked = viewModel.fields.keyBlocked === true;

  const known = SESSION_RAIL[session];
  // An unrecognised session, or an idle session the authority cannot vouch for
  // as genuinely receiving, both land on the doubt rail.
  const state = known && (session !== 'idle' || rf === 'receiving') ? known : DOUBT_RAIL;
  const failed = session === 'failed';

  const tone = state.label === null ? tokens.rx.idle
    : state.keyed || failed ? tokens.tx.active
      : tokens.tx.tuning;
  // Treatment follows the RESOLVED state, not the raw session: a slab that
  // still looks idle under a doubt rail is the contradiction R9 exists to
  // prevent.
  //
  // F3/N3: the inert treatment applies ONLY where the session has nothing
  // louder to say. `keyBlocked` is true in every TX-adjacent session — you
  // cannot key what is already keyed — so an unconditional `blocked ?
  // 'blocked' : …` reported a dotted inert slab while the rail was flooded.
  // `stylesheet.test.ts` pins the opposite for the CSS half ("keyed outranks
  // the inert treatment on SPECIFICITY"); the descriptor now agrees with it.
  const held = KEY_TREATMENT[session];
  const treatment: KeyTreatment = held !== undefined && held !== 'idle' ? held
    : blocked ? 'blocked'
      : state === DOUBT_RAIL ? 'pending' : 'idle';

  return {
    kind: 'fieldline-state-feedback',
    rail: { widthPx: state.width, tone, edge: 'inline-start', fullBleed: true },
    band: {
      treatment: state.band,
      // The fault code rides IN the band rather than in a separate micro-label:
      // takeover means the operator does not have to look anywhere else.
      text: failed && fault ? `TX FAULT: ${fault}` : state.label,
      tone,
      textTone: state.band === 'absent' ? null
        : state.band === 'filled' ? FIELDLINE_KNOCKOUT : tone,
    },
    numeralTone: state.keyed ? 'tx-target' : 'primary',
    // The meter re-zones with the transmitter, not with the request to key.
    meterScaleLabel: state.keyed ? 'PO' : 'S',
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
