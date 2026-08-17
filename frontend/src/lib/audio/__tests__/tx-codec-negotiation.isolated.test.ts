/**
 * TX codec negotiation on the audio WebSocket (MOR-1791).
 *
 * The defect: a server with no native opus codec cannot decode browser Opus
 * TX frames. It knows this at TX start, but never said so, and the browser
 * kept sending Opus — the radio keyed while every frame was dropped
 * fail-closed and nothing reached the air.
 *
 * The server now answers `audio_start direction=tx` with an `audio_tx_format`
 * ack naming the codec it can accept. These tests cover both branches of that
 * ack plus the operator-visible fallback indication, using the REAL `TxMic`
 * so the switch is exercised through the actual capture path rather than a
 * stand-in. Nothing here depends on a native Opus library existing anywhere —
 * the server's answer is synthesized.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TxMic } from '../tx-mic';
import { AUDIO_HEADER_SIZE, CODEC_PCM16, SAMPLE_RATE } from '../constants';

const setTxCodecFallbackMock = vi.fn();
const setTxEnabledMock = vi.fn();

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    start = vi.fn();
    stop = vi.fn();
    flush = vi.fn();
    setJitterBounds = vi.fn();
    stats = vi.fn(() => ({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 }));
    feed = vi.fn();
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

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('../../stores/audio.svelte', () => ({
  setRxEnabled: vi.fn(),
  setTxEnabled: setTxEnabledMock,
  setTxCodecFallback: setTxCodecFallbackMock,
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

  constructor(_url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: unknown) { this.sent.push(data); }
  close() { this.readyState = 3; }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  serverClose(code = 1006, reason = 'rearm') {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }

  /** Deliver a server→client control frame on the audio WS. */
  serverText(msg: unknown) {
    this.onmessage?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) });
  }
}

async function openManager() {
  const { audioManager } = await import('../audio-manager');
  audioManager.startRx();
  const ws = FakeWebSocket.instances[0];
  ws.open();
  return { audioManager, ws };
}

function sentTypes(ws: FakeWebSocket): string[] {
  return ws.sent.map((s) => (JSON.parse(s as string) as { type: string }).type);
}

/**
 * Install a browser media environment with BOTH capture paths available.
 *
 * `contextSampleRate` is what the AudioContext actually gives us: anything
 * other than `SAMPLE_RATE` makes `startPcmFallback()` refuse, which is the
 * only way the PCM16 leg can fail on a browser that has WebCodecs.
 */
function installMediaGlobals({ contextSampleRate = SAMPLE_RATE } = {}) {
  const track = { stop: vi.fn(), kind: 'audio' };
  const reader = {
    read: vi.fn(() => Promise.resolve({ done: true })),
    cancel: vi.fn().mockResolvedValue(undefined),
  };
  const encoder = { configure: vi.fn(), encode: vi.fn(), close: vi.fn(), state: 'configured' };
  let processor: any = null;
  const context = {
    sampleRate: contextSampleRate,
    destination: {},
    createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
    createScriptProcessor: vi.fn(() => {
      processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null };
      return processor;
    }),
    close: vi.fn().mockResolvedValue(undefined),
  };
  (globalThis as any).AudioEncoder = function (this: any) {
    Object.assign(this, encoder);
    return encoder;
  };
  (globalThis as any).MediaStreamTrackProcessor = function () {
    return { readable: { getReader: () => reader } };
  };
  (globalThis as any).AudioContext = vi.fn(function () { return context; });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } },
    writable: true,
    configurable: true,
  });
  return { track, reader, encoder, context, getProcessor: () => processor };
}

function clearMediaGlobals(): void {
  delete (globalThis as any).AudioEncoder;
  delete (globalThis as any).MediaStreamTrackProcessor;
  delete (globalThis as any).AudioContext;
}

