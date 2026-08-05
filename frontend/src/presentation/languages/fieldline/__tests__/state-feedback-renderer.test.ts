/**
 * MOR-1074 — the `fieldline` TX renderer slice: the flooding left rail, the
 * full-width band and the action slab (MOR-977 §2.2 "takeover").
 *
 * SAFETY INVARIANT R9 is the spine of this file, exactly as it is of
 * studioline's twin. The renderer DISPLAYS TX state; it never decides it. Every
 * input below is produced by the real MOR-1064 vocabulary
 * (`rfState`/`txSessionState` over a `TxAuthoritySnapshot`) rather than by
 * hand-written strings, so a renderer that quietly grew a second opinion — or
 * started reading a raw `ptt` — fails here rather than in a shack. A second
 * design language is exactly where that regression would be cheapest to
 * introduce and most expensive to notice, which is why the pins are duplicated
 * rather than assumed to be inherited.
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import {
  rfState, txSessionState, type TxAuthoritySnapshot,
} from '../../../../semantic/rx-tx-surface';
import { renderStateFeedback as renderStudioline } from '../../studioline/state-feedback-renderer';
import { STUDIOLINE_TOKENS } from '../../studioline/tokens';
import { FIELDLINE_KNOCKOUT, FIELDLINE_PALETTE, FIELDLINE_TOKENS } from '../tokens';
import { renderStateFeedback, type FieldlineStateFeedback } from '../state-feedback-renderer';

const authority = (over: Partial<TxAuthoritySnapshot> = {}): TxAuthoritySnapshot => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault: null, ...over,
});

/** Exactly the projection a consumer performs: App-authority conclusions in, flat primitives out. */
const fields = (tx: TxAuthoritySnapshot, extra: Record<string, string | number | boolean | null> = {}) => ({
  rf: rfState(tx), session: txSessionState(tx), fault: tx.fault, keyBlocked: false, ...extra,
});
const fromAuthority = (
  tx: TxAuthoritySnapshot, extra: Record<string, string | number | boolean | null> = {},
): FieldlineStateFeedback =>
  renderStateFeedback({ kind: 'state-feedback', fields: fields(tx, extra) }, FIELDLINE_TOKENS);

