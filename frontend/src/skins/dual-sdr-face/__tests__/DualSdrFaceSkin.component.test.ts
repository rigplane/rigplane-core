import { readFileSync } from 'node:fs';
import { mount, tick, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CANONICAL_LAYOUT_MODES, type CanonicalLayoutMode } from '../../../presentation/layout-mode';

const lifecycle = vi.hoisted(() => ({
  acquire: vi.fn(() => ({ resource: 'hardware-scope', token: Symbol('lease') })),
  release: vi.fn(() => true),
}));

const navigation = vi.hoisted(() => ({
  selections: [] as CanonicalLayoutMode[],
  mount(anchor: Node) {
    const select = document.createElement('select');
    select.dataset.testid = 'skin-navigation-probe';
    for (const mode of CANONICAL_LAYOUT_MODES) {
      const option = document.createElement('option');
      option.value = mode;
      select.append(option);
    }
    select.addEventListener('change', () => {
      navigation.selections.push(select.value as CanonicalLayoutMode);
    });
    anchor.parentNode?.insertBefore(select, anchor);
  },
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    acquireHardwareScope: lifecycle.acquire,
    releaseHardwareScope: lifecycle.release,
    scope: {
      subscribeHardware: vi.fn(() => () => {}),
      subscribeHealth: vi.fn(() => () => {}),
    },
  },
}));

vi.mock('../../../components-v2/wiring/SemanticRadioSurfaces.svelte', () => ({ default: () => {} }));
vi.mock('../DualSdrFace.svelte', () => ({ default: () => {} }));
vi.mock('../../../components-v2/layout/StatusBar.svelte', () => ({ default: navigation.mount }));
vi.mock('../../../components-v2/layout/RadioLayout.svelte', () => ({ default: navigation.mount }));
vi.mock('../../../components-v2/layout/LcdLayout.svelte', () => ({ default: navigation.mount }));

import DualSdrFaceSkin from '../DualSdrFaceSkin.svelte';
import { loadSkin, resolveSkinId } from '../../registry';

describe('DualSdrFaceSkin production entrypoint', () => {
  const source = readFileSync('src/skins/dual-sdr-face/DualSdrFaceSkin.svelte', 'utf8');
  const statusBarSource = readFileSync('src/components-v2/layout/StatusBar.svelte', 'utf8');
  const pickerModes = [...statusBarSource.matchAll(/\{ value: '([^']+)', label:/g)]
    .map((match) => match[1] as CanonicalLayoutMode);

  beforeEach(() => {
    lifecycle.acquire.mockClear();
    lifecycle.release.mockClear();
    navigation.selections.length = 0;
  });

  it('mounts the exact face through the canonical read-only semantic view', () => {
    expect(source).toContain("import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte'");
    expect(source).toContain("import DualSdrFace from './DualSdrFace.svelte'");
    expect(source).toMatch(/<SemanticRadioSurfaces\s+\{readonlyDisplay\}\s*\/>/);
    expect(source).toMatch(/<DualSdrFace\s+\{view\}\s+scopeSource=\{hardwareScope\}\s*\/>/);
  });

  it('keeps the picker options synchronized with the canonical layout vocabulary', () => {
    expect(new Set(pickerModes)).toEqual(CANONICAL_LAYOUT_MODES);
  });

  it.each(pickerModes)(
    'keeps a mounted selector path from picker-selectable mode %s',
    async (layoutPreference) => {
      const skinId = resolveSkinId({
        capabilities: null,
        layoutPreference,
        isMobile: false,
        hasAnyScope: true,
      });
      const Component = await loadSkin(skinId);
      const target = document.createElement('div');
      const mounted = mount(Component, { target });

      const selector = target.querySelector<HTMLSelectElement>(
        'select[data-testid="skin-navigation-probe"]',
      );
      expect(selector, `${layoutPreference} resolved to ${skinId} without navigation`).not.toBeNull();

      const returnMode = layoutPreference === 'standard' ? 'lcd-cockpit' : 'standard';
      selector!.value = returnMode;
      selector!.dispatchEvent(new Event('change', { bubbles: true }));
      expect(navigation.selections).toEqual([returnMode]);

      unmount(mounted);
      target.remove();
    },
  );

  it('uses the canonical hardware scope and grants no command callback', () => {
    expect(source).toContain('runtime.scope.subscribeHardware(listener)');
    expect(source).toContain('runtime.scope.subscribeHealth');
    expect(source).toContain("runtime.acquireHardwareScope('DualSdrFace')");
    expect(source).toContain('runtime.releaseHardwareScope(lease)');
    expect(source).not.toContain('onPreChange=');
  });

  it('holds one canonical hardware-scope lease per mount and releases it on every unmount', async () => {
    const firstTarget = document.createElement('div');
    const first = mount(DualSdrFaceSkin, { target: firstTarget });
    await tick();
    expect(lifecycle.acquire).toHaveBeenCalledTimes(1);
    expect(lifecycle.acquire).toHaveBeenLastCalledWith('DualSdrFace');
    const firstLease = lifecycle.acquire.mock.results[0].value;
    unmount(first);
    await tick();
    expect(lifecycle.release).toHaveBeenCalledWith(firstLease);

    const secondTarget = document.createElement('div');
    const second = mount(DualSdrFaceSkin, { target: secondTarget });
    await tick();
    expect(lifecycle.acquire).toHaveBeenCalledTimes(2);
    const secondLease = lifecycle.acquire.mock.results[1].value;
    expect(secondLease).not.toBe(firstLease);
    unmount(second);
    await tick();
    expect(lifecycle.release).toHaveBeenCalledTimes(2);
    expect(lifecycle.release).toHaveBeenLastCalledWith(secondLease);
  });
});