describe('AudioManager consumes the server TX codec ack', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    setTxCodecFallbackMock.mockClear();
    setTxEnabledMock.mockClear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  afterEach(() => {
    clearMediaGlobals();
  });

  it('raises the fallback indication when the server cannot decode Opus', async () => {
    const { audioManager, ws } = await openManager();

    ws.serverText({
      type: 'audio_tx_format',
      codec: 'pcm16',
      opus_decode: false,
      sample_rate: 48000,
    });

    expect(audioManager.txCodecFallback).toBe(true);
    expect(setTxCodecFallbackMock).toHaveBeenCalledWith(true);
  });

  it('shows nothing new when the server can decode Opus', async () => {
    const { audioManager, ws } = await openManager();

    ws.serverText({
      type: 'audio_tx_format',
      codec: 'opus',
      opus_decode: true,
      sample_rate: 48000,
    });

    expect(audioManager.txCodecFallback).toBe(false);
    expect(setTxCodecFallbackMock).not.toHaveBeenCalled();
  });

  it('ignores unrelated and malformed text frames', async () => {
    const { audioManager, ws } = await openManager();

    ws.serverText({ type: 'audio_format', codec: 'pcm16' });
    ws.serverText('}{ not json');

    expect(audioManager.txCodecFallback).toBe(false);
    expect(setTxCodecFallbackMock).not.toHaveBeenCalled();
  });

  it('drops the indication when the link goes away', async () => {
    const { audioManager, ws } = await openManager();
    ws.serverText({ type: 'audio_tx_format', codec: 'pcm16', opus_decode: false });
    expect(audioManager.txCodecFallback).toBe(true);

    ws.serverClose();

    expect(audioManager.txCodecFallback).toBe(false);
    expect(setTxCodecFallbackMock).toHaveBeenLastCalledWith(false);
  });

  it('never claims a working fallback when the PCM16 path refuses to start', async () => {
    // The one way the switch can fail on a WebCodecs browser: the
    // AudioContext will not give us 48 kHz.
    installMediaGlobals({ contextSampleRate: 44100 });
    const { audioManager } = await import('../audio-manager');
    expect(await audioManager.startTx()).toBeNull(); // Opus capture is up
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    ws.serverText({ type: 'audio_tx_format', codec: 'pcm16', opus_decode: false });

    // The chip must never say "Transmission is working" while the radio is
    // keyed and the switch did not happen.
    expect(audioManager.txCodecFallback).toBe(false);
    expect(setTxCodecFallbackMock).not.toHaveBeenCalledWith(true);
    // The TX audio session ends through the existing teardown: the server
    // releases the TX lease, disarming the radio's TX audio leg.
    expect(sentTypes(ws)).toContain('audio_stop');
    expect(setTxEnabledMock).toHaveBeenLastCalledWith(false);
    expect(audioManager.txEnabled).toBe(false);
    // And the next key reports it as a TX audio START failure — the exact
    // input the TX controller turns into `audio-failed` and de-keys on.
    expect(await audioManager.startTx()).toContain('sample rate');
  });

  it('notifies onTxAudioDied subscribers exactly on the mid-TX failure path (MOR-1796)', async () => {
    installMediaGlobals({ contextSampleRate: 44100 });
    const { audioManager } = await import('../audio-manager');
    const died: string[] = [];
    const throwing = () => { died.push('throwing'); throw new Error('faulty subscriber'); };
    const unsubscribeThrowing = audioManager.onTxAudioDied(throwing);
    const unsubscribed = audioManager.onTxAudioDied(() => died.push('unsubscribed'));
    audioManager.onTxAudioDied(() => died.push('kept'));
    unsubscribed();

    expect(await audioManager.startTx()).toBeNull();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    expect(died).toEqual([]); // a healthy start notifies nobody

    ws.serverText({ type: 'audio_tx_format', codec: 'pcm16', opus_decode: false });

    // The throwing subscriber is isolated; the removed one stays silent.
    expect(died).toEqual(['throwing', 'kept']);
    unsubscribeThrowing();
    unsubscribeThrowing(); // idempotent
  });

  it('leaves the pin alone when the ack names a codec it does not know', async () => {
    installMediaGlobals();
    const { audioManager } = await import('../audio-manager');
    await audioManager.startTx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.serverText({ type: 'audio_tx_format', codec: 'pcm16', opus_decode: false });
    expect(audioManager.txCodecFallback).toBe(true);

    // Fail-safe, not fail-open: an unrecognized codec must not clear a pin
    // that a previous ack established.
    ws.serverText({ type: 'audio_tx_format', codec: 'flac', opus_decode: false });

    expect(audioManager.txCodecFallback).toBe(true);
    audioManager.stopTx();
    expect(await audioManager.startTx()).toBeNull();
    expect(audioManager.txCodec).toBe('pcm16');
  });
});

