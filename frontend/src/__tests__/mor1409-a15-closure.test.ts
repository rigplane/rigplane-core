/**
 * MOR-1409 A15 — final closure pins.
 *
 * A15 deletes `components-v2/wiring/command-bus.ts` (27 lines of
 * identity-preserving re-export) and `components-v2/wiring/state-adapter.ts`
 * (718 lines of stale projection twin), removes the last presentation-layer
 * radio-store edge from `StatusBar.svelte`, and shrinks the enforcement
 * ledger to the rows that still name a file which exists.
 *
 * Most of A15's evidence is ABSENCE, and an absence pin is trivially
 * satisfiable by deleting the test. These pins therefore assert absence
 * against the LIVE tree — module existence on disk, reference-shaped sweeps
 * over real source, and the executable plugin's own exported owner sets —
 * never against a snapshot a later edit could quietly re-baseline.
 *
 * The closure sweeps are REFERENCE-shaped, not substring-shaped. Thirteen
 * production PROSE references to the deleted modules survive in doc comments
 * across twelve frozen non-owner files and are deliberately accepted
 * (correction 5248024803 item 9); rewriting them would be gratuitous churn in
 * frozen files at a closure gate. Comment text is stripped before every
 * sweep, so an accepted prose reference can neither mask a real reference nor
 * fake one.
 *
 * Honesty note about which pins are kills and which are regression pins:
 * the `runtime.send(` and `setPollingMultiplier` production sweeps were
 * already zero at A15's base (A13b and A10 respectively closed them). They
 * are carried here as REGRESSION pins, not as A15 kills, and are labelled as
 * such rather than presented as green-because-of-A15.
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const FRONTEND_ROOT = process.cwd();

// Composed from fragments on purpose: the repo-wide sweep below scans test
// files too, and a bare literal here would make this file its own hit.
const BUS = ['wiring', 'command-bus'].join('/');
const ADAPTER = ['wiring', 'state-adapter'].join('/');

const BUS_FILE = `src/components-v2/${BUS}.ts`;
const ADAPTER_FILE = `src/components-v2/${ADAPTER}.ts`;

/** Every source file under `src/`, with test files partitioned out. */
function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(resolve(FRONTEND_ROOT, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) walk(rel, out);
    else if (/\.(ts|svelte)$/.test(entry.name)) out.push(rel);
  }
  return out;
}

const isTestPath = (path: string) => path.includes('/__tests__/')
  || /\.(test|spec)\.ts$/.test(path);

const ALL_SOURCES = walk('src');
const PRODUCTION_SOURCES = ALL_SOURCES.filter((path) => !isTestPath(path));

/**
 * Strip comment text so a doc-comment reference can never satisfy — or
 * defeat — a reference-shaped sweep. `//` preceded by `:` is left alone so a
 * URL inside a string literal cannot swallow the rest of a real line.
 */
function stripComments(source: string): string {
  return source
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1');
}

const sourceOf = new Map(
  ALL_SOURCES.map((path) => [path, stripComments(readFileSync(resolve(FRONTEND_ROOT, path), 'utf8'))]),
);

/**
 * The four reference shapes correction 5248024803 item 9 names, and no more:
 * `from '…'`, `vi.mock('…')`, `import('…')`, `readFileSync('…')`.
 *
 * A bare `resolve('…/command-bus.ts')` is deliberately NOT a reference. Naming
 * a path is how an absence pin asserts the file is gone (`existsSync(...)` ===
 * false), and several A15 owners do exactly that; treating path construction
 * as a reference would make the emptiness proof forbid its own evidence.
 * Reading is the hazard, so `readFileSync` is matched through a wrapping
 * `resolve(...)` call.
 */
function referenceForms(modulePath: string): RegExp[] {
  // Match on the specifier's FINAL SEGMENT, not on the `wiring/` prefix: the
  // modules' own siblings imported them as `'../command-bus'`, which contains
  // no `wiring/` at all. Anchoring on the prefix let two mutation-battery
  // mutants (a re-pointed sibling import, and the restored
  // `state-adapter.test.ts`) survive — the sweep must catch the relative form
  // or it does not prove emptiness.
  const leaf = modulePath.split('/').pop() as string;
  const quoted = `['"\`][^'"\`]*[./]${leaf}(\\.ts)?['"\`]`;
  return [
    new RegExp(`from\\s+${quoted}`),
    new RegExp(`vi\\.mock\\(\\s*${quoted}`),
    new RegExp(`import\\(\\s*${quoted}`),
    new RegExp(`readFileSync\\([^)]*${modulePath}`),
  ];
}

function referencingFiles(paths: string[], modulePath: string): string[] {
  const forms = referenceForms(modulePath);
  return paths.filter((path) => forms.some((form) => form.test(sourceOf.get(path) ?? '')));
}

