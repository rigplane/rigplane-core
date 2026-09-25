/**
 * MOR-2587. The `fast` vitest project (`frontend/vite.config.ts`) runs with
 * `isolate: false`, so a module-level `vi.mock` and an unrestored global are
 * shared with every other file on the same worker. This file fails when a
 * `fast` file does either, and names that file.
 *
 * A module mock is an offender only when another `fast` file imports that
 * same module for real. A global stub is an offender when `vi.stubGlobal` is
 * not followed by `vi.unstubAllGlobals` in `afterEach` or `afterAll`, or when
 * a direct `window` / `navigator` / `globalThis` property assignment is never
 * assigned back in the same file.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const FRONTEND_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');
const SRC = path.join(FRONTEND_ROOT, 'src');

function isFast(name: string): boolean {
  return name.endsWith('.test.ts')
    && !name.endsWith('.isolated.test.ts')
    && !name.endsWith('.component.test.ts')
    && !name.endsWith('.component.svelte.test.ts');
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(absolute, out);
    else if (isFast(entry.name)) out.push(absolute);
  }
  return out;
}

function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(?<!:)\/\/.*$/gm, '');
}

function resolveSpecifier(spec: string, fromFile: string): string | null {
  let target: string;
  if (spec.startsWith('$lib/')) target = path.join(SRC, 'lib', spec.slice('$lib/'.length));
  else if (spec.startsWith('.')) target = path.resolve(path.dirname(fromFile), spec);
  else return null;
  return target.replace(/\.(?:svelte|ts|js)$/, '');
}

const MOCK_CALL = /(?<![\w.])vi\.mock\s*\(\s*(['"`])(.+?)\1/g;
const REAL_IMPORT = /(?:^|\n)\s*import\s+(?!type\b)[^;]*?\sfrom\s*['"]([^'"]+)['"]/g;
const DYNAMIC_IMPORT = /(?<![\w.])import\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
const STUB_GLOBAL = /(?<![\w.])vi\.stubGlobal\s*\(/;
const HOOK_UNSTUB = /after(?:Each|All)\s*\([\s\S]*?unstubAllGlobals\s*\(/;
const ASSIGN = /(?<![\w.])(window|navigator|globalThis)\.(\w+)\s*=/g;

function fastLeakOffenders(files: readonly string[]): string[] {
  const code = new Map(files.map((file) => [file, stripComments(readFileSync(file, 'utf8'))]));
  const imports = new Map<string, Set<string>>();
  for (const [file, text] of code) {
    const specs = new Set<string>();
    for (const pattern of [REAL_IMPORT, DYNAMIC_IMPORT]) {
      for (const match of text.matchAll(pattern)) {
        const resolved = resolveSpecifier(match[1], file);
        if (resolved) specs.add(resolved);
      }
    }
    imports.set(file, specs);
  }

  const offenders: string[] = [];
  for (const [file, text] of code) {
    const reasons: string[] = [];
    for (const match of text.matchAll(MOCK_CALL)) {
      const resolved = resolveSpecifier(match[2], file);
      if (!resolved) continue;
      const shared = [...imports].some(([other, specs]) => other !== file && specs.has(resolved));
      if (shared) reasons.push(`vi.mock('${match[2]}')`);
    }
    if (STUB_GLOBAL.test(text) && !HOOK_UNSTUB.test(text)) {
      reasons.push('vi.stubGlobal without restore');
    }
    const writes = new Map<string, number>();
    for (const match of text.matchAll(ASSIGN)) {
      const prop = `${match[1]}.${match[2]}`;
      writes.set(prop, (writes.get(prop) ?? 0) + 1);
    }
    for (const [prop, count] of writes) {
      if (count < 2) reasons.push(`${prop} assigned without restore`);
    }
    if (reasons.length > 0) offenders.push(`${path.relative(FRONTEND_ROOT, file)}: ${reasons.join(', ')}`);
  }
  return offenders.sort();
}

describe('fast pool leak guard (MOR-2587)', () => {
  it('names every fast file that mocks a shared module or leaves a global stub unrestored', () => {
    expect(fastLeakOffenders(walk(SRC))).toEqual([]);
  });
});
