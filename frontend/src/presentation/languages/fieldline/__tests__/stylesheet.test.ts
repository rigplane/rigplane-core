/**
 * MOR-1074 — cascade pins for the `fieldline` stylesheet.
 *
 * WHY THESE RESOLVE THE CASCADE BY HAND. Same reason as studioline's twin
 * file: three of fieldline's state channels are decided by the CASCADE, not by
 * any single declaration — the fault rail beats the RF-doubt rail only because
 * it comes later at equal specificity, the fault BAND beats the doubt band the
 * same way, and the keyed slab beats the `:disabled` treatment only because it
 * scores higher. None of that is observable from the renderer unit tests, and
 * none of it is observable in jsdom either: jsdom's `getComputedStyle` resolves
 * no `:has()` and substitutes no custom property, so it reports an empty border
 * width and a literal `var(...)` colour. The studioline review cycle proved the
 * gap the expensive way — the ordering bug was reintroduced and every unit and
 * in-page assertion stayed green while the fault rail rendered amber.
 *
 * So this file parses the sheet and ranks the matching rules the way a browser
 * would — (specificity, source order) — and asserts the WINNER. That catches
 * both mutation classes: moving a rule earlier, and weakening its selector.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { FIELDLINE_PALETTE } from '../tokens';

const source = readFileSync('src/presentation/languages/fieldline/fieldline.css', 'utf8');
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
 * column is constant and omitted. `:has()` contributes its argument, exactly as
 * the spec says.
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

type Target = 'surface' | 'band' | 'slab';

/** Does `selector` match this state, for the given target element? */
function matches(selector: string, state: SurfaceState, target: Target): boolean {
  const wantsSlab = selector.includes('.rx-tx-key');
  const wantsBand = selector.includes('.rx-tx-state');
  const wants: Target = wantsSlab ? 'slab' : wantsBand ? 'band' : 'surface';
  if (wants !== target) return false;
  if (wants === 'surface' && !selector.includes('.rx-tx-surface')) return false;
  if (selector.includes(':disabled') && !state.keyDisabled) return false;
  for (const [, attribute, value] of selector.matchAll(/:has\(\[data-(session|rf)='([^']+)']\)/g)) {
    if ((attribute === 'session' ? state.session : state.rf) !== value) return false;
  }
  // A rule scoped to a state attribute this selector never names still applies.
  return true;
}

/** The declaration a browser would use: highest specificity, then latest. */
function winner(property: string, state: SurfaceState, target: Target = 'surface'): string | undefined {
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
  it('found the rail, band and slab rules it is about to rank', () => {
    expect(RULES.length).toBeGreaterThan(20);
    expect(RULES.filter((r) => STATE_SCOPED.test(r.selector))).not.toHaveLength(0);
  });
});

describe('F1 — the fault rail WINS over the RF-doubt rail, it does not merely exist', () => {
  it('renders a fault red, not the amber of the doubt rail it overlaps with', () => {
    expect(winner('border-inline-start', FAULT)).toBe('24px solid var(--dl-fieldline-tx-active)');
    expect(winner('border-inline-start', FAULT)).not.toContain('tx-tuning');
  });

  it('still leaves RF doubt amber when the session has NOT failed', () => {
    expect(winner('border-inline-start', RF_UNKNOWN)).toBe('16px solid var(--dl-fieldline-tx-tuning)');
    expect(winner('border-inline-start', { session: 'idle', rf: 'uncertain' }))
      .toBe('16px solid var(--dl-fieldline-tx-tuning)');
  });

  it('the two rules really do collide — the pin above is not vacuous', () => {
    const colliding = RULES.filter(
      (r) => r.declarations['border-inline-start'] && matches(r.selector, FAULT, 'surface'));
    expect(colliding.length).toBeGreaterThanOrEqual(2);
    expect(colliding.map((r) => r.declarations['border-inline-start']))
      .toContain('16px solid var(--dl-fieldline-tx-tuning)');
  });

  it('every rail flood is one of the declared 8/16/24px widths', () => {
    for (const state of [PENDING, KEYED, RELEASING, FAULT, RF_UNKNOWN]) {
      expect(winner('border-inline-start', state))
        .toMatch(/^(?:16|24)px solid var\(--dl-fieldline-tx-\w+\)$/);
    }
    // The RX baseline is the token'd 8px rail, on the RX tone.
    expect(winner('border-inline-start', RX))
      .toBe('var(--dl-fieldline-rail) solid var(--dl-fieldline-rx-idle)');
  });

  it('the carrier is the INLINE-START edge — a top rail would be studioline', () => {
    expect(css).not.toMatch(/border-top:/);
    expect(css).toMatch(/border-inline-start:/);
  });
});

