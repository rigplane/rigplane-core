/**
 * MOR-1073 — the `studioline` token slice.
 *
 * Two things are pinned here that a snapshot could not pin usefully:
 *
 *  1. FOCUS-RING SYNTAX (MOR-1232 finding D5). The contract stores the ring
 *     as one opaque string; the consumer applies it to `outline`. A
 *     box-shadow-shaped literal (`0 0 0 2px …`) assigned to `outline` is
 *     silently invalid CSS — the ring simply does not appear, which is the
 *     exact accessibility failure the mandatory-ring rule exists to prevent.
 *     Every declared literal is therefore parsed as the `outline` shorthand.
 *
 *  2. CONTRAST AS ARITHMETIC, NOT OPINION. MOR-977 §3.2 makes studioline
 *     guarantee a contrast regime; §4.4 makes rail colour a state channel.
 *     Both mean every state tone and the ring itself must clear 3:1 against
 *     BOTH studioline surfaces (dark shack and light), so the ratios are
 *     computed here rather than eyeballed. The placeholder's `var(--accent)`
 *     is what fails this on a light skin (handed-over obligation 2).
 */
import { describe, it, expect } from 'vitest';
import { validateManifest, REQUIRED_TOKEN_GROUPS } from '../../contract';
import { studioline, fieldline } from '../../declarations';
import { validManifest } from '../../__tests__/fixtures';
import { STUDIOLINE_PALETTE, STUDIOLINE_SURFACES, STUDIOLINE_TOKENS } from '../tokens';

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
/** The literal colour a token falls back to when its custom property is unset. */
const fallbackHex = (token: string): string => {
  const hit = /#[0-9a-fA-F]{6}/.exec(token);
  expect(hit, `expected a literal #rrggbb fallback in "${token}"`).not.toBeNull();
  return hit![0];
};

describe('focus ring is outline syntax, everywhere it is declared (MOR-1232 D5)', () => {
  it.each([
    ['studioline', studioline.tokens.focusRing],
    ['fieldline (shared placeholder)', fieldline.tokens.focusRing],
    ['the shared test fixture', validManifest().tokens.focusRing],
  ])('%s declares a ring assignable to `outline`', (_name, ring) => {
    expect(ring).toMatch(OUTLINE_SHORTHAND);
    // The shape that motivated the re-shape: a box-shadow spread list.
    expect(ring).not.toMatch(/^0 0 0 /);
  });
});

describe('studioline contrast regime (MOR-977 §3.2, §4.4)', () => {
  const surfaces = Object.entries(STUDIOLINE_SURFACES);

  it('declares both surfaces it must survive on', () => {
    expect(Object.keys(STUDIOLINE_SURFACES).sort()).toEqual(['dark', 'light']);
  });

  // Obligation 2: the DL placeholder's var(--accent) has no such guarantee —
  // studioline pins its own value and proves it, on both surfaces.
  it.each(surfaces)('the focus ring clears 3:1 on the %s surface', (_mode, surface) => {
    expect(contrast(fallbackHex(studioline.tokens.focusRing), surface)).toBeGreaterThanOrEqual(3);
  });

  it.each(
    surfaces.flatMap(([mode, surface]) =>
      Object.entries(STUDIOLINE_PALETTE).map(([tone, hex]) => [tone, mode, hex, surface] as const)),
  )('state tone "%s" clears 3:1 on the %s surface', (_tone, _mode, hex, surface) => {
    expect(contrast(hex, surface)).toBeGreaterThanOrEqual(3);
  });

  it('every rx/tx token resolves to a palette entry with a literal fallback', () => {
    const stateTokens = [...Object.values(studioline.tokens.rx), ...Object.values(studioline.tokens.tx)];
    expect(stateTokens).toHaveLength(6);
    for (const token of stateTokens) {
      expect(Object.values(STUDIOLINE_PALETTE)).toContain(fallbackHex(token));
    }
  });
});

describe('studioline token set implements the MOR-977 §2.3 grammar', () => {
  it('validates against the MOR-1072 contract', () => {
    expect(() => validateManifest(studioline)).not.toThrow();
    expect(REQUIRED_TOKEN_GROUPS.every((g) => studioline.tokens[g] !== undefined)).toBe(true);
  });

  it('is the manifest studioline registers — not a copy', () => {
    expect(studioline.tokens).toBe(STUDIOLINE_TOKENS);
  });

  it('numerals are ultralight, proportional, and tabular (the hard §4.4 dependency)', () => {
    expect(STUDIOLINE_TOKENS.typography.weight).toBe(200);
    expect(STUDIOLINE_TOKENS.typography.fontVariantNumeric).toBe('tabular-nums');
    expect(STUDIOLINE_TOKENS.typography.fontFamily).not.toMatch(/mono/i);
  });

  it('is unenclosed: no radius, and the only border is the 1px rail', () => {
    expect(STUDIOLINE_TOKENS.geometry.radius).toBe('0px');
    expect(STUDIOLINE_TOKENS.geometry.borderWidth).toBe('1px');
  });

  it('the meter is a continuous 4px rail, not segments', () => {
    expect(STUDIOLINE_TOKENS.meters.trackWidth).toBe('4px');
    expect(STUDIOLINE_TOKENS.meters.segmentGap).toBe('0px');
  });

  it('frequency groups are ranked at the numeral weight', () => {
    expect(STUDIOLINE_TOKENS.frequency.rankedGroups).toBe(true);
    expect(STUDIOLINE_TOKENS.frequency.digitWeight).toBe(STUDIOLINE_TOKENS.typography.weight);
  });

  it('its one animation is droppable, so the bundle is reduced-motion safe', () => {
    expect(STUDIOLINE_TOKENS.motion.reducedMotionSafe).toBe(true);
  });

  it('holds all three density steps and declares the cockpit compatible (MOR-977 §4.2)', () => {
    expect(studioline.density).toEqual({ kind: 'clamped', supported: ['comfortable', 'compact', 'dense'] });
    expect(studioline.layoutCompatibility).toContainEqual(
      expect.objectContaining({ layoutId: 'dual-receiver-cockpit', compatible: true }),
    );
  });

  it('leaves fieldline on the shared placeholder — this slice owns one language', () => {
    expect(fieldline.tokens).not.toBe(STUDIOLINE_TOKENS);
    expect(fieldline.tokens.typography.weight).not.toBe(STUDIOLINE_TOKENS.typography.weight);
  });
});
