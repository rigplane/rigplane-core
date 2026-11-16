/**
 * MOR-4463: the stacked-trace preference — off by default, kept per browser,
 * and an "unavailable" mark that lasts for the page's lifetime.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const KEY = 'rigplane-spectrum-stacked-trace';
const load = () => import('../stacked-trace.svelte');

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
});

describe('stacked-trace preference', () => {
  it('is off until the user turns it on', async () => {
    const store = await load();
    expect(store.isStackedTraceEnabled()).toBe(false);
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('persists the choice and restores it on the next load', async () => {
    const first = await load();
    first.setStackedTraceEnabled(true);
    expect(first.isStackedTraceEnabled()).toBe(true);
    expect(localStorage.getItem(KEY)).toBe('1');
    vi.resetModules();
    const second = await load();
    expect(second.isStackedTraceEnabled()).toBe(true);
    second.setStackedTraceEnabled(false);
    expect(localStorage.getItem(KEY)).toBe('0');
    vi.resetModules();
    expect((await load()).isStackedTraceEnabled()).toBe(false);
  });

  it('reads anything but its own "on" value as off', async () => {
    localStorage.setItem(KEY, 'true');
    expect((await load()).isStackedTraceEnabled()).toBe(false);
  });

  it('marks the view unavailable for the page lifetime without touching the choice', async () => {
    const store = await load();
    store.setStackedTraceEnabled(true);
    expect(store.isStackedTraceUnavailable()).toBe(false);
    store.markStackedTraceUnavailable();
    expect(store.isStackedTraceUnavailable()).toBe(true);
    expect(store.isStackedTraceEnabled()).toBe(true);
    expect(localStorage.getItem(KEY)).toBe('1');
    vi.resetModules();
    expect((await load()).isStackedTraceUnavailable()).toBe(false);
  });
});
