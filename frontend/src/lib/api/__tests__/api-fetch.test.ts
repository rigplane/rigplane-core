/**
 * `apiFetch` unit tests (C4, no behaviour change).
 *
 * Pins that the REST seam forwards path/init to `fetch` unchanged and
 * returns the response untouched. Call-site behaviour (URLs, headers,
 * redirect options, error handling) therefore stays exactly as today.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { apiFetch } from '../api-fetch';

describe('apiFetch', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('forwards path and init unchanged and returns the response as-is', async () => {
    const response = new Response(JSON.stringify({ ok: true }), { status: 200 });
    const stub = vi.fn(async () => response);
    vi.stubGlobal('fetch', stub);
    const init: RequestInit = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      redirect: 'error',
      body: JSON.stringify({ a: 1 }),
    };
    const result = await apiFetch('/api/v1/radio/power', init);
    expect(result).toBe(response);
    expect(stub).toHaveBeenCalledTimes(1);
    expect(stub).toHaveBeenCalledWith('/api/v1/radio/power', init);
  });

  it('works without init', async () => {
    const response = new Response('{}', { status: 200 });
    const stub = vi.fn(async () => response);
    vi.stubGlobal('fetch', stub);
    await apiFetch('/api/v1/capabilities');
    expect(stub).toHaveBeenCalledWith('/api/v1/capabilities', undefined);
  });
});
