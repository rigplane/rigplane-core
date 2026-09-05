import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import stateFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import capsFixture from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';

vi.mock('../skins/registry', async (importOriginal) => ({
  ...await importOriginal<typeof import('../skins/registry')>(),
  loadSkin: async () => (await import('./LayoutStub.svelte')).default,
  presentationResourcePlan: () => [],
}));
vi.mock('../AppGlobalHost.svelte', async () => ({ default: (await import('./LayoutStub.svelte')).default }));
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => ({ default: (await import('./LayoutStub.svelte')).default }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  provideManagedAppTxHost: () => ({ refreshAuthority() {}, dispose() {}, release() {} }),
}));
vi.mock('../lib/media/media-session', () => ({ initMediaSession() {}, destroyMediaSession() {} }));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { onChange: () => () => {}, onTxAudioDied: () => () => {}, rxEnabled: false },
}));

import App from '../App.svelte';
import { runtime } from '$lib/runtime/frontend-runtime';
import { disconnectAll } from '$lib/transport/ws-client';
import { fetchInfo, getAuthHeaders, getAuthToken } from '$lib/transport/http-client';

const credential = 'synthetic-first-entry';
const caps = { ...capsFixture, stateContractVersion: 1, providerGeneration: 0 };
const requests: string[] = [];
let app: ReturnType<typeof mount> | undefined;
const promptMock = vi.fn();
const fetchMock = vi.fn();

async function settle() {
  for (let i = 0; i < 25; i++) { await Promise.resolve(); flushSync(); }
}
function startApp() {
  app = mount(App, { target: document.body });
  flushSync();
}
function response(status: number, body: unknown = {}) {
  return { status, ok: status === 200, json: async () => body };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.body.innerHTML = '';
  instances.length = 0;
  requests.length = 0;
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('prompt', promptMock);
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
    requests.push(url);
    expect(url.startsWith('/api/v1/')).toBe(true);
    expect(url).not.toContain(credential);
    expect(init.redirect).toBe('error');
    expect(new Headers(init.headers).has('Authorization')).toBe(false);
    return response(200, url.endsWith('/capabilities') ? caps : { version: 'test' });
  });
});
afterEach(async () => {
  if (app) { await unmount(app); app = undefined; }
  disconnectAll();
  await settle();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('credential-free App and real runtime startup', () => {
  it.each([null, credential])('connects and accepts state/capabilities with retired stored token %s', async (stored) => {
    if (stored) localStorage.setItem('rigplane-auth-token', stored);
    const reads = vi.spyOn(localStorage, 'getItem');
    const writes = vi.spyOn(localStorage, 'setItem');
    startApp();
    await settle();
    expect(promptMock).not.toHaveBeenCalled();
    expect(requests).toEqual(['/api/v1/info']);
    expect(instances).toHaveLength(1);
    const socket = instances[0];
    expect(new URL(socket.url, 'http://localhost').searchParams.has('token')).toBe(false);
    socket.simulateOpen();
    const state = { ...stateFixture, stateContractVersion: 1, providerGeneration: 0 };
    socket.simulateMessage(JSON.stringify({ type: 'state_update', data: { type: 'full', data: state, stateContractVersion: 1, providerGeneration: 0 } }));
    await settle();
    expect(requests.at(-1)).toBe('/api/v1/capabilities');
    expect(runtime.caps?.model).toBe(caps.model);
    expect(runtime.state?.main.freqHz).toBe(state.main.freqHz);
    expect(runtime.connectionWs).toBe(true);
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(reads.mock.calls.filter(([key]) => key.endsWith('auth-token'))).toEqual([]);
    expect(writes.mock.calls.filter(([key]) => key.endsWith('auth-token'))).toEqual([]);
  });

  it.each(['network', '503', '401'])('does not turn %s failure into an auth prompt', async (failure) => {
    if (failure === 'network') fetchMock.mockRejectedValue(new TypeError('Network unavailable'));
    else fetchMock.mockResolvedValue(response(Number(failure)));
    startApp();
    await settle();
    expect(promptMock).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
    expect(document.querySelector('.retry-indicator')).not.toBeNull();
  });

  it.each([200, 401])('does not prompt or open WS if unmounted before preflight %s', async (status) => {
    let finish!: (value: ReturnType<typeof response>) => void;
    fetchMock.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    startApp();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    await unmount(app!); app = undefined;
    expect(signal.aborted).toBe(true);
    finish(response(status));
    await settle();
    expect(promptMock).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
  });

  it('does not fetch or open WS when bootstrap was already cancelled', async () => {
    const abort = new AbortController();
    abort.abort();
    await expect(runtime.bootstrap(abort.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(promptMock).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
  });

  it('keeps deprecated helpers empty without accessing browser storage', () => {
    localStorage.setItem('rigplane-auth-token', credential);
    const reads = vi.spyOn(localStorage, 'getItem');
    const writes = vi.spyOn(localStorage, 'setItem');
    expect(getAuthToken()).toBeNull();
    expect(getAuthHeaders()).toEqual({});
    expect(reads).not.toHaveBeenCalled();
    expect(writes).not.toHaveBeenCalled();
  });

  it.each([401, 503])('reports HTTP %s once without prompting, retrying or persisting', async (status) => {
    const reads = vi.spyOn(localStorage, 'getItem');
    const writes = vi.spyOn(localStorage, 'setItem');
    fetchMock.mockResolvedValue(response(status));
    await expect(fetchInfo()).rejects.toThrow(`fetchInfo: ${status}`);
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith('/api/v1/info', {
      headers: {}, signal: undefined, redirect: 'error',
    });
    expect(promptMock).not.toHaveBeenCalled();
    expect(reads).not.toHaveBeenCalled();
    expect(writes).not.toHaveBeenCalled();
  });
});