describe('TxMic adopts the codec the server can accept', () => {
  let media: ReturnType<typeof installMediaGlobals>;

  beforeEach(() => {
    media = installMediaGlobals();
  });

  afterEach(() => {
    clearMediaGlobals();
  });

  it('switches a live Opus capture to PCM16 without reacquiring the microphone', async () => {
    const sent = vi.fn();
    const mic = new TxMic(sent);
    expect(await mic.start()).toBeNull();
    expect(mic.codec).toBe('opus');

    expect(mic.applyServerCodec('pcm16')).toEqual({ switched: true, error: null });

    expect(mic.codec).toBe('pcm16');
    expect(media.encoder.close).toHaveBeenCalled();
    expect(media.reader.cancel).toHaveBeenCalled();
    // Same MediaStream — no second permission prompt, no second capture path.
    expect(media.track.stop).not.toHaveBeenCalled();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);

    media.getProcessor().onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960).fill(0.25) },
    });
    expect(sent).toHaveBeenCalledOnce();
    const packet = sent.mock.calls[0][0] as ArrayBuffer;
    expect(new DataView(packet).getUint8(1)).toBe(CODEC_PCM16);
    expect(packet.byteLength).toBe(AUDIO_HEADER_SIZE + 960 * 2);
    mic.stop();
  });

  it('opens later sessions on PCM16 from the first frame', async () => {
    const sent = vi.fn();
    const mic = new TxMic(sent);
    await mic.start();
    mic.applyServerCodec('pcm16');
    mic.stop();
    media.encoder.configure.mockClear();

    expect(await mic.start()).toBeNull();

    expect(mic.codec).toBe('pcm16');
    expect(media.encoder.configure).not.toHaveBeenCalled();
    mic.stop();
  });

  it('leaves a working Opus capture alone when the server can decode it', async () => {
    const mic = new TxMic(vi.fn());
    await mic.start();

    expect(mic.applyServerCodec('opus')).toEqual({ switched: false, error: null });

    expect(mic.codec).toBe('opus');
    expect(media.encoder.close).not.toHaveBeenCalled();
    expect(media.context.createScriptProcessor).not.toHaveBeenCalled();
    mic.stop();
  });

  it('keeps the live capture whole when the PCM16 leg refuses to start', async () => {
    // The failure must not kill capture out from under a keyed transmitter:
    // PCM16 is brought up BEFORE the Opus leg is torn down, so a refusal
    // leaves the running capture exactly as it was.
    clearMediaGlobals();
    media = installMediaGlobals({ contextSampleRate: 44100 });
    const sent = vi.fn();
    const mic = new TxMic(sent);
    await mic.start();

    const result = mic.applyServerCodec('pcm16');

    expect(result.switched).toBe(false);
    expect(result.error).toContain('sample rate');
    expect(mic.active).toBe(true);
    expect(mic.codec).toBe('opus');
    expect(media.encoder.close).not.toHaveBeenCalled();
    expect(media.reader.cancel).not.toHaveBeenCalled();
    expect(media.track.stop).not.toHaveBeenCalled();
    mic.stop();
  });
});
