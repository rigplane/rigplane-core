/**
 * MOR-1073 — the `studioline` TX renderer slice: the full-bleed top rail and
 * the intentional TX-key treatment (handed-over obligation 3, N2).
 *
 * SAFETY INVARIANT R9 is the spine of this file. The renderer DISPLAYS TX
 * state; it never decides it. Every input below is produced by the real
 * MOR-1064 vocabulary (`rfState`/`txSessionState` over a `TxAuthoritySnapshot`)
 * rather than hand-written strings, so a renderer that quietly grew a second
 * opinion — or started reading a raw `ptt` — fails here rather than in a
 * shack. The two pins that make that concrete are "a ptt field cannot move
 * the rail" and "an unrecognised state fails closed to keyed-looking doubt".
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import {
  rfState, txSessionState, type TxAuthoritySnapshot,
} from '../../../../semantic/rx-tx-surface';
import { STUDIOLINE_PALETTE, STUDIOLINE_TOKENS } from '../tokens';
import { renderStateFeedback, type StudiolineStateFeedback } from '../state-feedback-renderer';

const authority = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null, ...over,
});

/** Exactly the projection a consumer performs: App-authority conclusions in, flat primitives out. */
const fromAuthority = (
  tx: TxAuthoritySnapshot, extra: Record<string, string | number | boolean | null> = {},
): StudiolineStateFeedback => renderStateFeedback({
  kind: 'state-feedback',
  fields: { rf: rfState(tx), session: txSessionState(tx), fault: tx.fault, keyBlocked: false, ...extra },
}, STUDIOLINE_TOKENS);

