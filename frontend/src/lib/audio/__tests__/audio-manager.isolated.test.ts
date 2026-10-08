import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ManagedTxController } from '../../runtime/tx-controller/managed-controller';

const rxStart = vi.fn();
const rxStop = vi.fn();
const rxFlush = vi.fn();
const rxSetJitterBounds = vi.fn();
const playback = {
  schemaVersion: 1, contextState: 'suspended', sampleRate: 48000,
  receivedFrames: 2, validFrames: 2, decodedFrames: 0, pcmPeak: null,
  scheduledFrames: 0, suspendedDrops: 2, resumeOutcome: 'rejected', lastResumeError: 'NotAllowedError',
};
const rxStats = vi.fn(() => ({ underruns: 3, bufferDepthMs: 140, droppedFrames: 2, playback }));
const txStart = vi.fn().mockResolvedValue(null);
const txStop = vi.fn();
const txApplyServerCodec = vi.fn(() => ({ switched: false, error: null }));
let txCaptureFailureCategory: string | null = null;
let txCaptureDied: ((reason: string) => void) | null = null;
let txSend: ((data: ArrayBuffer) => void) | null = null;

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    start = rxStart;
    stop = rxStop;
    flush = rxFlush;
    setJitterBounds = rxSetJitterBounds;
    stats = rxStats;
    setFocus = vi.fn();
    setSplitStereo = vi.fn();
    setChannelGainDb = vi.fn();
    get focus() { return 'main'; }
    get splitStereo() { return true; }
    get mainGainDb() { return 0; }
    get subGainDb() { return 0; }
    set volume(_value: number) {}
  },
}));

vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = txStart;
    stop = txStop;
    applyServerCodec = txApplyServerCodec;
    get active() { return true; }
    get lastCaptureFailureCategory() { return txCaptureFailureCategory; }
    static supported() { return true; }
    constructor(
      send: (data: ArrayBuffer) => void,
      onCaptureDied?: (reason: string) => void,
    ) {
      txCaptureDied = onCaptureDied ?? null;
      txSend = send;
    }
  },
}));

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('../../stores/audio.svelte', () => ({
  setRxEnabled: vi.fn(),
  setTxEnabled: vi.fn(),
  setTxCodecFallback: vi.fn(),
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

  /** Simulate the server dropping this socket (soft_reconnect / audio
   *  re-arm): fires onclose so the manager's reconnect path runs. */
  serverClose(code = 1006, reason = 'rearm') {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
}

describe('AudioManager controller currency', () => {
  beforeEach(() => {
    vi.resetModules(); vi.useFakeTimers(); vi.clearAllMocks();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
  });
  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy(); vi.useRealTimers(); vi.unstubAllGlobals();
  });

  function binding(current: () => boolean) {
    return {
      remote: () => true, ready: current, epoch: () => 7,
      current: (epoch: number) => epoch === 7 && current(),
      protocols: () => [`rigplane-controller-v1.${'a'.repeat(64)}`],
    };
  }

  it('attaches RX/audio to the lease and never re-arms TX after audio loss', async () => {
    const { audioManager } = await import('../audio-manager');
    let current = true;
    audioManager.setControllerBinding(binding(() => current));
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    expect(socket.protocols).toEqual([`rigplane-controller-v1.${'a'.repeat(64)}`]);
    expect(socket.url).not.toContain('a'.repeat(64));
    socket.open();
    const starting = audioManager.startTx();
    await vi.advanceTimersByTimeAsync(0);
    socket.onmessage?.({ data: JSON.stringify({ type: 'audio_tx_format', codec: 'opus' }) });
    await starting;
    current = false;
    txSend?.(new ArrayBuffer(4));
    expect(socket.sent.some((frame) => frame instanceof ArrayBuffer)).toBe(false);
    socket.serverClose();
    expect(audioManager.txEnabled).toBe(false);
    expect(txStop).toHaveBeenCalled();
    current = true;
    await vi.advanceTimersByTimeAsync(500);
    const reconnected = FakeWebSocket.instances[1];
    reconnected.open();
    expect(reconnected.sent.some((raw) => {
      const msg = JSON.parse(raw as string); return msg.type === 'audio_start' && msg.direction === 'tx';
    })).toBe(false);
  });

  it('rejects a late microphone start after authority loss without enabling TX', async () => {
    const { audioManager } = await import('../audio-manager');
    let current = true;
    audioManager.setControllerBinding(binding(() => current));
    let settle!: () => void;
    txStart.mockImplementationOnce(() => new Promise((resolve) => { settle = () => resolve(null); }));
    const pending = audioManager.startTx();
    current = false;
    audioManager.stopTx();
    settle();
    expect(await pending).not.toBeNull();
    expect(audioManager.txEnabled).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('does not request capture or open audio without current control', async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.setControllerBinding(binding(() => false));
    expect(await audioManager.startTx()).not.toBeNull();
    expect(txStart).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });
});

describe('remote TX audio admission before managed PTT', () => {
  const controllers: ManagedTxController[] = [];
  beforeEach(() => {
    vi.resetModules(); vi.useFakeTimers(); vi.clearAllMocks();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
  });
  afterEach(async () => {
    for (const controller of controllers.splice(0)) controller.dispose();
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy(); vi.useRealTimers(); vi.unstubAllGlobals();
  });

  function serverText(socket: FakeWebSocket, message: Record<string, unknown>) {
    socket.onmessage?.({ data: JSON.stringify(message) });
  }
  const ack = { type: 'audio_tx_format', codec: 'opus', sample_rate: 48000 };

  async function rig() {
    const { audioManager } = await import('../audio-manager');
    let current = true;
    audioManager.setControllerBinding({
      remote: () => true, ready: () => current, epoch: () => 7,
      current: (epoch) => epoch === 7 && current,
      protocols: () => [`rigplane-controller-v1.${'a'.repeat(64)}`],
    });
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    let keyed = false;
    const sendPtt = vi.fn(async (operation: 'ptt_on' | 'ptt_off') => {
      keyed = operation === 'ptt_on';
      return 'accepted' as const;
    });
    const submit = vi.fn(async (operation: 'transmit_on' | 'force_off') => {
      keyed = operation === 'transmit_on';
      return 'accepted' as const;
    });
    const controller = new ManagedTxController({
      snapshot: () => ({
        phase: keyed ? 'active' : 'idle', intent: keyed ? 'momentary' : null,
        radioTx: keyed ? 'on' : 'off', txRisk: keyed ? 'confirmed-on' : 'none', fault: null,
        faultDetail: null, fresh: true, releaseRequired: false,
        configuredSeconds: 180, remainingMs: null, lastOperation: null,
      }),
      refresh: async () => {}, invalidate: vi.fn(), setTot: async () => {},
      sendPtt, submit, startAudio: () => audioManager.startTx(),
      stopLocalAudio: () => audioManager.stopTx(),
      onAudioDied: (handler) => audioManager.onTxAudioDied(handler),
    });
    controllers.push(controller);
    return { audioManager, controller, socket, sendPtt, submit, retire: () => { current = false; } };
  }

  it('keeps positive PTT and PCM pending until the existing format acknowledgement', async () => {
    const r = await rig();
    r.controller.pttOn();
    await vi.advanceTimersByTimeAsync(0);
    expect(r.socket.sent).toContain(JSON.stringify({ type: 'audio_start', direction: 'tx' }));
    txSend?.(new ArrayBuffer(4));
    expect(r.sendPtt).not.toHaveBeenCalled();
    expect(r.socket.sent.some((frame) => frame instanceof ArrayBuffer)).toBe(false);
    serverText(r.socket, { type: 'error', message: 'audio_config: CI-V send failed' });
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).not.toHaveBeenCalled();

    serverText(r.socket, ack);
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on');
    txSend?.(new ArrayBuffer(4));
    expect(r.socket.sent.some((frame) => frame instanceof ArrayBuffer)).toBe(true);
    expect(r.submit).not.toHaveBeenCalled();
  });

  it.each(['controller_audio_busy', 'controller_invalid', 'audio_start: TX audio unavailable'])(
    'stops refused capture through managed force_off without keying or replay: %s', async (message) => {
      const r = await rig();
      r.controller.pttOn();
      await vi.advanceTimersByTimeAsync(0);
      serverText(r.socket, { type: 'error', message });
      await vi.advanceTimersByTimeAsync(0);
      expect(r.audioManager.txEnabled).toBe(false);
      expect(txStop).toHaveBeenCalled();
      expect(r.sendPtt).not.toHaveBeenCalled();
      expect(r.submit).toHaveBeenCalledExactlyOnceWith('force_off');
      serverText(r.socket, { type: 'error', message });
      serverText(r.socket, ack);
      await vi.advanceTimersByTimeAsync(0);
      expect(r.sendPtt).not.toHaveBeenCalled();
      expect(r.submit).toHaveBeenCalledTimes(1);
    },
  );

  it('retires cancelled admission and accepts only a fresh press and its socket acknowledgement', async () => {
    const r = await rig();
    r.controller.pttOn();
    await vi.advanceTimersByTimeAsync(0);
    await r.controller.pttOff();
    r.controller.pttOn();
    await vi.advanceTimersByTimeAsync(0);
    const freshSocket = FakeWebSocket.instances.at(-1)!;
    expect(freshSocket).not.toBe(r.socket);
    freshSocket.open();
    serverText(r.socket, ack);
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).not.toHaveBeenCalled();
    serverText(freshSocket, ack);
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on');
    await r.controller.pttOff();
    expect(r.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
    expect(r.submit).not.toHaveBeenCalled();
  });

  it('settles a pending start as a failure when the audio socket closes', async () => {
    const r = await rig();
    const starting = r.audioManager.startTx();
    await vi.advanceTimersByTimeAsync(0);
    r.socket.serverClose();
    await expect(starting).resolves.not.toBeNull();
    await vi.advanceTimersByTimeAsync(0);
    expect(r.audioManager.txEnabled).toBe(false);
    expect(r.submit).toHaveBeenCalledExactlyOnceWith('force_off');
  });

  it('does not release a pending positive after authority loss and a late acknowledgement', async () => {
    const r = await rig();
    r.controller.pttOn();
    await vi.advanceTimersByTimeAsync(0);
    r.retire();
    serverText(r.socket, ack);
    await r.controller.releaseSession();
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).not.toHaveBeenCalled();
    expect(r.audioManager.txEnabled).toBe(false);
  });

  it('uses managed force_off for a controller refusal after admission too', async () => {
    const r = await rig();
    r.controller.pttOn();
    await vi.advanceTimersByTimeAsync(0);
    serverText(r.socket, ack);
    await vi.advanceTimersByTimeAsync(0);
    expect(r.sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on');
    serverText(r.socket, { type: 'error', message: 'controller_audio_busy' });
    await vi.advanceTimersByTimeAsync(0);
    expect(r.audioManager.txEnabled).toBe(false);
    expect(r.submit).toHaveBeenCalledExactlyOnceWith('force_off');
  });
});

function rxStartMessages(ws: FakeWebSocket): Array<Record<string, unknown>> {
  return ws.sent
    .map((s) => JSON.parse(s as string) as Record<string, unknown>)
    .filter((m) => m.type === 'audio_start' && m.direction === 'rx');
}

describe('AudioManager websocket subscriptions', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  it('sends rx audio_start when startRx is called after config already opened the websocket', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.setAudioConfig({ focus: 'main', split_stereo: true });
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    audioManager.startRx();

    expect(rxStartMessages(ws)).toContainEqual(
      expect.objectContaining({
        type: 'audio_start',
        direction: 'rx',
        preferred_rx_codec: 'pcm16',
      }),
    );
  });

  it('sends rx audio_stop before closing an open websocket', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    audioManager.stopRx();

    expect(ws.sent).toContain(JSON.stringify({ type: 'audio_stop', direction: 'rx' }));
  });

  it('requests opus when AudioDecoder is available', async () => {
    vi.stubGlobal('AudioDecoder', class {});
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    expect(rxStartMessages(ws)).toContainEqual(
      expect.objectContaining({
        type: 'audio_start',
        direction: 'rx',
        preferred_rx_codec: 'opus',
      }),
    );
  });

  it('requests pcm16 inside the Tauri shell even when AudioDecoder is available', async () => {
    vi.stubGlobal('AudioDecoder', class {});
    vi.stubGlobal('__TAURI_INTERNALS__', {});
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    expect(rxStartMessages(ws)).toContainEqual(
      expect.objectContaining({
        type: 'audio_start',
        direction: 'rx',
        preferred_rx_codec: 'pcm16',
      }),
    );
  });
});