const RX = authority();
const PENDING = authority({ phase: 'key-confirm-pending', intent: 'latched', mayOwnKey: true, txRisk: 'uncertain' });
const TX = authority({ phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const RELEASING = authority({ phase: 'releasing', radioTx: 'on', txRisk: 'confirmed-on', mayOwnKey: true });
const FAULT = authority({ phase: 'failed', radioTx: 'unknown', txRisk: 'uncertain', fault: 'audio-failed' });
const UNKNOWN_RF = authority({ radioTx: 'unknown' });
const ALL = [RX, PENDING, TX, RELEASING, FAULT, UNKNOWN_RF];

describe('the LEFT rail is the always-visible TX carrier (MOR-977 §2.2)', () => {
  it.each([
    ['RX', RX], ['pending', PENDING], ['TX', TX], ['releasing', RELEASING], ['fault', FAULT], ['rf-unknown', UNKNOWN_RF],
  ])('%s renders a full-bleed rail on the inline start edge', (_name, tx) => {
    expect(fromAuthority(tx).rail).toMatchObject({ fullBleed: true, edge: 'inline-start' });
  });

  it('RX is the quiet 8px baseline', () => {
    const { rail, band, meterScaleLabel, numeralTone } = fromAuthority(RX);
    expect(rail).toMatchObject({ widthPx: 8, tone: FIELDLINE_TOKENS.rx.idle });
    expect(band).toMatchObject({ treatment: 'absent', text: null, textTone: null });
    expect(meterScaleLabel).toBe('S');
    expect(numeralTone).toBe('primary');
  });

  it('pending floods to 16px and outlines the band, before any RF is out', () => {
    const { rail, band, meterScaleLabel } = fromAuthority(PENDING);
    expect(rail).toMatchObject({ widthPx: 16, tone: FIELDLINE_TOKENS.tx.tuning });
    expect(band).toMatchObject({ treatment: 'outlined', text: 'KEYING' });
    expect(meterScaleLabel).toBe('S');
  });

  it('TX takes over: 24px rail, a FILLED "ON AIR" band, meter re-zoned to power', () => {
    const { rail, band, meterScaleLabel, numeralTone } = fromAuthority(TX);
    expect(rail).toMatchObject({ widthPx: 24, tone: FIELDLINE_TOKENS.tx.active });
    expect(band).toMatchObject({ treatment: 'filled', text: 'ON AIR', textTone: FIELDLINE_KNOCKOUT });
    expect(meterScaleLabel).toBe('PO');
    expect(numeralTone).toBe('tx-target');
  });

  it('releasing steps back to 16px without pretending RX has resumed', () => {
    const { rail, band, meterScaleLabel } = fromAuthority(RELEASING);
    expect(rail.widthPx).toBe(16);
    expect(band.text).toBe('UNKEYING');
    expect(meterScaleLabel).toBe('PO');
  });

  it('a fault puts its code IN the band — an unheard-of code still shows', () => {
    const f = fromAuthority(FAULT);
    expect(f.rail).toMatchObject({ widthPx: 24, tone: FIELDLINE_TOKENS.tx.active });
    expect(f.band).toMatchObject({ treatment: 'filled', text: 'TX FAULT: audio-failed', textTone: FIELDLINE_KNOCKOUT });
    expect(fromAuthority(authority({ phase: 'failed', fault: 'no-such-code-4711' })).band.text)
      .toBe('TX FAULT: no-such-code-4711');
  });

  it('unknown RF is a THIRD thing — never collapsed into RX (MOR-977 §1.2.1)', () => {
    const unknown = fromAuthority(UNKNOWN_RF);
    expect(unknown.rail).toMatchObject({ widthPx: 16, tone: FIELDLINE_TOKENS.tx.tuning });
    expect(unknown.band.text).toBe('TX?');
    expect(unknown.rail).not.toEqual(fromAuthority(RX).rail);
  });
});

describe('state survives forced-colors and colour-vision deficiency', () => {
  it('no two states are distinguished by colour alone', () => {
    const colourless = ALL.map((tx) => {
      const s = fromAuthority(tx);
      return `${s.rail.widthPx}/${s.band.treatment}/${s.band.text ?? 'RX'}`;
    });
    expect(new Set(colourless).size).toBe(ALL.length);
  });

  it('uses only the declared 8/16/24px rail floods', () => {
    for (const tx of ALL) expect([8, 16, 24]).toContain(fromAuthority(tx).rail.widthPx);
  });

  it('every band treatment is one of the three declared forms', () => {
    for (const tx of ALL) expect(['absent', 'outlined', 'filled']).toContain(fromAuthority(tx).band.treatment);
  });
});

describe('the action slab gets an intentional treatment, not a UA default (N2)', () => {
  it.each([
    ['idle', RX, 'idle'], ['pending', PENDING, 'pending'],
    ['keyed', TX, 'keyed'], ['fault', FAULT, 'fault'],
  ])('%s renders the "%s" slab treatment', (_name, tx, treatment) => {
    expect(fromAuthority(tx).slab.treatment).toBe(treatment);
  });

  it('is a full-width 48px slab — gloves, in daylight, possibly one-handed', () => {
    expect(fromAuthority(RX).slab).toMatchObject({ fullWidth: true, minHeightPx: 48 });
    expect(FIELDLINE_TOKENS.geometry.radius).toBe('0px');
  });

  it('carries state in edge GEOMETRY as well as fill, so colour is never the only channel', () => {
    const geometry = (tx: TxAuthoritySnapshot, extra = {}): string => {
      const { slab } = fromAuthority(tx, extra);
      return `${slab.edgeStyle}/${slab.filled ? 'solid' : 'empty'}`;
    };
    const treatments = {
      idle: geometry(RX), pending: geometry(PENDING), keyed: geometry(TX),
      fault: geometry(FAULT), blocked: geometry(RX, { keyBlocked: true }),
    };
    expect(treatments).toEqual({
      idle: 'solid/empty', pending: 'solid/empty', keyed: 'solid/solid',
      fault: 'dashed/empty', blocked: 'dotted/empty',
    });
    // idle and pending share an edge/fill pair in the DESCRIPTOR; the hatch that
    // separates them is a stylesheet concern, pinned in `stylesheet.test.ts`.
    expect(new Set(Object.values(treatments)).size).toBe(4);
  });

  it('fills solid with a knocked-out BLACK label while keyed, and stays outlined otherwise', () => {
    expect(fromAuthority(TX).slab).toMatchObject({
      fill: FIELDLINE_TOKENS.tx.active, filled: true, tone: FIELDLINE_KNOCKOUT,
    });
    expect(fromAuthority(RX).slab.filled).toBe(false);
  });

  it('a blocked slab is rendered inert-but-PRESENT, never absent (MOR-977 §1.2.3)', () => {
    const blocked = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'receiving', session: 'idle', fault: null, keyBlocked: true },
    }, FIELDLINE_TOKENS);
    expect(blocked.slab).toMatchObject({ treatment: 'blocked', present: true, filled: false });
    expect(blocked.slab.tone).toBe(FIELDLINE_PALETTE.inert);
  });

  it('every treatment is present — no state hides the control', () => {
    for (const tx of ALL) expect(fromAuthority(tx).slab.present).toBe(true);
  });

  it('carries the mandatory focus ring rather than re-suppressing it (MOR-977 §1.2.5)', () => {
    expect(fromAuthority(RX).slab.focusRing).toBe(FIELDLINE_TOKENS.focusRing);
    expect(fromAuthority(RX).slab.focusRing).not.toMatch(/none/);
  });
});

