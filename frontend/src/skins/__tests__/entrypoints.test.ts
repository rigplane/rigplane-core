/**
 * MOR-2038 — every `SkinId` registered in `skins/registry.ts` has a
 * behavioral entry-point pin: five directly in this file, the sixth
 * (`dual-receiver-cockpit`) in its own dedicated suite, referenced and
 * guarded here (point 2 below) rather than duplicated. Generalized over the
 * registry instead of a hardcoded pair list (the previous version of this
 * file covered only `desktop-v2`/`sdr-test`).
 *
 * `SKIN_LOADERS` — the registry's `Record<SkinId, () => Promise<...>>` of
 * lazy dynamic imports — is not exported; only `loadSkin(id)` is. Every case
 * below that mounts a component fetches it through `loadSkin`, the same
 * function `App.svelte` calls, instead of importing a skin's `.svelte` file
 * directly — a loader repointed at the wrong module is visible here exactly
 * as it would be to the app. The one exception is the `dual-receiver-cockpit`
 * coverage guard (point 2 below), which mounts nothing itself.
 *
 * Completeness has two layers, because there is no runtime-enumerable list
 * of `SkinId` values to iterate — the union has no runtime representation,
 * and both `SKIN_LOADERS` and the sibling `SKIN_RESOURCE_PLAN` are private:
 *
 * 1. `SKIN_ENTRYPOINT_COVERAGE` below is typed `Record<SkinId, ...>`. A
 *    `SkinId` added to the registry without a matching entry here is a
 *    missing-property error in this object literal — caught by
 *    `npm run check` (`svelte-check --tsconfig ./tsconfig.app.json`, whose
 *    `include` covers every `src/**\/*.ts`, this file included). Plain
 *    `vitest run` would NOT catch it: Vite/esbuild transpile this file by
 *    stripping types, so a table that silently fell out of sync would still
 *    run, just without the new id.
 * 2. For the one id whose coverage lives in another file
 *    (`dual-receiver-cockpit`), a dedicated suite below checks that the
 *    named file still exists and its source text still contains the skin's
 *    entry-component filename. That is a substring check, not a behavioral
 *    one — it cannot confirm a test in that file actually mounts the
 *    component, only that a prior manual confirmation of that hasn't
 *    visibly rotted (the file deleted, renamed, or edited to drop the
 *    reference). `mobile` used to live in this same bucket, on a file that,
 *    on closer reading, never mounted `MobileSkin` at all — it only
 *    `?raw`-imported it as a text fixture — so it now gets a real mount pin
 *    instead (below), the same way `lcd-cockpit`/`lcd-scope` do.
 *
 * `lcd-cockpit`/`lcd-scope` get a real mount pin for the first time here.
 * Both wrappers are zero-prop and hardcode a `variant` literal into
 * `LcdLayout`; the `variant` prop's shape
 * (`variant?: 'cockpit' | 'scope'`) is already pinned on LcdLayout itself by
 * `components-v2/layout/__tests__/LcdLayout.command-bus-migration.isolated.test.ts`
 * and `...autostep-lifecycle.isolated.test.ts` — nothing here duplicates
 * that. This file pins only the two wrappers' half: that each forwards its
 * own literal down, not LcdLayout's behavior for either value.
 *
 * `mobile` gets a real mount pin the same way: `MobileSkin.svelte` is a
 * zero-prop delegate straight to `MobileRadioLayout`, so its pin mocks
 * `MobileRadioLayout.svelte` (the same technique as the `RadioLayout`/
 * `LcdLayout` mocks above) and asserts that loading `mobile` actually
 * mounts it.
 */
import { existsSync, readFileSync } from 'node:fs';
import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SkinId } from '../registry';

const mountedSkinIds = vi.hoisted(() => [] as SkinId[]);
const mountedLcdVariants = vi.hoisted(() => [] as Array<'cockpit' | 'scope'>);
const mobileLayoutMounts = vi.hoisted(() => ({ count: 0 }));

vi.mock('../../components-v2/layout/RadioLayout.svelte', () => ({
  default: (_anchor: unknown, props: { skinId?: SkinId }) => {
    if (props.skinId) mountedSkinIds.push(props.skinId);
  },
}));

vi.mock('../../components-v2/layout/LcdLayout.svelte', () => ({
  default: (_anchor: unknown, props: { variant?: 'cockpit' | 'scope' }) => {
    if (props.variant) mountedLcdVariants.push(props.variant);
  },
}));

// MobileSkin takes no props at all (`<MobileRadioLayout />`), so there is no
// value to pin here beyond "loading `mobile` actually mounts this component".
vi.mock('../../components-v2/layout/MobileRadioLayout.svelte', () => ({
  default: () => {
    mobileLayoutMounts.count += 1;
  },
}));

import { loadSkin } from '../registry';

const components: Record<string, unknown>[] = [];

