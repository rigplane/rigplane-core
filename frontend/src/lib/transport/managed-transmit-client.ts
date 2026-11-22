import { validateManagedTransmitDocument, type ManagedTransmitDocument } from '../types/managed-transmit';
import { apiFetch } from '../api/api-fetch';
import { getAuthHeaders } from './http-client';
import { controllerClient } from './controller-client';
import { getLocale } from '../i18n';
// MOR-4660 OWNER STRING REVIEW: USB_TX_MOD_INPUT_MESSAGES (en/ru/ja).
export const USB_TX_MOD_INPUT_MESSAGES = {
  'en-US': ["The radio takes its microphone from {source}. Set the radio's MOD input to USB.", "The radio's microphone input could not be read. Set the radio's MOD input to USB."],
  'ru-RU': ['Трансивер получает звук для передачи со входа {source}. Установите MOD input трансивера в USB.', 'Не удалось прочитать настройку входа микрофона трансивера. Установите MOD input трансивера в USB.'],
  'ja-JP': ['無線機のマイク入力は {source} です。無線機の MOD 入力を USB に設定してください。', '無線機のマイク入力設定を読み取れませんでした。無線機の MOD 入力を USB に設定してください。'],
} as const;

export interface TxModInputFailure {
  error: 'tx_mod_input';
  message: string;
  source: string | null;
}

export function usbTxModInputMessage(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null;
  const failure = body as Partial<TxModInputFailure>;
  if (failure.error !== 'tx_mod_input' || typeof failure.message !== 'string'
    || (failure.source !== null && typeof failure.source !== 'string')) return null;
  const locale = getLocale();
  const messages = USB_TX_MOD_INPUT_MESSAGES[locale === 'ru-RU' || locale === 'ja-JP' ? locale : 'en-US'];
  return failure.source === null ? messages[1] : messages[0].replace('{source}', failure.source);
}

const BASE = '/api/v1/managed-transmit';

export class ManagedTransmitError extends Error {
  constructor(readonly code: string, readonly status: number, message?: string) {
    super(message ?? `managed transmit command: ${status} (${code})`);
  }
}

export class ManagedTransmitClient {
  async snapshot(): Promise<ManagedTransmitDocument> { const response = await apiFetch(BASE, { headers: getAuthHeaders() }); if (!response.ok) throw new Error(`managed transmit snapshot: ${response.status}`); return validateManagedTransmitDocument(await response.json()); }
  async command(operation: 'transmit_on' | 'force_off'): Promise<'accepted' | 'rejected'> {
    if (operation === 'transmit_on') {
      controllerClient.assertHostAction();
      if (controllerClient.pttStyle === 'momentary') {
        throw new ManagedTransmitError('controller_tx_unsupported', 403);
      }
    }
    const response = await apiFetch(`${BASE}/command`, {
      method: 'POST',
      headers: { ...getAuthHeaders(), ...controllerClient.headers(), 'Content-Type': 'application/json' },
      redirect: 'error',
      body: JSON.stringify({ operation }),
    });
    if (response.status === 202) return 'accepted';
    const body: unknown = await response.json().catch(() => null);
    const modInputMessage = usbTxModInputMessage(body);
    if (modInputMessage) throw new ManagedTransmitError('tx_mod_input', response.status, modInputMessage);
    if (response.status === 409) return 'rejected';
    const error = (body as { error?: unknown } | null)?.error;
    const code = typeof error === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(error)
      ? error : 'tx_request_failed';
    throw new ManagedTransmitError(code, response.status);
  }
  async setTot(configuredSeconds: number | null): Promise<ManagedTransmitDocument> { controllerClient.assertHostAction(); const response = await apiFetch(`${BASE}/tot`, { method: 'PUT', headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ configuredSeconds }) }); if (!response.ok) throw new Error(`managed transmit TOT: ${response.status}`); return validateManagedTransmitDocument(await response.json()); }
}
