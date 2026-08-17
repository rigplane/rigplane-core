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
  setTxEnabled: vi.fn(),
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

describe('AudioManager consumes the server TX codec ack', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    setTxCodecFallbackMock.mockClear();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
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
});

describe('TxMic adopts the codec the server can accept', () => {
  let mockTrack: any, mockEncoder: any, mockReader: any, context: any, processor: any;

  beforeEach(() => {
    mockTrack = { stop: vi.fn(), kind: 'audio' };
    mockReader = {
      read: vi.fn(() => Promise.resolve({ done: true })),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    mockEncoder = { configure: vi.fn(), encode: vi.fn(), close: vi.fn(), state: 'configured' };
    (globalThis as any).AudioEncoder = function (this: any) {
      Object.assign(this, mockEncoder);
      return mockEncoder;
    };
    (globalThis as any).MediaStreamTrackProcessor = function () {
      return { readable: { getReader: () => mockReader } };
    };
    processor = null;
    context = {
      sampleRate: SAMPLE_RATE,
      destination: {},
      createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
      createScriptProcessor: vi.fn(() => {
        processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null };
        return processor;
      }),
      close: vi.fn().mockResolvedValue(undefined),
    };
    (globalThis as any).AudioContext = vi.fn(function () { return context; });
    const stream = { getTracks: () => [mockTrack], getAudioTracks: () => [mockTrack] };
    Object.defineProperty(globalThis, 'navigator', {
      value: { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    delete (globalThis as any).AudioEncoder;
    delete (globalThis as any).MediaStreamTrackProcessor;
    delete (globalThis as any).AudioContext;
  });

  it('switches a live Opus capture to PCM16 without reacquiring the microphone', async () => {
    const sent = vi.fn();
    const mic = new TxMic(sent);
    expect(await mic.start()).toBeNull();
    expect(mic.codec).toBe('opus');

    expect(mic.applyServerCodec('pcm16')).toBe(true);

    expect(mic.codec).toBe('pcm16');
    expect(mockEncoder.close).toHaveBeenCalled();
    expect(mockReader.cancel).toHaveBeenCalled();
    // Same MediaStream — no second permission prompt, no second capture path.
    expect(mockTrack.stop).not.toHaveBeenCalled();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1);

    processor.onaudioprocess({
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
    mockEncoder.configure.mockClear();

    expect(await mic.start()).toBeNull();

    expect(mic.codec).toBe('pcm16');
    expect(mockEncoder.configure).not.toHaveBeenCalled();
    mic.stop();
  });

  it('leaves a working Opus capture alone when the server can decode it', async () => {
    const mic = new TxMic(vi.fn());
    await mic.start();

    expect(mic.applyServerCodec('opus')).toBe(false);

    expect(mic.codec).toBe('opus');
    expect(mockEncoder.close).not.toHaveBeenCalled();
    expect(context.createScriptProcessor).not.toHaveBeenCalled();
    mic.stop();
  });
});
