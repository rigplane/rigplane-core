/**
 * MOR-1099 — the semantic-architecture guard against a repeat of
 * `SdrVfoScreen.svelte`: a `.svelte` file left under `src/skins/` that
 * nothing imports (a pre-migration prototype nobody wired in or deleted).
 * `skins/__tests__/entrypoints.test.ts` pins that every REGISTERED `SkinId`
 * mounts something real, but it is keyed off `SKIN_ENTRYPOINT_COVERAGE`, a
 * hand-written table of registry entries — it would not have caught
 * `SdrVfoScreen.svelte`, which was never a registered `SkinId` at all, only
 * an unmounted sibling file inside `sdr-test/`. Neither would any other
 * existing test: a repo-wide search for a census over `src/skins/**\/*.svelte`
 * (grep for `readdirSync` under `frontend/src/__tests__`, `frontend/src/skins`
 * and `frontend/src/presentation` on 2026-08-31) found none.
 *
 * This file derives the `.svelte` file set from the filesystem (no
 * hand-maintained list) and asserts each one has a NON-TEST importer — a
 * static `from '...'` or dynamic `import('...')` specifier matched inside a
 * file outside any `__tests__` directory. Test files are excluded from the
 * importer-candidate set on purpose: a test can quote a path as a string
 * fixture (an eslint-rule test case) or a docstring can quote one as a
 * worked example, and under a text-level scan both are indistinguishable
 * from a real import — only a non-test import site is evidence the file
 * actually ships. An earlier version of this file scanned every
 * `.ts`/`.svelte` file including tests, and it was inert for 3 of the 6
 * rows as a result: `SdrTestSkin.svelte` was credited by this very file's
 * own docstring example string, and `LcdScopeSkin.svelte`/
 * `LcdCockpitSkin.svelte` were credited by `architecture-boundaries.test.ts`'s
 * eslint-fixture template literals — none of the three had any way to fail
 * this test even when their real (registry.ts) import was deleted. Caught
 * in independent review; fixed by excluding `__tests__` paths from the
 * importer-candidate set below.
 *
 * Same text-level technique `presentation/layouts/__tests__/sdr-registration.test.ts`
 * and `__tests__/architecture-boundaries.test.ts` already use (source read
 * via `readFileSync`, matched with a regex) rather than a real module
 * resolver — sufficient here because the question is "does a non-test file
 * contain an import specifier ending in this filename", not "does it
 * resolve" or "does it mount".
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SKINS_ROOT = 'src/skins';
const SRC_ROOT = 'src';

function walk(dir: string, matches: (path: string) => boolean, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      walk(full, matches, acc);
    } else if (matches(full)) {
      acc.push(full);
    }
  }
  return acc;
}

const skinSvelteFiles = walk(SKINS_ROOT, (path) => path.endsWith('.svelte'));

// Deliberately excludes any path containing `__tests__`: a test file quoting
// another file's path as a string (a docstring example, an eslint-rule
// fixture) reads identically to a real import under this text-level scan,
// and crediting it would let an unmounted file hide behind its own test
// suite's prose. See the file header for the concrete instance this caught.
const nonTestSourceFiles = walk(
  SRC_ROOT,
  (path) => (path.endsWith('.ts') || path.endsWith('.svelte')) && !path.includes('__tests__'),
);

function escapeForRegex(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * True when some OTHER non-test file under `src/` has an import specifier
 * (static `from '...'`/`"..."` or dynamic `import('...')`) ending in this
 * file's own basename — e.g. `from '../../skins/sdr-test/SdrTestSkin.svelte'`
 * or `import('./sdr-test/SdrTestSkin.svelte')`. Every current basename under
 * `src/skins/**\/*.svelte` is unique, so a basename match cannot cross-credit
 * a different skin's importer — but basename uniqueness alone does not stop
 * a test file's fixture string or a docstring's worked example from
 * matching the same regex a real import would; only excluding `__tests__`
 * from the candidate set (`nonTestSourceFiles`, above) does that.
 */
function hasNonTestImporter(targetFile: string): boolean {
  const basename = targetFile.split('/').pop()!;
  const importPattern = new RegExp(
    `(?:from\\s+|import\\()['"][^'"]*/${escapeForRegex(basename)}['"]`,
  );
  return nonTestSourceFiles.some((file) => {
    if (file === targetFile) return false;
    return importPattern.test(readFileSync(file, 'utf8'));
  });
}

describe('every skin .svelte file has a non-test importer under src/ (MOR-1099)', () => {
  // Kills: an unmounted prototype left under skins/ — the exact shape
  // `SdrVfoScreen.svelte` was before this ticket deleted it. A file that
  // fails this must either gain a real (non-test) importer or be deleted.
  it.each(skinSvelteFiles)('%s has a non-test importer under src/', (file) => {
    expect(
      hasNonTestImporter(file),
      `${file}: no import specifier in any non-test file under src/ resolves to this file — ` +
        'an unmounted .svelte file under skins/ must not linger (MOR-1099)',
    ).toBe(true);
  });
});
