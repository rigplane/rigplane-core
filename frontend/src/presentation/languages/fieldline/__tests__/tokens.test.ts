/**
 * MOR-1074 — the `fieldline` token slice.
 *
 * Same two things are pinned here as in studioline's twin file, because the
 * point of a SECOND language is that the same obligations bind it:
 *
 *  1. FOCUS-RING SYNTAX (MOR-1232 D5) — the ring is stored as one opaque
 *     string and applied to `outline`, where a box-shadow-shaped literal is
 *     silently invalid and the ring simply never appears.
 *
 *  2. CONTRAST AS ARITHMETIC, NOT OPINION (owner decision Q1, 2026-08-04).
 *     fieldline supplies its own placeholder/ring tone and every state tone
 *     clears 3:1 against BOTH its surfaces — computed here, not eyeballed.
 *     Label TEXT is held to 4.5:1 against its own ground in BOTH modes, which
 *     is the bar studioline missed in light mode (finding MOR-1277).
 *
 * The third group is what makes this file worth having at all: the token set
 * must be the OPPOSITE of studioline on every axis a token can express, or the
 * "second language" claim is decoration. Those assertions compare the two
 * declared bundles directly rather than restating fieldline's numbers.
 */
import { describe, it, expect } from 'vitest';
import { validateManifest, REQUIRED_TOKEN_GROUPS } from '../../contract';
import { studioline, fieldline } from '../../declarations';
import { STUDIOLINE_TOKENS } from '../../studioline/tokens';
import {
  FIELDLINE_KNOCKOUT, FIELDLINE_LABEL_TONES, FIELDLINE_PALETTE, FIELDLINE_SURFACES, FIELDLINE_TOKENS,
} from '../tokens';

/** WCAG relative luminance / contrast ratio, on `#rrggbb` only. */
function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16);
  const channel = (c: number): number => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}
function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** `<width> <style> <color>` — the shorthand form `outline` actually accepts. */
const OUTLINE_SHORTHAND = /^\d+(?:\.\d+)?px\s+(?:solid|dashed|dotted|double)\s+\S.*$/;
const fallbackHex = (token: string): string => {
  const hit = /#[0-9a-fA-F]{6}/.exec(token);
  expect(hit, `expected a literal #rrggbb fallback in "${token}"`).not.toBeNull();
  return hit![0];
};

