import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import {
  getLocale,
  setLocale,
  STORAGE_KEY,
  SUPPORTED_LOCALES,
  _resetLocale,
} from '../store.svelte';

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
});

afterEach(() => {
  localStorage.clear();
  // Same fast-pool leak guard as `index.test.ts`/`locale-contract.test.ts`:
  // an explicit `setLocale` here (ja-JP, qps-ploc) would otherwise leak into
  // the next file in the worker.
  _resetLocale();
});

describe('locale store', () => {
  it('lists the bundled locales', () => {
    expect(SUPPORTED_LOCALES).toContain('en-US');
    expect(SUPPORTED_LOCALES).toContain('ja-JP');
    expect(SUPPORTED_LOCALES).toContain('qps-ploc');
  });

  it('persists an explicit selection to localStorage under the stable key', () => {
    setLocale('ja-JP');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ja-JP');
    expect(getLocale()).toBe('ja-JP');
  });

  it('rejects an unknown locale string', () => {
    const before = getLocale();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    setLocale('xx-YY' as any);
    expect(getLocale()).toBe(before);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('round-trips qps-ploc as an explicit developer opt-in', () => {
    setLocale('qps-ploc');
    expect(getLocale()).toBe('qps-ploc');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('qps-ploc');
  });

  it('clears persisted value on _resetLocale (test hook)', () => {
    setLocale('ja-JP');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ja-JP');
    _resetLocale();
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
