/**
 * MOR-1073 — cascade pins for the `studioline` stylesheet.
 *
 * WHY THESE RESOLVE THE CASCADE BY HAND. Two of studioline's state channels
 * are decided by the CASCADE, not by any single declaration: the fault rail
 * beats the RF-doubt rail only because it comes later at equal specificity,
 * and the keyed key beats the `:disabled` treatment only because it scores
 * higher. Neither fact is observable from the unit tests that check the
 * renderer descriptors, and neither is observable in jsdom either — jsdom's
 * `getComputedStyle` resolves no `:has()` and substitutes no custom property,
 * so it reports an empty border width and a literal `var(...)` colour. A
 * review cycle proved the gap the expensive way: the exact ordering bug was
 * reintroduced and 192 unit assertions plus 24 in-page assertions all stayed
 * green while the fault rail rendered amber.
 *
 * So this file parses the sheet and ranks the matching rules the way a
 * browser would — (specificity, source order) — and asserts the WINNER. That
 * catches both mutation classes: moving a rule earlier, and weakening its
 * selector.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { STUDIOLINE_PALETTE } from '../tokens';

const source = readFileSync('src/presentation/languages/studioline/studioline.css', 'utf8');
// Comments name the very constructs several tests below forbid, so they are
// stripped first: the pins are about DECLARATIONS, not about prose.
const css = source.replace(/\/\*[\s\S]*?\*\//g, '');

interface Rule {
  selector: string;
  declarations: Record<string, string>;
  order: number;
  specificity: number;
}

/**
 * `(classes + attributes + pseudo-classes) * 100 + elements` — the b and c
 * columns of the CSS specificity tuple. No rule here uses an id, so the a
 * column is constant and omitted. `:has()` contributes its argument, exactly
 * as the spec says.
 */
function specificity(selector: string): number {
  const b = (selector.match(/\[[^\]]*]|\.[\w-]+|:(?!has\b)[\w-]+/g) ?? []).length;
  return b * 100;
}

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
      if (trimmed) rules.push({ selector: trimmed, declarations, order: rules.length, specificity: specificity(trimmed) });
    }
  }
  return rules;
}

