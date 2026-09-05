import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagedTransmitClient } from '../managed-transmit-client';
const document = { schemaVersion: 1, sampledAt: '2026-09-04T00:00:00.000Z', managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null, lastActuation: null, abortErrors: [], tot: { configuredSeconds: 180, active: true, remainingMs: 900, expiresAt: 'ignored' } }, txObservation: { observedPtt: 'unknown' } };

describe('ManagedTransmitClient HTTP authentication', () => {
  const requests = [
    { name: 'snapshot', path: '', method: 'GET', body: undefined, call: (client: ManagedTransmitClient) => client.snapshot(), result: document },
    { name: 'command', path: '/command', method: 'POST', body: { operation: 'transmit_on' }, call: (client: ManagedTransmitClient) => client.command('transmit_on'), result: 'accepted' },
    { name: 'TOT', path: '/tot', method: 'PUT', body: { configuredSeconds: 60 }, call: (client: ManagedTransmitClient) => client.setTot(60), result: document },
  ];

  beforeEach(() => {
    const values = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  function respondTo(expectedToken: string | null) {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const authorized = new Headers(init?.headers).get('Authorization') === expectedToken;
      return { ok: authorized, status: authorized ? 202 : 401, json: async () => document };
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it.each(requests)('authenticates $name with the application token and preserves the request', async ({ path, method, body, call, result }) => {
    localStorage.setItem('rigplane-auth-token', 'synthetic-test-token');
    const fetchMock = respondTo('Bearer synthetic-test-token');

    await expect(call(new ManagedTransmitClient())).resolves.toEqual(result);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/v1/managed-transmit${path}`);
    expect(init?.method ?? 'GET').toBe(method);
    expect(init?.body).toBe(body === undefined ? undefined : JSON.stringify(body));
    expect(new Headers(init?.headers).get('Content-Type')).toBe(body === undefined ? null : 'application/json');
  });

  it.each(requests)('uses rotated and removed application tokens on the same client for $name', async ({ name, call, result }) => {
    const client = new ManagedTransmitClient();
    localStorage.setItem('rigplane-auth-token', 'synthetic-first-token');
    respondTo('Bearer synthetic-first-token');
    await expect(call(client)).resolves.toEqual(result);
    localStorage.setItem('rigplane-auth-token', 'synthetic-next-token');
    respondTo('Bearer synthetic-next-token');
    await expect(call(client)).resolves.toEqual(result);
    localStorage.removeItem('rigplane-auth-token');
    await expect(call(client)).rejects.toThrow(`managed transmit ${name}: 401`);
  });

  it.each(requests)('supports an unauthenticated server for $name without an Authorization header', async ({ call, result }) => {
    respondTo(null);
    await expect(call(new ManagedTransmitClient())).resolves.toEqual(result);
  });

  it.each(requests)('preserves the $name 401 error for missing and invalid tokens', async ({ name, call }) => {
    respondTo('Bearer synthetic-valid-token');
    const client = new ManagedTransmitClient();
    await expect(call(client)).rejects.toThrow(`managed transmit ${name}: 401`);
    localStorage.setItem('rigplane-auth-token', 'synthetic-invalid-token');
    await expect(call(client)).rejects.toThrow(`managed transmit ${name}: 401`);
  });

  it('supports snapshot without browser storage when the server permits it', async () => {
    vi.stubGlobal('localStorage', undefined);
    respondTo(null);
    await expect(new ManagedTransmitClient().snapshot()).resolves.toEqual(document);
  });

  it('preserves an authenticated command rejection', async () => {
    localStorage.setItem('rigplane-auth-token', 'synthetic-test-token');
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 409 });
    vi.stubGlobal('fetch', fetchMock);
    await expect(new ManagedTransmitClient().command('force_off')).resolves.toBe('rejected');
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer synthetic-test-token');
  });
});
describe('ManagedTransmitClient', () => { it('uses only managed authority routes and exact command body', async () => { globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 202, json: async () => document }); const client = new ManagedTransmitClient(); await client.command('force_off'); expect(fetch).toHaveBeenCalledWith('/api/v1/managed-transmit/command', expect.objectContaining({ body: JSON.stringify({ operation: 'force_off' }) })); }); it('rejects a malformed document rather than deriving legacy truth', async () => { globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...document, txObservation: { observedPtt: true } }) }); await expect(new ManagedTransmitClient().snapshot()).rejects.toThrow('txObservation.observedPtt'); }); it.each([{ operation: 'unknown', result: 'accepted', attemptId: 'x' }, { operation: 'ptt_on', result: 'future', attemptId: 'x' }, { operation: 'ptt_on', result: 'accepted' }])('rejects malformed last actuation', async (lastActuation) => { globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...document, managedTransmit: { ...document.managedTransmit, lastActuation } }) }); await expect(new ManagedTransmitClient().snapshot()).rejects.toThrow('lastActuation'); }); it.each([[{ operation: 'unknown', error: 'x' }], [{ operation: 'stop_cw' }], [{ operation: 'stop_cw', error: 'x', extra: true }]])('rejects malformed abort errors', async (abortErrors) => { globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...document, managedTransmit: { ...document.managedTransmit, abortErrors } }) }); await expect(new ManagedTransmitClient().snapshot()).rejects.toThrow('abortErrors'); }); });
