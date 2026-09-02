/**
 * MOR-2148 — structural pins for the `segmentline` stylesheet.
 *
 * The activation-attribute doubling (`[data-design-language='segmentline']
 * [data-design-language]` on every selector) is already pinned, for every
 * discovered language sheet including this one, by the general
 * `../../__tests__/activation-attribute.test.ts` (MOR-1275) — not repeated
 * here.
 *
 * Unlike `studioline`/`fieldline`, this file has no `.rx-tx-*` cascade to
 * rank — no two rules here target the same selector at competing
 * specificity, even though `.rx-tx-surface`/`.rx-tx-key`/`.rx-tx-unkey` are,
 * since this stylesheet's retarget onto real emitted markup, the real
 * selectors here too, same as the sibling sheets. The glass, the cells and
 * the seven-segment readout cell are pinned below directly, by parsing
 * declarations rather than by ranking a cascade collision.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { SEGMENTLINE_PALETTE, SEGMENTLINE_SURFACES } from '../tokens';

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

describe('the glass: fixed-native geometry over the amber ground', () => {
  it('is the declared 14px padding inside a 2px bezel, 10px outer radius', () => {
    const glass = findExact(`${ATTR} .rx-tx-surface`)!;
    expect(glass.declarations.padding).toBe('14px');
    expect(glass.declarations.border).toBe('2px solid var(--dl-segmentline-bezel-edge)');
    expect(glass.declarations['border-radius']).toBe('10px');
    expect(glass.declarations.background).toBe('var(--dl-segmentline-glass)');
  });

  it('sets colour explicitly rather than inheriting it', () => {
    const glass = findExact(`${ATTR} .rx-tx-surface`)!;
    expect(glass.declarations.color).toBe('var(--dl-segmentline-ink-strong)');
  });
});

describe('cells: the family\'s only control shape, never a filled button', () => {
  it('is outlined with no fill', () => {
    const cell = findExact(`${ATTR} .rx-tx-key`)!;
    expect(cell.declarations.background).toBe('none');
    expect(cell.declarations.border).toBe('var(--dl-segmentline-cell-border) solid var(--dl-segmentline-ink-soft)');
  });
});

describe('the seven-segment readout is shrink-wrapped, per-glyph', () => {
  it('the readout box is width:max-content — never a stretched flex child', () => {
    const freq = findExact(`${ATTR} .vfo-freq`)!;
    expect(freq.declarations.width).toBe('max-content');
    expect(freq.declarations['font-family']).toMatch(/DSEG7/);
  });

  it('each glyph is its own inline-block cell, so total width is font-size alone', () => {
    const cell = findExact(`${ATTR} .digit`)!;
    expect(cell.declarations.display).toBe('inline-block');
  });
});

describe('the shipped gauges take a tone annotation via data-dl-*, never a geometry change', () => {
  it('an unread meter is marked unknown, never rendered as a gauge resting at zero', () => {
    const unknown = findExact(`${ATTR} [data-dl-unknown='true']`)!;
    expect(unknown.declarations.color).toBe('var(--dl-segmentline-ink-soft)');
  });
});

describe('the root declares the HIGH ink ramp and cell geometry at their literal values', () => {
  it('scales the ink ramp to the declared 1/0.34/0.09 alphas', () => {
    // `--dl-segmentline-ink-mid` (0.65) is gone (MOR-2163 review: its last
    // consumer, the removed `.dl-freq [data-tone='muted']`/`.dl-meter-scale`
    // rules, no longer exists) — three alphas remain, not the original five.
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-ink-strong']).toBe('rgba(var(--dl-segmentline-ink) / 1)');
    expect(root.declarations['--dl-segmentline-ink-soft']).toBe('rgba(var(--dl-segmentline-ink) / 0.34)');
    expect(root.declarations['--dl-segmentline-ink-ghost']).toBe('rgba(var(--dl-segmentline-ink) / 0.09)');
  });

  it('the cell border custom property is the declared 1.25px', () => {
    const root = findExact(ATTR)!;
    expect(root.declarations['--dl-segmentline-cell-border']).toBe('1.25px');
  });
});

describe('the CSS half honours the same constraints as the token half', () => {
  // Restored (review finding, MOR-2163): these two were deleted alongside
  // the DIM-preset `it` block's third assertion (`toMatch(/\[data-dl-contrast=
  // 'dim'\]/)`, correctly dropped — that rule no longer exists, retargeted
  // onto no reachable class). These two forbid a DIFFERENT rule from EVER
  // appearing, an invariant unrelated to whether `[data-dl-contrast='dim']`
  // itself is still declared — deleting them alongside it left `data-density`
  // guarded nowhere in the repo.
  it('never keys density or the OS colour-scheme signal into a rule (MOR-977 §3.2)', () => {
    expect(css).not.toMatch(/data-density/);
    expect(css).not.toMatch(/prefers-color-scheme/);
  });

  it('adds and removes nothing — a language may not become a capability fork', () => {
    expect(css).not.toMatch(/display:\s*none/);
    expect(css).not.toMatch(/visibility:\s*hidden/);
    // Unlike studioline/fieldline (which declare no `content` at all),
    // segmentline has a decorative pseudo-element (the glass texture) and
    // its content must be an empty string — never text content.
    const contentDeclarations = RULES.map((r) => r.declarations.content).filter((v) => v !== undefined);
    expect(contentDeclarations.length).toBeGreaterThan(0);
    for (const value of contentDeclarations) expect(value).toBe("''");
  });

  it('never re-suppresses the focus ring, and applies it as `outline` (MOR-977 §1.2.5)', () => {
    expect(css).not.toMatch(/outline:\s*none/);
    const focus = findExact(`${ATTR} .rx-tx-key:focus-visible`)!;
    expect(focus.declarations.outline).toBe('2px solid var(--dl-segmentline-ink-strong)');
  });

  it('wins on specificity rather than on `!important`', () => {
    expect(css).not.toMatch(/!important/);
  });

  it('declares every palette entry it references as a custom property', () => {
    // `txHot` dropped (MOR-2163 review, same fix that removed
    // `--dl-segmentline-tx-active`): its sole CSS consumer, the TX-active
    // perimeter border-color rule, was already deleted by the retarget —
    // no rule in this file uses that hex any more, under any name.
    for (const hex of [
      SEGMENTLINE_SURFACES.glass, SEGMENTLINE_SURFACES.bezel,
      SEGMENTLINE_PALETTE.txMark, SEGMENTLINE_PALETTE.tuning,
    ]) {
      expect(css.toLowerCase()).toContain(hex.toLowerCase());
    }
  });
});
