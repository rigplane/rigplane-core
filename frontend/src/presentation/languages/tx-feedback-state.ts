/**
 * MOR-2031 — the design-language-agnostic half of TX state feedback.
 *
 * The `KEY_TREATMENT` ordering below — the F3/N3 fix that makes `keyBlocked`
 * lose to a louder session — was hand-duplicated in a single commit,
 * MOR-1275.
 *
 * Lives under `presentation/languages/`, not `semantic/`: the renderers
 * that consume it are `presentation/` modules, and the v3 ADR's one-way
 * dependency direction only lets `presentation/` depend on `semantic/`
 * type-only (see `projection.ts`, pinned by `projection.test.ts`'s "imports
 * only types" check) — never through a runtime value import. The one type
 * this module still needs from `semantic/rx-tx-surface` (`TxSessionState`,
 * for `isTxSessionState` below) is imported that way for exactly that
 * reason: erased at build, so it carries no runtime coupling back to
 * `semantic/`.
 *
 * `TxFeedbackState.rail` names the session BUCKET only, never a magnitude:
 * fieldline and studioline rank the six buckets differently (fieldline's
 * rail floods `pending` to a MID-tier 16px; studioline's rail thickens
 * `pending` to its TOP-tier 3px — both pinned in each language's own
 * `state-feedback-renderer.test.ts`), so a shared ordinal could not serve
 * both. Each language keeps its own `Record<TxFeedbackRail, …>`, indexed
 * by the bucket this function resolves.
 *
 * SAFETY INVARIANT R9, identical to `semantic/rx-tx-surface.ts`'s `rfState`/
 * `txSessionState`: this function DISPLAYS the App TX authority's
 * conclusions; it never forms its own. It takes a named struct of exactly
 * four primitives — never a whole `RendererViewModel` — so a fifth field
 * cannot be smuggled into a TX decision without changing this signature.
 * Unrecognised values fail CLOSED to the `'doubt'` rail, never to `'idle'`:
 * "nothing is happening" is the dangerous claim.
 */
import type { DesignLanguageTokens } from './contract';
import type { TxSessionState } from '../../semantic/rx-tx-surface';

export type TxFeedbackRail = 'idle' | 'pending' | 'keyed' | 'releasing' | 'failed' | 'doubt';
export type TxKeyTreatment = 'idle' | 'pending' | 'keyed' | 'fault' | 'blocked';
/** Symbolic only — NOT a token value; `toneFor` below maps it onto a language's palette. */
export type TxFeedbackTone = 'rx-idle' | 'tx-active' | 'tx-tuning';

/**
 * The symbolic tone, resolved against one language's palette. Extracted
 * once a third `state-feedback-renderer.ts` carried a literal copy of this
 * three-way choice (MOR-2149 added segmentline's). Each language still owns
 * its own palette; only the choice between the three token slots is shared.
 */
export const toneFor = (tone: TxFeedbackTone, tokens: DesignLanguageTokens): string =>
  tone === 'rx-idle' ? tokens.rx.idle
    : tone === 'tx-active' ? tokens.tx.active
      : tokens.tx.tuning;

export interface TxFeedbackState {
  readonly rail: TxFeedbackRail;
  readonly treatment: TxKeyTreatment;
  /** Transmitter engaged: true for exactly the `keyed` and `releasing` rails. */
  readonly keyed: boolean;
  /** The resolved RX baseline (`rail === 'idle'`) — the only silent state. */
  readonly quiet: boolean;
  readonly tone: TxFeedbackTone;
  readonly failed: boolean;
  /** `` `TX FAULT: ${fault}` `` when `failed` and `fault` is non-empty, else `null`. */
  readonly faultText: string | null;
}

/**
 * Session → key treatment, unconditional on `keyBlocked`. Keyed by the RAW
 * session, not the resolved rail: an unrecognised session falls through to
 * the fallback in `resolveTxFeedbackState` below — UNLESS it names an
 * `Object.prototype` member (`constructor`, `toString`, `valueOf`,
 * `hasOwnProperty`, `__proto__`), which plain `[]` access finds on the
 * prototype chain instead, so `treatment` escapes as that member rather
 * than falling through. Unreachable in production: `RxTxSurface.svelte`
 * only ever supplies a real `TxSessionState`.
 *
 * MOR-2036: `satisfies Record<TxSessionState, TxKeyTreatment>` pins these
 * keys to EXACTLY `TxSessionState`'s five members. Before this pin the
 * `Record<string, TxKeyTreatment>` annotation alone let either drift through
 * silently — a sixth, non-session key (excess property) or a missing one
 * (required property) both now fail `npm run check`; `isTxSessionState`
 * below reuses these keys rather than re-enumerating them, so the pin
 * covers that reuse too.
 */
const KEY_TREATMENT: Record<string, TxKeyTreatment> = {
  idle: 'idle', pending: 'pending', keyed: 'keyed', releasing: 'keyed', failed: 'fault',
} satisfies Record<TxSessionState, TxKeyTreatment>;

/** True for exactly `TxSessionState`'s five members — reuses `KEY_TREATMENT`'s keys, which the `satisfies` clause above keeps pinned to that exact set (MOR-2036). */
const isTxSessionState = (value: string): value is TxSessionState =>
  Object.prototype.hasOwnProperty.call(KEY_TREATMENT, value);

/**
 * Reads a flat renderer field as a string, defaulting an absent or
 * non-string value to `''`.
 */
export const stringField = (
  fields: Readonly<Record<string, string | number | boolean | null>>, key: string,
): string => {
  const value = fields[key];
  return typeof value === 'string' ? value : '';
};

export function resolveTxFeedbackState(fields: {
  rf: string; session: string; fault: string; keyBlocked: boolean;
}): TxFeedbackState {
  const { rf, session, fault, keyBlocked } = fields;
  // An unrecognised session, or an idle session the authority cannot vouch
  // for as genuinely receiving, both land on the doubt rail.
  const rail: TxFeedbackRail = isTxSessionState(session) && (session !== 'idle' || rf === 'receiving')
    ? session
    : 'doubt';
  const failed = session === 'failed';
  const keyed = rail === 'keyed' || rail === 'releasing';
  const quiet = rail === 'idle';
  const tone: TxFeedbackTone = quiet ? 'rx-idle' : keyed || failed ? 'tx-active' : 'tx-tuning';

  // The FALLBACK below (once `KEY_TREATMENT` has nothing louder to say)
  // follows the RESOLVED rail, not the raw session: a control that still
  // looks idle under a doubt rail is the contradiction R9 exists to
  // prevent. `keyBlocked` is checked only in that same fallback — see
  // `KEY_TREATMENT`'s own comment for why the raw-session lookup below
  // wins whenever it yields anything other than `'idle'`.
  const held = KEY_TREATMENT[session];
  const treatment: TxKeyTreatment = held !== undefined && held !== 'idle' ? held
    : keyBlocked ? 'blocked'
      : rail === 'doubt' ? 'pending' : 'idle';

  return {
    rail, treatment, keyed, quiet, tone, failed,
    faultText: failed && fault ? `TX FAULT: ${fault}` : null,
  };
}
