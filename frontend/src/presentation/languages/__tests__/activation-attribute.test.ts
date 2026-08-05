/**
 * MOR-1278 — the activation-attribute pin, for EVERY design language.
 *
 * `[data-design-language]` is the canonical — and only — sanctioned
 * design-language activation mechanism (doctrine: `../contract.ts` on
 * `DesignLanguageManifest.id`, and
 * `docs/plans/2026-07-25-ui-composition-architecture-v3.md` under "Design
 * language"). A `.studioline-*` class, a `[data-theme='fieldline']` attribute
 * or any other competing selector would silently start working the moment a
 * host introduced it, without ever touching the attribute this contract
 * requires — and the renderer half (MOR-1275) reads that same attribute, so a
 * second switch would let the CSS and renderer halves disagree about which
 * language is on.
 *
 * GENERALISED (MOR-1275). This replaces the two per-language copies that used
 * to live in `studioline/__tests__/activation-attribute.test.ts` and inside
 * `fieldline/__tests__/stylesheet.test.ts`. A copy per language is a pin that
 * a THIRD language is added without: the doctrine holds for every family, so
 * the test discovers families from the filesystem instead of naming them, and
 * derives the attribute each sheet must be scoped to from its own directory
 * name. The `sheets are discovered` guard below is what keeps the glob from
 * going vacuously green if the layout ever moves.
 *
 * Parsing uses the same flat selector/rule idiom the per-language
 * `stylesheet.test.ts` files already use — no new parser.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect } from 'vitest';

const ROOT = 'src/presentation/languages';

interface Sheet { language: string; path: string; css: string }

/** Every `languages/<family>/<family>.css`, keyed by the directory that names the family. */
function discoverSheets(): Sheet[] {
  return readdirSync(ROOT)
    .filter((entry) => statSync(join(ROOT, entry)).isDirectory() && !entry.startsWith('__'))
    .flatMap((language) =>
      readdirSync(join(ROOT, language))
        .filter((file) => file.endsWith('.css'))
        .map((file) => {
          const path = join(ROOT, language, file);
          // Comments name the very constructs the pins below forbid, so they
          // are stripped first: this is about SELECTORS, not about prose.
          return { language, path, css: readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '') };
        }));
}

const SHEETS = discoverSheets();

/** Every comma-separated selector in the sheet, one entry per rule (flat CSS, no at-rules). */
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

describe('MOR-1278 — the glob finds design-language stylesheets to pin', () => {
  it('discovered at least one sheet, so no assertion below can pass vacuously', () => {
    expect(SHEETS.length).toBeGreaterThanOrEqual(1);
  });

  it('discovered every registered family that ships CSS', () => {
    // Named on purpose: a family whose sheet stopped being discovered (moved,
    // renamed) would otherwise silently drop out of every pin below.
    expect(SHEETS.map((s) => s.language)).toEqual(
      expect.arrayContaining(['studioline', 'fieldline']),
    );
  });
});

describe.each(SHEETS)(
  'MOR-1278 — $language is activated ONLY by [data-design-language]', ({ language, css }) => {
    // Derived from the directory name, never hardcoded: the doctrine says the
    // attribute's value MUST equal the registered manifest id.
    const attribute = `[data-design-language='${language}']`;
    const SELECTORS = selectors(css);

    it('parses into selectors worth pinning', () => {
      expect(SELECTORS.length).toBeGreaterThan(20);
    });

    it('opens every selector with the sanctioned attribute, doubled for specificity', () => {
      // The doubled attribute is the deliberate one-step raise both sheets use
      // to outrank Svelte's own (0,2,0) component rules; a single attribute
      // would tie and lose on order, i.e. the language would silently fail to
      // restyle the surfaces it exists to restyle.
      const unscoped = SELECTORS.filter(
        (s) => !s.startsWith(`${attribute}[data-design-language]`));
      expect(unscoped).toEqual([]);
    });

    it('roots no selector in a competing mechanism (class, alternate attribute, :root, bare element)', () => {
      // A competing activation mechanism shows up as a selector whose FIRST
      // compound — before the first combinator/space — is not the sanctioned
      // attribute: `.fieldline-foo …`, `[data-theme='fieldline'] …`, `:root …`.
      const competing = SELECTORS.map((s) => s.split(/\s+/)[0])
        .filter((c) => !c.startsWith(attribute));
      expect(competing).toEqual([]);
    });

    it(`contains no bare .${language}-* class selector anywhere`, () => {
      // Catches a class-based activation selector even when it is added
      // mid-selector (combined with the attribute rather than replacing it)
      // rather than only at the selector root.
      expect(css).not.toMatch(new RegExp(`\\.${language}-`));
    });
  });
