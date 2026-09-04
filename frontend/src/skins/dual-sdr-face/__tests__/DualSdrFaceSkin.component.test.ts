import { readFileSync } from 'node:fs';
import { mount, tick, unmount } from 'svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const lifecycle = vi.hoisted(() => ({
  acquire: vi.fn(() => ({ resource: 'hardware-scope', token: Symbol('lease') })),
  release: vi.fn(() => true),
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

import DualSdrFaceSkin from '../DualSdrFaceSkin.svelte';

describe('DualSdrFaceSkin production entrypoint', () => {
  const source = readFileSync('src/skins/dual-sdr-face/DualSdrFaceSkin.svelte', 'utf8');

  beforeEach(() => {
    lifecycle.acquire.mockClear();
    lifecycle.release.mockClear();
  });

  it('mounts the exact face through the canonical read-only semantic view', () => {
    expect(source).toContain("import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte'");
    expect(source).toContain("import DualSdrFace from './DualSdrFace.svelte'");
    expect(source).toMatch(/<SemanticRadioSurfaces\s+\{readonlyDisplay\}\s*\/>/);
    expect(source).toMatch(/<DualSdrFace\s+\{view\}\s+scopeSource=\{hardwareScope\}\s*\/>/);
  });

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
