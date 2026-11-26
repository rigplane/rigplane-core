import { managedTxRefusalToastCode, validateManagedTransmitDocument, type ManagedTransmitDocument } from '../types/managed-transmit';
import { apiFetch } from '../api/api-fetch';
import { getAuthHeaders } from './http-client';
import { controllerClient } from './controller-client';
const BASE = '/api/v1/managed-transmit';

export class ManagedTransmitError extends Error {
  constructor(readonly code: string, readonly status: number, readonly reason?: string) {
    super(`managed transmit command: ${status} (${code})`);
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
    const reason = (body as { code?: unknown } | null)?.code;
    const error = (body as { error?: unknown } | null)?.error;
    if (typeof reason === 'string') {
      const code = typeof error === 'string' && managedTxRefusalToastCode(error) !== 'txStartRefused'
        ? error : reason;
      throw new ManagedTransmitError(code, response.status, reason);
    }
    if (response.status === 409) return 'rejected';
    const code = typeof error === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(error)
      ? error : 'tx_request_failed';
    throw new ManagedTransmitError(code, response.status);
  }
  async setTot(configuredSeconds: number | null): Promise<ManagedTransmitDocument> { controllerClient.assertHostAction(); const response = await apiFetch(`${BASE}/tot`, { method: 'PUT', headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' }, body: JSON.stringify({ configuredSeconds }) }); if (!response.ok) throw new Error(`managed transmit TOT: ${response.status}`); return validateManagedTransmitDocument(await response.json()); }
}
