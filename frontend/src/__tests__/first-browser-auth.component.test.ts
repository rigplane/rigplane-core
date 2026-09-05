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

const credential = 'synthetic-first-entry';
const caps = { ...capsFixture, stateContractVersion: 1, providerGeneration: 0 };
const requests: string[] = [];
let app: ReturnType<typeof mount> | undefined;
let protectedServer = true;
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
  protectedServer = true;
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('prompt', promptMock);
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'error').mockImplementation(() => {});
  promptMock.mockImplementation(() => {
    expect(instances).toHaveLength(0);
    expect(localStorage.getItem('rigplane-auth-token')).toBeNull();
    return credential;
  });
  fetchMock.mockImplementation(async (url: string, init: RequestInit) => {
    requests.push(url);
    expect(url.startsWith('/api/v1/')).toBe(true);
    expect(url).not.toContain(credential);
    expect(init.redirect).toBe('error');
    const authorized = new Headers(init.headers).get('Authorization') === `Bearer ${credential}`;
    if (protectedServer && !authorized) return response(401);
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

describe('first browser authentication through App and the real runtime', () => {
  it('prompts from empty storage before control WS, then accepts state and capabilities', async () => {
    startApp();
    await settle();
    expect(promptMock).toHaveBeenCalledExactlyOnceWith('Enter auth token:');
    expect(requests).toEqual(['/api/v1/info', '/api/v1/info']);
    expect(localStorage.getItem('rigplane-auth-token')).toBe(credential);
    expect(instances).toHaveLength(1);
    const socket = instances[0];
    expect(new URL(socket.url).searchParams.get('token')).toBe(credential);
    socket.simulateOpen();
    const state = { ...stateFixture, stateContractVersion: 1, providerGeneration: 0 };
    socket.simulateMessage(JSON.stringify({ type: 'state_update', data: { type: 'full', data: state, stateContractVersion: 1, providerGeneration: 0 } }));
    await settle();
    expect(requests.at(-1)).toBe('/api/v1/capabilities');
    expect(runtime.caps?.model).toBe(caps.model);
    expect(runtime.state?.main.freqHz).toBe(state.main.freqHz);
    expect(runtime.connectionWs).toBe(true);
    expect(document.querySelector('[role="alert"]')).toBeNull();
  });

  it.each(['valid stored token', 'no-auth server'])('preserves %s startup', async (mode) => {
    if (mode === 'valid stored token') localStorage.setItem('rigplane-auth-token', credential);
    else protectedServer = false;
    startApp();
    await settle();
    expect(promptMock).not.toHaveBeenCalled();
    expect(requests).toEqual(['/api/v1/info']);
    expect(instances).toHaveLength(1);
  });

  it.each([null, '', 'synthetic-wrong'])('stops cancelled or rejected input %s without automatic reload', async (answer) => {
    vi.useFakeTimers();
    promptMock.mockReturnValue(answer);
    startApp();
    await settle();
    expect(promptMock).toHaveBeenCalledTimes(1);
    expect(instances).toHaveLength(0);
    expect(localStorage.getItem('rigplane-auth-token')).toBeNull();
    expect(document.querySelector('[role="alert"]')?.textContent).toMatch(/reload/i);
    expect(document.querySelector('.retry-indicator')).toBeNull();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(promptMock).toHaveBeenCalledTimes(1);
    expect(requests).toHaveLength(answer ? 2 : 1);
    vi.useRealTimers();
  });

  it.each(['network', '503'])('does not turn %s failure into an auth prompt', async (failure) => {
    if (failure === 'network') fetchMock.mockRejectedValue(new TypeError('Network unavailable'));
    else fetchMock.mockResolvedValue(response(503));
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

  it('does not request credentials when bootstrap was already cancelled', async () => {
    const abort = new AbortController();
    abort.abort();
    await expect(runtime.bootstrap(abort.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(promptMock).not.toHaveBeenCalled();
    expect(instances).toHaveLength(0);
  });
});
