/**
 * MOR-2149 — the `segmentline` TX renderer slice: the glass perimeter (a
 * bezel tone plus an inward glow) as the always-visible TX carrier, mirroring
 * `studioline/__tests__/state-feedback-renderer.test.ts`'s shape.
 *
 * SAFETY INVARIANT R9 is the spine of this file, exactly as it is of the two
 * sibling renderers'. Every input below is produced by the real MOR-1064
 * vocabulary (`rfState`/`txSessionState` over a `TxAuthoritySnapshot`) rather
 * than by hand-written strings, so a renderer that quietly grew a second
 * opinion — or started reading a raw `ptt`, or `channel`/`phase`/`treatment`
 * fields no real caller supplies — fails here rather than in a shack.
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import {
  rfState, txSessionState, type TxAuthoritySnapshot,
} from '../../../../semantic/rx-tx-surface';
import { SEGMENTLINE_INK, SEGMENTLINE_PALETTE, SEGMENTLINE_TOKENS } from '../tokens';
import { renderStateFeedback, type SegmentlineStateFeedback } from '../state-feedback-renderer';

const authority = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, ...over,
});

/** Exactly the projection the real call site (`RxTxSurface.svelte`) performs: server conclusions in, flat primitives out. */
const fromAuthority = (
  tx: TxAuthoritySnapshot, extra: Record<string, string | number | boolean | null> = {},
): SegmentlineStateFeedback => renderStateFeedback({
  kind: 'state-feedback',
  fields: { rf: rfState(tx), session: txSessionState(tx), fault: tx.fault, keyBlocked: false, ...extra },
}, SEGMENTLINE_TOKENS);

const RX = authority();
const PENDING = authority({ phase: 'key-confirm-pending', intent: 'latched', txRisk: 'uncertain' });
const TX = authority({ phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on' });
const RELEASING = authority({ phase: 'releasing', radioTx: 'on', txRisk: 'confirmed-on' });
const FAULT = authority({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' });
const UNKNOWN_RF = authority({ radioTx: 'unknown' });
const ALL = [RX, PENDING, TX, RELEASING, FAULT, UNKNOWN_RF];

describe('the perimeter is the always-visible TX carrier (segmentline\'s own grammar)', () => {
  it('RX is quiet: unlit, no label, RX tone', () => {
    const { perimeter, meterScaleLabel, numeralTone } = fromAuthority(RX);
    expect(perimeter).toMatchObject({ lit: false, label: null, tone: SEGMENTLINE_TOKENS.rx.idle, insetShadow: '' });
    expect(meterScaleLabel).toBe('S');
    expect(numeralTone).toBe('primary');
  });

  it('pending lights the perimeter before any RF is out', () => {
    const { perimeter, meterScaleLabel } = fromAuthority(PENDING);
    expect(perimeter).toMatchObject({ lit: true, label: 'KEYING', tone: SEGMENTLINE_TOKENS.tx.tuning });
    expect(perimeter.insetShadow).not.toBe('');
    expect(meterScaleLabel).toBe('S');
  });

  it('TX lights the perimeter and re-zones the meter to power', () => {
    const { perimeter, meterScaleLabel, numeralTone } = fromAuthority(TX);
    expect(perimeter).toMatchObject({ lit: true, label: 'TX', tone: SEGMENTLINE_TOKENS.tx.active });
    expect(meterScaleLabel).toBe('PO');
    expect(numeralTone).toBe('tx-target');
  });

  it('releasing stays lit without pretending RX has resumed', () => {
    const { perimeter, meterScaleLabel } = fromAuthority(RELEASING);
    expect(perimeter).toMatchObject({ lit: true, label: 'UNKEYING' });
    expect(meterScaleLabel).toBe('PO');
  });

  it('a fault reports its code via `micro` — an unheard-of code still shows', () => {
    expect(fromAuthority(FAULT).perimeter).toMatchObject({ lit: true, label: 'TX FAULT' });
    expect(fromAuthority(FAULT).micro).toBe('TX FAULT: audio-failed');
    expect(fromAuthority(authority({ phase: 'failed', fault: 'no-such-code-4711' })).micro)
      .toBe('TX FAULT: no-such-code-4711');
  });

  it('unknown RF is a THIRD thing — never collapsed into RX (MOR-977 §1.2.1)', () => {
    const unknown = fromAuthority(UNKNOWN_RF);
    expect(unknown.perimeter).toMatchObject({ lit: true, label: 'TX?' });
    expect(unknown.perimeter).not.toEqual(fromAuthority(RX).perimeter);
  });
});

describe('state survives forced-colors and colour-vision deficiency', () => {
  it('no two states share the same lit/label pair — colour is never the only channel', () => {
    const colourless = ALL.map((tx) => {
      const { perimeter } = fromAuthority(tx);
      return `${perimeter.lit}/${perimeter.label ?? 'RX'}`;
    });
    expect(new Set(colourless).size).toBe(ALL.length);
  });
});

describe('the TX cell gets an intentional treatment (segmentline never fills a control)', () => {
  it.each([
    ['idle', RX, 'idle'], ['pending', PENDING, 'pending'],
    ['keyed', TX, 'keyed'], ['fault', FAULT, 'fault'],
  ])('%s renders the "%s" cell treatment', (_name, tx, treatment) => {
    expect(fromAuthority(tx).cell.treatment).toBe(treatment);
  });

  it('is `active` only while actually keyed — pending is engaged-intent, not engaged-yet', () => {
    expect(fromAuthority(TX).cell.active).toBe(true);
    expect(fromAuthority(RELEASING).cell.active).toBe(true);
    expect(fromAuthority(PENDING).cell.active).toBe(false);
    expect(fromAuthority(RX).cell.active).toBe(false);
  });

  it('every treatment is present — no state hides the control', () => {
    for (const tx of ALL) expect(fromAuthority(tx).cell.present).toBe(true);
  });

  it('carries the mandatory focus ring rather than re-suppressing it (MOR-977 §1.2.5)', () => {
    expect(fromAuthority(RX).cell.focusRing).toBe(SEGMENTLINE_TOKENS.focusRing);
    expect(fromAuthority(RX).cell.focusRing).not.toMatch(/none/);
  });
});

describe('the cell tone pins to the exact declared ink/palette entries, not a scaled guess', () => {
  // keyed and blocked resolve to two DIFFERENT declared constants — a swap
  // between them (or a drift of either constant by a shade) fails here.
  it('keyed is the txMark hot ink — the SAME constant segmentline.css\'s hot cell rule uses', () => {
    expect(fromAuthority(TX).cell.tone).toBe(SEGMENTLINE_PALETTE.txMark);
  });

  it('a blocked cell is rendered inert-but-PRESENT, at the faintest ink (MOR-977 §1.2.3)', () => {
    const blocked = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'receiving', session: 'idle', fault: null, keyBlocked: true },
    }, SEGMENTLINE_TOKENS);
    expect(blocked.cell).toMatchObject({ treatment: 'blocked', present: true, active: false });
    expect(blocked.cell.tone).toBe(SEGMENTLINE_INK.ghost);
  });

  it('idle and pending share the dim baseline ink, distinct from both keyed and blocked', () => {
    expect(fromAuthority(RX).cell.tone).toBe(SEGMENTLINE_INK.soft);
    expect(fromAuthority(PENDING).cell.tone).toBe(SEGMENTLINE_INK.soft);
    expect(fromAuthority(RX).cell.tone).not.toBe(SEGMENTLINE_PALETTE.txMark);
    expect(fromAuthority(RX).cell.tone).not.toBe(SEGMENTLINE_INK.ghost);
  });

  it('a cell disabled BECAUSE it is already keyed keeps the KEYED treatment (F3/N3)', () => {
    const withCell = (over: Record<string, string | number | boolean | null>) =>
      renderStateFeedback({
        kind: 'state-feedback',
        fields: { rf: 'receiving', session: 'idle', fault: null, keyBlocked: true, ...over },
      }, SEGMENTLINE_TOKENS).cell;

    expect(withCell({ rf: 'transmitting', session: 'keyed' })).toMatchObject({ treatment: 'keyed', active: true });
    expect(withCell({ rf: 'transmitting', session: 'releasing' }).treatment).toBe('keyed');
    // The one session where "you may not key" IS the whole story keeps it.
    expect(withCell({}).treatment).toBe('blocked');
  });
});

describe('perimeter.transition follows the token motion duration, not a private string', () => {
  it('pins the exact string built from SEGMENTLINE_TOKENS.motion.durationMs (90ms)', () => {
    expect(SEGMENTLINE_TOKENS.motion.durationMs).toBe(90);
    expect(fromAuthority(TX).perimeter.transition).toBe('border-color 90ms linear, box-shadow 90ms linear');
  });

  // A token set whose duration differs from 90 — a literal replacement
  // (e.g. a mutated constant string) cannot happen to match both this and
  // the 90ms pin above.
  it('changes when the token duration changes — the read is load-bearing', () => {
    const otherTokens = { ...SEGMENTLINE_TOKENS, motion: { ...SEGMENTLINE_TOKENS.motion, durationMs: 250 } };
    const r = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'transmitting', session: 'keyed', fault: null, keyBlocked: false },
    }, otherTokens);
    expect(r.perimeter.transition).toBe('border-color 250ms linear, box-shadow 250ms linear');
  });
});

