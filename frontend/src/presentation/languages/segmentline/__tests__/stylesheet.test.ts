/**
 * MOR-2148 — structural pins for the `segmentline` stylesheet.
 *
 * The activation-attribute doubling (`[data-design-language='segmentline']
 * [data-design-language]` on every selector) is already pinned, for every
 * discovered language sheet including this one, by the general
 * `../../__tests__/activation-attribute.test.ts` (MOR-1275) — not repeated
 * here.
 *
 * Unlike `studioline`/`fieldline`, this file has no `.rx-tx-surface`/
 * `.rx-tx-key`/`.rx-tx-state` cascade to rank: those rules style the
 * stateFeedback renderer's output, and segmentline ships no renderers yet
 * (MOR-2149). What exists today is the glass, the cell, the seven-segment
 * readout cell, the segmented meter track and the DIM contrast preset —
 * pinned below directly, by parsing declarations rather than by ranking a
 * cascade collision (there is none yet to rank).
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { SEGMENTLINE_PALETTE, SEGMENTLINE_SURFACES, SEGMENTLINE_TOKENS } from '../tokens';

const source = readFileSync('src/presentation/languages/segmentline/segmentline.css', 'utf8');
// Comments name the very constructs several tests below forbid, so they are
// stripped first: the pins are about DECLARATIONS, not about prose.
const css = source.replace(/\/\*[\s\S]*?\*\//g, '');

const ATTR = "[data-design-language='segmentline'][data-design-language]";

interface Rule { selector: string; declarations: Record<string, string>; order: number }

function parseRules(sheet: string): Rule[] {
  const rules: Rule[] = [];
  for (const [, selectorList, body] of sheet.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const declarations: Record<string, string> = {};
    for (const declaration of body.split(';')) {
      const colon = declaration.indexOf(':');
      if (colon > 0) declarations[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
    }
    // A comma-separated list is N rules that happen to share a body.
    for (const selector of selectorList.split(',')) {
      const trimmed = selector.trim();
      if (trimmed) rules.push({ selector: trimmed, declarations, order: rules.length });
    }
  }
  return rules;
}

const RULES = parseRules(css);
/** Exact selector match — the base (non-media-query) copy, since `.find`
 *  returns the first of the two identical selectors the reduced-motion
 *  block at the bottom of the file repeats. */
const findExact = (selector: string): Rule | undefined => RULES.find((r) => r.selector === selector);

describe('the sheet parses into something worth asserting against', () => {
  it('found the glass, cell, readout and meter rules it is about to pin', () => {
    expect(RULES.length).toBeGreaterThan(20);
    expect(RULES.some((r) => r.selector.includes('.dl-glass'))).toBe(true);
    expect(RULES.some((r) => r.selector.includes('.dl-cell'))).toBe(true);
    expect(RULES.some((r) => r.selector.includes('.dl-freq'))).toBe(true);
    expect(RULES.some((r) => r.selector.includes('.dl-meter'))).toBe(true);
  });
});

describe('the glass: fixed-native geometry over the amber ground', () => {
  it('is the declared 14px padding inside a 2px bezel, 10px outer radius', () => {
    const glass = findExact(`${ATTR} .dl-glass`)!;
    expect(glass.declarations.padding).toBe('14px');
    expect(glass.declarations.border).toBe('2px solid var(--dl-segmentline-bezel-edge)');
    expect(glass.declarations['border-radius']).toBe('10px');
    expect(glass.declarations.background).toBe('var(--dl-segmentline-glass)');
  });

  it('sets colour explicitly rather than inheriting it', () => {
    const glass = findExact(`${ATTR} .dl-glass`)!;
    expect(glass.declarations.color).toBe('var(--dl-segmentline-ink-strong)');
  });

  it('the TX perimeter requires the tx="active" attribute, and is geometry only (no colour emitted here)', () => {
    const border = findExact(`${ATTR} .dl-glass[data-tx='active']`)!;
    expect(border.declarations['border-color']).toBe('var(--dl-segmentline-tx-active)');
    const glow = findExact(`${ATTR} .dl-glass[data-tx='active']::after`)!;
    expect(glow.declarations['box-shadow']).toBeDefined();
    // The renderer emits colour; this file emits only the frame — no `color`
    // or `background` declared on the glow itself.
    expect(glow.declarations.color).toBeUndefined();
    expect(glow.declarations.background).toBeUndefined();
  });
});

describe('cells: the family\'s only control shape, never a filled button', () => {
  it('is outlined with no fill', () => {
    const cell = findExact(`${ATTR} .dl-cell`)!;
    expect(cell.declarations.background).toBe('none');
    expect(cell.declarations.border).toBe('var(--dl-segmentline-cell-border) solid var(--dl-segmentline-ink-soft)');
  });

  it('the hot+active combination is required together — an inactive TX-capable cell stays dim ink (v3 ADR invariant 9)', () => {
    const hot = findExact(`${ATTR} .dl-cell[data-tone='hot'][data-active='true']`)!;
    expect(hot.declarations.color).toBe('var(--dl-segmentline-tx-mark)');
    // No rule keys off [data-tone='hot'] alone (without [data-active]) —
    // that shape is exactly what would mark TX-capability itself as hot,
    // rather than the lit+capable combination.
    expect(RULES.some((r) => /\[data-tone='hot'\]$/.test(r.selector))).toBe(false);
  });
});

