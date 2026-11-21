/**
 * MOR-3753 PR B — runtime-level regression for the automatic cleanup
 * callers FrontendRuntime registers: the bootstrap-registered controller
 * onLoss callbacks and the bootstrap cleanup's remote disconnect must run
 * as AUTOMATIC teardown ({ automatic: true }), so a real latched audio
 * failure survives them; an idle automatic loss latches nothing; only an
 * explicit user stop/disconnect acknowledges the latch, without repeating
 * the teardown.
 *
 * Real FrontendRuntime + SystemController + AudioManager + audio store.
 * Fake boundaries only: controller client, transport, WebSocket, mic and
 * RX player (same seams as the MOR-3753 A manager-level fixtures). Audio
 * demand is driven through the real runtime/manager entry points; the
 * onLoss callback is always the one bootstrap actually registered — never
 * a direct AudioManager call standing in for it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';

// ── Shared fake boundaries. Hoisted: vi.resetModules() re-runs mock
//    factories per test, and assertions must keep seeing the same fn
//    instances the fresh module graph consumes. ──

const mic = vi.hoisted(() => ({
  rxStart: vi.fn(),
  rxStop: vi.fn(),
  txStart: vi.fn(),
  txStop: vi.fn(),
}));

const fakes = vi.hoisted(() => ({
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
  getControlSession: vi.fn(() => ({ state: 'disconnected', epoch: 0 })),
  onControlSessionTransition: vi.fn(() => () => {}),
  emitLocalNotification: vi.fn(),
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));

const controller = vi.hoisted(() => {
  const lossHandlers: Array<() => void> = [];
  return {
    remote: false,
    active: true,
    epoch: 7,
    prepare: vi.fn(async () => {}),
    waitForPrimary: vi.fn(async () => {}),
    release: vi.fn(async () => {}),
    lose: vi.fn(),
    onLoss: vi.fn((callback: () => void) => {
      lossHandlers.push(callback);
      return () => {};
    }),
    onChange: vi.fn((_callback: () => void) => () => {}),
    current: vi.fn(() => true),
    protocols: vi.fn(() => [] as string[]),
    assertHostAction: vi.fn(),
    lossHandlers,
  };
});

vi.mock('$lib/audio/rx-player', () => ({
  RxPlayer: class {
    start = mic.rxStart;
    stop = mic.rxStop;
    flush = vi.fn();
    feed = vi.fn();
    setJitterBounds = vi.fn();
    diagnosticSnapshot = vi.fn(() => null);
    stats = vi.fn(() => ({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 }));
    setFocus = vi.fn();
    setSplitStereo = vi.fn();
    setChannelGainDb = vi.fn();
    get focus() { return 'main'; }
    get splitStereo() { return false; }
    get mainGainDb() { return 0; }
    get subGainDb() { return 0; }
    set volume(_value: number) {}
  },
}));

vi.mock('$lib/audio/tx-mic', () => ({
  TxMic: class {
    start = mic.txStart;
    stop = mic.txStop;
    applyServerCodec = vi.fn(() => ({ switched: false, error: null }));
    get active() { return true; }
    get lastCaptureFailureCategory() { return null; }
    static supported() { return true; }
  },
}));

vi.mock('$lib/transport/controller-client', async (original) => ({
  ...await original<typeof import('$lib/transport/controller-client')>(),
  controllerClient: controller,
}));

// ── Remaining transport/store boundaries; the audio stack stays REAL ──

vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(),
  fetchCapabilities: vi.fn(),
  fetchInfo: vi.fn().mockResolvedValue({}),
  fetchMemoryPage: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  connect: fakes.connect,
  sendRaw: fakes.sendRaw,
  sendCommand: fakes.sendCommand,
  disconnect: fakes.disconnect,
  disconnectAll: fakes.disconnectAll,
  reconnectAll: fakes.reconnectAll,
  isConnected: fakes.isConnected,
  onMessage: fakes.onMessage,
  addMessageHandler: fakes.addMessageHandler,
  getChannel: fakes.getChannel,
  getControlSession: fakes.getControlSession,
  onControlSessionTransition: fakes.onControlSessionTransition,
  emitLocalNotification: fakes.emitLocalNotification,
}));

vi.mock('$lib/media/media-session', () => ({
  initMediaSession: fakes.initMediaSession,
  destroyMediaSession: fakes.destroyMediaSession,
}));

vi.mock('$lib/runtime/commands/radio-intents', () => ({
  dispatchRadioIntent: vi.fn(() => ({ id: 'test-lifecycle', status: 'pending' })),
  isNormalizedLevel: (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1,
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  setCapabilities: vi.fn(),
  subscribeCapabilities: vi.fn(),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  subscribeRadioState: vi.fn(),
  resetRadioState: vi.fn(),
  getRadioState: vi.fn(() => null),
  getActiveReceiver: vi.fn(() => null),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'disconnected'),
  isConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => ''),
  getRadioPowerOn: vi.fn(() => null),
  getRadioHealth: vi.fn(() => null),
  setWsConnected: vi.fn(),
  setRadioStatus: vi.fn(),
  setReconnecting: vi.fn(),
  setRadioPowerOn: vi.fn(),
  setRigConnected: vi.fn(),
  setRadioReady: vi.fn(),
  setControlConnected: vi.fn(),
  markStateUpdated: vi.fn(),
  markScopeFrame: vi.fn(),
  // The real AudioManager (this rig) reports socket state through it.
  setAudioConnected: vi.fn(),
}));

vi.mock('../adapters/mod-input-auto.svelte', () => ({
  clearLegacyPendingModInputRestore: vi.fn(),
}));

class FakeWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  binaryType = '';
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;

  protocol: string;
  constructor(public url: string, public protocols: string[] = []) {
    this.protocol = protocols[0] ?? '';
    FakeWebSocket.instances.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
}

function serverText(socket: FakeWebSocket, message: Record<string, unknown>) {
  socket.onmessage?.({ data: JSON.stringify(message) });
}

const settle = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

/** Fresh real runtime/manager/store per test (vi.resetModules re-runs the
 *  module graph; the hoisted fake singletons stay shared). */
