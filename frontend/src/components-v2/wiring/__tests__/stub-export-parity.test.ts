/**
 * MOR-1320 — the fixture harness's `command-bus` stub must never omit a name
 * the real module exports.
 *
 * `vite.fixtures.config.ts` re-points `components-v2/wiring/command-bus` at
 * `fixtures/stubs/command-bus.ts` wholesale (see that file's own docstring):
 * an ES module's named imports are resolved at MODULE RESOLUTION time, so a
 * stub missing even one export the importing tree names kills the harness
 * before a single fixture can mount — not a degraded capture, a dark one.
 * This happened twice on the real module (MOR-1271: `makeTxHandlers` /
 * `makeVoxHandlers`; MOR-1320: `makeAudioRoutingHandlers` / `makeModeHandlers`
 * / `makeRxAudioHandlers`, all added by the MOR-1279 rxAudio slice), and both
 * times the drift was invisible until someone ran `fixtures/capture.mjs` by
 * hand. This test makes the drift visible at commit time instead.
 *
 * Deliberately does not mock or exercise EITHER module — it only imports the
 * two module namespace objects and compares their key sets. That is
 * sufficient because the failure mode is entirely name-level (an import that
 * cannot resolve), not behavioral; the stub's *behavior* (recording rather
 * than commanding) is covered by `fixtures/harness-state.ts`'s own callers
 * and by capture.mjs's assertions, not by this file.
 *
 * `stub ⊇ real` (not `===`) is the correct direction: the stub is allowed to
 * carry a name the real module has since retired (harmless — nothing can
 * import a name that no longer exists), but never allowed to be missing a
 * name the real module currently exports.
 */
import { describe, expect, it } from 'vitest';
import * as real from '../command-bus';
// fixtures/ lives outside src/, which is why this guard has to reach the
// stub by relative path rather than through any configured alias.
import * as stub from '../../../../fixtures/stubs/command-bus';

const realExports = Object.keys(real).sort();
const stubExports = new Set(Object.keys(stub));

describe('fixtures/stubs/command-bus.ts stays a superset of the real module', () => {
  it('exports at least one factory, so this guard cannot pass on an empty real module', () => {
    expect(realExports.length).toBeGreaterThan(0);
  });

  it.each(realExports)('exports "%s"', (name) => {
    expect(stubExports.has(name)).toBe(true);
  });

  it.each(realExports)('"%s" is a function on both the real module and the stub', (name) => {
    expect(typeof (real as Record<string, unknown>)[name]).toBe('function');
    expect(typeof (stub as Record<string, unknown>)[name]).toBe('function');
  });
});
