/**
 * Unit tests for FrontendRuntime.bootstrap().
 *
 * Uses vi.mock to stub transport + store modules.  This file lives in the
 * `isolated` vitest project (see vite.config.ts) so its mocks don't leak
 * into the shared module cache used by the `fast` project.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock transport and store modules before importing the runtime ──

vi.mock('$lib/transport/http-client', () => ({
  fetchCapabilities: vi.fn(),
  startPolling: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  connect: vi.fn(),
  sendRaw: vi.fn(),
  sendCommand: vi.fn(),
  disconnect: vi.fn(),
  disconnectAll: vi.fn(),
  reconnectAll: vi.fn(),
  isConnected: vi.fn(() => false),
  onMessage: vi.fn(() => () => {}),
  addMessageHandler: vi.fn(() => () => {}),
  getChannel: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  setCapabilities: vi.fn(),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getRadioState: vi.fn(() => null),
  setRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  resetRadioState: vi.fn(),
  // MOR-618: imported by adapters/mod-input-auto.svelte (bootstrap step 6).
  subscribeRadioState: vi.fn(() => () => {}),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'disconnected'),
  isConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => ''),
  getRadioPowerOn: vi.fn(() => null),
  setHttpConnected: vi.fn(),
  setWsConnected: vi.fn(),
  setRadioStatus: vi.fn(),
  setReconnecting: vi.fn(),
  setRadioPowerOn: vi.fn(),
  setRigConnected: vi.fn(),
  setRadioReady: vi.fn(),
  setControlConnected: vi.fn(),
  markStateUpdated: vi.fn(),
}));

vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({})),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  toggleMute: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    startRx: vi.fn(),
    stopRx: vi.fn(),
    startTx: vi.fn(),
    stopTx: vi.fn(),
    setRxVolume: vi.fn(),
    destroy: vi.fn(),
  },
}));

vi.mock('$lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));

// system-controller uses imports from above mocks — provide a lightweight stub
vi.mock('./system-controller', async () => {
  const { systemController: _sc } = await vi.importActual<typeof import('../system-controller')>(
    '../system-controller',
  );
  return { systemController: _sc };
});

// ── Import modules under test after mocks are hoisted ──

import { fetchCapabilities, startPolling } from '$lib/transport/http-client';
import { connect, sendRaw } from '$lib/transport/ws-client';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioState } from '$lib/stores/radio.svelte';
import { systemController } from '../system-controller';

// FrontendRuntime is a singleton — re-import fresh each time via a factory helper
// so we can reset _bootstrapCleanup and _bootstrapInFlight between tests.
async function freshRuntime() {
  // Dynamic import after vi.mock registrations ensures mocks are active.
  const mod = await import('../frontend-runtime');
  // Reset private state between tests by casting to access both sentinels
  const rt = mod.runtime as unknown as { _bootstrapCleanup: null; _bootstrapInFlight: null };
  rt._bootstrapCleanup = null;
  rt._bootstrapInFlight = null;
  return mod.runtime;
}

// ── Fixtures ──

const fakeCaps = { modes: ['USB', 'LSB'], scope: false } as any;
const fakeStopPolling = vi.fn();

// ── Tests ──

describe('FrontendRuntime.bootstrap()', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    (startPolling as ReturnType<typeof vi.fn>).mockReturnValue(fakeStopPolling);
  });

  it('runs the full bootstrap sequence on the first call', async () => {
    const rt = await freshRuntime();

    const cleanup = await rt.bootstrap();

    // 1. fetchCapabilities called
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);

    // 2. capabilities pushed into store
    expect(setCapabilities).toHaveBeenCalledWith(fakeCaps);

    // 3. polling started once (initial startPolling call; registerPolling registers
    //    a factory that calls startPolling only when systemController.connect() fires)
    expect(startPolling).toHaveBeenCalledTimes(1);
    expect(startPolling).toHaveBeenCalledWith(expect.any(Function), 1000);

    // 4. WebSocket connected
    expect(connect).toHaveBeenCalledWith('/api/v1/ws');

    // 5. subscribe message sent
    expect(sendRaw).toHaveBeenCalledWith({ type: 'subscribe', streams: ['events'] });

    // returns a callable cleanup
    expect(typeof cleanup).toBe('function');
  });

  it('is idempotent — second call is a no-op and returns the same cleanup', async () => {
    const rt = await freshRuntime();

    const cleanup1 = await rt.bootstrap();
    const cleanup2 = await rt.bootstrap();

    // Transport functions only invoked once total
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both calls return the same handle
    expect(cleanup1).toBe(cleanup2);
  });

  it('cleanup function stops polling', async () => {
    const rt = await freshRuntime();
    const cleanup = await rt.bootstrap();

    cleanup();

    expect(fakeStopPolling).toHaveBeenCalledTimes(1);
  });

  it('propagates fetchCapabilities error and allows retry', async () => {
    const rt = await freshRuntime();
    const error = new Error('network failure');
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockRejectedValueOnce(error);

    await expect(rt.bootstrap()).rejects.toThrow('network failure');

    // connect and sendRaw must NOT have been called
    expect(connect).not.toHaveBeenCalled();
    expect(sendRaw).not.toHaveBeenCalled();

    // Runtime is not latched — retry should work
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    const cleanup = await rt.bootstrap();
    expect(typeof cleanup).toBe('function');
    expect(fetchCapabilities).toHaveBeenCalledTimes(2);
  });

  it('startPolling callback calls setRadioState with the received state', async () => {
    const rt = await freshRuntime();
    await rt.bootstrap();

    // Capture the callback passed to the (single) startPolling call
    const calls = (startPolling as ReturnType<typeof vi.fn>).mock.calls;
    const pollCallback = calls[0][0] as (s: unknown) => void;

    const fakeState = { revision: 1 } as any;
    pollCallback(fakeState);

    expect(setRadioState).toHaveBeenCalledWith(fakeState);
  });

  it('registers polling factory with systemController', async () => {
    const registerSpy = vi.spyOn(systemController, 'registerPolling');
    const rt = await freshRuntime();
    await rt.bootstrap();

    expect(registerSpy).toHaveBeenCalledTimes(1);
    expect(registerSpy).toHaveBeenCalledWith(expect.any(Function));
  });

  it('serializes concurrent callers — both share single in-flight bootstrap', async () => {
    const rt = await freshRuntime();

    // Invoke bootstrap concurrently (not sequentially)
    const [cleanup1, cleanup2] = await Promise.all([rt.bootstrap(), rt.bootstrap()]);

    // Each transport function called exactly once, not twice
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(startPolling).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both callers get the same cleanup function
    expect(cleanup1).toBe(cleanup2);
    expect(cleanup1).toBe(fakeStopPolling);
  });
});