describe('AudioManager audio_stats uplink (MOR-585)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function statsMessages(ws: FakeWebSocket): unknown[] {
    return ws.sent
      .map((s) => JSON.parse(s as string))
      .filter((m) => m.type === 'audio_stats');
  }

  it('reports safe capture failure immediately over the existing RX socket without TX admission', async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];
    txCaptureFailureCategory = 'NotFoundError';
    txStart.mockResolvedValueOnce('TX MIC: capture failed (NotFoundError)');
    try {
      await expect(audioManager.startTx()).resolves.toBe('TX MIC: capture failed (NotFoundError)');
      expect(statsMessages(ws)).toContainEqual(expect.objectContaining({
        microphoneCaptureError: 'NotFoundError',
      }));
      expect(ws.sent.some(s => typeof s === 'string' && JSON.parse(s).direction === 'tx')).toBe(false);
      expect(audioManager.txEnabled).toBe(false);
      expect(FakeWebSocket.instances).toHaveLength(1);
    } finally {
      txCaptureFailureCategory = null;
      audioManager.stopRx();
    }
  });

  it('sends periodic audio_stats with player counters while RX is active', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    vi.advanceTimersByTime(1500);
    expect(statsMessages(ws)).toEqual([{
      type: 'audio_stats',
      underruns: 3,
      buffer_depth_ms: 140,
      dropped_frames: 2,
      playback,
      microphoneCaptureError: null,
    }]);

    vi.advanceTimersByTime(3000);
    expect(statsMessages(ws).length).toBe(3);  // low rate: one per 1.5 s

    audioManager.stopRx();
  });

  it('notifies only an actual resume rejection, deduplicates it, and clears on a new session', async () => {
    const { audioManager } = await import('../audio-manager');
    const notify = vi.fn();
    audioManager.setOperatorNotifier(notify);
    audioManager.startRx();
    FakeWebSocket.instances[0].open();
    vi.advanceTimersByTime(4500);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify).toHaveBeenCalledWith('error', expect.not.stringContaining('NotAllowedError'), 'rxAudioResumeFailed');
    playback.resumeOutcome = 'pending';
    playback.lastResumeError = null as any;
    vi.advanceTimersByTime(1500);
    playback.resumeOutcome = 'rejected';
    playback.lastResumeError = 'NotAllowedError';
    vi.advanceTimersByTime(1500);
    expect(notify).toHaveBeenCalledTimes(1);
    playback.contextState = 'running';
    vi.advanceTimersByTime(1500);
    expect(notify).toHaveBeenCalledTimes(1);
    playback.contextState = 'suspended';
    audioManager.stopRx();
    vi.advanceTimersByTime(3000);
    expect(notify).toHaveBeenCalledTimes(1);
    audioManager.startRx();
    FakeWebSocket.instances.at(-1)!.open();
    vi.advanceTimersByTime(1500);
    expect(notify).toHaveBeenCalledTimes(2);
    audioManager.stopRx();
  });

  it('does not send audio_stats when only TX is active', async () => {
    const { audioManager } = await import('../audio-manager');

    await audioManager.startTx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    vi.advanceTimersByTime(5000);
    expect(statsMessages(ws)).toEqual([]);

    audioManager.stopTx();
  });

  it('clears the stats timer on close — no timer leak after stopRx', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    audioManager.stopRx();
    ws.sent = [];
    vi.advanceTimersByTime(10000);
    expect(statsMessages(ws)).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('AudioManager reconnect coalescing (MOR-924)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('re-sends audio_start with the SAME stable client_id after a server-side reconnect', async () => {
    const { audioManager } = await import('../audio-manager');

    // Initial RX subscribe.
    audioManager.startRx();
    const first = FakeWebSocket.instances[0];
    first.open();
    const firstStart = rxStartMessages(first);
    expect(firstStart).toHaveLength(1);
    const clientId = firstStart[0].client_id;
    // Identity must be a non-empty stable token sent on the wire.
    expect(typeof clientId).toBe('string');
    expect(clientId).not.toBe('');

    // Server drops the socket (soft_reconnect / audio re-arm). The manager
    // schedules a backoff reconnect.
    first.serverClose();
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(600); // > BACKOFF_MIN
    expect(FakeWebSocket.instances).toHaveLength(2);

    // The reconnected socket opens and must re-subscribe with the SAME
    // identity so the broadcaster can drop the prior zombie subscription
    // (instead of fanning RX out to two subscribers — the silent-audio bug).
    const second = FakeWebSocket.instances[1];
    second.open();
    const secondStart = rxStartMessages(second);
    expect(secondStart).toHaveLength(1);
    expect(secondStart[0].client_id).toBe(clientId);

    audioManager.stopRx();
  });
});


