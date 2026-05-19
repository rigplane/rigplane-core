import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { t, tPlural, messageFromReasonCode, setLocale } from '../index';
import { _resetLocale } from '../store.svelte';

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
  // Tests in jsdom default to en-US (or whatever the jsdom navigator
  // reports). Pin to en-US to avoid env drift.
  setLocale('en-US');
});

afterEach(() => {
  localStorage.clear();
});

describe('t()', () => {
  it('resolves a key from the en-US source catalog', () => {
    expect(t('common.action.save')).toBe('Save');
  });

  it('interpolates named placeholders', () => {
    expect(
      t('core.statusbar.connection.connectingTo', {
        radio: 'IC-7610',
        transport: 'LAN',
      }),
    ).toBe('Connecting to IC-7610 on LAN…');
  });

  it('resolves a translated value for ja-JP when the catalog covers the key', () => {
    // Both ja-JP and ru-RU are now complete pilot translations, so the
    // en-US silent-fallback path is no longer exercised by simply
    // switching locale for a generic action key. The fallback behaviour
    // itself is unit-tested at the runtime layer (see runtime.test.ts);
    // here we just confirm setLocale('ja-JP') resolves to the Japanese
    // string rather than the en-US source.
    setLocale('ja-JP');
    expect(t('common.action.cancel')).toBe('キャンセル');
  });

  it('returns translated value for ja-JP when present', () => {
    setLocale('ja-JP');
    expect(t('core.statusbar.connection.connected')).toBe('接続済み');
  });

  it('preserves glossary-token interpolation values verbatim across locales', () => {
    // Glossary tokens are composed at the call site: the caller asks for
    // `t('glossary.callsign')` and threads the returned value into another
    // string via params. The runtime must NOT mutate interpolated values.
    setLocale('ja-JP');
    const callsign = t('glossary.callsign');
    expect(callsign).toBe('コールサイン');
    // Now thread it into a parametric sentence using en-US fallback for
    // demonstration:
    setLocale('en-US');
    const sentence = t('core.error.transport.timeout.body', {
      host: '192.168.55.40',
    });
    expect(sentence).toContain('192.168.55.40');
  });
});

describe('tPlural()', () => {
  it('selects en-US singular for count=1', () => {
    expect(tPlural('core.diagnostics.attachedFiles', 1)).toBe('1 file attached');
  });

  it('selects en-US plural for count=5', () => {
    expect(tPlural('core.diagnostics.attachedFiles', 5)).toBe(
      '5 files attached',
    );
  });

  it('ja-JP uses .other only', () => {
    // Japanese has only `.other` in CLDR. The ja-JP catalog still
    // provides a `.one` key for parity with en-US (i18n-check enforces
    // strict key parity), but the runtime resolves to the same value
    // for any count via the `.other` selector for ja-JP. Either form
    // is acceptable here; we assert the literal translation we ship.
    setLocale('ja-JP');
    expect(tPlural('core.diagnostics.attachedFiles', 1)).toBe(
      'ファイル 1 件を添付',
    );
  });
});

describe('messageFromReasonCode()', () => {
  it('resolves a known reason code under the core.toast namespace', () => {
    expect(messageFromReasonCode('licenseExpired')).toBe(
      'Your license has expired. Reactivate to continue.',
    );
  });

  it('threads params into the resolved toast', () => {
    expect(messageFromReasonCode('updateAvailable', { version: '2.1.0' })).toBe(
      'An update is available: 2.1.0.',
    );
  });

  it('falls back to core.toast.unknown for an unknown code', () => {
    expect(messageFromReasonCode('completelyMadeUpCode')).toBe(
      'Something went wrong. Try again later.',
    );
  });

  it('rejects malformed codes safely (returns the unknown toast)', () => {
    expect(messageFromReasonCode('../injection.attempt')).toBe(
      'Something went wrong. Try again later.',
    );
    expect(messageFromReasonCode('')).toBe(
      'Something went wrong. Try again later.',
    );
  });
});

describe('pseudo-locale active', () => {
  it('wraps t() output in ⟦…⟧', () => {
    setLocale('qps-ploc');
    const out = t('common.action.save');
    expect(out.startsWith('⟦')).toBe(true);
    expect(out.endsWith('⟧')).toBe(true);
  });

  it('preserves glossary-token style interpolated values verbatim under pseudo-locale', () => {
    setLocale('qps-ploc');
    const out = t('core.statusbar.connection.connectingTo', {
      radio: 'IC-7610',
      transport: 'LAN',
    });
    expect(out).toContain('IC-7610');
    expect(out).toContain('LAN');
  });

  it('wraps tPlural() output', () => {
    setLocale('qps-ploc');
    const out = tPlural('core.diagnostics.attachedFiles', 3);
    expect(out.startsWith('⟦')).toBe(true);
    expect(out.endsWith('⟧')).toBe(true);
    expect(out).toContain('3'); // count param survives
  });

  it('wraps messageFromReasonCode output', () => {
    setLocale('qps-ploc');
    const out = messageFromReasonCode('licenseExpired');
    expect(out.startsWith('⟦')).toBe(true);
    expect(out.endsWith('⟧')).toBe(true);
  });
});
