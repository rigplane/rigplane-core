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
import { ManagedTxController, type ManagedOperation } from '../../runtime/tx-controller/managed-controller';
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
  const track = Object.assign(new EventTarget(), { stop: vi.fn(), kind: 'audio' });
  let finishRead!: (value: any) => void;
  let rejectRead!: (reason: Error) => void;
  const pendingRead = new Promise<any>((resolve, reject) => { finishRead = resolve; rejectRead = reject; });
  let encoderCallbacks: AudioEncoderInit;
  const reader = {
    read: vi.fn(() => pendingRead),
    cancel: vi.fn(async () => { finishRead({ done: true }); }),
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
  (globalThis as any).AudioEncoder = function (this: any, callbacks: AudioEncoderInit) {
    encoderCallbacks = callbacks;
    Object.assign(this, encoder);
    return encoder;
  };
  (globalThis as any).MediaStreamTrackProcessor = function () {
    return { readable: { getReader: () => reader } };
  };
  (globalThis as any).AudioContext = vi.fn(function () { return context; });
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream;
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } },
    writable: true,
    configurable: true,
  });
  return {
    track, stream, reader, encoder, context, getProcessor: () => processor,
    eof: () => finishRead({ done: true }),
    sample: (value: unknown) => finishRead({ done: false, value }),
    reject: () => rejectRead(new Error('capture reader failed')),
    encoderError: () => encoderCallbacks.error(new DOMException('encoder failed')),
    encoderOutput: () => encoderCallbacks.output({ byteLength: 1, copyTo: vi.fn() } as unknown as EncodedAudioChunk, {}),
  };
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

  it.each(['eof', 'reject', 'encoderError', 'encode-throws', 'opus-ended', 'pcm-ended'] as const)(
    'capture %s reaches the existing canonical ForceOFF exactly once', async (failure) => {
      const media = installMediaGlobals();
      if (failure === 'pcm-ended') delete (globalThis as any).AudioEncoder;
      const { audioManager } = await import('../audio-manager');
      const died = vi.fn();
      audioManager.onTxAudioDied(died);
      // The canonical snapshot tracks the accepted latch: fresh RX only
      // before the ON admission and after its ForceOFF.
      let latched = false;
      const submit = vi.fn<(operation: ManagedOperation) => Promise<'accepted'>>(async (operation) => {
        latched = operation === 'transmit_on';
        return 'accepted';
      });
      const idle = { phase: 'idle' as const, intent: null, radioTx: 'off' as const,
        txRisk: 'none' as const, fault: null, faultDetail: null, fresh: true,
        releaseRequired: false, configuredSeconds: 180, remainingMs: null, lastOperation: null };
      const tx = new ManagedTxController({
        snapshot: () => latched
          ? { ...idle, phase: 'active', intent: 'latched' as const, radioTx: 'on' as const,
              txRisk: 'confirmed-on' as const, releaseRequired: true, lastOperation: 'transmit_on' as const }
          : idle,
        refresh: async () => {}, invalidate: vi.fn(), sendPtt: async () => 'accepted',
        submit, setTot: async () => {}, startAudio: () => audioManager.startTx(),
        stopLocalAudio: () => audioManager.stopTx(),
        onAudioDied: (handler) => audioManager.onTxAudioDied(handler),
      });
      try {
        tx.transmitOn();
        await vi.waitFor(() => expect(submit).toHaveBeenCalledExactlyOnceWith('transmit_on'));
        FakeWebSocket.instances[0].open();
        if (failure === 'opus-ended' || failure === 'pcm-ended') media.track.dispatchEvent(new Event('ended'));
        else if (failure === 'encode-throws') {
          media.encoder.encode.mockImplementationOnce(() => { throw new Error('encode failed'); });
          media.sample({ close: vi.fn() });
        } else media[failure]();
        if (failure === 'encoderError') {
          media.track.dispatchEvent(new Event('ended'));
          media.eof();
        }
        await vi.waitFor(() => expect(died).toHaveBeenCalledOnce());
        expect(submit.mock.calls).toEqual([['transmit_on'], ['force_off']]);
        expect(audioManager.txEnabled).toBe(false);
        expect(audioManager.txCodec).toBeNull();
        expect(media.track.stop).toHaveBeenCalledOnce();
        // Concurrent and late producer signals cannot submit another global OFF.
        media.track.dispatchEvent(new Event('ended'));
        if (failure !== 'pcm-ended') media.encoderError();
        media.eof();
        await Promise.resolve();
        expect(died).toHaveBeenCalledOnce();
        expect(submit).toHaveBeenCalledTimes(2);
      } finally { tx.dispose(); audioManager.stopTx(); }
    },
  );

  it.each(['eof', 'reject'] as const)('does not enable TX when capture %s occurs during start', async (failure) => {
    const media = installMediaGlobals();
    const { audioManager } = await import('../audio-manager');
    media[failure]();
    expect(await audioManager.startTx()).not.toBeNull();
    expect(audioManager.txEnabled).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('cancels pending microphone permission without a late audio start or death notification', async () => {
    const media = installMediaGlobals();
    let grant!: (stream: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementationOnce(() => new Promise((resolve) => { grant = resolve; }));
    const { audioManager } = await import('../audio-manager');
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    const starting = audioManager.startTx();
    audioManager.stopTx();
    grant(media.stream);
    expect(await starting).not.toBeNull();
    expect(audioManager.txEnabled).toBe(false);
    expect(media.track.stop).toHaveBeenCalledOnce();
    expect(died).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it('does not stop a replacement capture when cancelled permission resolves late', async () => {
    const old = installMediaGlobals();
    let grant!: (stream: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementationOnce(() => new Promise((resolve) => { grant = resolve; }));
    const { audioManager } = await import('../audio-manager');
    const starting = audioManager.startTx();
    audioManager.stopTx();
    const current = installMediaGlobals();
    expect(await audioManager.startTx()).toBeNull();
    grant(old.stream);
    expect(await starting).not.toBeNull();
    expect(old.track.stop).toHaveBeenCalledOnce();
    expect(current.track.stop).not.toHaveBeenCalled();
    expect(audioManager.txEnabled).toBe(true);
    audioManager.stopTx();
  });

  it('ignores an old reader rejection after restart', async () => {
    const old = installMediaGlobals();
    old.reader.cancel.mockImplementationOnce(async () => {});
    const { audioManager } = await import('../audio-manager');
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    await audioManager.startTx();
    audioManager.stopTx();
    installMediaGlobals();
    await audioManager.startTx();
    old.reject();
    await Promise.resolve();
    expect(audioManager.txEnabled).toBe(true);
    expect(died).not.toHaveBeenCalled();
    audioManager.stopTx();
  });

  it('ignores old capture callbacks after restart and intentional codec-switch cancellation', async () => {
    const old = installMediaGlobals();
    old.reader.cancel.mockImplementationOnce(async () => {}); // terminal result arrives after restart
    const oldTrackListener = vi.spyOn(old.track, 'addEventListener');
    const { audioManager } = await import('../audio-manager');
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    await audioManager.startTx();
    audioManager.stopTx();
    const current = installMediaGlobals();
    await audioManager.startTx();
    const ws = FakeWebSocket.instances.at(-1)!;
    ws.open();
    const sentBefore = ws.sent.length;
    old.encoderOutput();
    old.encoderError();
    const oldEnded = oldTrackListener.mock.calls[0][1] as EventListener;
    oldEnded(new Event('ended'));
    const lateSample = { close: vi.fn() };
    old.sample(lateSample);
    await Promise.resolve();
    expect(lateSample.close).toHaveBeenCalledOnce();
    expect(current.encoder.encode).not.toHaveBeenCalled();
    expect(ws.sent).toHaveLength(sentBefore);
    expect(audioManager.txEnabled).toBe(true);
    expect(died).not.toHaveBeenCalled();
    ws.serverText({ type: 'audio_tx_format', codec: 'pcm16', opus_decode: false });
    current.encoderError();
    await Promise.resolve();
    expect(audioManager.txCodec).toBe('pcm16');
    expect(died).not.toHaveBeenCalled();
    audioManager.stopTx();
    current.track.dispatchEvent(new Event('ended'));
    expect(died).not.toHaveBeenCalled();
  });

  it('forwards the negotiated TX rate to the PCM16 leg (MOR-1794)', async () => {
    installMediaGlobals();
    const { audioManager } = await import('../audio-manager');
    const sent: ArrayBuffer[] = [];
    const realSend = WebSocket.prototype.send;
    expect(await audioManager.startTx()).toBeNull();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.send = (data: unknown) => {
      if (data instanceof ArrayBuffer) sent.push(data);
      else realSend.call(ws, data);
    };

    ws.serverText({
      type: 'audio_tx_format', codec: 'pcm16', opus_decode: false, sample_rate: 8000,
    });

    const created = vi.mocked(globalThis.AudioContext).mock.results.at(-1)?.value as
      { createScriptProcessor: ReturnType<typeof vi.fn> } | undefined;
    const processor = created?.createScriptProcessor.mock.results[0]?.value as
      { onaudioprocess: (event: unknown) => void };
    processor.onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960 * 6).fill(0.5) },
    });

    expect(sent).toHaveLength(1);
    const header = new DataView(sent[0]);
    expect(header.getUint16(4, true)).toBe(8000 / 100);
    expect(sent[0].byteLength).toBe(AUDIO_HEADER_SIZE + 160 * 2);
    audioManager.stopTx();
  });

  it('refuses a negotiated TX rate the contract does not admit (MOR-1794)', async () => {
    installMediaGlobals();
    const { audioManager } = await import('../audio-manager');
    const died = vi.fn();
    audioManager.onTxAudioDied(died);
    expect(await audioManager.startTx()).toBeNull();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    ws.serverText({
      type: 'audio_tx_format', codec: 'pcm16', opus_decode: false, sample_rate: 44100,
    });

    // Same fault path as a PCM leg that will not start: the session ends and
    // the operator hears why, instead of modulated garbage.
    expect(audioManager.txEnabled).toBe(false);
    expect(sentTypes(ws)).toContain('audio_stop');
    expect(died).toHaveBeenCalledOnce();
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

  it('delivers PCM16 at a negotiated rate below 48 kHz (MOR-1794)', async () => {
    // The radio contract admits 8/16/24 kHz. The server names that rate in
    // the audio_tx_format ack, but the PCM16 leg used to ignore it and push
    // unresampled 48 kHz frames — pitch-shifted modulation on the air while
    // every indicator reported a working transmission.
    clearMediaGlobals();
    media = installMediaGlobals({ contextSampleRate: 48000 });
    const sent = vi.fn();
    const mic = new TxMic(sent);
    expect(await mic.start()).toBeNull();

    expect(mic.applyServerCodec('pcm16', 16000)).toEqual({ switched: true, error: null });

    // One 20 ms frame at the capture rate is not yet a 20 ms frame at the
    // negotiated rate, so nothing may leave until a full output frame exists.
    media.getProcessor().onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960).fill(0.5) },
    });
    expect(sent).not.toHaveBeenCalled();

    media.getProcessor().onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960).fill(0.5) },
    });
    media.getProcessor().onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960).fill(0.5) },
    });

    expect(sent).toHaveBeenCalledOnce();
    const packet = sent.mock.calls[0][0] as ArrayBuffer;
    const header = new DataView(packet);
    expect(header.getUint8(1)).toBe(CODEC_PCM16);
    // Advertised rate must be the rate actually delivered, not the hard 48 kHz.
    expect(header.getUint16(4, true)).toBe(16000 / 100);
    expect(packet.byteLength).toBe(AUDIO_HEADER_SIZE + (16000 * 0.02) * 2);
    const samples = new Int16Array(packet, AUDIO_HEADER_SIZE);
    expect(samples).toHaveLength(320);
    // Constant input resamples to that same constant — not to stretched time.
    expect(samples.every((sample) => Math.abs(sample - 16383) <= 1)).toBe(true);
    mic.stop();
  });

  it('refuses the PCM16 fallback when the negotiated rate is not a contract rate (MOR-1794)', async () => {
    clearMediaGlobals();
    media = installMediaGlobals({ contextSampleRate: 48000 });
    const mic = new TxMic(vi.fn());
    await mic.start();

    const result = mic.applyServerCodec('pcm16', 44100);

    // Honest refusal through the existing fault path, never silent garble.
    expect(result.switched).toBe(false);
    expect(result.error).toContain('16000');
    expect(mic.active).toBe(true);
    expect(mic.codec).toBe('opus');
    expect(media.encoder.close).not.toHaveBeenCalled();
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
