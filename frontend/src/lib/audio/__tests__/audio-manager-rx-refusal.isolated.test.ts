import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const rxStart = vi.fn();
const rxStop = vi.fn();

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    start = rxStart;
    stop = rxStop;
    flush = vi.fn();
    setJitterBounds = vi.fn();
    diagnosticSnapshot = vi.fn(() => null);
    stats = vi.fn(() => ({
      underruns: 0,
      bufferDepthMs: 0,
      droppedFrames: 0,
    }));
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

const txStart = vi.fn().mockResolvedValue(null);
const txStop = vi.fn();

vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = txStart;
    stop = txStop;
    applyServerCodec = vi.fn(() => ({ switched: false, error: null }));
    get active() { return true; }
    get lastCaptureFailureCategory() { return null; }
    static supported() { return true; }
    constructor(
      _send: (data: ArrayBuffer) => void,
      _onCaptureDied?: (reason: string) => void,
    ) {}
  },
}));

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
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

  constructor(public url: string) {
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

describe('[new contract] AudioManager failure persistence and recovery (MOR-3753)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost' });
    vi.useFakeTimers();
  });

  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('server RX refusal clears demand but latches rxError until an explicit stop', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    expect(audioManager.rxEnabled).toBe(true);
    expect(audio.getAudioState().rxError).toBe(false);

    const socket = FakeWebSocket.instances[0];
    socket.open();

    // The server refuses the stream: demand ends (stopRx runs inside the
    // handler) but the failure must stay latched — demand alone would now
    // read "idle, all fine".
    serverText(socket, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audioManager.rxEnabled).toBe(false);
    expect(audio.getAudioState().rxEnabled).toBe(false);
    expect(audio.getAudioState().rxError).toBe(true);

    // The no-op stop after a refusal is still an explicit stop: neutral.
    audioManager.stopRx();
    expect(audio.getAudioState().rxError).toBe(false);
  });

  it('a retried RX stream recovers cleanly; a second refusal re-latches', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    const first = FakeWebSocket.instances[0];
    first.open();
    serverText(first, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audio.getAudioState().rxError).toBe(true);

    // A new attempt supersedes the latch and re-opens the socket.
    audioManager.startRx();
    expect(audio.getAudioState().rxError).toBe(false);
    expect(audioManager.rxEnabled).toBe(true);
    const second = FakeWebSocket.instances[1];
    expect(second).toBeDefined();
    second.open();
    expect(
      second.sent.some((raw) => {
        const msg = JSON.parse(raw as string);
        return msg.type === 'audio_start' && msg.direction === 'rx';
      }),
    ).toBe(true);

    // Accepted (no refusal): demand live, no error — successful recovery.
    expect(audioManager.rxEnabled).toBe(true);
    expect(audio.getAudioState().rxError).toBe(false);

    // A fresh refusal on the retry latches again.
    serverText(second, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audioManager.rxEnabled).toBe(false);
    expect(audio.getAudioState().rxError).toBe(true);
  });

  it('RX refusal keeps a concurrent TX stream alive', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    expect(await audioManager.startTx()).toBeNull();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    expect(audioManager.txEnabled).toBe(true);

    serverText(socket, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audioManager.rxEnabled).toBe(false);
    expect(audio.getAudioState().rxError).toBe(true);
    expect(audioManager.txEnabled).toBe(true);
  });

  it('server TX refusal clears demand but latches txError until an explicit stop', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    expect(await audioManager.startTx()).toBeNull();
    expect(audio.getAudioState().txError).toBe(false);
    const socket = FakeWebSocket.instances[0];
    socket.open();
    expect(audioManager.txEnabled).toBe(true);

    serverText(socket, { type: 'error', message: 'audio_start: TX audio unavailable' });
    expect(audioManager.txEnabled).toBe(false);
    expect(audio.getAudioState().txEnabled).toBe(false);
    expect(audio.getAudioState().txError).toBe(true);

    audioManager.stopTx();
    expect(audio.getAudioState().txError).toBe(false);
  });

  it('a genuine mic start failure latches txError without TX admission; an explicit stop acknowledges', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    txStart.mockResolvedValueOnce('TX MIC: permission denied');

    await expect(audioManager.startTx()).resolves.toBe('TX MIC: permission denied');

    expect(audioManager.txEnabled).toBe(false);
    expect(audio.getAudioState().txError).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(0);

    audioManager.stopTx();
    expect(audio.getAudioState().txError).toBe(false);
  });

  it('a retried capture clears the latched failure at entry, while the new capture is still pending', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    txStart.mockResolvedValueOnce('TX MIC: permission denied');
    await expect(audioManager.startTx()).resolves.toBe('TX MIC: permission denied');
    expect(audio.getAudioState().txError).toBe(true);

    let settleCapture!: (reason: string | null) => void;
    txStart.mockImplementationOnce(() => new Promise((resolve) => {
      settleCapture = resolve;
    }));
    const retry = audioManager.startTx();
    expect(audio.getAudioState().txError).toBe(false);
    expect(audioManager.txEnabled).toBe(false);

    settleCapture('TX MIC: capture failed (NotFoundError)');
    await expect(retry).resolves.toBe('TX MIC: capture failed (NotFoundError)');
    expect(audio.getAudioState().txError).toBe(true);
    expect(audioManager.txEnabled).toBe(false);
    audioManager.stopTx();
  });

  it('operator cancellation of the capture start latches no failure', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    txStart.mockResolvedValueOnce('TX MIC: capture start cancelled');

    await expect(audioManager.startTx()).resolves.toBe('TX MIC: capture start cancelled');

    expect(audioManager.txEnabled).toBe(false);
    expect(audio.getAudioState().txError).toBe(false);
  });

  it('a thrown capture-setup failure latches the failed state and still propagates', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    const failure = new Error('AudioContext unavailable');
    txStart.mockRejectedValueOnce(failure);

    await expect(audioManager.startTx()).rejects.toBe(failure);

    expect(audioManager.txEnabled).toBe(false);
    expect(audio.getAudioState().txError).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(0);

    audioManager.stopTx();
    expect(audio.getAudioState().txError).toBe(false);
  });

  it('a socket that dies under RX demand latches rxError until the reconnect recovers', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    const first = FakeWebSocket.instances[0];
    first.open();
    expect(audio.getAudioState().rxError).toBe(false);

    first.onclose?.({ code: 1006, reason: '' });
    expect(audioManager.rxEnabled).toBe(true);
    expect(audio.getAudioState().rxError).toBe(true);
    expect(audio.getAudioState().txError).toBe(false);

    vi.advanceTimersByTime(600);
    const second = FakeWebSocket.instances[1];
    second.open();
    expect(audio.getAudioState().rxError).toBe(false);
    expect(audioManager.rxEnabled).toBe(true);

    audioManager.stopRx();
    expect(audio.getAudioState().rxError).toBe(false);
  });

  it('a socket error under RX-only demand latches only rxError; an explicit stop clears it', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();

    socket.onerror?.({});
    expect(audio.getAudioState().rxError).toBe(true);
    expect(audio.getAudioState().txError).toBe(false);

    audioManager.stopRx();
    expect(audio.getAudioState().rxError).toBe(false);
  });

  it('a socket that dies under TX-only demand latches only txError and keeps TX for the resume', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    expect(await audioManager.startTx()).toBeNull();
    const socket = FakeWebSocket.instances[0];
    socket.open();

    socket.onclose?.({ code: 1006, reason: 'rearm' });
    expect(audioManager.txEnabled).toBe(true);
    expect(audio.getAudioState().txError).toBe(true);
    expect(audio.getAudioState().rxError).toBe(false);

    audioManager.stopTx();
    expect(audio.getAudioState().txError).toBe(false);
  });

  it('a server close with no browser demand latches nothing and reconnects nothing', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.setAudioConfig({ focus: 'main', split_stereo: true });
    const socket = FakeWebSocket.instances[0];
    socket.open();

    socket.onclose?.({ code: 1006, reason: 'rearm' });
    expect(audio.getAudioState().rxError).toBe(false);
    expect(audio.getAudioState().txError).toBe(false);
    vi.advanceTimersByTime(5000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it('automatic stopRx/destroy preserve the latched rxError and manufacture none; an explicit destroy acknowledges', async () => {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');

    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    serverText(socket, {
      type: 'error',
      message: 'audio_start: RX audio failed to start: device busy',
    });
    expect(audio.getAudioState().rxError).toBe(true);
    expect(rxStop).toHaveBeenCalledTimes(1);

    // Automatic cleanup preserves the latch; idle audio manufactures none
    // and the media teardown does not re-run.
    audioManager.stopRx({ automatic: true });
    expect(audio.getAudioState().rxError).toBe(true);
    audioManager.destroy({ automatic: true });
    expect(audio.getAudioState().rxError).toBe(true);
    expect(audio.getAudioState().txError).toBe(false);
    expect(rxStop).toHaveBeenCalledTimes(1);

    // An explicit destroy acknowledges — still without media work.
    audioManager.destroy();
    expect(audio.getAudioState().rxError).toBe(false);
    expect(rxStop).toHaveBeenCalledTimes(1);
  });
});