describe('the seven-segment readout is shrink-wrapped, per-glyph', () => {
  it('the readout box is width:max-content — never a stretched flex child', () => {
    const freq = findExact(`${ATTR} .dl-freq`)!;
    expect(freq.declarations.width).toBe('max-content');
    expect(freq.declarations['font-family']).toMatch(/DSEG7/);
  });

  it('each glyph is its own inline-block cell, so total width is font-size alone', () => {
    const cell = findExact(`${ATTR} .dl-freq-cell`)!;
    expect(cell.declarations.display).toBe('inline-block');
  });
});

describe('the segmented meter track derives its pitch from the tokens, never a literal', () => {
  it('declares the 7px/3px pitch as custom properties, and the fill reads them back', () => {
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-track-width']).toBe('7px');
    expect(root.declarations['--dl-segmentline-segment-gap']).toBe('3px');
    const fill = findExact(`${ATTR} .dl-meter-fill`)!;
    expect(fill.declarations['background-image']).toContain('var(--dl-segmentline-track-width)');
    expect(fill.declarations['background-image']).toContain('var(--dl-segmentline-segment-pitch)');
  });

  it('an unread meter is marked unknown, never rendered as a gauge resting at zero', () => {
    const unknown = findExact(`${ATTR} [data-dl-unknown='true']`)!;
    expect(unknown.declarations.color).toBe('var(--dl-segmentline-ink-soft)');
  });
});

describe('the root declares the HIGH ink ramp and cell geometry at their literal values', () => {
  it('scales the ink ramp to the declared 1/0.65/0.34/0.09/0.5 alphas', () => {
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-ink-strong']).toBe('rgba(var(--dl-segmentline-ink) / 1)');
    expect(root.declarations['--dl-segmentline-ink-mid']).toBe('rgba(var(--dl-segmentline-ink) / 0.65)');
    expect(root.declarations['--dl-segmentline-ink-soft']).toBe('rgba(var(--dl-segmentline-ink) / 0.34)');
    expect(root.declarations['--dl-segmentline-ink-ghost']).toBe('rgba(var(--dl-segmentline-ink) / 0.09)');
    expect(root.declarations['--dl-segmentline-ink-telemetry']).toBe('rgba(var(--dl-segmentline-ink) / 0.5)');
  });

  it('the cell border custom property is the declared 1.25px', () => {
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-cell-border']).toBe('1.25px');
  });
});

describe('the DIM contrast preset is an explicit opt-in, never density or an OS signal', () => {
  it("keys off [data-dl-contrast='dim'], never [data-density] or prefers-color-scheme", () => {
    expect(css).toMatch(/\[data-dl-contrast='dim'\]/);
    expect(css).not.toMatch(/data-density/);
    expect(css).not.toMatch(/prefers-color-scheme/);
  });

  it('scales the ink ramp to the declared 0.55/0.36/0.20/0.06/0.30 alphas', () => {
    const dim = findExact(`${ATTR} [data-dl-contrast='dim']`)!;
    expect(dim.declarations['--dl-segmentline-ink-strong']).toBe('rgba(var(--dl-segmentline-ink) / 0.55)');
    expect(dim.declarations['--dl-segmentline-ink-mid']).toBe('rgba(var(--dl-segmentline-ink) / 0.36)');
    expect(dim.declarations['--dl-segmentline-ink-soft']).toBe('rgba(var(--dl-segmentline-ink) / 0.2)');
    expect(dim.declarations['--dl-segmentline-ink-ghost']).toBe('rgba(var(--dl-segmentline-ink) / 0.06)');
    expect(dim.declarations['--dl-segmentline-ink-telemetry']).toBe('rgba(var(--dl-segmentline-ink) / 0.3)');
  });
});

describe('the CSS half honours the same constraints as the token half', () => {
  it('adds and removes nothing — a language may not become a capability fork', () => {
    expect(css).not.toMatch(/display:\s*none/);
    expect(css).not.toMatch(/visibility:\s*hidden/);
    // Unlike studioline/fieldline (which declare no `content` at all),
    // segmentline has two decorative pseudo-elements (the glass texture and
    // the TX glow) and both must be empty strings — never text content.
    const contentDeclarations = RULES.map((r) => r.declarations.content).filter((v) => v !== undefined);
    expect(contentDeclarations.length).toBeGreaterThan(0);
    for (const value of contentDeclarations) expect(value).toBe("''");
  });

  it('never re-suppresses the focus ring, and applies it as `outline` (MOR-977 §1.2.5)', () => {
    expect(css).not.toMatch(/outline:\s*none/);
    const focus = findExact(`${ATTR} .dl-cell:focus-visible`)!;
    expect(focus.declarations.outline).toBe('2px solid var(--dl-segmentline-ink-strong)');
  });

  it('wins on specificity rather than on `!important`', () => {
    expect(css).not.toMatch(/!important/);
  });

  it('declares every palette entry it references as a custom property', () => {
    for (const hex of [
      SEGMENTLINE_SURFACES.glass, SEGMENTLINE_SURFACES.bezel,
      SEGMENTLINE_PALETTE.txHot, SEGMENTLINE_PALETTE.txMark, SEGMENTLINE_PALETTE.tuning,
    ]) {
      expect(css.toLowerCase()).toContain(hex.toLowerCase());
    }
  });

  it('the meter pitch is the same 7px/3px pair on both halves of the slice', () => {
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-track-width']).toBe(SEGMENTLINE_TOKENS.meters.trackWidth);
    expect(root.declarations['--dl-segmentline-segment-gap']).toBe(SEGMENTLINE_TOKENS.meters.segmentGap);
  });
});
