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
 * `ptt`, and because it names every field it consumes, one cannot be smuggled
 * in. Unrecognised values fail CLOSED to the doubt rail, never to the quiet
 * RX one: "nothing is happening" is the dangerous claim.
 *
 * Colour is never the sole channel — every state carries a distinct rail
 * THICKNESS and a distinct text label, so the state survives forced-colors
 * and colour-vision deficiency (MOR-977 §4.4 required mitigation).
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';
import { STUDIOLINE_PALETTE } from './tokens';

export type KeyTreatment = 'idle' | 'pending' | 'keyed' | 'fault' | 'blocked';

export interface StudiolineRail {
  readonly thicknessPx: 1 | 2 | 3;
  readonly tone: string;
  readonly label: string | null;
  /** Non-negotiable: a full-bleed rail cannot be scrolled, cropped, or collapsed out of any layout. */
  readonly fullBleed: true;
}

export interface StudiolineKey {
  readonly treatment: KeyTreatment;
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

/** Rail step + label per session state. `null` label is the RX baseline — the only silent state. */
const SESSION_RAIL: Record<string, { thickness: 1 | 2 | 3; label: string | null; keyed: boolean }> = {
  idle: { thickness: 1, label: null, keyed: false },
  pending: { thickness: 3, label: 'KEYING', keyed: false },
  keyed: { thickness: 3, label: 'TX', keyed: true },
  releasing: { thickness: 2, label: 'UNKEYING', keyed: true },
  failed: { thickness: 3, label: 'TX FAULT', keyed: false },
};

/** The fail-closed rail: doubt about RF outranks a tidy silhouette. */
const DOUBT_RAIL = { thickness: 3, label: 'TX?', keyed: false } as const;

const KEY_TREATMENT: Record<string, KeyTreatment> = {
  idle: 'idle', pending: 'pending', keyed: 'keyed', releasing: 'keyed', failed: 'fault',
};

const stringField = (fields: RendererViewModel['fields'], key: string): string => {
  const value = fields[key];
  return typeof value === 'string' ? value : '';
};

export function renderStateFeedback(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): StudiolineStateFeedback {
  const rf = stringField(viewModel.fields, 'rf');
  const session = stringField(viewModel.fields, 'session');
  const fault = stringField(viewModel.fields, 'fault');
  const blocked = viewModel.fields.keyBlocked === true;

  const known = SESSION_RAIL[session];
  // An unrecognised session, or an idle session the authority cannot vouch
  // for as genuinely receiving, both land on the doubt rail.
  const state = known && (session !== 'idle' || rf === 'receiving') ? known : DOUBT_RAIL;
  const failed = session === 'failed';

  const tone = state.label === null ? tokens.rx.idle
    : state.keyed || failed ? tokens.tx.active
      : tokens.tx.tuning;
  // Treatment follows the RESOLVED state, not the raw session: a key that
  // still looks idle under a doubt rail is the contradiction R9 exists to
  // prevent.
  const treatment: KeyTreatment = blocked ? 'blocked'
    : state === DOUBT_RAIL ? 'pending'
      : (KEY_TREATMENT[session] ?? 'pending');

  return {
    kind: 'studioline-state-feedback',
    rail: { thicknessPx: state.thickness, tone, label: state.label, fullBleed: true },
    numeralTone: state.keyed ? 'tx-target' : 'primary',
    // The meter re-zones with the transmitter, not with the request to key.
    meterScaleLabel: state.keyed ? 'PO' : 'S',
    micro: failed && fault ? `TX FAULT: ${fault}` : null,
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
