import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  applyContractOnBoot,
  PRO_LOCALE_QUERY_PARAM,
  PRO_LOCALE_STORAGE_KEY,
  readLocaleFromExternalStorage,
  readLocaleFromQuery,
  resolveInitialLocale,
} from '../locale-contract';
import { getLocale, setLocale, STORAGE_KEY, _resetLocale } from '../store.svelte';

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
});

afterEach(() => {
  localStorage.clear();
  _resetLocale();
  vi.restoreAllMocks();
});

describe('readLocaleFromQuery', () => {
  it('returns null when no locale query is present', () => {
    expect(readLocaleFromQuery('https://core.local/')).toBeNull();
  });

  it('returns a supported locale tagged as pro-shell-query', () => {
    const out = readLocaleFromQuery('https://core.local/?locale=ja-JP');
    expect(out).toEqual({ locale: 'ja-JP', source: 'pro-shell-query' });
  });

  it('accepts qps-ploc as a deliberate dev opt-in', () => {
    expect(readLocaleFromQuery('https://core.local/?locale=qps-ploc')).toEqual({
      locale: 'qps-ploc',
      source: 'pro-shell-query',
    });
  });

  it('rejects an unsupported locale tag', () => {
    expect(readLocaleFromQuery('https://core.local/?locale=de-DE')).toBeNull();
  });

  it('rejects an empty locale param', () => {
    expect(readLocaleFromQuery('https://core.local/?locale=')).toBeNull();
    expect(readLocaleFromQuery('https://core.local/?locale=%20%20')).toBeNull();
  });

  it('rejects nonsense values without throwing', () => {
    expect(readLocaleFromQuery('https://core.local/?locale=../etc/passwd')).toBeNull();
    expect(readLocaleFromQuery('https://core.local/?locale=ja_JP')).toBeNull();
  });

  it('returns null when the URL is malformed', () => {
    expect(readLocaleFromQuery('not a url')).toBeNull();
  });

  it('reads from window.location when no url is given', () => {
    // jsdom's window.location is mutable via history; set search and verify.
    const originalSearch = window.location.search;
    window.history.replaceState({}, '', `/?${PRO_LOCALE_QUERY_PARAM}=ja-JP`);
    try {
      expect(readLocaleFromQuery()).toEqual({
        locale: 'ja-JP',
        source: 'pro-shell-query',
      });
    } finally {
      window.history.replaceState({}, '', `/${originalSearch}`);
    }
  });
});

describe('readLocaleFromExternalStorage', () => {
  it('returns null when the storage key is unset', () => {
    expect(readLocaleFromExternalStorage()).toBeNull();
  });

  it('parses a well-formed envelope and tags the source', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({
        locale: 'ja-JP',
        source: 'pro-shell',
        updatedAt: '2026-05-19T18:00:00Z',
        supportedLocales: ['en-US', 'ja-JP', 'qps-ploc'],
      }),
    );
    expect(readLocaleFromExternalStorage()).toEqual({
      locale: 'ja-JP',
      source: 'pro-shell-storage',
    });
  });

  it('ignores malformed JSON without throwing', () => {
    localStorage.setItem(PRO_LOCALE_STORAGE_KEY, 'not json');
    expect(readLocaleFromExternalStorage()).toBeNull();
  });

  it('ignores an envelope without a locale field', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ source: 'pro-shell' }),
    );
    expect(readLocaleFromExternalStorage()).toBeNull();
  });

  it('ignores an envelope whose locale is not bundled', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 'pt-BR', source: 'pro-shell' }),
    );
    expect(readLocaleFromExternalStorage()).toBeNull();
  });

  it('ignores a numeric or null locale value', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 42 }),
    );
    expect(readLocaleFromExternalStorage()).toBeNull();
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: null }),
    );
    expect(readLocaleFromExternalStorage()).toBeNull();
  });
});

describe('resolveInitialLocale precedence', () => {
  it('prefers the URL query over the storage envelope', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 'qps-ploc' }),
    );
    expect(resolveInitialLocale('https://core.local/?locale=ja-JP')).toEqual({
      locale: 'ja-JP',
      source: 'pro-shell-query',
    });
  });

  it('falls back to the storage envelope when no query is present', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 'ja-JP' }),
    );
    expect(resolveInitialLocale('https://core.local/')).toEqual({
      locale: 'ja-JP',
      source: 'pro-shell-storage',
    });
  });

  it('returns null when neither surface has a valid hint', () => {
    expect(resolveInitialLocale('https://core.local/')).toBeNull();
  });

  it('falls through to storage when the query holds an invalid tag', () => {
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 'ja-JP' }),
    );
    expect(resolveInitialLocale('https://core.local/?locale=de-DE')).toEqual({
      locale: 'ja-JP',
      source: 'pro-shell-storage',
    });
  });
});

describe('applyContractOnBoot — integration with the store', () => {
  it('Pro query overrides an existing Core explicit setting', () => {
    // Core user previously picked en-US explicitly.
    setLocale('en-US');
    expect(getLocale()).toBe('en-US');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('en-US');

    const out = applyContractOnBoot('https://core.local/?locale=ja-JP');
    expect(out).toEqual({ locale: 'ja-JP', source: 'pro-shell-query' });
    expect(getLocale()).toBe('ja-JP');

    // The user's explicit Core key is NOT overwritten — standalone behavior
    // resumes on the next launch without a Pro hint.
    expect(localStorage.getItem(STORAGE_KEY)).toBe('en-US');
  });

  it('Pro storage envelope overrides an existing Core explicit setting', () => {
    setLocale('en-US');
    localStorage.setItem(
      PRO_LOCALE_STORAGE_KEY,
      JSON.stringify({ locale: 'ja-JP', source: 'pro-shell' }),
    );

    const out = applyContractOnBoot('https://core.local/');
    expect(out).toEqual({ locale: 'ja-JP', source: 'pro-shell-storage' });
    expect(getLocale()).toBe('ja-JP');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('en-US');
  });

  it('no-op when no Pro hint exists; standalone behavior preserved', () => {
    setLocale('ja-JP');
    const before = getLocale();
    expect(before).toBe('ja-JP');

    const out = applyContractOnBoot('https://core.local/');
    expect(out).toBeNull();
    expect(getLocale()).toBe(before);
    expect(localStorage.getItem(STORAGE_KEY)).toBe('ja-JP');
  });

  it('invalid Pro hint is ignored; Core explicit setting wins', () => {
    setLocale('ja-JP');
    const out = applyContractOnBoot('https://core.local/?locale=de-DE');
    expect(out).toBeNull();
    expect(getLocale()).toBe('ja-JP');
  });

  it('Pro hint outranks the browser-detected default', () => {
    // No explicit Core key is set; store starts at the detected value.
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    const out = applyContractOnBoot('https://core.local/?locale=ja-JP');
    expect(out).toEqual({ locale: 'ja-JP', source: 'pro-shell-query' });
    expect(getLocale()).toBe('ja-JP');
    // Still does not persist as the Core explicit setting.
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