describe('fieldline contrast regime (owner decision Q1; MOR-977 §3.2)', () => {
  const surfaces = Object.entries(FIELDLINE_SURFACES);

  it('declares both grounds it must survive on — dark shack and daylight', () => {
    expect(Object.keys(FIELDLINE_SURFACES).sort()).toEqual(['dark', 'light']);
    // Daylight is the hard case: pure white, not studioline's warm off-white.
    expect(FIELDLINE_SURFACES.light).toBe('#FFFFFF');
  });

  it('declares its OWN ring tone rather than inheriting var(--accent)', () => {
    expect(fieldline.tokens.focusRing).toMatch(OUTLINE_SHORTHAND);
    expect(fieldline.tokens.focusRing).not.toMatch(/--accent/);
    expect(fieldline.tokens.focusRing).not.toMatch(/^0 0 0 /);
  });

  it.each(surfaces)('the focus ring clears 3:1 on the %s surface', (_mode, surface) => {
    expect(contrast(fallbackHex(fieldline.tokens.focusRing), surface)).toBeGreaterThanOrEqual(3);
  });

  it.each(
    surfaces.flatMap(([mode, surface]) =>
      Object.entries(FIELDLINE_PALETTE).map(([t, hex]) => [t, mode, hex, surface] as const)),
  )('state tone "%s" clears 3:1 on the %s surface', (_tone, _mode, hex, surface) => {
    expect(contrast(hex, surface)).toBeGreaterThanOrEqual(3);
  });

  // The declared margin, not just the floor: flat sunlight-safe fills have no
  // gradient or glow to fall back on, so the whole palette is held higher.
  it.each(surfaces)('every tone in fact clears 3.9:1 on the %s surface', (_mode, surface) => {
    for (const hex of Object.values(FIELDLINE_PALETTE)) {
      expect(contrast(hex, surface)).toBeGreaterThanOrEqual(3.9);
    }
  });

  // MOR-1277 is the finding this pin exists to avoid repeating: 3:1 is the
  // NON-TEXT floor, and a label is text.
  it.each(Object.entries(FIELDLINE_LABEL_TONES))(
    'label text and muted label text clear 4.5:1 on their own %s ground', (mode, tones) => {
      const surface = FIELDLINE_SURFACES[mode as keyof typeof FIELDLINE_SURFACES];
      expect(contrast(tones.text, surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(tones.muted, surface)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it('knocked-out black label text clears 4.5:1 on the filled TX slab, in both modes', () => {
    // One value for both modes, so one assertion covers both (MOR-977 §2.2).
    expect(contrast(FIELDLINE_KNOCKOUT, FIELDLINE_PALETTE.txActive)).toBeGreaterThanOrEqual(4.5);
  });

  it('every rx/tx token resolves to a palette entry with a literal fallback', () => {
    const stateTokens = [...Object.values(FIELDLINE_TOKENS.rx), ...Object.values(FIELDLINE_TOKENS.tx)];
    expect(stateTokens).toHaveLength(6);
    for (const token of stateTokens) {
      expect(Object.values(FIELDLINE_PALETTE)).toContain(fallbackHex(token));
    }
  });
});

describe('fieldline token set implements the MOR-977 §2.2 grammar', () => {
  it('validates against the MOR-1072 contract', () => {
    expect(() => validateManifest(fieldline)).not.toThrow();
    expect(REQUIRED_TOKEN_GROUPS.every((g) => fieldline.tokens[g] !== undefined)).toBe(true);
  });

  it('is the manifest fieldline registers — not a copy', () => {
    expect(fieldline.tokens).toBe(FIELDLINE_TOKENS);
  });

  it('numerals are monospace slab digits at 700, and still declare tabular figures', () => {
    expect(FIELDLINE_TOKENS.typography.weight).toBe(700);
    expect(FIELDLINE_TOKENS.typography.fontFamily).toMatch(/mono/i);
    expect(FIELDLINE_TOKENS.typography.fontVariantNumeric).toBe('tabular-nums');
  });

  it('is hard-edged: zero radius with a 3px opaque border', () => {
    expect(FIELDLINE_TOKENS.geometry.radius).toBe('0px');
    expect(FIELDLINE_TOKENS.geometry.borderWidth).toBe('3px');
  });

  it('the meter is discrete segments with a real gap, not a continuous rail', () => {
    expect(FIELDLINE_TOKENS.meters.segmentGap).toBe('3px');
    expect(Number.parseFloat(FIELDLINE_TOKENS.meters.segmentGap)).toBeGreaterThan(0);
  });

  it('frequency groups are NOT ranked — the Hz digits carry the numeral weight too', () => {
    expect(FIELDLINE_TOKENS.frequency.rankedGroups).toBe(false);
    expect(FIELDLINE_TOKENS.frequency.digitWeight).toBe(FIELDLINE_TOKENS.typography.weight);
  });

  it('declares zero motion, so the grammar is intact under the reduced-motion clamp', () => {
    expect(FIELDLINE_TOKENS.motion.durationMs).toBe(0);
    expect(FIELDLINE_TOKENS.motion.reducedMotionSafe).toBe(true);
  });

  it('clamps out "dense" and declares the cockpit incompatible (MOR-977 §4.4)', () => {
    expect(fieldline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact'] });
    expect(fieldline.layoutCompatibility).toContainEqual(
      expect.objectContaining({ layoutId: 'dual-receiver-cockpit', compatible: false }),
    );
  });
});

describe('fieldline is a materially different language, not a recolour of studioline', () => {
  // The MOR-977 §4.3.1 claim, asserted rather than asserted-in-prose: the two
  // bundles disagree on every axis a TOKEN can express. Colour is deliberately
  // absent from this list — a language that differed only in colour would pass
  // a colour comparison and still be a reskin.
  it.each([
    ['numeral weight', (t: typeof FIELDLINE_TOKENS) => t.typography.weight],
    ['numeral face is monospace', (t: typeof FIELDLINE_TOKENS) => /mono/i.test(t.typography.fontFamily)],
    ['border weight', (t: typeof FIELDLINE_TOKENS) => t.geometry.borderWidth],
    ['meter segment gap', (t: typeof FIELDLINE_TOKENS) => t.meters.segmentGap],
    ['meter track width', (t: typeof FIELDLINE_TOKENS) => t.meters.trackWidth],
    ['frequency group ranking', (t: typeof FIELDLINE_TOKENS) => t.frequency.rankedGroups],
    ['digit weight', (t: typeof FIELDLINE_TOKENS) => t.frequency.digitWeight],
    ['focus-ring width', (t: typeof FIELDLINE_TOKENS) => t.focusRing.split(' ')[0]],
  ])('differs from studioline on %s', (_axis, read) => {
    expect(read(FIELDLINE_TOKENS)).not.toEqual(read(STUDIOLINE_TOKENS));
  });

  it('shares the contract, not the bundle: both satisfy every required token group', () => {
    expect(fieldline.tokens).not.toBe(studioline.tokens);
    for (const group of REQUIRED_TOKEN_GROUPS) {
      expect(fieldline.tokens[group]).toBeDefined();
      expect(studioline.tokens[group]).toBeDefined();
    }
  });
});
