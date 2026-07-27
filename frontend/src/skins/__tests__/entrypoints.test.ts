import { mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SkinId } from '../registry';

const mountedSkinIds = vi.hoisted(() => [] as SkinId[]);

vi.mock('../../components-v2/layout/RadioLayout.svelte', () => ({
  default: (_anchor: unknown, props: { skinId?: SkinId }) => {
    if (props.skinId) mountedSkinIds.push(props.skinId);
  },
}));

import DesktopSkin from '../desktop-v2/DesktopSkin.svelte';
import SdrTestSkin from '../sdr-test/SdrTestSkin.svelte';

const components: Record<string, unknown>[] = [];

afterEach(() => {
  while (components.length) unmount(components.pop()!);
  mountedSkinIds.length = 0;
});

describe('desktop skin entrypoints', () => {
  it.each([
    [DesktopSkin, 'desktop-v2'],
    [SdrTestSkin, 'sdr-test'],
  ] as const)('passes its stable skin ID explicitly', (component, skinId) => {
    const target = document.createElement('div');
    components.push(mount(component, { target }));
    expect(mountedSkinIds).toEqual([skinId]);
  });
});
