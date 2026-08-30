/**
 * MOR-2038 — a behavioral entry-point pin for every `SkinId` registered in
 * `skins/registry.ts`, generalized over that registry instead of a
 * hardcoded pair list (the previous version of this file covered only
 * `desktop-v2`/`sdr-test`).
 *
 * `SKIN_LOADERS` — the registry's `Record<SkinId, () => Promise<...>>` of
 * lazy dynamic imports — is not exported; only `loadSkin(id)` is. So every
 * case below fetches its component through `loadSkin`, the same function
 * `App.svelte` calls, instead of importing a skin's `.svelte` file directly.
 * A loader repointed at the wrong module is visible here exactly as it
 * would be to the app.
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
 * 2. For the two ids whose coverage lives in another file
 *    (`dual-receiver-cockpit`, `mobile`), the 'skin entrypoint coverage
 *    completeness' suite asserts the named file still exists and still
 *    mentions the skin's own entry component, so a rename or deletion over
 *    there turns this file red instead of the exemption rotting silently.
 *
 * `lcd-cockpit`/`lcd-scope` get a real mount pin for the first time here.
 * Both wrappers are zero-prop and hardcode a `variant` literal into
 * `LcdLayout`; the `variant` prop's shape
 * (`variant?: 'cockpit' | 'scope'`) is already pinned on LcdLayout itself by
 * `components-v2/layout/__tests__/LcdLayout.command-bus-migration.isolated.test.ts`
 * and `...autostep-lifecycle.isolated.test.ts` — nothing here duplicates
 * that. This file pins only the two wrappers' half: that each forwards its
 * own literal down, not LcdLayout's behavior for either value.
 */
import { existsSync, readFileSync } from 'node:fs';
import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SkinId } from '../registry';

const mountedSkinIds = vi.hoisted(() => [] as SkinId[]);
const mountedLcdVariants = vi.hoisted(() => [] as Array<'cockpit' | 'scope'>);

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

import { loadSkin } from '../registry';

const components: Record<string, unknown>[] = [];

afterEach(() => {
  while (components.length) unmount(components.pop()!);
  mountedSkinIds.length = 0;
  mountedLcdVariants.length = 0;
});

type EntrypointCoverage =
  | { readonly kind: 'radio-layout' }
  | { readonly kind: 'lcd-layout'; readonly variant: 'cockpit' | 'scope' }
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
  mobile: {
    kind: 'covered-elsewhere',
    testFile: 'src/components-v2/layout/__tests__/semantic-mobile-migration.component.test.ts',
    entryComponentFile: 'MobileSkin.svelte',
  },
};

const allSkinIds = Object.keys(SKIN_ENTRYPOINT_COVERAGE) as SkinId[];

const radioLayoutSkinIds = allSkinIds.filter((id) => SKIN_ENTRYPOINT_COVERAGE[id].kind === 'radio-layout');

const lcdLayoutCases = allSkinIds.flatMap((id) => {
  const coverage = SKIN_ENTRYPOINT_COVERAGE[id];
  return coverage.kind === 'lcd-layout' ? [[id, coverage.variant] as const] : [];
});

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

describe('skin entrypoint coverage completeness', () => {
  // Kills: a `covered-elsewhere` entry surviving after its named test file
  // is deleted or renamed, or one that never named a file that actually
  // exercises this skin's entry component to begin with.
  it.each(coveredElsewhereCases)('%s names a real test file that still mentions its entrypoint', (skinId, coverage) => {
    expect(existsSync(coverage.testFile), `${skinId}: ${coverage.testFile} does not exist`).toBe(true);
    const source = readFileSync(coverage.testFile, 'utf8');
    expect(
      source.includes(coverage.entryComponentFile),
      `${skinId}: ${coverage.testFile} no longer mentions ${coverage.entryComponentFile}`,
    ).toBe(true);
  });
});
