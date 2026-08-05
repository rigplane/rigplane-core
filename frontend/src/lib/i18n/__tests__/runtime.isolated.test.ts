import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import {
  registerCatalog,
  resolve,
  interpolate,
  lookup,
  setMissingKeyHandler,
  _resetCatalogs,
} from '../runtime';

const EN = {
  $schema: { _comment: 'doc' },
  'common.action.save': 'Save',
  'common.action.cancel': 'Cancel',
  'core.statusbar.connection.connectingTo': 'Connecting to {radio} on {transport}…',
  'core.error.literalBrace.example': "Use '{'name} to refer to a placeholder.",
};

const JA = {
  $schema: { _comment: 'doc' },
  'common.action.save': '保存',
  'core.statusbar.connection.connectingTo': '{radio} に {transport} で接続中…',
};

beforeEach(() => {
  _resetCatalogs();
  registerCatalog('en-US', EN);
  registerCatalog('ja-JP', JA);
});

afterEach(() => {
  _resetCatalogs();
});

describe('interpolate', () => {
  it('replaces named placeholders', () => {
    expect(
      interpolate('Hello {name}, you have {count} items', {
        name: 'Sergey',
        count: 3,
      }),
    ).toBe('Hello Sergey, you have 3 items');
  });

  it('leaves unknown placeholders intact', () => {
    expect(interpolate('Hello {name}, {unknown}', { name: 'X' })).toBe(
      'Hello X, {unknown}',
    );
  });

  it("supports literal brace escape '{'", () => {
    // The escape `'{'` (three chars: quote, brace, quote) becomes a literal `{`.
    // A literal `}` needs no escape because `}` alone is not a placeholder
    // start.
    expect(interpolate("Use '{'name} verbatim", { name: 'X' })).toBe(
      'Use {name} verbatim',
    );
  });

  it('handles missing params', () => {
    expect(interpolate('No params here')).toBe('No params here');
  });

  it('coerces numbers to strings', () => {
    expect(interpolate('Count: {count}', { count: 42 })).toBe('Count: 42');
  });
});

describe('lookup + resolve', () => {
  it('returns the translation in the active locale', () => {
    expect(lookup('common.action.save', 'ja-JP')).toBe('保存');
  });

  it('falls back to en-US silently when the key is missing in the active locale', () => {
    expect(lookup('common.action.cancel', 'ja-JP')).toBe('Cancel');
  });

  it('returns [missing:key] in dev when neither locale has the key', () => {
    expect(lookup('core.nonexistent.key', 'ja-JP')).toBe(
      '[missing:core.nonexistent.key]',
    );
  });

  it('calls the missing-key hook on softer (locale-only) miss', () => {
    const hook = vi.fn();
    setMissingKeyHandler(hook);
    lookup('common.action.cancel', 'ja-JP');
    expect(hook).toHaveBeenCalledWith(
      expect.objectContaining({
        key: 'common.action.cancel',
        locale: 'ja-JP',
      }),
    );
  });

  it('calls the missing-key hook on hard miss (no en-US either)', () => {
    const hook = vi.fn();
    setMissingKeyHandler(hook);
    lookup('does.not.exist', 'ja-JP');
    expect(hook).toHaveBeenCalledWith(
      expect.objectContaining({ key: 'does.not.exist' }),
    );
  });

  it('does NOT call missing-key hook for fully-present en-US key in en-US locale', () => {
    const hook = vi.fn();
    setMissingKeyHandler(hook);
    lookup('common.action.save', 'en-US');
    expect(hook).not.toHaveBeenCalled();
  });

  it('interpolates via resolve()', () => {
    expect(
      resolve('core.statusbar.connection.connectingTo', 'ja-JP', {
        radio: 'IC-7610',
        transport: 'LAN',
      }),
    ).toBe('IC-7610 に LAN で接続中…');
  });

  it('strips the $schema documentation wrapper from catalogs', () => {
    // `$schema` is not a real message key — looking it up must miss.
    expect(lookup('$schema', 'en-US')).toBe('[missing:$schema]');
  });

  it("propagates literal-brace escape through resolve()", () => {
    expect(resolve('core.error.literalBrace.example', 'en-US', { name: 'foo' }))
      .toBe('Use {name} to refer to a placeholder.');
  });
});
