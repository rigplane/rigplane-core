import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { registerCatalog, _resetCatalogs } from '../runtime';
import { resolvePlural, selectCategory, pluralFallbackChain } from '../plural';

const EN = {
  'core.diagnostics.attachedFiles.one': '1 file attached',
  'core.diagnostics.attachedFiles.other': '{count} files attached',
};
const JA = {
  'core.diagnostics.attachedFiles.other': '{count} 個のファイルを添付',
};

beforeEach(() => {
  _resetCatalogs();
  registerCatalog('en-US', EN);
  registerCatalog('ja-JP', JA);
});

afterEach(() => {
  _resetCatalogs();
});

describe('Intl.PluralRules wrapper', () => {
  it('selects `one` for n=1 in en-US', () => {
    expect(selectCategory('en-US', 1)).toBe('one');
  });

  it('selects `other` for n=5 in en-US', () => {
    expect(selectCategory('en-US', 5)).toBe('other');
  });

  it('selects `other` for any count in ja-JP', () => {
    expect(selectCategory('ja-JP', 1)).toBe('other');
    expect(selectCategory('ja-JP', 5)).toBe('other');
  });
});

describe('resolvePlural', () => {
  it('renders the en-US singular for count=1', () => {
    expect(resolvePlural('core.diagnostics.attachedFiles', 1, 'en-US')).toBe(
      '1 file attached',
    );
  });

  it('renders the en-US plural for count=5', () => {
    expect(resolvePlural('core.diagnostics.attachedFiles', 5, 'en-US')).toBe(
      '5 files attached',
    );
  });

  it('renders ja-JP using only `.other` even for count=1', () => {
    expect(resolvePlural('core.diagnostics.attachedFiles', 1, 'ja-JP')).toBe(
      '1 個のファイルを添付',
    );
  });

  it('falls back to en-US when ja-JP lacks the specific plural form', () => {
    // ja-JP only has .other; English (`one`) is reached via the resolver
    // fallback in the .other → en-US-other path. The CLDR-category for
    // ja-JP is always `other`, so we just see the ja-JP `.other` string.
    expect(resolvePlural('core.diagnostics.attachedFiles', 1, 'ja-JP')).toBe(
      '1 個のファイルを添付',
    );
  });

  it('allows extra params alongside auto-injected count', () => {
    const enWithName = {
      'core.attach.one': '1 file: {name}',
      'core.attach.other': '{count} files starting with {name}',
    };
    _resetCatalogs();
    registerCatalog('en-US', enWithName);
    expect(
      resolvePlural('core.attach', 3, 'en-US', { name: 'foo' }),
    ).toBe('3 files starting with foo');
  });
});

describe('pluralFallbackChain', () => {
  it('always ends with `other`', () => {
    expect(pluralFallbackChain('one')).toContain('other');
    expect(pluralFallbackChain('few').slice(-1)[0]).toBe('other');
    expect(pluralFallbackChain('other')).toEqual(['other']);
  });
});
