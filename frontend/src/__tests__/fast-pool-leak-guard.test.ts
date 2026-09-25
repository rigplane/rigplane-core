/**
 * MOR-2587. The `fast` vitest project (`frontend/vite.config.ts`) runs with
 * `isolate: false`, so a module-level `vi.mock` and an unrestored global are
 * shared with every other file on the same worker. This file fails when a
 * `fast` file does either, and names that file.
 *
 * A module mock is an offender only when another `fast` file imports that
 * same module for real. A global stub is an offender when `vi.stubGlobal` is
 * not followed by `vi.unstubAllGlobals` in `afterEach` or `afterAll`, or when
 * a direct `window` / `navigator` / `globalThis` property assignment does not
 * capture the original into a variable and write that same variable back
 * (`obj.prop = saved`, or `Object.defineProperty` with `value: saved`).
 * Two one-way writes are not a restore.
 */

import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const FRONTEND_ROOT = path.resolve(fileURLToPath(import.meta.url), '../../..');
const SRC = path.join(FRONTEND_ROOT, 'src');

const EXPECTED_FAST_INCLUDE = ['src/**/*.test.ts'];
const EXPECTED_FAST_EXCLUDE = [
  'src/**/*.isolated.test.ts',
  'src/**/*.component.test.ts',
  'src/**/*.component.svelte.test.ts',
];

function quotedList(block: string, key: string): string[] {
  const body = block.match(new RegExp(`${key}:\\s*\\[([\\s\\S]*?)\\]`))?.[1] ?? '';
  return [...body.matchAll(/'([^']+)'/g)].map((match) => match[1]);
}

function fastGlobs(config: string): { include: string[]; exclude: string[] } {
  const fast = config.split("name: 'isolated'")[0]?.split("name: 'fast'")[1];
  if (!fast) throw new Error('fast-pool-leak-guard: vite.config.ts has no fast project');
  const include = quotedList(fast, 'include');
  const exclude = quotedList(fast, 'exclude');
  if (include.length === 0 || exclude.length === 0) {
    throw new Error('fast-pool-leak-guard: could not read fast include/exclude from vite.config.ts');
  }
  return { include, exclude };
}

function globSuffix(glob: string): string {
  const star = glob.lastIndexOf('*');
  if (!glob.startsWith('src/**/') || star < 0) {
    throw new Error(`fast-pool-leak-guard cannot read glob ${glob} from vite.config.ts`);
  }
  return glob.slice(star + 1);
}

function isFast(name: string, globs: { include: string[]; exclude: string[] }): boolean {
  return globs.include.some((glob) => name.endsWith(globSuffix(glob)))
    && !globs.exclude.some((glob) => name.endsWith(globSuffix(glob)));
}

function walk(dir: string, globs: { include: string[]; exclude: string[] }, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const absolute = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(absolute, globs, out);
    else if (isFast(entry.name, globs)) out.push(absolute);
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
const ASSIGN = /(?<![\w.])(window|navigator|globalThis)\.(\w+)\s*=\s*([^;\n]+)/g;
const CAPTURE = /(?:const|let|var\s+)?(?<![\w.])(\w+)\s*=\s*(window|navigator|globalThis)\.(\w+)\b/g;
const DEFINE = /Object\.defineProperty\(\s*(window|navigator|globalThis)\s*,\s*['"](\w+)['"]/g;

function savedNames(text: string): Map<string, Set<string>> {
  const saved = new Map<string, Set<string>>();
  for (const match of text.matchAll(CAPTURE)) {
    const prop = `${match[2]}.${match[3]}`;
    const names = saved.get(prop) ?? new Set<string>();
    names.add(match[1]);
    saved.set(prop, names);
  }
  return saved;
}

function writesSaved(prop: string, saved: Map<string, Set<string>>, rhs: string): boolean {
  const ident = rhs.trim().match(/^([A-Za-z_$][\w$]*)/)?.[1];
  return ident !== undefined && (saved.get(prop)?.has(ident) ?? false);
}

function unrestoredAssignments(text: string): string[] {
  const saved = savedNames(text);
  const stubbed = new Set<string>();
  const restored = new Set<string>();
  for (const match of text.matchAll(ASSIGN)) {
    const prop = `${match[1]}.${match[2]}`;
    if (writesSaved(prop, saved, match[3])) restored.add(prop);
    else stubbed.add(prop);
  }
  for (const match of text.matchAll(DEFINE)) {
    const prop = `${match[1]}.${match[2]}`;
    const slice = text.slice(match.index ?? 0, (match.index ?? 0) + 400);
    const names = saved.get(prop);
    if (names && [...names].some((name) => new RegExp(`\\bvalue\\s*:\\s*${name}\\b`).test(slice))) {
      restored.add(prop);
    }
  }
  return [...stubbed].filter((prop) => !restored.has(prop)).map((prop) => `${prop} assigned without restore`);
}

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
    reasons.push(...unrestoredAssignments(text));
    if (reasons.length > 0) offenders.push(`${path.relative(FRONTEND_ROOT, file)}: ${reasons.join(', ')}`);
  }
  return offenders.sort();
}

describe('fast pool leak guard (MOR-2587)', () => {
  const globs = fastGlobs(readFileSync(path.join(FRONTEND_ROOT, 'vite.config.ts'), 'utf8'));

  it('reads fast membership from the fast project in vite.config.ts', () => {
    expect(globs.include).toEqual(EXPECTED_FAST_INCLUDE);
    expect(globs.exclude).toEqual(EXPECTED_FAST_EXCLUDE);
  });

  it('names every fast file that mocks a shared module or leaves a global stub unrestored', () => {
    expect(fastLeakOffenders(walk(SRC, globs))).toEqual([]);
  });
});
