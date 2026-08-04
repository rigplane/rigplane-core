/**
 * MOR-1278 — activation-attribute pin.
 *
 * `[data-design-language]` is the canonical — and only — sanctioned
 * design-language activation mechanism (doctrine: `../../contract.ts` on
 * `DesignLanguageManifest.id`, and
 * `docs/plans/2026-07-25-ui-composition-architecture-v3.md` under "Design
 * language"). This test parses `studioline.css` with the same flat
 * selector/rule idiom `stylesheet.test.ts` already uses (no new parser) and
 * fails if ANY rule that styles studioline surfaces is not scoped under
 * `[data-design-language='studioline']` — a `.studioline-*` class, a
 * `[data-theme='studioline']` attribute, or any other competing activation
 * selector would silently start working the moment a host introduced it,
 * without ever touching the attribute this contract requires.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';

const source = readFileSync('src/presentation/languages/studioline/studioline.css', 'utf8');
// Same comment-stripping idiom as stylesheet.test.ts — pins are about
// selectors, not the prose describing them.
const css = source.replace(/\/\*[\s\S]*?\*\//g, '');

const ACTIVATION_ATTRIBUTE = "[data-design-language='studioline']";

/** Every comma-separated selector in the sheet, one entry per rule (flat CSS, no at-rules — same assumption stylesheet.test.ts's parser makes). */
function selectors(sheet: string): string[] {
  const out: string[] = [];
  for (const [, selectorList] of sheet.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
    for (const selector of selectorList.split(',')) {
      const trimmed = selector.trim();
      if (trimmed) out.push(trimmed);
    }
  }
  return out;
}

const SELECTORS = selectors(css);

describe('MOR-1278 — the sheet has selectors worth pinning', () => {
  it('found more than a handful of rules', () => {
    expect(SELECTORS.length).toBeGreaterThan(20);
  });
});

describe('MOR-1278 — every studioline rule is scoped under [data-design-language]; nothing else may activate it', () => {
  it('every selector opens with the sanctioned activation attribute', () => {
    const unscoped = SELECTORS.filter((s) => !s.startsWith(ACTIVATION_ATTRIBUTE));
    expect(unscoped).toEqual([]);
  });

  it('no selector\'s root compound is a competing mechanism (class, alternate attribute, :root, bare element)', () => {
    // A competing activation mechanism would show up as a selector whose
    // FIRST compound (before the first combinator/space) is not the
    // sanctioned attribute — e.g. `.studioline-foo …`, `[data-theme=
    // 'studioline'] …`, or `:root …`.
    const rootCompounds = SELECTORS.map((s) => s.split(/\s+/)[0]);
    const competing = rootCompounds.filter((c) => !c.startsWith(ACTIVATION_ATTRIBUTE));
    expect(competing).toEqual([]);
  });

  it('no bare `.studioline-*` class selector exists anywhere in the sheet', () => {
    // Catches a competing class-based activation selector even if it were
    // added mid-selector (e.g. combined with the attribute rather than
    // replacing it) rather than only at the selector root.
    expect(css).not.toMatch(/\.studioline-/);
  });
});
