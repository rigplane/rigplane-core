/**
 * MOR-2148 — the `segmentline` token slice.
 *
 * Unlike `studioline`/`fieldline`, segmentline's state tones are not flat
 * hex swatches on two fixed grounds — every rx/tx tone falls back to one
 * ink at a declared alpha (`SEGMENTLINE_INK`) or to the TX/tuning palette
 * (`SEGMENTLINE_PALETTE`), composited over a single amber glass. The WCAG
 * two-surface contrast arithmetic the sibling files run does not apply to
 * that shape (there is one ground, and an alpha ink's effective colour
 * depends on what it sits on), so this file pins the literal fallback each
 * token resolves to instead of computing a contrast ratio.
 *
 * FOCUS-RING SYNTAX (MOR-1232 finding D5) still applies exactly as it does
 * for studioline/fieldline: the contract stores the ring as one opaque
 * string, and a box-shadow-shaped literal there is silently invalid CSS.
 */
import { describe, it, expect } from 'vitest';
import { validateManifest, REQUIRED_TOKEN_GROUPS } from '../../contract';
import { segmentline, studioline, fieldline } from '../../declarations';
import { SEGMENTLINE_INK, SEGMENTLINE_PALETTE, SEGMENTLINE_TOKENS } from '../tokens';

/** `<width> <style> <color>` — the shorthand form `outline` actually accepts. */
const OUTLINE_SHORTHAND = /^\d+(?:\.\d+)?px\s+(?:solid|dashed|dotted|double)\s+\S.*$/;

/** The literal fallback inside `var(--dl-segmentline-<name>, <fallback>)`. */
function fallbackLiteral(token: string): string {
  const hit = /^var\(--dl-segmentline-[\w-]+,\s*(.+)\)$/.exec(token);
  expect(hit, `expected a var(--dl-segmentline-*, <fallback>) token in "${token}"`).not.toBeNull();
  return hit![1];
}

describe('focus ring is outline syntax (MOR-1232 D5)', () => {
  it('declares a ring assignable to `outline`', () => {
    expect(segmentline.tokens.focusRing).toMatch(OUTLINE_SHORTHAND);
    // The shape that motivated the re-shape: a box-shadow spread list.
    expect(segmentline.tokens.focusRing).not.toMatch(/^0 0 0 /);
  });

  it('falls back to the declared focus palette entry, not an invented literal', () => {
    expect(fallbackLiteral(segmentline.tokens.focusRing.replace(/^2px solid /, ''))).toBe(SEGMENTLINE_PALETTE.focus);
  });
});