afterEach(() => {
  while (components.length) unmount(components.pop()!);
  mountedSkinIds.length = 0;
  mountedLcdVariants.length = 0;
  mobileLayoutMounts.count = 0;
});

type EntrypointCoverage =
  | { readonly kind: 'radio-layout' }
  | { readonly kind: 'lcd-layout'; readonly variant: 'cockpit' | 'scope' }
  | { readonly kind: 'mobile-layout' }
  | { readonly kind: 'covered-elsewhere'; readonly testFile: string; readonly entryComponentFile: string };

/**
 * One entry per `SkinId` — see the file header for what makes this table
 * exhaustive and what a `covered-elsewhere` entry is checked against.
 */
const SKIN_ENTRYPOINT_COVERAGE: Readonly<Record<SkinId, EntrypointCoverage>> = {
  'desktop-v2': { kind: 'radio-layout' },
  'sdr-test': { kind: 'radio-layout' },
  'lcd-cockpit': { kind: 'lcd-layout', variant: 'cockpit' },
  'lcd-scope': { kind: 'lcd-layout', variant: 'scope' },
  'dual-receiver-cockpit': {
    kind: 'covered-elsewhere',
    testFile: 'src/skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts',
    entryComponentFile: 'DualReceiverCockpit.svelte',
  },
  mobile: { kind: 'mobile-layout' },
};

const allSkinIds = Object.keys(SKIN_ENTRYPOINT_COVERAGE) as SkinId[];

const radioLayoutSkinIds = allSkinIds.filter((id) => SKIN_ENTRYPOINT_COVERAGE[id].kind === 'radio-layout');

const lcdLayoutCases = allSkinIds.flatMap((id) => {
  const coverage = SKIN_ENTRYPOINT_COVERAGE[id];
  return coverage.kind === 'lcd-layout' ? [[id, coverage.variant] as const] : [];
});

const mobileLayoutSkinIds = allSkinIds.filter((id) => SKIN_ENTRYPOINT_COVERAGE[id].kind === 'mobile-layout');

const coveredElsewhereCases = allSkinIds.flatMap((id) => {
  const coverage = SKIN_ENTRYPOINT_COVERAGE[id];
  return coverage.kind === 'covered-elsewhere' ? [[id, coverage] as const] : [];
});

describe('desktop skin entrypoints', () => {
  // Kills: a RadioLayout-backed skin no longer passing its own SkinId
  // literal, or passing a different skin's — the assertion pins the exact
  // value, not merely that some string arrived.
  it.each(radioLayoutSkinIds)('mounts RadioLayout with its own stable skin ID (%s)', async (skinId) => {
    const Component = await loadSkin(skinId);
    const target = document.createElement('div');
    components.push(mount(Component, { target }));
    expect(mountedSkinIds).toEqual([skinId]);
  });
});

describe('LCD skin entrypoints', () => {
  // Kills: LcdCockpitSkin/LcdScopeSkin forwarding the wrong `variant`
  // literal to LcdLayout, forwarding the other skin's literal, or dropping
  // the prop entirely (the guard in the mock above then leaves
  // mountedLcdVariants empty, which also fails the equality below).
  it.each(lcdLayoutCases)('forwards variant to LcdLayout (%s -> %s)', async (skinId, variant) => {
    const Component = await loadSkin(skinId);
    const target = document.createElement('div');
    components.push(mount(Component, { target }));
    expect(mountedLcdVariants).toEqual([variant]);
  });
});

describe('mobile skin entrypoint', () => {
  // Kills: MobileSkin resolving to anything other than MobileRadioLayout —
  // a stub, another skin's layout, or nothing mounted at all.
  it.each(mobileLayoutSkinIds)('mounts MobileRadioLayout (%s)', async (skinId) => {
    const Component = await loadSkin(skinId);
    const target = document.createElement('div');
    components.push(mount(Component, { target }));
    expect(mobileLayoutMounts.count).toBe(1);
  });
});

describe('externally-covered skin entrypoints', () => {
  // Kills: the named file being deleted or renamed, or edited so its source
  // no longer mentions the entry component's filename. This is a substring
  // match on source text, not a proof of behavior: it cannot tell a suite
  // that genuinely mounts the component apart from one that only imports it
  // as a `?raw` text fixture, or one whose tests are all `describe.skip`-ed.
  // Treat a pass here as "the reference hasn't visibly rotted", not as "this
  // skin is covered" — the latter has to be confirmed by hand when the entry
  // is written or changed (see the file header for the case that wasn't).
  it.each(coveredElsewhereCases)('%s names a real test file that still mentions its entrypoint', (skinId, coverage) => {
    expect(existsSync(coverage.testFile), `${skinId}: ${coverage.testFile} does not exist`).toBe(true);
    const source = readFileSync(coverage.testFile, 'utf8');
    expect(
      source.includes(coverage.entryComponentFile),
      `${skinId}: ${coverage.testFile} no longer mentions ${coverage.entryComponentFile}`,
    ).toBe(true);
  });
});
