/**
 * MOR-2040 — `validateRadioViewModel` (`semantic/radio-view-model.ts`) existed
 * but was never called outside a test: production got only the compile-time
 * return-type check on `toRadioViewModel`, which cannot catch a value that
 * type-checks yet breaks a runtime/cross-field invariant (an unsafe `as`, a
 * bad merge, …). `guardRadioViewModel` closes that gap at
 * `components-v2/wiring/` — a seam that may value-import `semantic/` without
 * an eslint boundary exception, but not the only one (`lib/media/
 * media-session.ts` is another, ruled out for a different reason — see
 * `radio-view-model-guard.ts`'s module doc). This is the seam that hands the
 * adapter's output to the semantic surfaces the rest of this file protects,
 * and it covers one of `toRadioViewModel`'s five production call sites; the
 * module doc named above records the other four and why each is unguarded.
 *
 * Kills two regressions: the guard silently degrading into a no-op
 * pass-through (a mangled view model would then reach
 * `SemanticRadioSurfaces.svelte` uncaught), and the guard misfiring on a
 * legitimately valid — or legitimately absent — view model.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { guardRadioViewModel } from '../radio-view-model-guard';
import { topologyFixtures } from '../../../semantic/fixtures/topologies';
import type { RadioViewModel } from '../../../semantic/radio-view-model';

// `topology-fixtures.test.ts` already pins every entry here as
// validator-clean — reused rather than hand-built so this file is not a
// second place that can drift from what "valid" means.
const valid: RadioViewModel = topologyFixtures['2/main_sub'];

describe('guardRadioViewModel', () => {
  it('passes a valid view model through unchanged', () => {
    expect(guardRadioViewModel(valid)).toBe(valid);
  });

  it('passes null through unchanged — no observed state is not a mangled shape', () => {
    expect(guardRadioViewModel(null)).toBeNull();
  });

  // The failure mode this file exists to kill: an adapter regression that
  // still satisfies `toRadioViewModel`'s return-type annotation (e.g. via an
  // unsafe cast or a careless spread) but carries a field outside the
  // contract. Only the runtime validator's `exactKeys` check catches this.
  it('throws on a view model carrying a field outside the contract', () => {
    const mangled = { ...valid, bogusField: 'nope' } as RadioViewModel;
    expect(() => guardRadioViewModel(mangled)).toThrow(TypeError);
  });

  // Proves the guard is actually called from the seam that builds the
  // canonical view model — not just correct in isolation. Without this, a
  // future edit could drop the `guardRadioViewModel(...)` wrapper from
  // `SemanticRadioSurfaces.svelte` and every test above would keep passing
  // while production went back to "never called."
  it('is wired into the seam that builds the canonical view model', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/components-v2/wiring/SemanticRadioSurfaces.svelte'),
      'utf8',
    );
    expect(source).toMatch(/canonicalView[^=]*=\s*\$derived\(\s*guardRadioViewModel\(\s*toRadioViewModel\(/);
  });
});
