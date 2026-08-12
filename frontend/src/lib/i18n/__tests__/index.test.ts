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
  _resetLocale();
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
    // itself is unit-tested at the runtime layer (see runtime.isolated.test.ts);
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

  // MOR-1422: the `disabledReasonText` shared helper (`semantic/
  // disabled-reason.ts`) resolves these two keys for every semantic surface
  // that renders a present-but-unusable control. Both locales are asserted
  // by their OWN text (not just "resolves to something") so a catalog that
  // dropped one language's entry — silently falling back to en-US — fails
  // this test instead of shipping unnoticed.
  it('carries the MOR-1422 disabled-reason strings in en-US', () => {
    expect(t('core.disabledReason.missing')).toBe('Not supported by this radio');
    expect(t('core.disabledReason.unobserved')).toBe('Not yet observed');
  });

  it('carries the MOR-1422 disabled-reason strings in ru-RU (own catalog entries)', () => {
    setLocale('ru-RU');
    expect(t('core.disabledReason.missing')).toBe('Не поддерживается этим трансивером');
    expect(t('core.disabledReason.unobserved')).toBe('Ещё не считано');
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

  // MOR-1422: the client-synthesized `sendCommand` refusal notice
  // (`$lib/transport/ws-client`) resolves through this SAME function — a
  // missing catalog entry would silently fall back to `core.toast.unknown`
  // rather than fail loudly, so the fallback text is exactly what a missing
  // key looks like and is what these two locale assertions rule out.
  it('resolves the MOR-1422 command-refusal reason code in en-US', () => {
    expect(messageFromReasonCode('commandRefusedLinkDegraded')).toBe(
      'Command not sent — link to the radio is degraded',
    );
  });

  it('resolves the MOR-1422 command-refusal reason code in ru-RU (own catalog entry, not the en-US fallback)', () => {
    setLocale('ru-RU');
    expect(messageFromReasonCode('commandRefusedLinkDegraded')).toBe(
      'Команда не отправлена — связь с трансивером деградировала',
    );
  });

  // MOR-1445: a command accepted at enqueue can still fail once the poller
  // actually executes it against the radio. The server sends this reason
  // code to the issuing session only, never a broadcast (with the backend
  // exception text threaded as `reason`) so the operator sees a localized
  // toast instead of a raw English string.
  // ja-JP is a known, separately-tracked backlog gap for this code (same
  // status as commandRefusedLinkDegraded above) — not asserted here.
  it('resolves the MOR-1445 post-ack command-failure reason code in en-US, threading the reason', () => {
    expect(
      messageFromReasonCode('commandExecutionFailed', { reason: 'radio did not respond' }),
    ).toBe('Command failed: radio did not respond');
  });

  it('resolves the MOR-1445 post-ack command-failure reason code in ru-RU (own catalog entry, not the en-US fallback)', () => {
    setLocale('ru-RU');
    expect(
      messageFromReasonCode('commandExecutionFailed', { reason: 'radio did not respond' }),
    ).toBe('Команда не выполнена: radio did not respond');
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