describe('AudioManager TX failure notifications (MOR-1783)', () => {
  const notifyOperator = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
    vi.clearAllMocks();
  });

  function serverText(ws: FakeWebSocket, msg: Record<string, unknown>): void {
    ws.onmessage?.({ data: JSON.stringify(msg) });
  }

  /** Import the module and inject a spy in place of the operator notifier
   *  frontend-runtime.ts wires in production. */
  async function withNotifier() {
    const { audioManager } = await import('../audio-manager');
    audioManager.setOperatorNotifier(notifyOperator);
    return audioManager;
  }

  it.each([false, true])('reports a refused RX start once and preserves TX=%s', async (txActive) => {
    const audioManager = await withNotifier();
    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    if (txActive) await audioManager.startTx();
    ws.sent = [];

    const refusal = {
      type: 'error',
      message: 'audio_start: RX audio failed to start: native device private detail',
    };
    serverText(ws, refusal);
    serverText(ws, refusal);

    expect(notifyOperator).toHaveBeenCalledTimes(1);
    expect(notifyOperator).toHaveBeenCalledWith(
      'error', expect.not.stringContaining('private detail'), 'rxAudioStartFailed',
    );
    expect(audioManager.rxEnabled).toBe(false);
    expect(audioManager.txEnabled).toBe(txActive);
    expect(rxStop).toHaveBeenCalledTimes(1);
    expect(ws.sent).toContain(JSON.stringify({ type: 'audio_stop', direction: 'rx' }));
    expect(txStop).not.toHaveBeenCalled();

    rxStart.mockClear();
    audioManager.startRx();
    expect(audioManager.rxEnabled).toBe(true);
    expect(rxStart).toHaveBeenCalledTimes(1);
    audioManager.stopRx();
    if (txActive) audioManager.stopTx();
  });

  it('server TX refusal raises one txAudioServerUnavailable banner and releases TX through the died path', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const audioManager = await withNotifier();
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    await audioManager.startTx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    serverText(ws, { type: 'error', message: 'audio_start: TX audio unavailable' });

    expect(notifyOperator).toHaveBeenCalledTimes(1);
    expect(notifyOperator).toHaveBeenCalledWith(
      'error', expect.any(String), 'txAudioServerUnavailable',
    );
    expect(died).toHaveBeenCalledTimes(1);
    expect(audioManager.txEnabled).toBe(false);
    expect(ws.sent).toContain(JSON.stringify({ type: 'audio_stop', direction: 'tx' }));
  });

  it('ignores audio-WS error envelopes that are not the TX refusal', async () => {
    const audioManager = await withNotifier();
    await audioManager.startTx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    serverText(ws, { type: 'error', message: 'audio_config: CI-V send failed' });
    serverText(ws, {
      type: 'error',
      message: "audio_config: invalid focus 'both'; expected one of ['main', 'sub']",
    });
    serverText(ws, { type: 'error', message: 'something else entirely' });

    expect(notifyOperator).not.toHaveBeenCalled();
    expect(audioManager.txEnabled).toBe(true);
    audioManager.stopTx();
  });

  it.each([
    ['TX MIC: permission denied', 'txAudioMicPermissionDenied'],
    ['TX MIC: microphone capture not supported', 'txAudioCaptureUnsupported'],
    ['TX MIC: insecure context', 'txAudioInsecureContext'],
    ['TX MIC: capture failed (NotFoundError)', 'txAudioStartFailed'],
    ['TX MIC: capture failed (NotReadableError)', 'txAudioStartFailed'],
    ['TX MIC: capture failed (OverconstrainedError)', 'txAudioStartFailed'],
    ['TX MIC: capture failed (AbortError)', 'txAudioStartFailed'],
    ['TX MIC: capture failed (unknown)', 'txAudioStartFailed'],
    ['TX MIC: PCM capture not supported', 'txAudioCaptureUnsupported'],
    ['TX MIC: unsupported mic sample rate 48000 Hz', 'txAudioStartFailed'],
  ])('local startTx failure %s raises the %s banner', async (reason, code) => {
    txStart.mockResolvedValueOnce(reason);
    const audioManager = await withNotifier();

    await expect(audioManager.startTx()).resolves.toBe(reason);

    expect(notifyOperator).toHaveBeenCalledTimes(1);
    expect(notifyOperator).toHaveBeenCalledWith('error', expect.any(String), code);
    expect(audioManager.txEnabled).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it.each([
    'TX MIC: capture start cancelled',
    'TX MIC: capture stopped before start completed',
  ])('operator cancellation %s raises no banner', async (reason) => {
    const logInfo = vi.spyOn(console, 'log').mockImplementation(() => {});
    txStart.mockResolvedValueOnce(reason);
    const audioManager = await withNotifier();

    await expect(audioManager.startTx()).resolves.toBe(reason);

    expect(notifyOperator).not.toHaveBeenCalled();
    expect(logInfo).toHaveBeenCalledWith(expect.stringContaining(reason));
    expect(audioManager.txEnabled).toBe(false);
  });

  it('mid-TX capture death raises one txAudioStopped banner and fires the died path', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const audioManager = await withNotifier();
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    await audioManager.startTx();
    FakeWebSocket.instances[0].open();

    txCaptureDied?.('TX MIC: microphone track ended');

    expect(notifyOperator).toHaveBeenCalledTimes(1);
    expect(notifyOperator).toHaveBeenCalledWith(
      'error', expect.stringContaining('microphone track ended'), 'txAudioStopped',
    );
    expect(died).toHaveBeenCalledTimes(1);
    expect(audioManager.txEnabled).toBe(false);
  });

  it('capture death while TX is not enabled raises no banner', async () => {
    const audioManager = await withNotifier();

    txCaptureDied?.('TX MIC: microphone track ended');

    expect(notifyOperator).not.toHaveBeenCalled();
    expect(audioManager.txEnabled).toBe(false);
  });

  it('raises no banner and does not throw when no notifier is injected', async () => {
    const { audioManager } = await import('../audio-manager');
    txStart.mockResolvedValueOnce('TX MIC: permission denied');

    await expect(audioManager.startTx()).resolves.toBe('TX MIC: permission denied');

    expect(audioManager.txEnabled).toBe(false);
  });
});


