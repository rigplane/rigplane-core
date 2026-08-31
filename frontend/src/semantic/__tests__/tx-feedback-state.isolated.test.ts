/**
 * MOR-2031 — `resolveTxFeedbackState`, the ONE fail-closed decision both
 * `fieldline` and `studioline` state-feedback renderers now defer to instead
 * of each carrying its own copy. Before this ticket the two renderers were
 * NOT byte-identical (159 vs 124 lines — fieldline alone owns
 * `BandTreatment`, `FieldlineBand`, `SLAB_EDGE` and a knockout branch); what
 * was identical, character for character, was the DECISION itself — the
 * `renderStateFeedback` header down through the `held`/`treatment` ternary.
 * This file pins that one shared decision once, at its source, rather than
 * per language.
 *
 * Every fixture below, except the deliberately unrecognised ones (a
 * hand-written `{ rf: 'who-knows', session: 'brand-new-phase' }` literal in
 * each `describe` block), is produced by the real MOR-1064 vocabulary
 * (`rfState`/`txSessionState` over a `TxAuthoritySnapshot`), the same way
 * both renderers' own test files build theirs.
 *
 * ISOLATED POOL: the last `describe` block registers a manifest into the
 * shared design-language registry (`presentation/languages/contract.ts`) —
 * global module state that must not leak into sibling test files sharing a
 * worker under `isolate: false`. `vite.config.ts`'s pool-membership
 * convention routes any `*.isolated.test.ts` file to the isolated project,
 * hence this file's name.
 */
import { describe, expect, it, vi } from 'vitest';
import { rfState, txSessionState, type TxAuthoritySnapshot } from '../rx-tx-surface';
import {
  resolveTxFeedbackState, stringField, type TxFeedbackRail, type TxFeedbackState,
} from '../../presentation/languages/tx-feedback-state';
import * as txFeedbackState from '../../presentation/languages/tx-feedback-state';
import {
  registerDesignLanguage, resolveRenderer, type RendererViewModel,
} from '../../presentation/languages/contract';
import { validManifest } from '../../presentation/languages/__tests__/fixtures';
import { renderStateFeedback as renderFieldlineFeedback } from '../../presentation/languages/fieldline/state-feedback-renderer';
import { FIELDLINE_TOKENS } from '../../presentation/languages/fieldline/tokens';
import { renderStateFeedback as renderStudiolineFeedback } from '../../presentation/languages/studioline/state-feedback-renderer';
import { STUDIOLINE_TOKENS } from '../../presentation/languages/studioline/tokens';

const authority = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null, ...over,
});

/** The same four primitives a renderer reads off its `RendererViewModel`, via `stringField`. */
const fieldsOf = (
  tx: TxAuthoritySnapshot, keyBlocked = false,
): { rf: string; session: string; fault: string; keyBlocked: boolean } => ({
  rf: rfState(tx), session: txSessionState(tx), fault: tx.fault ?? '', keyBlocked,
});