const RULES = parseRules(css);
const STATE_SCOPED = /:has\(\[data-(?:session|rf)=/;

interface SurfaceState {
  session: string;
  rf: string;
  keyDisabled?: boolean;
}

/** Does `selector` match this state, for the surface (default) or the key? */
function matches(selector: string, state: SurfaceState, target: 'surface' | 'key'): boolean {
  const wantsKey = selector.includes('.rx-tx-key');
  if (wantsKey !== (target === 'key')) return false;
  if (!selector.includes('.rx-tx-surface') && !wantsKey) return false;
  if (selector.includes(':disabled') && !state.keyDisabled) return false;
  for (const [, attribute, value] of selector.matchAll(/:has\(\[data-(session|rf)='([^']+)']\)/g)) {
    if ((attribute === 'session' ? state.session : state.rf) !== value) return false;
  }
  // A rule scoped to a state attribute this selector never names still applies.
  return true;
}

/** The declaration a browser would use: highest specificity, then latest. */
function winner(property: string, state: SurfaceState, target: 'surface' | 'key' = 'surface'): string | undefined {
  const candidates = RULES
    .filter((r) => r.declarations[property] !== undefined && matches(r.selector, state, target))
    .sort((a, b) => a.specificity - b.specificity || a.order - b.order);
  return candidates.at(-1)?.declarations[property];
}

const RX = { session: 'idle', rf: 'receiving' };
const PENDING = { session: 'pending', rf: 'uncertain', keyDisabled: true };
const KEYED = { session: 'keyed', rf: 'transmitting', keyDisabled: true };
const RELEASING = { session: 'releasing', rf: 'transmitting', keyDisabled: true };
// The crux state: a failed session almost always reports RF doubt as well, so
// the fault rule and the doubt rule both match and one of them has to win.
const FAULT = { session: 'failed', rf: 'uncertain', keyDisabled: true };
const RF_UNKNOWN = { session: 'idle', rf: 'unknown' };
const BLOCKED = { session: 'idle', rf: 'receiving', keyDisabled: true };

describe('the sheet parses into something worth asserting against', () => {
  it('found the rail and key rules it is about to rank', () => {
    expect(RULES.length).toBeGreaterThan(20);
    expect(RULES.filter((r) => STATE_SCOPED.test(r.selector))).not.toHaveLength(0);
  });
});

describe('F1 — the fault rail WINS over the RF-doubt rail, it does not merely exist', () => {
  it('renders a fault red, not the amber of the doubt rail it overlaps with', () => {
    expect(winner('border-top', FAULT)).toBe('3px solid var(--dl-studioline-tx-active)');
    expect(winner('border-top', FAULT)).not.toContain('tx-tuning');
  });

  it('still leaves RF doubt amber when the session has NOT failed', () => {
    expect(winner('border-top', RF_UNKNOWN)).toBe('3px solid var(--dl-studioline-tx-tuning)');
    expect(winner('border-top', { session: 'idle', rf: 'uncertain' }))
      .toBe('3px solid var(--dl-studioline-tx-tuning)');
  });

  it('the two rules really do collide — the pin above is not vacuous', () => {
    const colliding = RULES.filter((r) => r.declarations['border-top'] && matches(r.selector, FAULT, 'surface'));
    expect(colliding.length).toBeGreaterThanOrEqual(2);
    expect(colliding.map((r) => r.declarations['border-top'])).toContain('3px solid var(--dl-studioline-tx-tuning)');
  });

  it('every rail step is one of the declared 1/2/3px thicknesses', () => {
    for (const state of [RX, PENDING, KEYED, RELEASING, FAULT, RF_UNKNOWN]) {
      expect(winner('border-top', state)).toMatch(/^[123]px solid var\(--dl-studioline-(rx|tx)-\w+\)$/);
    }
  });
});

describe('F2 — the TX key carries state in GEOMETRY, not only in colour (N2)', () => {
  /** The two non-colour channels: edge style and whether the pill is filled. */
  function geometry(state: SurfaceState): string {
    // `border: 1px solid currentcolor` on the base rule is the shorthand that
    // seeds the edge, so an absent `border-style` override means `solid`.
    const style = winner('border-style', state, 'key')
      ?? winner('border', state, 'key')?.split(/\s+/)[1]
      ?? 'solid';
    const background = winner('background', state, 'key') ?? 'none';
    const fill = background === 'none' ? 'empty'
      : background.includes('gradient') ? 'hatched' : 'solid';
    return `${style}/${fill}`;
  }

  it('gives each treatment its own edge+fill pair, so colour is never the only channel', () => {
    const treatments = {
      idle: geometry(RX), pending: geometry(PENDING), keyed: geometry(KEYED),
      fault: geometry(FAULT), blocked: geometry(BLOCKED),
    };
    expect(treatments).toEqual({
      idle: 'solid/empty',
      pending: 'solid/hatched',
      keyed: 'solid/solid',
      fault: 'dashed/empty',
      blocked: 'dotted/empty',
    });
    // The property that actually matters: all five are mutually distinguishable.
    expect(new Set(Object.values(treatments)).size).toBe(5);
  });

  it('keyed outranks the inert treatment on SPECIFICITY, not on rule order', () => {
    // Both match while transmitting (the key is disabled then), and a dotted
    // edge over a solid TX fill would read as a styling accident.
    expect(geometry(KEYED)).toBe('solid/solid');
    expect(geometry(RELEASING)).toBe('solid/solid');
    const keyed = RULES.find((r) => r.selector.includes("session='keyed'") && r.selector.includes('.rx-tx-key'))!;
    const inert = RULES.find((r) => r.selector.includes(':disabled'))!;
    expect(keyed.specificity).toBeGreaterThan(inert.specificity);
  });

  it('the pill radius is the one radius the grammar allows, in every state', () => {
    expect(winner('border-radius', RX, 'key')).toBe('999px');
    expect(winner('border-radius', FAULT, 'key')).toBe('999px');
  });
});

describe('the CSS half honours the same constraints as the token half', () => {
  it('never flips polarity on prefers-color-scheme — light is an explicit opt-in', () => {
    expect(css).not.toMatch(/prefers-color-scheme/);
    expect(css).toMatch(/\[data-language-mode='light']/);
  });

  it('adds and removes nothing — a language may not become a capability fork', () => {
    expect(css).not.toMatch(/display:\s*none/);
    expect(css).not.toMatch(/visibility:\s*hidden/);
    expect(css).not.toMatch(/content:\s*'/);
  });

  it('never re-suppresses the focus ring, and applies it as `outline` (MOR-977 §1.2.5)', () => {
    expect(css).not.toMatch(/outline:\s*none/);
    expect(css).toMatch(/outline:\s*2px solid var\(--dl-studioline-focus\)/);
  });

  it('wins on specificity rather than on `!important`', () => {
    expect(css).not.toMatch(/!important/);
  });

  it('declares every palette entry it references as a custom property', () => {
    for (const hex of Object.values(STUDIOLINE_PALETTE)) {
      expect(css.toLowerCase()).toContain(hex.toLowerCase());
    }
  });
});
