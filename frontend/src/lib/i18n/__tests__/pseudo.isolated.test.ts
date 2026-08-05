import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { registerCatalog, _resetCatalogs } from '../runtime';
import { pseudoize, resolvePseudo } from '../pseudo';

const EN = {
  'common.action.save': 'Save',
  'common.action.cancel': 'Cancel',
  'core.statusbar.connection.connectingTo':
    'Connecting to {radio} on {transport}…',
  'core.error.literalBrace.example': "Use '{name} to refer to a placeholder.",
};

beforeEach(() => {
  _resetCatalogs();
  registerCatalog('en-US', EN);
});

afterEach(() => {
  _resetCatalogs();
});

describe('pseudoize', () => {
  it('wraps every output in ⟦…⟧', () => {
    const out = pseudoize('Save');
    expect(out.startsWith('⟦')).toBe(true);
    expect(out.endsWith('⟧')).toBe(true);
  });

  it('substitutes ASCII letters with diacritic forms', () => {
    const out = pseudoize('Save');
    // Inside the brackets the four letters should each have been mapped.
    expect(out).toMatch(/[ŠšŚś].*[Ááâã]/);
    expect(out).not.toContain('Save'); // raw English must not remain
  });

  it('expands the string by at least 35%', () => {
    const source = 'Save';
    const out = pseudoize(source);
    // Strip the ⟦⟧ brackets, count characters.
    const inner = out.slice(1, -1);
    expect(inner.length).toBeGreaterThanOrEqual(Math.ceil(source.length * 1.35));
  });

  it('preserves {name} placeholders verbatim', () => {
    const out = pseudoize('Connecting to {radio} on {transport}…');
    expect(out).toContain('{radio}');
    expect(out).toContain('{transport}');
  });

  it("preserves '{' literal-brace escape verbatim", () => {
    const out = pseudoize("Use '{'name} verbatim");
    expect(out).toContain("'{'");
    // The escape is preserved as a passthrough atom; surrounding ASCII
    // text is still diacritic-substituted.
    expect(out).not.toContain('Use');
  });

  it('handles empty strings without crashing', () => {
    expect(pseudoize('')).toBe('⟦⟧');
  });
});

describe('resolvePseudo', () => {
  it('runs the catalog value through the transform', () => {
    const out = resolvePseudo('common.action.save');
    expect(out.startsWith('⟦')).toBe(true);
    expect(out.endsWith('⟧')).toBe(true);
  });

  it('interpolates params after pseudo-transform without diacritizing them', () => {
    const out = resolvePseudo('core.statusbar.connection.connectingTo', {
      radio: 'IC-7610',
      transport: 'LAN',
    });
    // Brand-like values (radios, transport names) must come through
    // verbatim — they would be glossary/protocol values in real call sites.
    expect(out).toContain('IC-7610');
    expect(out).toContain('LAN');
  });

  it('returns [missing:key] when en-US lacks the key', () => {
    expect(resolvePseudo('does.not.exist')).toBe('[missing:does.not.exist]');
  });
});
