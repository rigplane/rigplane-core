import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiFetch } from '../api-fetch';
import { controllerClient } from '../../transport/controller-client';

const controllerKey = 'a'.repeat(64);

// Controller acquisition has no credential yet; release supplies its captured
// key after invalidating the session. The companion route has its own contract.
const credentialExceptions = [
  ['POST', '/api/v1/controller'],
  ['DELETE', '/api/v1/controller'],
  ['PUT', '/api/local/v1/rc28/tuning-step'],
] as const;

describe('apiFetch', () => {
  beforeEach(() => {
    vi.spyOn(controllerClient, 'remote', 'get').mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('forwards a sessionless request unchanged and returns the response as-is', async () => {
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

  it.each(['POST', 'PUT', 'PATCH', 'DELETE'])(
    'keeps local %s sessionless when a stale grant remains', async (method) => {
      vi.spyOn(controllerClient, 'remote', 'get').mockReturnValue(false);
      vi.spyOn(controllerClient, 'headers').mockReturnValue({ 'X-RigPlane-Controller': controllerKey });
      const response = new Response('{}');
      const stub = vi.fn(async () => response);
      vi.stubGlobal('fetch', stub);
      const init = { method, headers: { 'Content-Type': 'application/json' }, body: '{}' };
      expect(await apiFetch('/api/v1/eibi/fetch', init)).toBe(response);
      expect(stub).toHaveBeenCalledWith('/api/v1/eibi/fetch', init);
      expect(controllerClient.headers).not.toHaveBeenCalled();
    },
  );

  it('preserves an explicitly supplied local session credential', async () => {
    vi.spyOn(controllerClient, 'remote', 'get').mockReturnValue(false);
    const stub = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', stub);
    const init = { method: 'POST', headers: { 'X-RigPlane-Controller': controllerKey } };
    await apiFetch('/api/v1/eibi/fetch', init);
    expect(stub).toHaveBeenCalledWith('/api/v1/eibi/fetch', init);
  });

  it.each(['POST', 'PUT', 'PATCH', 'DELETE', 'post'])(
    'attaches the controller credential to %s independently of the mutation path',
    async (method) => {
      vi.spyOn(controllerClient, 'headers').mockReturnValue({ 'X-RigPlane-Controller': controllerKey });
      const response = new Response('{}');
      const stub = vi.fn(async (_path: string, _init?: RequestInit) => response);
      vi.stubGlobal('fetch', stub);
      for (const path of [
        '/api/v1/eibi/fetch',
        '/api/v1/managed-transmit/command',
        '/api/v1/managed-transmit/tot',
        '/api/v1/radio/power',
        '/api/v1/band-plan/config',
        '/api/v1/diagnose/preview',
        '/api/v1/diagnose/send',
        '/api/v1/diagnose/save',
        '/api/v1/diagnose/preview/preview-id',
        '/api/v1/future-mutation',
      ]) {
        const init = { method, body: '{}', redirect: 'error' as const };
        expect(await apiFetch(path, init)).toBe(response);
        const [url, sent] = stub.mock.calls.at(-1)!;
        expect(url).toBe(path);
        expect(new Headers(sent?.headers).get('X-RigPlane-Controller')).toBe(controllerKey);
        expect(sent).toMatchObject(init);
        expect(init).not.toHaveProperty('headers');
      }
    },
  );

  it.each(([
    { 'Content-Type': 'application/json', 'X-Diagnostic-CSRF': 'csrf' },
    new Headers({ 'Content-Type': 'application/json', 'X-Diagnostic-CSRF': 'csrf' }),
    [['Content-Type', 'application/json'], ['X-Diagnostic-CSRF', 'csrf']],
  ] satisfies HeadersInit[]).map((headers) => ({ headers })))(
    'preserves caller headers without changing them ($headers)', async ({ headers }) => {
      vi.spyOn(controllerClient, 'headers').mockReturnValue({ 'X-RigPlane-Controller': controllerKey });
      const stub = vi.fn(async (_path: string, _init?: RequestInit) => new Response('{}'));
      vi.stubGlobal('fetch', stub);
      await apiFetch('/api/v1/eibi/fetch', { method: 'POST', headers });
      const sent = new Headers(stub.mock.calls[0][1]?.headers);
      expect(sent.get('X-RigPlane-Controller')).toBe(controllerKey);
      expect(sent.get('Content-Type')).toBe('application/json');
      expect(sent.get('X-Diagnostic-CSRF')).toBe('csrf');
      expect(new Headers(headers).has('X-RigPlane-Controller')).toBe(false);
    },
  );

  it.each([
    ...credentialExceptions,
    ['GET', '/api/v1/eibi/status'],
    ['HEAD', '/api/v1/info'],
    ['POST', 'https://example.invalid/api/v1/eibi/fetch'],
  ] as const)('leaves %s %s credentials with the caller', async (method, path) => {
    vi.spyOn(controllerClient, 'headers').mockReturnValue({ 'X-RigPlane-Controller': controllerKey });
    const stub = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', stub);
    const init = { method };
    await apiFetch(path, init);
    expect(stub).toHaveBeenCalledWith(path, init);
  });

  it('preserves the captured release credential after the current session is gone', async () => {
    vi.spyOn(controllerClient, 'headers').mockReturnValue({});
    const stub = vi.fn(async () => new Response('{}'));
    vi.stubGlobal('fetch', stub);
    const init = { method: 'DELETE', headers: { 'X-RigPlane-Controller': controllerKey } };
    await apiFetch('/api/v1/controller', init);
    expect(stub).toHaveBeenCalledWith('/api/v1/controller', init);
  });
});
