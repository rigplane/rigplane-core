import { describe, expect, it, vi } from 'vitest';
import type { SkinId } from '../registry';

const entrypoints = vi.hoisted(() => ({
  desktop: { name: 'desktop-v2' },
  cockpit: { name: 'lcd-cockpit' },
  scope: { name: 'lcd-scope' },
  mobile: { name: 'mobile' },
  sdr: { name: 'sdr-test' },
}));

const lazyImports = vi.hoisted(() => ({
  desktop: vi.fn(() => ({ default: entrypoints.desktop })),
  cockpit: vi.fn(() => ({ default: entrypoints.cockpit })),
  scope: vi.fn(() => ({ default: entrypoints.scope })),
  mobile: vi.fn(() => ({ default: entrypoints.mobile })),
  sdr: vi.fn(() => ({ default: entrypoints.sdr })),
}));

vi.mock('../desktop-v2/DesktopSkin.svelte', () => lazyImports.desktop());
vi.mock('../lcd-cockpit/LcdCockpitSkin.svelte', () => lazyImports.cockpit());
vi.mock('../lcd-scope/LcdScopeSkin.svelte', () => lazyImports.scope());
vi.mock('../mobile/MobileSkin.svelte', () => lazyImports.mobile());
vi.mock('../sdr-test/SdrTestSkin.svelte', () => lazyImports.sdr());

import { loadSkin, resolvePersistedSkinId, resolveSkinId } from '../registry';

const resolve = (overrides: Partial<Parameters<typeof resolveSkinId>[0]> = {}) =>
  resolveSkinId({
    capabilities: null,
    layoutPreference: 'auto',
    isMobile: false,
    hasAnyScope: false,
    ...overrides,
  });

describe('skin registry', () => {
  it('gives mobile precedence over every forced layout preference', () => {
    for (const layoutPreference of ['auto', 'lcd', 'lcd-cockpit', 'lcd-scope', 'standard', 'sdr-test'] as const) {
      expect(resolve({ isMobile: true, layoutPreference, hasAnyScope: true })).toBe('mobile');
    }
  });

  it.each([
    ['standard', 'desktop-v2'],
    ['lcd', 'lcd-cockpit'],
    ['lcd-cockpit', 'lcd-cockpit'],
    ['lcd-scope', 'lcd-scope'],
    ['sdr-test', 'sdr-test'],
  ] as const)('resolves forced %s preference to %s', (layoutPreference, skinId) => {
    expect(resolve({ layoutPreference, hasAnyScope: false })).toBe(skinId);
  });

  it.each([
    [true, 'desktop-v2'],
    [false, 'lcd-cockpit'],
  ] as const)('resolves auto according to scope availability (%s)', (hasAnyScope, skinId) => {
    expect(resolve({ hasAnyScope })).toBe(skinId);
  });

  it('normalizes the persisted amber-lcd preference before resolution', () => {
    expect(resolvePersistedSkinId('amber-lcd')).toBe('lcd-cockpit');
    expect(resolvePersistedSkinId('desktop-v2')).toBe('desktop-v2');
  });

  it('does not import a skin entrypoint while the registry is initialized', () => {
    expect(lazyImports.desktop).not.toHaveBeenCalled();
    expect(lazyImports.cockpit).not.toHaveBeenCalled();
    expect(lazyImports.scope).not.toHaveBeenCalled();
    expect(lazyImports.mobile).not.toHaveBeenCalled();
    expect(lazyImports.sdr).not.toHaveBeenCalled();
  });

  it.each([
    ['desktop-v2', entrypoints.desktop, lazyImports.desktop],
    ['lcd-cockpit', entrypoints.cockpit, lazyImports.cockpit],
    ['lcd-scope', entrypoints.scope, lazyImports.scope],
    ['mobile', entrypoints.mobile, lazyImports.mobile],
    ['sdr-test', entrypoints.sdr, lazyImports.sdr],
  ] as const)('lazily loads the %s entrypoint', async (skinId: SkinId, entrypoint, lazyImport) => {
    await expect(loadSkin(skinId)).resolves.toBe(entrypoint);
    expect(lazyImport).toHaveBeenCalledTimes(1);
  });
});
