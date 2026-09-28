import { describe, expect, it } from 'vitest';

import enUS from '../../i18n/locales/en-US.json';
import jaJP from '../../i18n/locales/ja-JP.json';
import ruRU from '../../i18n/locales/ru-RU.json';

// MOR-2845: the toast that fires when the mic start path hits a non-secure
// context. Pinned as the literal shipped texts (no t(key) indirection) so a
// wording drift or a dropped locale key fails here, not in the field.
describe('TX insecure-context toast wording (MOR-2845)', () => {
  it('ships the literal en-US text', () => {
    expect(enUS['core.toast.txAudioInsecureContext']).toBe(
      "The microphone needs a secure connection. Open this page with https:// (the server's --tls address) or on localhost.",
    );
  });

  it('ships the literal ru-RU text', () => {
    expect(ruRU['core.toast.txAudioInsecureContext']).toBe(
      'Микрофону нужно защищённое соединение. Откройте страницу по адресу https:// (сервер с --tls) или на localhost.',
    );
  });

  it('ships the literal ja-JP text', () => {
    expect(jaJP['core.toast.txAudioInsecureContext']).toBe(
      'マイクには安全な接続が必要です。このページを https://（サーバーの --tls アドレス）または localhost で開いてください。',
    );
  });
});
