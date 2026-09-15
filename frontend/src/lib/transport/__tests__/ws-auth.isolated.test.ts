import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MockWebSocket, instances } from './support/fake-ws-backend';

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal('location', { protocol: 'https:', host: 'radio.example.test' });
  instances.length = 0;
  localStorage.clear();
  vi.spyOn(console, 'info').mockImplementation(() => {});
});

afterEach(async () => {
  const { disconnectAll } = await import('../ws-client');
  disconnectAll();
  localStorage.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function expectNoToken(path: string): void {
  const url = new URL(instances.at(-1)!.url, 'https://radio.example.test');
  expect(url.pathname).toBe(path);
  expect(url.searchParams.getAll('token')).toEqual([]);
}

async function rotateAndReconnect(token: string | null): Promise<void> {
  if (token) localStorage.setItem('rigplane-auth-token', token);
  else localStorage.removeItem('rigplane-auth-token');
  instances.at(-1)!.simulateClose();
  await vi.advanceTimersByTimeAsync(1250);
}

describe('credential-free production WebSockets', () => {
  it('ignores retired storage on automatic and explicit reconnect, preserving query parameters', async () => {
    const { connect, disconnectAll, reconnectAll, getLastCloseInfo } = await import('../ws-client');
    const first = 'synthetic +&?=#/ token';
    localStorage.setItem('rigplane-auth-token', first);
    connect('/api/v1/ws?receiver=1&token=synthetic-old&token=synthetic-older');
    expectNoToken('/api/v1/ws');
    instances[0].simulateOpen();
    await rotateAndReconnect('synthetic-rotated');
    expectNoToken('/api/v1/ws');
    expect(new URL(instances.at(-1)!.url, 'https://radio.example.test').searchParams.get('receiver')).toBe('1');
    localStorage.removeItem('rigplane-auth-token');
    instances.at(-1)!.simulateOpen();
    disconnectAll();
    reconnectAll();
    expectNoToken('/api/v1/ws');
    expect(JSON.stringify(getLastCloseInfo())).not.toContain('synthetic');
    expect(JSON.stringify(vi.mocked(console.info).mock.calls)).not.toContain('synthetic');
  });

  it('keeps anonymous control initial and reconnect token-free', async () => {
    const { connect } = await import('../ws-client');
    connect();
    expectNoToken('/api/v1/ws');
    await rotateAndReconnect(null);
    expectNoToken('/api/v1/ws');
  });

  it.each([
    ['audioFftDriver', '/api/v1/audio-scope'],
    ['hardwareScopeDriver', '/api/v1/scope'],
  ] as const)('keeps actual ScopeController %s token-free on initial and changed/absent-token reconnect', async (driverName, path) => {
    const { ScopeController } = await import('../../runtime/scope-controller.svelte');
    const controller = new ScopeController();
    const driver = controller[driverName];
    localStorage.setItem('rigplane-auth-token', 'synthetic +&?=#/ token');
    const handle = await driver.start();
    expectNoToken(path);
    instances.at(-1)!.simulateOpen();
    await rotateAndReconnect('synthetic-rotated');
    expectNoToken(path);
    instances.at(-1)!.simulateOpen();
    await rotateAndReconnect(null);
    expectNoToken(path);
    await driver.stop(handle);
  });

  it.each(['audioFftDriver', 'hardwareScopeDriver'] as const)('keeps anonymous %s initial and reconnect token-free', async (driverName) => {
    const { ScopeController } = await import('../../runtime/scope-controller.svelte');
    const controller = new ScopeController();
    const driver = controller[driverName];
    const handle = await driver.start();
    const firstUrl = instances.at(-1)!.url;
    await rotateAndReconnect(null);
    expect(instances.at(-1)!.url).toBe(firstUrl);
    expect(new URL(firstUrl, 'https://radio.example.test').searchParams.has('token')).toBe(false);
    await driver.stop(handle);
  });

  it.each(['wss://other.example.test/api/v1/scope', 'wss://radio.example.test:9443/api/v1/scope', 'ws://radio.example.test/api/v1/scope'])('does not attach application credentials to unrelated origin %s', async (url) => {
    const { WsChannel } = await import('../ws-client');
    localStorage.setItem('rigplane-auth-token', 'synthetic-private');
    const channel = new WsChannel();
    channel.connect(url);
    expect(instances.at(-1)!.url).toBe(url);
    channel.disconnect();
  });
});
