import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * MOR-1257 F1 (independent verification) — `skinOptions` is typed
 * `Array<{ value: CanonicalLayoutMode; ... }>`, which makes adding the
 * QA-only `'dual-receiver-cockpit'` value here a compile error (`npm run
 * check`) — the mutant the verifier added survived at the *type* level
 * (`LayoutMode` widening had turned it into a clean compile) until that
 * annotation was restored.
 *
 * This is a redundant, cheaper-to-run pin for the same invariant: a
 * source-level check that catches the regression even in a test-only run
 * that skips `npm run check`. Source-based (not a mounted component test)
 * because StatusBar.svelte pulls in the full connection/runtime store
 * surface — importing it for real would need the same heavy mock scaffolding
 * App.svelte's own tests carry, for a check that doesn't need a live DOM.
 */
describe('StatusBar skinOptions (MOR-1257 F1)', () => {
  it('never lists the QA-only dual-receiver-cockpit value', () => {
    const source = readFileSync('src/components-v2/layout/StatusBar.svelte', 'utf8');
    const match = source.match(/const skinOptions[^=]*=\s*\[([\s\S]*?)\n\s*\];/);
    expect(match, 'expected to find the skinOptions array literal').not.toBeNull();
    expect(match![1]).not.toMatch(/dual-receiver-cockpit/);
  });

  it('is typed CanonicalLayoutMode, not the wider LayoutMode', () => {
    const source = readFileSync('src/components-v2/layout/StatusBar.svelte', 'utf8');
    expect(source).toMatch(/const skinOptions:\s*Array<\{\s*value:\s*CanonicalLayoutMode;/);
  });
});
