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

describe('real capture results after manager stop (MOR-3753)', () => {
  let savedMic: typeof import('../tx-mic');
  let secureContext: PropertyDescriptor | undefined;
  let track: EventTarget & { stop: ReturnType<typeof vi.fn>; readyState: string };
  let capture: ReturnType<typeof vi.fn<() => Promise<MediaStream>>>;
  const setSecure = (value: boolean) => Object.defineProperty(window, 'isSecureContext', {
    configurable: true, value,
  });
  const enablePcm = (sampleRate = 48000) => vi.stubGlobal('AudioContext', class {
    sampleRate = sampleRate;
    destination = {};
    close = vi.fn(async () => {});
    createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() });
    createScriptProcessor = () => ({ connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null });
  });
  const stream = () => ({ getTracks: () => [track], getAudioTracks: () => [track] }) as unknown as MediaStream;
  const refuse = (reason: 'insecure' | 'unsupported') => { if (reason === 'insecure') setSecure(false); };
  async function rig() {
    const { audioManager } = await import('../audio-manager');
    const { getAudioState } = await import('../../stores/audio.svelte');
    return { audioManager, getAudioState };
  }

  beforeEach(async () => {
    savedMic = await import('../tx-mic');
    vi.doMock('../tx-mic', () => vi.importActual<typeof import('../tx-mic')>('../tx-mic'));
    vi.resetModules();
    secureContext = Object.getOwnPropertyDescriptor(window, 'isSecureContext');
    setSecure(true);
    track = Object.assign(new EventTarget(), { stop: vi.fn(), readyState: 'live' });
    capture = vi.fn<() => Promise<MediaStream>>().mockImplementation(async () => stream());
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: capture } });
    for (const key of ['AudioEncoder', 'MediaStreamTrackProcessor', 'AudioContext', 'webkitAudioContext']) {
      vi.stubGlobal(key, undefined);
    }
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
  });
  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.unstubAllGlobals();
    if (secureContext) Object.defineProperty(window, 'isSecureContext', secureContext);
    else Reflect.deleteProperty(window, 'isSecureContext');
    vi.doMock('../tx-mic', () => savedMic);
    vi.resetModules();
  });

  it.each(['insecure', 'unsupported'] as const)('explicit stop supersedes a fulfilled %s refusal', async (reason) => {
    const { audioManager, getAudioState } = await rig();
    refuse(reason);
    const pending = audioManager.startTx();
    audioManager.stopTx();
    await pending;
    expect(getAudioState().txError).toBe(false);
    expect(audioManager.txEnabled).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it.each(['insecure', 'unsupported'] as const)('a stopped %s result cannot contaminate the pending retry', async (reason) => {
    const { audioManager, getAudioState } = await rig();
    refuse(reason);
    const obsolete = audioManager.startTx();
    audioManager.stopTx();
    setSecure(true);
    enablePcm();
    let resolveCapture!: (value: MediaStream) => void;
    capture.mockImplementationOnce(() => new Promise<MediaStream>((resolve) => { resolveCapture = resolve; }));
    const retry = audioManager.startTx();
    await obsolete;
    expect(getAudioState().txError).toBe(false);
    resolveCapture(stream());
    await expect(retry).resolves.toBeNull();
    expect(getAudioState().txError).toBe(false);
    expect(audioManager.txEnabled).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it.each(['insecure', 'unsupported'] as const)('a current %s refusal still latches failure', async (reason) => {
    const { audioManager, getAudioState } = await rig();
    refuse(reason);
    expect(await audioManager.startTx()).toMatch(/^TX MIC:/);
    expect(getAudioState().txError).toBe(true);
    expect(audioManager.txEnabled).toBe(false);
  });

  it('a current PCM refusal still latches when TxMic internally tears capture down', async () => {
    const { audioManager, getAudioState } = await rig();
    enablePcm(44100);
    await expect(audioManager.startTx()).resolves.toBe('TX MIC: unsupported mic sample rate 44100 Hz');
    expect(track.stop).toHaveBeenCalledOnce();
    expect(getAudioState().txError).toBe(true);
    expect(audioManager.txEnabled).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});