describe('F2 — the TX slab carries state in GEOMETRY, not only in colour (N2)', () => {
  /** The two non-colour channels: edge style and whether the slab is filled. */
  function geometry(state: SurfaceState): string {
    // `border: var(--dl-fieldline-border) solid currentcolor` on the base rule
    // is the shorthand that seeds the edge, so an absent `border-style`
    // override means `solid`.
    const style = winner('border-style', state, 'slab')
      ?? winner('border', state, 'slab')?.split(/\s+/)[1]
      ?? 'solid';
    const background = winner('background', state, 'slab') ?? 'none';
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
    expect(geometry(KEYED)).toBe('solid/solid');
    expect(geometry(RELEASING)).toBe('solid/solid');
    const keyed = RULES.find((r) => r.selector.includes("session='keyed'") && r.selector.includes('.rx-tx-key'))!;
    const inert = RULES.find((r) => r.selector.includes(':disabled') && r.selector.includes('.rx-tx-key'))!;
    expect(keyed.specificity).toBeGreaterThan(inert.specificity);
  });

  it('the slab is square and full-width in every state — no pill anywhere', () => {
    expect(winner('border-radius', RX, 'slab')).toBe('0');
    expect(winner('border-radius', FAULT, 'slab')).toBe('0');
    expect(winner('width', RX, 'slab')).toBe('100%');
    expect(winner('min-height', RX, 'slab')).toBe('48px');
    expect(css).not.toMatch(/border-radius:\s*999px/);
  });
});

describe('F3 — the band takes over, and a fault band beats the doubt band', () => {
  it('floods solid with knocked-out black text while keyed', () => {
    expect(winner('background', KEYED, 'band')).toBe('var(--dl-fieldline-tx-active)');
    expect(winner('color', KEYED, 'band')).toBe('var(--dl-fieldline-knockout)');
  });

  it('a fault band is filled red, not the outlined amber of the doubt band', () => {
    expect(winner('background', FAULT, 'band')).toBe('var(--dl-fieldline-tx-active)');
    expect(winner('border-color', FAULT, 'band')).toBe('var(--dl-fieldline-tx-active)');
    expect(winner('color', FAULT, 'band')).toBe('var(--dl-fieldline-knockout)');
  });

  it('doubt and pending stay OUTLINED — an unfilled band is a different reading', () => {
    for (const state of [PENDING, RF_UNKNOWN]) {
      expect(winner('border-color', state, 'band')).toBe('var(--dl-fieldline-tx-tuning)');
      expect(winner('background', state, 'band')).toBe('none');
    }
  });

  it('RX shows no band at all: a transparent edge and no fill', () => {
    expect(winner('border', RX, 'band')).toBe('var(--dl-fieldline-border) solid transparent');
    expect(winner('background', RX, 'band')).toBe('none');
  });
});

describe('the CSS half honours the same constraints as the token half', () => {
  it('is activated ONLY by the design-language attribute (owner decision Q2)', () => {
    // Every rule in the sheet is rooted at the attribute — no class, prop or
    // context can switch this language on.
    for (const rule of RULES) {
      expect(rule.selector).toMatch(/^\[data-design-language='fieldline']\[data-design-language]/);
    }
  });

  it('never flips polarity on prefers-color-scheme — daylight is an explicit opt-in', () => {
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
    expect(css).toMatch(/outline:\s*3px solid var\(--dl-fieldline-focus\)/);
  });

  it('wins on specificity rather than on `!important`', () => {
    expect(css).not.toMatch(/!important/);
  });

  it('declares no motion at all, which is why it needs no reduced-motion block', () => {
    expect(css).not.toMatch(/transition/);
    expect(css).not.toMatch(/animation/);
    expect(css).not.toMatch(/@keyframes/);
  });

  it('is flat by construction: no shadow, no inset, no radial or linear wash', () => {
    expect(css).not.toMatch(/box-shadow/);
    // The one gradient is the pending HATCH, which is a pattern, not a wash.
    const gradients = css.match(/[\w-]*gradient\(/g) ?? [];
    expect(gradients).toEqual(['repeating-linear-gradient(']);
  });

  it('declares every palette entry it references as a custom property', () => {
    for (const hex of Object.values(FIELDLINE_PALETTE)) {
      expect(css.toLowerCase()).toContain(hex.toLowerCase());
    }
  });
});