async function rig() {
  const { runtime, presentationResources } = await import('../frontend-runtime');
  const { audioManager } = await import('$lib/audio/audio-manager');
  const store = await import('$lib/stores/audio.svelte');
  return { runtime, audioManager, store, presentationResources };
}

describe('FrontendRuntime automatic audio-loss cleanup (MOR-3753 B)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    mic.txStart.mockResolvedValue(null);
    controller.remote = false;
    controller.active = true;
    controller.lossHandlers.length = 0;
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost' });
  });

  afterEach(async () => {
    const { audioManager } = await import('$lib/audio/audio-manager');
    audioManager.destroy();
    vi.unstubAllGlobals();
  });

  it('local loss stays neutral when idle, preserves a latched TX failure, and yields to an explicit stop and retry', async () => {
    controller.remote = false;
    const { runtime, audioManager, store } = await rig();
    const cleanup = await runtime.bootstrap();
    expect(controller.lossHandlers).toHaveLength(1);
    const onLoss = controller.lossHandlers[0]!;

    // Idle automatic loss: no browser demand, nothing failing — the
    // automatic stop latches no error and stays silent.
    onLoss();
    expect(store.getAudioState().rxError).toBe(false);
    expect(store.getAudioState().txError).toBe(false);
    expect(fakes.emitLocalNotification).not.toHaveBeenCalled();

    // A real failure: the server refuses the TX start after demand was
    // set; demand ends, the failure latches.
    expect(await runtime.startTx()).toBeNull();
    await vi.waitFor(() => expect(FakeWebSocket.instances.at(-1)).toBeDefined());
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    expect(audioManager.txEnabled).toBe(true);
    serverText(socket, { type: 'error', message: 'audio_start: TX audio unavailable' });
    expect(audioManager.txEnabled).toBe(false);
    expect(store.getAudioState().txError).toBe(true);

    // The bootstrap-registered local-loss callback is automatic cleanup:
    // the latched failure must survive it — demand alone would read idle.
    onLoss();
    expect(store.getAudioState().txError).toBe(true);

    // An explicit user stop acknowledges the latch.
    runtime.stopTx();
    expect(store.getAudioState().txError).toBe(false);

    // Retry: a new attempt supersedes the latch and opens a fresh socket.
    expect(await runtime.startTx()).toBeNull();
    await vi.waitFor(() => expect(FakeWebSocket.instances.length).toBe(2));
    const retry = FakeWebSocket.instances.at(-1)!;
    expect(retry).not.toBe(socket);
    retry.open();
    expect(audioManager.txEnabled).toBe(true);
    expect(store.getAudioState().txError).toBe(false);

    runtime.stopTx();
    await cleanup();
  });

  it('remote loss after a refused RX start preserves the latched failure; the explicit user disconnect clears it without repeating teardown', async () => {
    controller.remote = true;
    const { runtime, audioManager, store } = await rig();
    const cleanup = await runtime.bootstrap();
    const onLoss = controller.lossHandlers.at(-1)!;

    // A real failure: the server refuses the RX stream after demand was
    // set; the handler's stopRx cleared the demand, the refusal latched.
    audioManager.startRx();
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    serverText(socket, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audioManager.rxEnabled).toBe(false);
    expect(store.getAudioState().rxError).toBe(true);

    // The bootstrap-registered remote-loss callback drives the real
    // SystemController disconnect as automatic teardown.
    onLoss();
    await vi.waitFor(() => expect(fakes.disconnectAll).toHaveBeenCalledOnce());
    await settle();
    expect(store.getAudioState().rxError).toBe(true);
    expect(store.getAudioState().txError).toBe(false);

    // The explicit user disconnect acknowledges the latch state-only —
    // no second WS/MediaSession teardown pass.
    const wsPasses = fakes.disconnectAll.mock.calls.length;
    const mediaPasses = fakes.destroyMediaSession.mock.calls.length;
    await runtime.system.disconnect();
    expect(store.getAudioState().rxError).toBe(false);
    expect(store.getAudioState().txError).toBe(false);
    expect(fakes.disconnectAll).toHaveBeenCalledTimes(wsPasses);
    expect(fakes.destroyMediaSession).toHaveBeenCalledTimes(mediaPasses);

    // The bootstrap cleanup afterwards is automatic and already down:
    // it neither re-tears-down nor manufactures an error.
    await cleanup();
    expect(store.getAudioState().rxError).toBe(false);
    expect(store.getAudioState().txError).toBe(false);
    expect(fakes.disconnectAll).toHaveBeenCalledTimes(wsPasses);
    expect(fakes.destroyMediaSession).toHaveBeenCalledTimes(mediaPasses);
  });

  it('automatic remote bootstrap cleanup preserves a latched TX socket failure; the explicit user disconnect afterwards acknowledges it', async () => {
    controller.remote = true;
    const { runtime, audioManager, store } = await rig();
    const cleanup = await runtime.bootstrap();

    // A real failure: the TX socket dies while the remote admission is
    // still pending — the failure latches and the demand ends.
    const admission = runtime.startTx();
    await vi.waitFor(() => expect(FakeWebSocket.instances.at(-1)).toBeDefined());
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    expect(audioManager.txEnabled).toBe(true);
    socket.onerror?.({});
    socket.onclose?.({ code: 1006, reason: '' });
    await expect(admission).resolves.toBe('Удалённое TX-аудио потеряно.');
    expect(audioManager.txEnabled).toBe(false);
    expect(store.getAudioState().txError).toBe(true);

    // The bootstrap cleanup's remote disconnect is automatic: it must not
    // acknowledge the latched failure.
    await cleanup();
    expect(store.getAudioState().txError).toBe(true);
    expect(fakes.disconnectAll).toHaveBeenCalledOnce();
    expect(fakes.destroyMediaSession).toHaveBeenCalledOnce();

    // The explicit user disconnect is still the acknowledging path — and
    // adds no second teardown pass on top of the automatic one.
    await runtime.system.disconnect();
    expect(store.getAudioState().txError).toBe(false);
    expect(store.getAudioState().rxError).toBe(false);
    expect(fakes.disconnectAll).toHaveBeenCalledOnce();
    expect(fakes.destroyMediaSession).toHaveBeenCalledOnce();
  });

  it('a failed re-bootstrap rolls back automatically: the original rejection is preserved, the latched failure survives, and the explicit user disconnect still acknowledges it', async () => {
    controller.remote = true;
    const { runtime, audioManager, store } = await rig();
    const cleanup = await runtime.bootstrap();

    // A real failure first: the server refuses the RX start; the latch
    // survives the automatic cleanup of the mounted session.
    audioManager.startRx();
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    serverText(socket, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(store.getAudioState().rxError).toBe(true);
    await cleanup();
    expect(store.getAudioState().rxError).toBe(true);
    expect(fakes.disconnectAll).toHaveBeenCalledOnce();

    // The remount bootstrap fails deterministically at controller
    // admission; its own rejection must surface unchanged.
    controller.prepare.mockRejectedValueOnce(new Error('controller busy'));
    await expect(runtime.bootstrap()).rejects.toThrow('controller busy');

    // The catch-path rollback is automatic: the latched failure survives
    // it (an explicit rollback would have acknowledged it state-only)
    // and it adds no second teardown pass.
    expect(store.getAudioState().rxError).toBe(true);
    expect(fakes.disconnectAll).toHaveBeenCalledOnce();

    // The explicit user disconnect remains the acknowledging path.
    await runtime.system.disconnect();
    expect(store.getAudioState().rxError).toBe(false);
    expect(fakes.disconnectAll).toHaveBeenCalledOnce();
  });

  async function liveRxRig() {
    const { subscribeCapabilities } = await import('$lib/stores/capabilities.svelte');
    let publish!: Parameters<typeof subscribeCapabilities>[0];
    vi.mocked(subscribeCapabilities).mockImplementation((listener) => {
      publish = listener;
      listener({
        model: 'test-rx', audio: true, scope: false, tx: false,
        capabilities: ['audio'], receivers: 1,
        freqRanges: [], modes: [], filters: [], txBands: null,
        audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
        webrtc: { available: false, enabled: false },
        vfoScheme: 'ab', audioFftAvailable: false, scopeSource: null,
        stateContractVersion: 1, providerGeneration: 0,
      } satisfies Capabilities);
      return vi.fn();
    });
    const r = await rig();
    const cleanup = await r.runtime.bootstrap();
    r.runtime.setRxLive(true);
    await vi.waitFor(() => expect(r.presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 1, activeHandle: r.audioManager,
    }));
    expect(r.audioManager.rxEnabled).toBe(true);
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    serverText(socket, {
      type: 'error', message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(r.audioManager.rxEnabled).toBe(false);
    expect(r.store.getAudioState().rxError).toBe(true);
    expect(r.presentationResources.snapshot('rx-audio').activeHandle).toBe(r.audioManager);
    expect(mic.rxStop).toHaveBeenCalledOnce();
    expect(mic.rxStart).toHaveBeenCalledTimes(2);
    return { ...r, cleanup, withdrawCapabilities: () => publish(null) };
  }

  it.each([false, true])('RX resource cleanup preserves a refusal and explicit stop acknowledges it (remote=%s)', async (remote) => {
    controller.remote = remote;
    const r = await liveRxRig();
    await r.cleanup();
    expect(r.store.getAudioState().rxError).toBe(true);
    expect(r.audioManager.rxEnabled).toBe(false);
    expect(r.presentationResources.snapshot('rx-audio').activeHandle).toBeUndefined();
    await r.cleanup();
    expect(r.store.getAudioState().rxError).toBe(true);
    expect(mic.rxStop).toHaveBeenCalledOnce();
    r.runtime.setRxLive(false);
    expect(r.store.getAudioState().rxError).toBe(false);
    expect(mic.rxStop).toHaveBeenCalledOnce();
    expect(mic.rxStart).toHaveBeenCalledTimes(2);
  });

  it('RX resource capability loss preserves refusal through repeated loss and explicit stop', async () => {
    const r = await liveRxRig();
    try {
      r.withdrawCapabilities();
      await vi.waitFor(() => expect(r.presentationResources.snapshot('rx-audio').activeHandle).toBeUndefined());
      expect(r.store.getAudioState().rxError).toBe(true);
      r.withdrawCapabilities();
      await settle();
      expect(r.store.getAudioState().rxError).toBe(true);
      expect(mic.rxStop).toHaveBeenCalledOnce();
      r.runtime.setRxLive(false);
      expect(r.store.getAudioState().rxError).toBe(false);
      expect(mic.rxStop).toHaveBeenCalledOnce();
      expect(mic.rxStart).toHaveBeenCalledTimes(2);
    } finally { await r.cleanup(); }
  });

  it('explicit RX resource release acknowledges refusal without repeating player stop', async () => {
    const r = await liveRxRig();
    try {
      r.runtime.setRxLive(false);
      await vi.waitFor(() => expect(r.presentationResources.snapshot('rx-audio').activeHandle).toBeUndefined());
      expect(r.store.getAudioState().rxError).toBe(false);
      r.runtime.setRxLive(false);
      await settle();
      expect(mic.rxStop).toHaveBeenCalledOnce();
      expect(mic.rxStart).toHaveBeenCalledTimes(2);
    } finally { await r.cleanup(); }
  });

});