describe('R9 — TX truth comes from App authority, never from the renderer', () => {
  it('a raw ptt field cannot move the rail: authority says RX, so it renders RX', () => {
    const withPtt = fromAuthority(RX, { ptt: true, radioStatePtt: true });
    expect(withPtt).toEqual(fromAuthority(RX));
    expect(withPtt.band.text).toBeNull();
  });

  it('a raw ptt field cannot clear a TX rail either', () => {
    expect(fromAuthority(TX, { ptt: false }).rail).toEqual(fromAuthority(TX).rail);
  });

  it('an unrecognised state fails CLOSED — doubt, never a quiet RX rail', () => {
    const bogus = renderStateFeedback({
      kind: 'state-feedback',
      fields: { rf: 'who-knows', session: 'brand-new-phase', fault: null, keyBlocked: false },
    }, FIELDLINE_TOKENS);
    expect(bogus.rail.widthPx).toBe(16);
    expect(bogus.band.text).toBe('TX?');
    expect(bogus.slab.treatment).not.toBe('idle');
  });

  it('is reachable only through the structural gate', () => {
    const smuggled = { kind: 'state-feedback', fields: { rf: 'receiving' }, capabilities: { tx: true } };
    expect(() => invokeRenderer(renderStateFeedback, smuggled, FIELDLINE_TOKENS)).toThrow(RendererInputError);
  });
});

describe('the two languages agree on the FACTS and disagree on the grammar', () => {
  // The acceptance core again, on the safety-critical surface: identical
  // authority in, identical conclusions out, materially different carriers.
  it.each([
    ['RX', RX], ['pending', PENDING], ['TX', TX], ['releasing', RELEASING], ['fault', FAULT], ['rf-unknown', UNKNOWN_RF],
  ])('%s: both languages agree on meter scale and numeral tone', (_name, tx) => {
    const mine = fromAuthority(tx);
    const theirs = renderStudioline({ kind: 'state-feedback', fields: fields(tx) }, STUDIOLINE_TOKENS);
    expect(mine.meterScaleLabel).toBe(theirs.meterScaleLabel);
    expect(mine.numeralTone).toBe(theirs.numeralTone);
  });

  it('the TX CARRIER is a different element in each language', () => {
    const theirs = renderStudioline({ kind: 'state-feedback', fields: fields(TX) }, STUDIOLINE_TOKENS);
    expect(theirs.rail).toMatchObject({ thicknessPx: 3 }); // a TOP rail, measured in thickness
    expect(fromAuthority(TX).rail).toMatchObject({ edge: 'inline-start', widthPx: 24 });
    expect(theirs.rail).not.toHaveProperty('edge');
  });

  it('takeover vs quiet: fieldline adds a band studioline has no equivalent of', () => {
    const theirs = renderStudioline({ kind: 'state-feedback', fields: fields(TX) }, STUDIOLINE_TOKENS);
    expect(theirs.rail.label).toBe('TX'); // a label ON the rail
    expect(fromAuthority(TX).band).toMatchObject({ treatment: 'filled', text: 'ON AIR' });
    expect(theirs).not.toHaveProperty('band');
  });

  it('slab vs pill: the same control, opposite geometry', () => {
    const theirs = renderStudioline({ kind: 'state-feedback', fields: fields(RX) }, STUDIOLINE_TOKENS);
    expect(theirs.key.radius).toBe('999px');
    expect(fromAuthority(RX).slab).toMatchObject({ fullWidth: true, minHeightPx: 48 });
    expect(fromAuthority(RX).slab).not.toHaveProperty('radius');
  });
});
