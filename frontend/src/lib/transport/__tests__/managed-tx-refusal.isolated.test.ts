import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { managedTxRefusalToastCode } from '../../types/managed-transmit';
import { messageFromReasonCode, setLocale } from '../../i18n';
import enUS from '../../i18n/locales/en-US.json';
import ruRU from '../../i18n/locales/ru-RU.json';
import jaJP from '../../i18n/locales/ja-JP.json';
import { MockWebSocket, instances } from './support/fake-ws-backend';

const wire = JSON.parse(readFileSync(resolve(process.cwd(), '../tests/fixtures/managed-tx-refusal-wire.json'), 'utf8'));
const cases = [
  ['tx_held_by_other', 'txHeldByOther'],
  ['release_in_progress', 'txReleaseInProgress'],
  ['authority_unavailable', 'txAuthorityUnavailable'],
  ['read_only', 'txReadOnly'],
  ['controller_refused', 'txControllerRefused'],
  ['radio_refused', 'txRadioRefused'],
  ['other', 'txStartRefused'],
  ['future_refusal', 'txStartRefused'],
  ['constructor', 'txStartRefused'],
] as const;

afterEach(() => { setLocale('en-US'); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('managed TX refusal presentation', () => {
  // The producer pins this closed list in the shared wire fixture; its Python
  // contract test asserts it equals ManagedTxRefusalReason.
  it.each(wire.codes as string[])('has a localized presentation for refusal code %s', (code) => {
    expect(cases.some(([covered]) => covered === code)).toBe(true);
    expect(managedTxRefusalToastCode(code)).not.toBe('');
  });

  it('presents an unknown refusal code with the generic text', () => {
    expect(managedTxRefusalToastCode('other')).toBe('txStartRefused');
  });

  it.each(cases)('maps %s to an existing toast in all supported locales', (code, toast) => {
    expect(managedTxRefusalToastCode(code)).toBe(toast);
    for (const locale of ['en-US', 'ru-RU', 'ja-JP'] as const) {
      setLocale(locale);
      const text = messageFromReasonCode(toast);
      expect(text).not.toContain('[missing:');
      expect(text).not.toBe(`core.toast.${toast}`);
      const catalog = { 'en-US': enUS, 'ru-RU': ruRU, 'ja-JP': jaJP }[locale];
      expect(catalog).toHaveProperty([`core.toast.${toast}`]);
      expect(text).toBe((catalog as unknown as Record<string, string>)[`core.toast.${toast}`]);
      if (code === 'future_refusal') expect(text).toBe(messageFromReasonCode('txStartRefused'));
    }
  });

  it.each([wire.wsHeld, wire.wsReleasing, wire.wsUnavailable, { ...wire.wsHeld, code: 'future_refusal' }])('localizes WS refusal $code and retains its wire code', async (frame) => {
    vi.resetModules(); vi.useFakeTimers(); instances.length = 0;
    vi.stubGlobal('WebSocket', MockWebSocket);
    const { WsChannel } = await import('../ws-client');
    const channel = new WsChannel();
    const received = vi.fn();
    channel.onMessage(received);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      channel.connect('ws://localhost/api/v1/ws');
      instances[0].simulateOpen();
      instances[0].simulateMessage(JSON.stringify(frame));
      expect(received).toHaveBeenCalledWith(frame);
      expect(received).toHaveBeenCalledWith(expect.objectContaining({
        type: 'notification', level: 'error', code: managedTxRefusalToastCode(frame.code),
      }));
      expect(warn).toHaveBeenCalledWith('[tx] Managed transmit refused:', frame.code);
    } finally { channel.disconnect(); warn.mockRestore(); }
  });
});