const RX = authority();
const PENDING = authority({ phase: 'key-confirm-pending', intent: 'latched', mayOwnKey: true, txRisk: 'uncertain' });
const TX = authority({ phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const RELEASING = authority({ phase: 'releasing', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const FAULT = authority({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' });
const UNKNOWN_RF = authority({ radioTx: 'unknown' });

describe('the top rail is the always-visible TX carrier (MOR-977 §2.3)', () => {
  it.each([
    ['RX', RX], ['pending', PENDING], ['TX', TX], ['releasing', RELEASING], ['fault', FAULT], ['rf-unknown', UNKNOWN_RF],
  ])('%s renders a full-bleed rail — it can never be cropped out', (_name, tx) => {
    expect(fromAuthority(tx).rail.fullBleed).toBe(true);
  });

  it('RX is the quiet 1px baseline', () => {
    const { rail, meterScaleLabel, numeralTone } = fromAuthority(RX);
    expect(rail).toMatchObject({ thicknessPx: 1, tone: STUDIOLINE_TOKENS.rx.idle, label: null });
    expect(meterScaleLabel).toBe('S');
    expect(numeralTone).toBe('primary');
  });

  it('pending thickens the rail to 3px in the desaturated intermediate, before any RF is out', () => {
    const { rail, meterScaleLabel } = fromAuthority(PENDING);
    expect(rail).toMatchObject({ thicknessPx: 3, tone: STUDIOLINE_TOKENS.tx.tuning, label: 'KEYING' });
    expect(meterScaleLabel).toBe('S');
  });

  it('TX floods the rail and re-zones the meter to power', () => {
    const { rail, meterScaleLabel, numeralTone } = fromAuthority(TX);
    expect(rail).toMatchObject({ thicknessPx: 3, tone: STUDIOLINE_TOKENS.tx.active, label: 'TX' });
    expect(meterScaleLabel).toBe('PO');
    expect(numeralTone).toBe('tx-target');
  });

  it('releasing steps the rail back to 2px without pretending RX has resumed', () => {
    const { rail, meterScaleLabel } = fromAuthority(RELEASING);
    expect(rail).toMatchObject({ thicknessPx: 2, label: 'UNKEYING' });
    expect(meterScaleLabel).toBe('PO');
  });

  it('a fault pins its code to the rail — an unheard-of code still shows', () => {
    expect(fromAuthority(FAULT).rail).toMatchObject({ thicknessPx: 3, tone: STUDIOLINE_TOKENS.tx.active, label: 'TX FAULT' });
    expect(fromAuthority(FAULT).micro).toBe('TX FAULT: audio-failed');
    expect(fromAuthority(authority({ phase: 'failed', fault: 'no-such-code-4711' })).micro)
      .toBe('TX FAULT: no-such-code-4711');
  });

  it('unknown RF is a THIRD thing — never collapsed into RX (MOR-977 §1.2.1)', () => {
    const unknown = fromAuthority(UNKNOWN_RF);
    expect(unknown.rail).toMatchObject({ thicknessPx: 3, tone: STUDIOLINE_TOKENS.tx.tuning, label: 'TX?' });
    expect(unknown.rail).not.toEqual(fromAuthority(RX).rail);
  });
});

describe('state survives forced-colors and colour-vision deficiency', () => {
  // MOR-977 §4.4 required mitigation: rail thickness AND text, never colour alone.
  it('no two states are distinguished by colour alone', () => {
    const states = [RX, PENDING, TX, RELEASING, FAULT, UNKNOWN_RF].map((tx) => fromAuthority(tx));
    const colourless = states.map((s) => `${s.rail.thicknessPx}/${s.rail.label ?? 'RX'}`);
    expect(new Set(colourless).size).toBe(states.length);
  });

  it('uses only the declared 1/2/3px rail steps', () => {
    for (const tx of [RX, PENDING, TX, RELEASING, FAULT, UNKNOWN_RF]) {
      expect([1, 2, 3]).toContain(fromAuthority(tx).rail.thicknessPx);
    }
  });
});

describe('the TX key gets an intentional treatment, not a UA default (N2)', () => {
  it.each([
    ['idle', RX, 'idle'], ['pending', PENDING, 'pending'],
    ['keyed', TX, 'keyed'], ['fault', FAULT, 'fault'],
  ])('%s renders the "%s" key treatment', (_name, tx, treatment) => {
    expect(fromAuthority(tx).key.treatment).toBe(treatment);
  });

  it('is a pill — the one radius studioline allows (MOR-977 §2.3 geometry)', () => {
    expect(fromAuthority(RX).key.radius).toBe('999px');
    expect(STUDIOLINE_TOKENS.geometry.radius).toBe('0px');
  });

  it('fills solid while keyed, and stays outlined otherwise', () => {
    expect(fromAuthority(TX).key).toMatchObject({ fill: STUDIOLINE_TOKENS.tx.active, filled: true });
    expect(fromAuthority(RX).key.filled).toBe(false);
  });

  it('a blocked key is rendered inert-but-PRESENT, never absent (MOR-977 §1.2.3)', () => {
    const blocked = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'receiving', session: 'idle', fault: null, keyBlocked: true },
    }, STUDIOLINE_TOKENS);
    expect(blocked.key).toMatchObject({ treatment: 'blocked', present: true, filled: false });
    expect(blocked.key.tone).toBe(STUDIOLINE_PALETTE.inert);
  });

  it('every treatment is present — no state hides the control', () => {
    for (const tx of [RX, PENDING, TX, RELEASING, FAULT, UNKNOWN_RF]) {
      expect(fromAuthority(tx).key.present).toBe(true);
    }
  });

  it('carries the mandatory focus ring rather than re-suppressing it (MOR-977 §1.2.5)', () => {
    expect(fromAuthority(RX).key.focusRing).toBe(STUDIOLINE_TOKENS.focusRing);
    expect(fromAuthority(RX).key.focusRing).not.toMatch(/none/);
  });
});

describe('R9 — TX truth comes from App authority, never from the renderer', () => {
  it('a raw ptt field cannot move the rail: authority says RX, so it renders RX', () => {
    const withPtt = fromAuthority(RX, { ptt: true, radioStatePtt: true });
    expect(withPtt).toEqual(fromAuthority(RX));
    expect(withPtt.rail.label).toBeNull();
  });

  it('a raw ptt field cannot clear a TX rail either', () => {
    expect(fromAuthority(TX, { ptt: false }).rail).toEqual(fromAuthority(TX).rail);
  });

  it('an unrecognised state fails CLOSED — doubt, never a quiet RX rail', () => {
    const bogus = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'who-knows', session: 'brand-new-phase', fault: null, keyBlocked: false },
    }, STUDIOLINE_TOKENS);
    expect(bogus.rail.thicknessPx).toBe(3);
    expect(bogus.rail.label).toBe('TX?');
    expect(bogus.key.treatment).not.toBe('idle');
  });

  it('is reachable only through the structural gate', () => {
    const smuggled = { kind: 'state-feedback', fields: { rf: 'receiving' }, capabilities: { tx: true } };
    expect(() => invokeRenderer(renderStateFeedback, smuggled, STUDIOLINE_TOKENS)).toThrow(RendererInputError);
  });
});
