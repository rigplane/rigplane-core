/**
 * MOR-1247 guard: `stageSizing` (the manifest field) and `fitsViewport` (the
 * function that reads it) are frozen declaration-only until the ScaledStage
 * primitive (MOR-1160 constraint 1) exists to mechanically enforce the
 * policy.
 *
 * This is a TEXTUAL TRIPWIRE, not a security boundary — it catches a file
 * outside this directory whose source contains the literal identifier
 * `stageSizing` or `fitsViewport`, which is what an ordinary import/read
 * looks like. Known, accepted evasions: computed-key access (building the
 * property/import name from concatenated substrings so the literal
 * identifier never appears contiguously in source), and an aliased
 * re-export from inside `layouts/` (e.g. `export { fitsViewport as
 * checkFit } from './contract'` here, consumed externally as `checkFit` —
 * the external file's source never contains the literal name either). Both
 * take deliberate effort to construct; this scan's job is to catch the
 * ordinary case, not to withstand someone trying to route around it.
 *
 * This is NOT expressible as an eslint import-boundary rule (the
 * `../../../__tests__/architecture-boundaries.test.ts` pattern): both names
 * are legitimate exports of `../contract` that other files in THIS directory
 * use freely, and eslint's `no-restricted-imports` bans a module specifier,
 * not a named read from an otherwise-legal import. Proof that the scan
 * catches an ordinary violation (temporary violation -> red -> reverted ->
 * green, sha256-verified) is recorded in the MOR-1247 build report, not
 * committed here.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

const SRC_ROOT = 'src';
const LAYOUTS_DIR = path.join(SRC_ROOT, 'presentation', 'layouts') + path.sep;
const SOURCE_EXTENSIONS = new Set(['.ts', '.svelte']);
const GUARDED_NAMES = [/\bstageSizing\b/, /\bfitsViewport\b/];

function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectSourceFiles(full));
    else if (SOURCE_EXTENSIONS.has(path.extname(entry.name))) out.push(full);
  }
  return out;
}

describe('stageSizing/fitsViewport stay declaration-only outside layouts/ (MOR-1247)', () => {
  it('no file outside presentation/layouts/ names stageSizing or fitsViewport', () => {
    const offenders = collectSourceFiles(SRC_ROOT)
      .filter((file) => !file.startsWith(LAYOUTS_DIR))
      .filter((file) => GUARDED_NAMES.some((re) => re.test(readFileSync(file, 'utf8'))));
    expect(offenders).toEqual([]);
  });
});