describe('MOR-1409 A15 — deleted module emptiness proof', () => {
  // Kills: shipping A15 without actually deleting the modules — every other
  // closure claim in this gate is downstream of these two files being gone.
  it('deletes the command-bus shim from the tree', () => {
    expect(existsSync(resolve(FRONTEND_ROOT, BUS_FILE))).toBe(false);
  });

  it('deletes the state-adapter projection twin from the tree', () => {
    expect(existsSync(resolve(FRONTEND_ROOT, ADAPTER_FILE))).toBe(false);
  });

  // Kills: re-pointing any production module back at either deleted path,
  // including through an alias, a re-export, or a dynamic import — final
  // static rule 4's "including alias/re-export/dynamic-import forms".
  it('leaves zero reference-shaped production references to the command-bus shim', () => {
    expect(referencingFiles(PRODUCTION_SOURCES, BUS)).toEqual([]);
  });

  it('leaves zero reference-shaped production references to the state-adapter twin', () => {
    expect(referencingFiles(PRODUCTION_SOURCES, ADAPTER)).toEqual([]);
  });

  // Kills: leaving any dependent behind. Unlike the production sweeps above
  // (already zero when A15 opened), this one scans TEST files too and is the
  // pin that proves the 24-file dependent migration actually happened rather
  // than being carried by a stale module that still resolves.
  it('leaves zero reference-shaped references to either deleted module anywhere under src/', () => {
    expect(referencingFiles(ALL_SOURCES, BUS)).toEqual([]);
    expect(referencingFiles(ALL_SOURCES, ADAPTER)).toEqual([]);
  });

  // REGRESSION pins (already zero at A15's base — A13b deleted
  // `FrontendRuntime.send`, A10 deleted `setPollingMultiplier`). Recorded as
  // the remaining two of correction 5247582313 §2's four closure greps so the
  // closing comment can cite a live check rather than a historical one.
  it('keeps production free of runtime.send( call sites (A13b regression pin)', () => {
    const hits = PRODUCTION_SOURCES.filter((path) => (sourceOf.get(path) ?? '').includes('runtime.send('));
    expect(hits).toEqual([]);
  });

  it('keeps production free of setPollingMultiplier references (A10 regression pin)', () => {
    const hits = PRODUCTION_SOURCES.filter((path) => (sourceOf.get(path) ?? '').includes('setPollingMultiplier'));
    expect(hits).toEqual([]);
  });
});

describe('MOR-1409 A15 — presentation authority closure', () => {
  const statusBar = () => readFileSync(
    resolve(FRONTEND_ROOT, 'src/components-v2/layout/StatusBar.svelte'), 'utf8',
  );

  // Kills: leaving StatusBar's `getFrequency()` edge in place. That accessor
  // returns `active?.freqHz ?? 0` — the last fabricated zero in shipped
  // presentation, and the last presentation-layer radio-store import.
  it('removes the last radio-store import from StatusBar', () => {
    expect(statusBar()).not.toMatch(/from\s+'\$lib\/stores\/radio\.svelte'/);
    expect(statusBar()).not.toContain('getFrequency');
  });
});

describe('MOR-1409 A15 — enforcement ledger closure', () => {
  const owners = async () => {
    const module = await import(
      pathToFileURL(resolve(FRONTEND_ROOT, 'scripts/radio-authority-eslint-plugin.mjs')).href
    );
    return module.radioAuthorityOwners as Record<string, string[]>;
  };

  // Kills: deleting the modules while leaving their declarations standing.
  // The backend gate (`test_ui_radio_control_contract.py`) asserts `is_file()`
  // on every declared owner path, so a stale row is not merely untruthful —
  // it is a red pytest. These pins state the same fact on the frontend side,
  // where the plugin is executable rather than merely declared.
  it('drops both deleted modules from the legacy writer owners', async () => {
    const { legacyWriterOwners } = await owners();
    expect(legacyWriterOwners).not.toContain(BUS_FILE);
    expect(legacyWriterOwners).not.toContain(ADAPTER_FILE);
  });

  it('drops the deleted shim from the acquisition owners', async () => {
    const { acquisitionOwners } = await owners();
    expect(acquisitionOwners).not.toContain(BUS_FILE);
  });

  // Kills: leaving the presentation-authority exception standing. Final
  // static rule 4 ("no presentation path imports raw transport or Store
  // writers") is UNPROVABLE while this guard carries any exception, which is
  // why the canonical §1409-14b shrink names it explicitly.
  it('empties the legacy presentation-authority exception set', async () => {
    const { legacyPresentationAuthority } = await owners();
    expect(legacyPresentationAuthority).toEqual([]);
  });

  // Kills: leaving `command-bus.ts` declared as an intent facade — a dangling
  // declaration inside the very artifact A15 exists to make truthful. Not
  // `is_file()`-asserted, so nothing but this pin holds it.
  it('drops the deleted shim from the declared intent facades', () => {
    const plugin = readFileSync(
      resolve(FRONTEND_ROOT, 'scripts/radio-authority-eslint-plugin.mjs'), 'utf8',
    );
    const contract = readFileSync(
      resolve(FRONTEND_ROOT, '../docs/internals/ui-radio-control-contract.toml'), 'utf8',
    );
    expect(plugin).not.toContain(`  '${BUS_FILE}',`);
    expect(contract).not.toContain(`  "frontend/${BUS_FILE}",`);
  });
});
