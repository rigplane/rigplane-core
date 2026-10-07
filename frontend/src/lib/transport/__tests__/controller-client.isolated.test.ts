import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const key = 'a'.repeat(64);
const grant = { controller_key: key, generation: 4, ttl_ms: 6000, heartbeat_ms: 2000 };
const status = { protocol_version: 1, mode: 'remote', state: 'held', generation: 4 };

function response(body: unknown, code = 200): Response {
  return new Response(JSON.stringify(body), { status: code });
}

function api(adopted: Record<string, unknown> = grant) {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return response(adopted, 201);
    return response(status);
  });
}

describe('controller protocol v1 client', () => {
  beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('keeps supported local mode anonymous without acquire, heartbeat or DELETE', async () => {
    const fetcher = vi.fn(async () => response({ ...status, mode: 'local' }));
    vi.stubGlobal('fetch', fetcher);
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await client.prepare();
    const send = vi.fn();
    client.controlOpened(send);
    await vi.advanceTimersByTimeAsync(8000);
    expect(client.remote).toBe(false);
    expect(client.protocols()).toEqual([]);
    expect(client.headers()).toEqual({});
    await client.release();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(send).not.toHaveBeenCalled();
  });

  it('adopts the native held grant before treating it as busy, without heartbeat or shared DELETE', async () => {
    const fetcher = api({ ...grant, controller_role: 'auxiliary' });
    vi.stubGlobal('fetch', fetcher);
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await client.prepare();
    expect(client.role).toBe('auxiliary');
    expect(client.active).toBe(true);
    await client.waitForPrimary();
    const send = vi.fn();
    client.controlOpened(send);
    await vi.advanceTimersByTimeAsync(10000);
    expect(send).not.toHaveBeenCalled();
    expect(client.protocols()).toEqual([`rigplane-controller-v1.${key}`]);
    expect(client.headers()).toEqual({ 'X-RigPlane-Controller': key });
    expect(JSON.stringify(client)).not.toContain(key);
    await client.release();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    await client.prepare();
    expect(client.role).toBe('auxiliary');
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(2);
    expect(fetcher.mock.calls.every(([url]) => !url.includes(key))).toBe(true);
  });

  it('serializes acquire and waits for its primary before child attachment', async () => {
    const fetcher = api();
    vi.stubGlobal('fetch', fetcher);
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await Promise.all([client.prepare(), client.prepare()]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({ kind: 'browser' });
    expect(client.role).toBe('primary');
    expect(client.active).toBe(false);
    let attached = false;
    const child = client.waitForPrimary().then(() => { attached = true; });
    await Promise.resolve();
    expect(attached).toBe(false);
    const send = vi.fn();
    client.controlOpened(send);
    await child;
    expect(client.active).toBe(true);
    expect(send).toHaveBeenCalledWith({ type: 'controller_heartbeat' });
    await client.release();
    expect(fetcher.mock.calls.at(-1)?.[1]).toMatchObject({
      method: 'DELETE', headers: { 'X-RigPlane-Controller': key }, redirect: 'error',
    });
  });

  it('carries the current key on force-off and refuses remote latch or TOT before HTTP', async () => {
    const fetcher = api({ ...grant, controller_role: 'auxiliary' });
    vi.stubGlobal('fetch', fetcher);
    const { controllerClient } = await import('../controller-client');
    const { ManagedTransmitClient } = await import('../managed-transmit-client');
    await controllerClient.prepare();
    const managed = new ManagedTransmitClient();
    await expect(managed.command('transmit_on')).rejects.toMatchObject({ code: 'controller_local_required' });
    await expect(managed.setTot(30)).rejects.toMatchObject({ code: 'controller_local_required' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    fetcher.mockImplementationOnce(async () => response({}, 202));
    await expect(managed.command('force_off')).resolves.toBe('accepted');
    expect(fetcher.mock.calls.at(-1)?.[1]).toMatchObject({
      method: 'POST', redirect: 'error',
      headers: { 'X-RigPlane-Controller': key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation: 'force_off' }),
    });
    await controllerClient.release();
  });

  it('expires on missing primary acknowledgements and fences before notifying consumers', async () => {
    vi.stubGlobal('fetch', api());
    const { ControllerClient } = await import('../controller-client');
    let now = 0;
    const client = new ControllerClient(() => now);
    await client.prepare();
    const old = client.epoch;
    const send = vi.fn();
    client.controlOpened(send);
    const lost = vi.fn(() => expect(client.current(old)).toBe(false));
    client.onLoss(lost);
    now = 2000;
    await vi.advanceTimersByTimeAsync(2000);
    client.acknowledge({ type: 'controller_heartbeat', generation: 4 });
    now = 7999;
    await vi.advanceTimersByTimeAsync(2000);
    expect(lost).not.toHaveBeenCalled();
    now = 8000;
    await vi.advanceTimersByTimeAsync(2000);
    expect(lost).toHaveBeenCalledOnce();
    expect(client.active).toBe(false);
    expect(client.headers()).toEqual({});
    const sends = send.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(send).toHaveBeenCalledTimes(sends);
  });

  it('reports actual POST busy without a lease even when anonymous GET says held', async () => {
    const fetcher = vi.fn(async (_url, init?: RequestInit) =>
      init?.method === 'POST' ? response({ error: 'controller_busy' }, 409) : response(status));
    vi.stubGlobal('fetch', fetcher);
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await expect(client.prepare()).rejects.toMatchObject({ code: 'controller_busy' });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(client.active).toBe(false);
    expect(client.headers()).toEqual({});
  });

  it.each([
    { controller_role: 'observer' }, { controller_key: 'bad' },
    { generation: -1 }, { generation: true }, { ttl_ms: 0 }, { heartbeat_ms: 1999 },
  ])('refuses malformed grant %j without attachment', async (delta) => {
    vi.stubGlobal('fetch', api({ ...grant, ...delta }));
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await expect(client.prepare()).rejects.toMatchObject({ code: 'controller_invalid' });
    expect(client.active).toBe(false);
    expect(client.headers()).toEqual({});
  });

  it('refuses unsupported status instead of falling back to unrestricted control', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ ...status, protocol_version: 2 })));
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    await expect(client.prepare()).rejects.toMatchObject({ code: 'controller_unsupported' });
    expect(client.active).toBe(false);
  });

  it('cannot publish a late acquire response after loss; a deliberate fresh prepare works', async () => {
    let finish!: (value: Response) => void;
    const delayed = new Promise<Response>((resolve) => { finish = resolve; });
    const fetcher = api();
    fetcher.mockImplementationOnce(async () => response(status)).mockImplementationOnce(() => delayed);
    vi.stubGlobal('fetch', fetcher);
    const { ControllerClient } = await import('../controller-client');
    const client = new ControllerClient();
    const pending = client.prepare();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    client.lose();
    finish(response(grant, 201));
    await expect(pending).rejects.toMatchObject({ code: 'controller_lost' });
    expect(client.headers()).toEqual({});
    await client.prepare();
    expect(client.protocols()).toEqual([`rigplane-controller-v1.${key}`]);
  });
});