describe('R9 — TX truth comes from the server projection, never from the renderer', () => {
  it('a raw ptt field cannot light the perimeter: authority says RX, so it renders RX', () => {
    const withPtt = fromAuthority(RX, { ptt: true, radioStatePtt: true });
    expect(withPtt).toEqual(fromAuthority(RX));
    expect(withPtt.perimeter.lit).toBe(false);
  });

  // The defect an earlier draft of this file carried: it read `channel`,
  // `phase` and `treatment` fields directly, which `RxTxSurface.svelte` (the
  // real caller) never supplies — so it would have rendered every real input
  // as the unlit RX default regardless of the authority's actual state. This
  // pins the fix: those three field names are ignored, only rf/session/
  // fault/keyBlocked move the render.
  it('channel/phase/treatment fields cannot move the perimeter — only rf/session/fault/keyBlocked can', () => {
    const spoofed = fromAuthority(TX, { channel: 'rx', phase: 'idle', treatment: 'wash' });
    expect(spoofed).toEqual(fromAuthority(TX));
    expect(spoofed.perimeter.lit).toBe(true);
    expect(spoofed.perimeter.label).toBe('TX');
  });

  it('an unrecognised state fails CLOSED — doubt, never a quiet RX perimeter', () => {
    const bogus = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'who-knows', session: 'brand-new-phase', fault: null, keyBlocked: false },
    }, SEGMENTLINE_TOKENS);
    expect(bogus.perimeter).toMatchObject({ lit: true, label: 'TX?' });
    expect(bogus.cell.treatment).not.toBe('idle');
  });

  it('is reachable only through the structural gate', () => {
    const smuggled = { kind: 'state-feedback', fields: { rf: 'receiving' }, capabilities: { tx: true } };
    expect(() => invokeRenderer(renderStateFeedback, smuggled, SEGMENTLINE_TOKENS)).toThrow(RendererInputError);
  });
});
