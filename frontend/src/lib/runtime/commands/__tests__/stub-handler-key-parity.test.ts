/**
 * MOR-1328 — a fixture stub that omits a handler key fails at the first
 * captured gesture, not at module resolution. Name-level export parity
 * cannot see that. This guard compares the keys each real factory returns
 * with the keys the fixture stub returns.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const realSource = readFileSync('src/lib/runtime/commands/panel-commands.ts', 'utf8');
const stubSource = readFileSync('fixtures/stubs/command-bus.ts', 'utf8');

function factoryBlock(source: string, name: string): string {
  const start = source.indexOf(`export function ${name}`);
  if (start < 0) return '';
  const next = source.indexOf('\nexport function ', start + 1);
  return source.slice(start, next < 0 ? source.length : next);
}

function returnedKeys(block: string): string[] {
  const open = block.indexOf('\n  return');
  if (open < 0) return [];
  const body = block.slice(open);
  const close = body.search(/\n {2}\};|\n {2}\}\);/);
  const object = close < 0 ? body : body.slice(0, close);
  const method = [...object.matchAll(/^\s{4}(?:async\s+)?(\w+)(?:<[^>]*>)?\s*(?:\(|:)/gm)].map((m) => m[1]);
  const recorded = [...object.matchAll(/'((?:on|dispatch|restore)\w*)'/g)].map((m) => m[1]);
  return [...new Set([...method, ...recorded])].sort();
}

function factoryNames(source: string): string[] {
  return [...source.matchAll(/^export function (make\w+)/gm)].map((m) => m[1]).sort();
}

describe('fixture command-bus stub returns every real factory key (MOR-1328)', () => {
  const names = factoryNames(realSource);

  it('sees the real factories, so an empty parse cannot pass', () => {
    expect(names.length).toBeGreaterThan(0);
    expect(names).toContain('makeVfoHandlers');
  });

  it.each(factoryNames(realSource))('%s returns at least the real key set', (name) => {
    const realKeys = returnedKeys(factoryBlock(realSource, name));
    const stubKeys = new Set(returnedKeys(factoryBlock(stubSource, name)));
    expect(realKeys.length).toBeGreaterThan(0);
    expect(realKeys.filter((key) => !stubKeys.has(key))).toEqual([]);
  });
});
