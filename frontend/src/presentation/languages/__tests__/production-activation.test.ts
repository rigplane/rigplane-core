/**
 * MOR-1400 A — production composition pin.
 *
 * The language stylesheets are intentionally scoped and inert until App owns
 * the canonical activation attributes. Keep this source-level guard close to
 * the language contract: the built-dist browser suite proves reachability,
 * while this prevents the production entrypoint from silently losing a
 * registered stylesheet or replacing the theme catalogue with a second
 * polarity list.
 *
 * The expected import list is derived from the declarations barrel's own
 * export surface, rather than hand-listed: a hand-listed literal per family
 * is exactly what let a fourth registered family with no App.svelte import
 * pass this check silently (MOR-2148 round-2 review). Deliberately NOT
 * sourced from `listDesignLanguageIds()`/the live registry — same reason
 * `../layout-compatibility-inventory.test.ts`'s own `SHIPPED_MANIFESTS`
 * gives for the same choice: that registry is module-scope, mutable state
 * a sibling test file can write a throwaway probe manifest into (e.g.
 * `registry.test.ts`'s "no hardcoded family count" case), and this project
 * runs under `isolate: false` (`vite.config.ts`), so such a probe can leak
 * into this file's read of the registry. The barrel's own exports carry no
 * such risk.
 *
 * This assumes the naming convention every family shipped so far follows —
 * `presentation/languages/<id>/<id>.css` — which the assertion below itself
 * enforces for every id the barrel currently exports.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import type { DesignLanguageManifest } from '../contract';
// Namespace import used ONLY to derive the manifest list structurally, same
// discipline as `../layout-compatibility-inventory.test.ts`'s
// `languageDeclarationsBarrel` import.
import * as languageDeclarationsBarrel from '../declarations';

const app = readFileSync('src/App.svelte', 'utf8');

/** Structural `DesignLanguageManifest` guard for filtering the barrel's
 *  export surface — mirrors `layout-compatibility-inventory.test.ts`'s
 *  local guard of the same name and shape. */
function isDesignLanguageManifest(value: unknown): value is DesignLanguageManifest {
  return (
    typeof value === 'object' && value !== null &&
    typeof (value as { id?: unknown }).id === 'string' &&
    typeof (value as { displayName?: unknown }).displayName === 'string' &&
    Array.isArray((value as { layoutCompatibility?: unknown }).layoutCompatibility) &&
    typeof (value as { renderers?: unknown }).renderers === 'object'
  );
}

const SHIPPED_MANIFESTS: readonly DesignLanguageManifest[] =
  Object.values(languageDeclarationsBarrel).filter(isDesignLanguageManifest);

describe('MOR-1400 A — production design-language activation', () => {
  it('production App imports every registered design-language stylesheet', () => {
    // Guards the derivation itself: if the barrel filter ever matched
    // nothing, every assertion below would pass vacuously.
    expect(SHIPPED_MANIFESTS.length).toBeGreaterThan(0);
    for (const { id } of SHIPPED_MANIFESTS) {
      expect(app).toContain(`import './presentation/languages/${id}/${id}.css';`);
    }
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
