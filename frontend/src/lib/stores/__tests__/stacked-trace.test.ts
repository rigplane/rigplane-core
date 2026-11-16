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

describe('stacked-trace settings', () => {
  const SETTINGS_KEY = 'rigplane-spectrum-stacked-trace-settings';
  const defaults = async () => (await import('../../renderers/stacked-trace-geometry')).defaultStackedTraceSettings;

  it('starts from the defaults and stores nothing until something is changed', async () => {
    const store = await load();
    expect(store.stackedTraceSettings()).toEqual(await defaults());
    expect(localStorage.getItem(SETTINGS_KEY)).toBeNull();
  });

  it('keeps a change per browser, held inside its range, and restores it on the next load', async () => {
    const first = await load();
    first.setStackedTraceSettings({ haze: 0.5 });
    first.setStackedTraceSettings({ rows: 1_000 });
    const expected = { ...await defaults(), haze: 0.5, rows: 160 };
    expect(first.stackedTraceSettings()).toEqual(expected);
    expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!)).toEqual(expected);
    vi.resetModules();
    expect((await load()).stackedTraceSettings()).toEqual(expected);
  });

  it('returns to the defaults and forgets the stored values', async () => {
    const store = await load();
    store.setStackedTraceSettings({ recede: 1.5 });
    store.setStackedTraceSettings(null);
    expect(store.stackedTraceSettings()).toEqual(await defaults());
    expect(localStorage.getItem(SETTINGS_KEY)).toBeNull();
    vi.resetModules();
    expect((await load()).stackedTraceSettings()).toEqual(await defaults());
  });

  it.each(['{not json', '"text"', '{"haze":"thick","rows":null}'])('reads stored %s as the defaults', async (stored) => {
    localStorage.setItem(SETTINGS_KEY, stored);
    expect((await load()).stackedTraceSettings()).toEqual(await defaults());
  });

  it('takes the stored values it can read and the defaults for the rest', async () => {
    localStorage.setItem(SETTINGS_KEY, '{"haze":0.4,"recede":"wide"}');
    expect((await load()).stackedTraceSettings()).toEqual({ ...await defaults(), haze: 0.4 });
  });
});