describe('AudioManager credential-free WebSockets', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'radio.example.test' });
    localStorage.clear();
  });

  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.stopRx();
    vi.restoreAllMocks();
    localStorage.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('ignores stored application tokens on initial RX and each reconnect', async () => {
    const { audioManager } = await import('../audio-manager');
    for (const token of ['synthetic +&?=#/ token', 'synthetic-rotated', null]) {
      if (token) localStorage.setItem('rigplane-auth-token', token);
      else localStorage.removeItem('rigplane-auth-token');
      if (!FakeWebSocket.instances.length) audioManager.startRx();
      else {
        FakeWebSocket.instances.at(-1)!.serverClose();
        await vi.advanceTimersByTimeAsync(1000);
      }
      const socket = FakeWebSocket.instances.at(-1)!;
      const url = new URL(socket.url);
      expect(url.origin).toBe('wss://radio.example.test');
      expect(url.pathname).toBe('/api/v1/audio');
      expect(url.searchParams.getAll('token')).toEqual([]);
      socket.open();
    }
  });

  it('does not log the error event carrying the socket URL', async () => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
    localStorage.setItem('rigplane-auth-token', 'synthetic-private');
    const { audioManager } = await import('../audio-manager');
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.onerror?.({ target: socket });
    expect(errorLog).toHaveBeenCalledWith('[audio-ws] error');
    expect(JSON.stringify(errorLog.mock.calls)).not.toContain('synthetic-private');
  });

  it('keeps initial and reconnected anonymous RX token-free', async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.startRx();
    FakeWebSocket.instances[0].serverClose();
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeWebSocket.instances.map((socket) => socket.url)).toEqual([
      'wss://radio.example.test/api/v1/audio',
      'wss://radio.example.test/api/v1/audio',
    ]);
  });
});
