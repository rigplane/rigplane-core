/**
 * MOR-1400 A — production composition pin.
 *
 * The language stylesheets are intentionally scoped and inert until App owns
 * the canonical activation attributes. Keep this source-level guard close to
 * the language contract: the built-dist browser suite proves reachability,
 * while this prevents the production entrypoint from silently losing either
 * stylesheet or replacing the theme catalogue with a second polarity list.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const app = readFileSync('src/App.svelte', 'utf8');

describe('MOR-1400 A — production design-language activation', () => {
  it('production App imports every registered design-language stylesheet', () => {
    expect(app).toContain("import './presentation/languages/studioline/studioline.css';");
    expect(app).toContain("import './presentation/languages/fieldline/fieldline.css';");
  });

  it('uses the existing theme catalogue for the canonical language-mode writer', () => {
    expect(app).toContain("import { getAvailableThemes } from './components-v2/theme/theme-switcher';");
    expect(app).toMatch(/getAvailableThemes\(\)\.find\(\(\{ id \}\) => id === getWorkspace\(\)\.theme\)/);
    expect(app).toMatch(/document\.documentElement\.dataset\.languageMode\s*=\s*theme\?\.category === 'light' \? 'light' : 'dark'/);
  });

  it('keeps the language and language-mode attributes in the same activation gate', () => {
    expect(app).toMatch(/if \(activated === null\) \{\s*delete document\.documentElement\.dataset\.designLanguage;\s*delete document\.documentElement\.dataset\.languageMode;/s);
  });
});
