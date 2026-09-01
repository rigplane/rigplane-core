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
 * hand-maintained list) and asserts each one is imported — by a static
 * `from '...'` or dynamic `import('...')` specifier — from at least one
 * other file under `src/`. That is a text-level import-graph check, the
 * same technique `presentation/layouts/__tests__/sdr-registration.test.ts`
 * and `__tests__/architecture-boundaries.test.ts` already use (source read
 * via `readFileSync`, matched with a regex) rather than a real module
 * resolver — sufficient here because the question is "does any import
 * specifier end in this filename", not "does it resolve".
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
const allSourceFiles = walk(
  SRC_ROOT,
  (path) => path.endsWith('.ts') || path.endsWith('.svelte'),
);

function escapeForRegex(literal: string): string {
  return literal.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * True when some OTHER file under `src/` has an import specifier (static
 * `from '...'`/`"..."` or dynamic `import('...')`) ending in this file's own
 * basename — e.g. `from '../../skins/sdr-test/SdrTestSkin.svelte'` or
 * `import('./sdr-test/SdrTestSkin.svelte')`. Every current basename under
 * `src/skins/**\/*.svelte` is unique, so a basename match cannot cross-credit
 * a different skin's importer.
 */
function hasAnyImporter(targetFile: string): boolean {
  const basename = targetFile.split('/').pop()!;
  const importPattern = new RegExp(
    `(?:from\\s+|import\\()['"][^'"]*/${escapeForRegex(basename)}['"]`,
  );
  return allSourceFiles.some((file) => {
    if (file === targetFile) return false;
    return importPattern.test(readFileSync(file, 'utf8'));
  });
}

describe('every skin .svelte file is reachable from the app (MOR-1099)', () => {
  // Kills: an unmounted prototype left under skins/ — the exact shape
  // `SdrVfoScreen.svelte` was before this ticket deleted it. A file that
  // fails this must either gain a real importer or be deleted.
  it.each(skinSvelteFiles)('%s has at least one importer under src/', (file) => {
    expect(
      hasAnyImporter(file),
      `${file}: no import specifier anywhere under src/ resolves to this file — ` +
        'an unmounted .svelte file under skins/ must not linger (MOR-1099)',
    ).toBe(true);
  });
});