describe('segmentline token set implements the MOR-2148 amber-LCD grammar', () => {
  it('validates against the MOR-1072 contract', () => {
    expect(() => validateManifest(segmentline)).not.toThrow();
    expect(REQUIRED_TOKEN_GROUPS.every((g) => segmentline.tokens[g] !== undefined)).toBe(true);
  });

  it('is the manifest segmentline registers — not a copy', () => {
    expect(segmentline.tokens).toBe(SEGMENTLINE_TOKENS);
  });

  it('the readout face is seven-segment, weight 700, and tabular', () => {
    expect(SEGMENTLINE_TOKENS.typography.fontFamily).toMatch(/DSEG7/);
    expect(SEGMENTLINE_TOKENS.typography.weight).toBe(700);
    expect(SEGMENTLINE_TOKENS.typography.fontVariantNumeric).toBe('tabular-nums');
  });

  it('cells are hairline-outlined and barely rounded — a printed segment box, not a button', () => {
    expect(SEGMENTLINE_TOKENS.geometry.radius).toBe('2px');
    expect(SEGMENTLINE_TOKENS.geometry.borderWidth).toBe('1.25px');
  });

  it('the meter is a 7px segmented track with a 3px gap, not a continuous rail', () => {
    expect(SEGMENTLINE_TOKENS.meters.trackWidth).toBe('7px');
    expect(SEGMENTLINE_TOKENS.meters.segmentGap).toBe('3px');
    expect(Number.parseFloat(SEGMENTLINE_TOKENS.meters.segmentGap)).toBeGreaterThan(0);
  });

  it('frequency groups are ranked at the numeral weight', () => {
    expect(SEGMENTLINE_TOKENS.frequency.rankedGroups).toBe(true);
    expect(SEGMENTLINE_TOKENS.frequency.digitWeight).toBe(SEGMENTLINE_TOKENS.typography.weight);
  });

  it('the glass steps rather than animates: a 90ms TX fade, reduced-motion safe', () => {
    expect(SEGMENTLINE_TOKENS.motion.durationMs).toBe(90);
    expect(SEGMENTLINE_TOKENS.motion.reducedMotionSafe).toBe(true);
  });

  it('every rx/tx token falls back to a declared ink or palette entry', () => {
    const stateTokens = [...Object.values(SEGMENTLINE_TOKENS.rx), ...Object.values(SEGMENTLINE_TOKENS.tx)];
    expect(stateTokens).toHaveLength(6);
    const declared = [...Object.values(SEGMENTLINE_INK), ...Object.values(SEGMENTLINE_PALETTE)];
    for (const token of stateTokens) {
      expect(declared).toContain(fallbackLiteral(token));
    }
  });

  it('rx/tx idle both fall back to the same soft ink — idle reads as absence, not as a colour choice', () => {
    // Against the literal, not `SEGMENTLINE_INK.soft` itself: comparing a
    // token to the very constant that builds it would let both sides drift
    // together under a mutated ramp and never fail.
    expect(fallbackLiteral(SEGMENTLINE_TOKENS.rx.idle)).toBe('rgba(26,16,0,0.34)');
    expect(fallbackLiteral(SEGMENTLINE_TOKENS.tx.idle)).toBe('rgba(26,16,0,0.34)');
  });

  it('pins the HIGH ink ramp to its declared literal alphas', () => {
    expect(SEGMENTLINE_INK.strong).toBe('rgba(26,16,0,1)');
    expect(SEGMENTLINE_INK.mid).toBe('rgba(26,16,0,0.65)');
    expect(SEGMENTLINE_INK.soft).toBe('rgba(26,16,0,0.34)');
    expect(SEGMENTLINE_INK.ghost).toBe('rgba(26,16,0,0.09)');
    expect(SEGMENTLINE_INK.telemetry).toBe('rgba(26,16,0,0.50)');
  });

  it('tx active is the hot TX red, never a scaled ink alpha', () => {
    expect(fallbackLiteral(SEGMENTLINE_TOKENS.tx.active)).toBe(SEGMENTLINE_PALETTE.txHot);
  });

  it('clamps out "dense": the outlined cells collide with the 7px meter pitch', () => {
    expect(segmentline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact'] });
  });

  it('declares all production segmentline layouts compatible — desktop-v2 stays explicitly incompatible', () => {
    expect(segmentline.layoutCompatibility).toEqual([
      { layoutId: 'peer-split', compatible: true },
      { layoutId: 'unified-instrument', compatible: true },
      { layoutId: 'panadapter-first', compatible: true },
      {
        layoutId: 'desktop-v2',
        compatible: false,
        reason: 'segmentline assumes a fixed-native instrument glass; desktop-v2 is fluid chrome.',
      },
    ]);
  });
});

describe('does not lend its token set to studioline or fieldline — the family is independent', () => {
  it('shares the contract, not the bundle: both satisfy every required token group', () => {
    expect(segmentline.tokens).not.toBe(studioline.tokens);
    expect(segmentline.tokens).not.toBe(fieldline.tokens);
    for (const group of REQUIRED_TOKEN_GROUPS) {
      expect(segmentline.tokens[group]).toBeDefined();
    }
  });

  it('differs from both siblings on meter pitch — the shape the family is named for', () => {
    expect(SEGMENTLINE_TOKENS.meters.trackWidth).not.toBe(studioline.tokens.meters.trackWidth);
    expect(SEGMENTLINE_TOKENS.meters.trackWidth).not.toBe(fieldline.tokens.meters.trackWidth);
  });
});