const RX = authority();
const PENDING = authority({ phase: 'key-confirm-pending', intent: 'latched', mayOwnKey: true, txRisk: 'uncertain' });
const TX = authority({ phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const RELEASING = authority({ phase: 'releasing', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const FAULT = authority({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' });
const UNKNOWN_RF = authority({ radioTx: 'unknown' });

describe('resolveTxFeedbackState — the shared session-bucket decision (MOR-2031)', () => {
  it.each([
    ['RX', RX, 'idle'], ['pending', PENDING, 'pending'], ['keyed', TX, 'keyed'],
    ['releasing', RELEASING, 'releasing'], ['fault', FAULT, 'failed'],
  ] as const)('%s resolves to the %s rail', (_name, tx, rail) => {
    expect(resolveTxFeedbackState(fieldsOf(tx)).rail).toBe(rail);
  });

  it('an RF-unknown idle session — the authority cannot vouch for RX — lands on doubt, not idle', () => {
    expect(resolveTxFeedbackState(fieldsOf(UNKNOWN_RF)).rail).toBe('doubt');
  });

  it('an entirely unrecognised session also lands on doubt (fail CLOSED, never to idle)', () => {
    const state = resolveTxFeedbackState({ rf: 'who-knows', session: 'brand-new-phase', fault: '', keyBlocked: false });
    expect(state.rail).toBe('doubt');
    expect(state.treatment).not.toBe('idle');
  });

  it('keyed is true for exactly the keyed and releasing rails', () => {
    const cases: ReadonlyArray<readonly [string, TxAuthoritySnapshot]> = [
      ['RX', RX], ['pending', PENDING], ['keyed', TX], ['releasing', RELEASING], ['fault', FAULT], ['rf-unknown', UNKNOWN_RF],
    ];
    const keyedness = Object.fromEntries(cases.map(([name, tx]) => [name, resolveTxFeedbackState(fieldsOf(tx)).keyed]));
    expect(keyedness).toEqual({
      RX: false, pending: false, keyed: true, releasing: true, fault: false, 'rf-unknown': false,
    });
  });

  it('quiet is true only for the idle rail — the sole silent state', () => {
    expect(resolveTxFeedbackState(fieldsOf(RX)).quiet).toBe(true);
    for (const tx of [PENDING, TX, RELEASING, FAULT, UNKNOWN_RF]) {
      expect(resolveTxFeedbackState(fieldsOf(tx)).quiet).toBe(false);
    }
  });

  it('tone is rx-idle only at the quiet baseline, tx-active while keyed or failed, tx-tuning otherwise', () => {
    expect(resolveTxFeedbackState(fieldsOf(RX)).tone).toBe('rx-idle');
    expect(resolveTxFeedbackState(fieldsOf(PENDING)).tone).toBe('tx-tuning');
    expect(resolveTxFeedbackState(fieldsOf(TX)).tone).toBe('tx-active');
    expect(resolveTxFeedbackState(fieldsOf(RELEASING)).tone).toBe('tx-active');
    expect(resolveTxFeedbackState(fieldsOf(FAULT)).tone).toBe('tx-active');
    expect(resolveTxFeedbackState(fieldsOf(UNKNOWN_RF)).tone).toBe('tx-tuning');
  });

  it('faultText is the shared "TX FAULT: <code>" template, built only when failed AND a code is present', () => {
    expect(resolveTxFeedbackState(fieldsOf(FAULT)).faultText).toBe('TX FAULT: audio-failed');
    // An unheard-of code still shows — this surface never swallows one.
    expect(resolveTxFeedbackState(fieldsOf(authority({ phase: 'failed', fault: 'no-such-code-4711' }))).faultText)
      .toBe('TX FAULT: no-such-code-4711');
    expect(resolveTxFeedbackState(fieldsOf(RX)).faultText).toBeNull();
  });

  it.each([
    ['idle', RX, 'idle'], ['pending', PENDING, 'pending'], ['keyed', TX, 'keyed'], ['fault', FAULT, 'fault'],
  ] as const)('%s treatment is %s', (_name, tx, treatment) => {
    expect(resolveTxFeedbackState(fieldsOf(tx)).treatment).toBe(treatment);
  });

  it('a control disabled BECAUSE it is already keyed keeps the keyed/pending/fault treatment (F3/N3, MOR-1275)', () => {
    // The contradiction the shared fix kills: `keyBlocked` is true in every
    // TX-adjacent session — you cannot key what is already keyed — so an
    // unconditional `blocked ? 'blocked' : …` would report an inert control
    // under a rail the RESOLVED state says is flooded.
    const withBlocked = (tx: TxAuthoritySnapshot): TxFeedbackState => resolveTxFeedbackState(fieldsOf(tx, true));
    expect(withBlocked(TX).treatment).toBe('keyed');
    expect(withBlocked(RELEASING).treatment).toBe('keyed');
    expect(withBlocked(PENDING).treatment).toBe('pending');
    expect(withBlocked(FAULT).treatment).toBe('fault');
    // The one session where "you may not key" IS the whole story keeps it.
    expect(withBlocked(RX).treatment).toBe('blocked');
  });
});

describe('a third design language inherits the decision without copying it (MOR-2031 acceptance)', () => {
  // This renderer contains NO fail-closed logic of its own — no session
  // table, no doubt fallback, no keyBlocked-vs-keyed ordering — only a
  // lookup table indexed by the SHARED resolver's `rail`. A fix to the
  // shared decision reaches this language for free.
  //
  // Output equality alone cannot show that: a hand-duplicated PRIVATE copy
  // of `resolveTxFeedbackState` (its own session table, its own doubt
  // fallback, its own keyBlocked ordering) reproduces the same
  // widths/keyed/treatment below and passes every `it.each` in this
  // `describe` block too. What such a copy cannot fake is the CALL itself
  // — see "thirdline actually calls" below, which spies on the shared
  // resolver instead of only comparing output.
  const THIRDLINE_RAIL_WIDTH: Record<TxFeedbackRail, number> = {
    idle: 2, pending: 5, keyed: 9, releasing: 5, failed: 9, doubt: 5,
  };

  function renderThirdlineFeedback(
    viewModel: RendererViewModel,
  ): { widthPx: number; keyed: boolean; treatment: string } {
    const resolved = resolveTxFeedbackState({
      rf: stringField(viewModel.fields, 'rf'),
      session: stringField(viewModel.fields, 'session'),
      fault: stringField(viewModel.fields, 'fault'),
      keyBlocked: viewModel.fields.keyBlocked === true,
    });
    return { widthPx: THIRDLINE_RAIL_WIDTH[resolved.rail], keyed: resolved.keyed, treatment: resolved.treatment };
  }

  const manifest = { ...validManifest(), id: 'thirdline', renderers: { stateFeedback: renderThirdlineFeedback } };
  registerDesignLanguage(manifest);
  const render = resolveRenderer(manifest, 'stateFeedback');
  const renderThirdline = (tx: TxAuthoritySnapshot): ReturnType<typeof renderThirdlineFeedback> =>
    render({ kind: 'state-feedback', fields: fieldsOf(tx) }, manifest.tokens) as ReturnType<typeof renderThirdlineFeedback>;

  it.each([
    ['RX', RX, 2, false, 'idle'],
    ['pending', PENDING, 5, false, 'pending'],
    ['keyed', TX, 9, true, 'keyed'],
    ['releasing', RELEASING, 5, true, 'keyed'],
    ['fault', FAULT, 9, false, 'fault'],
  ] as const)('%s: thirdline resolves the same decision as fieldline/studioline, from its own tiny table', (
    _name, tx, widthPx, keyed, treatment,
  ) => {
    expect(renderThirdline(tx)).toEqual({ widthPx, keyed, treatment });
  });

  it('an unrecognised session still fails CLOSED for thirdline, exactly as for the other two languages', () => {
    const out = render(
      { kind: 'state-feedback', fields: { rf: 'who-knows', session: 'brand-new-phase', fault: null, keyBlocked: false } },
      manifest.tokens,
    ) as ReturnType<typeof renderThirdlineFeedback>;
    expect(out.widthPx).toBe(THIRDLINE_RAIL_WIDTH.doubt);
    expect(out.treatment).not.toBe('idle');
  });

  it('thirdline actually calls the shared resolveTxFeedbackState (not a private copy of it)', () => {
    const spy = vi.spyOn(txFeedbackState, 'resolveTxFeedbackState');
    try {
      renderThirdline(TX);
      expect(spy).toHaveBeenCalledTimes(1);
    } finally {
      spy.mockRestore();
    }
  });
});

describe('fieldline and studioline both call the shared resolver, not a private copy (MOR-2031 acceptance)', () => {
  const viewModel: RendererViewModel = { kind: 'state-feedback', fields: fieldsOf(TX) };

  it('fieldline calls the shared resolveTxFeedbackState rather than a private copy of it', () => {
    const spy = vi.spyOn(txFeedbackState, 'resolveTxFeedbackState');
    try {
      renderFieldlineFeedback(viewModel, FIELDLINE_TOKENS);
      expect(spy).toHaveBeenCalledTimes(1);
    } finally {
      spy.mockRestore();
    }
  });

  it('studioline calls the shared resolveTxFeedbackState rather than a private copy of it', () => {
    const spy = vi.spyOn(txFeedbackState, 'resolveTxFeedbackState');
    try {
      renderStudiolineFeedback(viewModel, STUDIOLINE_TOKENS);
      expect(spy).toHaveBeenCalledTimes(1);
    } finally {
      spy.mockRestore();
    }
  });
});
