import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { TxMic } from '../tx-mic';
import { setTxMicProcessing, reloadTxMicProcessingForTests, TX_MIC_PROCESSING_KEY } from '../../stores/tx-mic-processing.svelte';
import { SAMPLE_RATE, CHANNELS, TX_BITRATE, AUDIO_HEADER_SIZE, CODEC_PCM16 } from '../constants';

let mockTrack: any, mockEncoder: any, mockReader: any;
beforeEach(() => {
  localStorage.removeItem(TX_MIC_PROCESSING_KEY);
  reloadTxMicProcessingForTests();
  delete (globalThis as any).AudioContext;
  delete (globalThis as any).webkitAudioContext;
  mockTrack = Object.assign(new EventTarget(), { stop: vi.fn(), kind: 'audio' });
  mockReader = { read: vi.fn(() => new Promise(() => {})), cancel: vi.fn().mockResolvedValue(undefined) };
  mockEncoder = { configure: vi.fn(), encode: vi.fn(), close: vi.fn(), state: 'configured' };
  (globalThis as any).AudioEncoder = function (this: any) { Object.assign(this, mockEncoder); return mockEncoder; };
  (globalThis as any).MediaStreamTrackProcessor = function () {
    return { readable: { getReader: () => mockReader } };
  };
  const stream = { getTracks: () => [mockTrack], getAudioTracks: () => [mockTrack] };
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } },
    writable: true, configurable: true,
  });
});
const originalNavigator = globalThis.navigator;
afterEach(() => {
  vi.restoreAllMocks();
  delete (globalThis as any).AudioEncoder;
  delete (globalThis as any).MediaStreamTrackProcessor;
  delete (globalThis as any).AudioContext;
  delete (globalThis as any).webkitAudioContext;
  Object.defineProperty(globalThis, 'navigator', {
    value: originalNavigator,
    writable: true,
    configurable: true,
  });
});

describe('TxMic', () => {
  it('detects support based on WebCodecs', () => {
    expect(TxMic.supported()).toBe(true);
    delete (globalThis as any).AudioEncoder;
    expect(TxMic.supported()).toBe(false);
  });
  it('does not report support without a microphone capture API', () => {
    Object.defineProperty(globalThis, 'navigator', {
      value: {},
      writable: true,
      configurable: true,
    });

    expect(TxMic.supported()).toBe(false);
  });
  it('errors when WebCodecs missing', async () => {
    delete (globalThis as any).AudioEncoder;
    const m = new TxMic(vi.fn());
    expect(await m.start()).toContain('microphone capture not supported');
  });
  it('requests raw mic capture: no browser voice processing (MOR-4312)', async () => {
    const m = new TxMic(vi.fn()); await m.start();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        channelCount: CHANNELS, sampleRate: SAMPLE_RATE,
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      },
    });
    expect(m.active).toBe(true); m.stop();
  });
  it('requests all browser voice processing when the mode is browser (MOR-4596)', async () => {
    setTxMicProcessing('browser');
    const m = new TxMic(vi.fn()); await m.start();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: {
        channelCount: CHANNELS, sampleRate: SAMPLE_RATE,
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      },
    });
    m.stop();
  });
  it('applies a mode change from the next start and keeps raw as the default (MOR-4596)', async () => {
    const m = new TxMic(vi.fn());
    await m.start(); m.stop();
    setTxMicProcessing('browser');
    await m.start(); m.stop();
    setTxMicProcessing('raw');
    await m.start(); m.stop();
    const flags = vi.mocked(navigator.mediaDevices.getUserMedia).mock.calls
      .map(([c]) => (c?.audio as MediaTrackConstraints).echoCancellation);
    expect(flags).toEqual([false, true, false]);
  });
  it('keeps the chosen mode across a reload (MOR-4596)', async () => {
    setTxMicProcessing('browser');
    expect(localStorage.getItem(TX_MIC_PROCESSING_KEY)).toBe('browser');
    reloadTxMicProcessingForTests();
    const m = new TxMic(vi.fn()); await m.start();
    expect((vi.mocked(navigator.mediaDevices.getUserMedia).mock.calls[0]?.[0]?.audio as MediaTrackConstraints)
      .noiseSuppression).toBe(true);
    m.stop();
    localStorage.setItem(TX_MIC_PROCESSING_KEY, 'garbage');
    reloadTxMicProcessingForTests();
    await m.start();
    expect((vi.mocked(navigator.mediaDevices.getUserMedia).mock.calls[1]?.[0]?.audio as MediaTrackConstraints)
      .noiseSuppression).toBe(false);
    m.stop();
  });
  it('handles permission denied', async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(
      new DOMException('denied', 'NotAllowedError'),
    );
    expect(await new TxMic(vi.fn()).start()).toContain('permission denied');
  });

  it('a rejection settling after stop() is the cancellation, not a refusal (MOR-3753)', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    let rejectCapture!: (reason?: unknown) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementationOnce(
      () => new Promise<MediaStream>((_resolve, reject) => { rejectCapture = reject; }),
    );
    const mic = new TxMic(vi.fn());
    const starting = mic.start();
    mic.stop();
    rejectCapture(new DOMException('denied late', 'NotAllowedError'));

    await expect(starting).resolves.toBe('TX MIC: capture start cancelled');
    expect(mic.lastCaptureFailureCategory).toBeNull();
    expect(warn).not.toHaveBeenCalled();
    expect(mic.active).toBe(false);
    expect(mockTrack.stop).not.toHaveBeenCalled();
  });

  it('a stream resolved after stop() is stopped and never keys or encodes (MOR-3753)', async () => {
    let resolveCapture!: (stream: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementationOnce(
      () => new Promise<MediaStream>((resolve) => { resolveCapture = resolve; }),
    );
    const send = vi.fn();
    const mic = new TxMic(send);
    const starting = mic.start();
    mic.stop();
    resolveCapture({
      getTracks: () => [mockTrack], getAudioTracks: () => [mockTrack],
    } as unknown as MediaStream);

    await expect(starting).resolves.toBe('TX MIC: capture start cancelled');
    expect(mockTrack.stop).toHaveBeenCalledOnce();
    expect(mockEncoder.configure).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    expect(mic.active).toBe(false);
  });
  it.each([
    ['NotAllowedError', 'TX MIC: permission denied'],
    ['NotFoundError', 'TX MIC: capture failed (NotFoundError)'],
    ['NotReadableError', 'TX MIC: capture failed (NotReadableError)'],
    ['OverconstrainedError', 'TX MIC: capture failed (OverconstrainedError)'],
    ['AbortError', 'TX MIC: capture failed (AbortError)'],
    ['SecurityError', 'TX MIC: capture failed (SecurityError)'],
    ['InvalidStateError', 'TX MIC: capture failed (InvalidStateError)'],
    ['TypeError', 'TX MIC: capture failed (TypeError)'],
  ])('preserves %s without opening capture or leaking the message', async (name, reason) => {
    const log = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(
      new DOMException('private device label and driver details', name),
    );
    const send = vi.fn();
    const mic = new TxMic(send);

    await expect(mic.start()).resolves.toBe(reason);

    expect(log).toHaveBeenCalledExactlyOnceWith(`[TxMic] getUserMedia rejected: ${name}`);
    expect(mic.lastCaptureFailureCategory).toBe(name);
    expect(mic.active).toBe(false);
    expect(mockEncoder.configure).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    mic.stop();
    expect(mockTrack.stop).not.toHaveBeenCalled();
  });
  it.each([
    new Error('private device label'),
    { name: 'private device label', message: 'private driver details' },
    'private error text',
    null,
    undefined,
  ])('keeps unknown rejected values generic and capture closed', async (error) => {
    const log = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(error);
    const send = vi.fn();
    const mic = new TxMic(send);

    await expect(mic.start()).resolves.toBe('TX MIC: capture failed (unknown)');

    expect(log).toHaveBeenCalledExactlyOnceWith('[TxMic] getUserMedia rejected: unknown');
    expect(mic.lastCaptureFailureCategory).toBe('unknown');
    expect(mic.active).toBe(false);
    expect(mockEncoder.configure).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
  it('does not auto-retry failures and can start and clean up on a later user attempt', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const capture = vi.mocked(navigator.mediaDevices.getUserMedia);
    capture.mockRejectedValueOnce(new DOMException('', 'NotFoundError'));
    capture.mockRejectedValueOnce(new DOMException('', 'NotReadableError'));
    const mic = new TxMic(vi.fn());

    await expect(mic.start()).resolves.toBe('TX MIC: capture failed (NotFoundError)');
    mic.stop();
    await expect(mic.start()).resolves.toBe('TX MIC: capture failed (NotReadableError)');
    mic.stop();
    expect(capture).toHaveBeenCalledTimes(2);
    expect(mockEncoder.configure).not.toHaveBeenCalled();
    expect(mockTrack.stop).not.toHaveBeenCalled();

    await expect(mic.start()).resolves.toBeNull();
    expect(capture).toHaveBeenCalledTimes(3);
    expect(mic.active).toBe(true);
    expect(mockEncoder.configure).toHaveBeenCalledOnce();
    mic.stop();
    expect(mic.active).toBe(false);
    expect(mockTrack.stop).toHaveBeenCalledOnce();
    expect(mockReader.cancel).toHaveBeenCalledOnce();
    expect(mockEncoder.close).toHaveBeenCalledOnce();
  });
  it('configures encoder correctly', async () => {
    const m = new TxMic(vi.fn()); await m.start();
    expect(mockEncoder.configure).toHaveBeenCalledWith({
      codec: 'opus', sampleRate: SAMPLE_RATE, numberOfChannels: CHANNELS, bitrate: TX_BITRATE,
    });
    m.stop();
  });
  describe('device-independent Opus capture (MOR-4594)', () => {
    // getUserMedia constraints are hints: a 44.1 kHz stereo device must still
    // reach the 48 kHz mono encoder through a context created at SAMPLE_RATE.
    function installContext(rate = SAMPLE_RATE) {
      const convertedTrack = Object.assign(new EventTarget(), { stop: vi.fn(), kind: 'audio' });
      const source = { connect: vi.fn(), disconnect: vi.fn() };
      const destination: any = {
        channelCount: 2, channelCountMode: 'max',
        stream: { getAudioTracks: () => [convertedTrack], getTracks: () => [convertedTrack] },
      };
      const context = {
        sampleRate: rate,
        createMediaStreamSource: vi.fn(() => source),
        createMediaStreamDestination: vi.fn(() => destination),
        resume: vi.fn().mockResolvedValue(undefined),
        close: vi.fn().mockResolvedValue(undefined),
      };
      const ctor = vi.fn(function () { return context; });
      (globalThis as any).AudioContext = ctor;
      return { context, ctor, source, destination, convertedTrack };
    }
    function spyProcessor() {
      const created = vi.fn();
      (globalThis as any).MediaStreamTrackProcessor = function (init: unknown) {
        created(init);
        return { readable: { getReader: () => mockReader } };
      };
      return created;
    }

    it('feeds the encoder a 48 kHz mono track made by a SAMPLE_RATE context', async () => {
      const { context, ctor, source, destination, convertedTrack } = installContext();
      const created = spyProcessor();
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBeNull();
      expect(ctor).toHaveBeenCalledWith({ sampleRate: SAMPLE_RATE });
      expect(source.connect).toHaveBeenCalledWith(destination);
      expect(destination.channelCount).toBe(CHANNELS);
      expect(destination.channelCountMode).toBe('explicit');
      expect(created).toHaveBeenCalledWith({ track: convertedTrack });
      expect(mockEncoder.configure).toHaveBeenCalledWith(
        expect.objectContaining({ sampleRate: SAMPLE_RATE, numberOfChannels: CHANNELS }),
      );
      m.stop();
      expect(context.close).toHaveBeenCalled();
      expect(mockTrack.stop).toHaveBeenCalled();
    });

    it('falls back to the raw track when the context cannot run at the encoder rate', async () => {
      const { context } = installContext(44100);
      const created = spyProcessor();
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBeNull();
      expect(created).toHaveBeenCalledWith({ track: mockTrack });
      expect(context.close).toHaveBeenCalled();
      m.stop();
    });

    it('uses the raw track when the context constructor refuses, and a retry still encodes', async () => {
      const { ctor } = installContext();
      ctor.mockImplementationOnce(function () { throw new DOMException('rate', 'NotSupportedError'); });
      const created = spyProcessor();
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBeNull();
      expect(created).toHaveBeenCalledWith({ track: mockTrack });
      m.stop();
      expect(await m.start()).toBeNull();
      expect(mockEncoder.configure).toHaveBeenCalledTimes(2);
      m.stop();
    });

    it('closes a half-built graph and uses the raw track when node setup throws', async () => {
      const { context } = installContext();
      context.createMediaStreamDestination.mockImplementation(() => { throw new Error('graph'); });
      const created = spyProcessor();
      vi.spyOn(console, 'warn').mockImplementation(() => {});
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBeNull();
      expect(created).toHaveBeenCalledWith({ track: mockTrack });
      expect(context.close).toHaveBeenCalled();
      m.stop();
    });

    it('ends the capture when the context cannot resume', async () => {
      const { context } = installContext();
      context.resume.mockRejectedValue(new Error('blocked'));
      const died = vi.fn();
      const m = new TxMic(vi.fn(), died);

      expect(await m.start()).toBeNull();
      await vi.waitFor(() => expect(died).toHaveBeenCalledWith(
        'TX MIC: audio context resume failed: Error: blocked',
      ));
      expect(m.active).toBe(false);
      expect(mockTrack.stop).toHaveBeenCalled();
      expect(context.close).toHaveBeenCalled();
    });

    it('ignores a resume failure from an Opus context a newer start retired', async () => {
      const first = installContext();
      let rejectOld!: (e: Error) => void;
      first.context.resume.mockReturnValue(new Promise((_, reject) => { rejectOld = reject; }));
      const died = vi.fn();
      const m = new TxMic(vi.fn(), died);
      expect(await m.start()).toBeNull();
      m.stop();
      installContext();
      expect(await m.start()).toBeNull();

      rejectOld(new Error('late'));
      await Promise.resolve(); await Promise.resolve();

      expect(died).not.toHaveBeenCalled();
      expect(m.active).toBe(true);
      m.stop();
    });

    it('ignores a resume failure from the Opus context a codec switch retired', async () => {
      const { context } = installContext();
      let rejectOld!: (e: Error) => void;
      context.resume.mockReturnValue(new Promise((_, reject) => { rejectOld = reject; }));
      (context as any).createScriptProcessor = vi.fn(() => ({
        connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null,
      }));
      (context as any).destination = {};
      const died = vi.fn();
      const m = new TxMic(vi.fn(), died);
      expect(await m.start()).toBeNull();
      expect(m.applyServerCodec('pcm16')).toEqual({ switched: true, error: null });

      rejectOld(new Error('late'));
      await Promise.resolve(); await Promise.resolve();

      expect(died).not.toHaveBeenCalled();
      expect(m.active).toBe(true);
      expect(m.codec).toBe('pcm16');
      m.stop();
    });

    it('does not stay half-started when the encoder refuses its configuration', async () => {
      installContext();
      mockEncoder.configure.mockImplementation(() => { throw new Error('unsupported config'); });
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBe('TX MIC: encoder configure failed: unsupported config');
      expect(m.active).toBe(false);
      expect(mockTrack.stop).toHaveBeenCalled();
    });

    it('keeps the PCM16 path at the encoder rate and mono', async () => {
      delete (globalThis as any).AudioEncoder;
      delete (globalThis as any).MediaStreamTrackProcessor;
      const { context, ctor } = installContext();
      const processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null };
      (context as any).createScriptProcessor = vi.fn(() => processor);
      (context as any).destination = {};
      const m = new TxMic(vi.fn());

      expect(await m.start()).toBeNull();
      expect(ctor).toHaveBeenCalledWith({ sampleRate: SAMPLE_RATE });
      expect((context as any).createScriptProcessor).toHaveBeenCalledWith(1024, CHANNELS, CHANNELS);
      m.stop();
    });
  });

  it('cleans up all resources on stop', async () => {
    const m = new TxMic(vi.fn()); await m.start(); m.stop();
    expect(mockTrack.stop).toHaveBeenCalled();
    expect(mockReader.cancel).toHaveBeenCalled();
    expect(mockEncoder.close).toHaveBeenCalled();
    expect(m.active).toBe(false);
  });
  it('is idempotent on double start', async () => {
    const m = new TxMic(vi.fn()); await m.start();
    expect(await m.start()).toBeNull();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1); m.stop();
  });

  it('falls back to PCM16 capture when WebCodecs are unavailable', async () => {
    delete (globalThis as any).AudioEncoder;
    delete (globalThis as any).MediaStreamTrackProcessor;
    let processor: any;
    const source = { connect: vi.fn(), disconnect: vi.fn() };
    const context = {
      sampleRate: SAMPLE_RATE,
      destination: {},
      createMediaStreamSource: vi.fn(() => source),
      createScriptProcessor: vi.fn(() => {
        processor = { connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null };
        return processor;
      }),
      close: vi.fn().mockResolvedValue(undefined),
    };
    (globalThis as any).AudioContext = vi.fn(function () { return context; });
    const sent = vi.fn();
    const m = new TxMic(sent);

    expect(await m.start()).toBeNull();
    processor.onaudioprocess({
      inputBuffer: { getChannelData: () => new Float32Array(960).fill(0.25) },
    });

    expect(sent).toHaveBeenCalledOnce();
    const packet = sent.mock.calls[0][0] as ArrayBuffer;
    const header = new DataView(packet);
    expect(header.getUint8(1)).toBe(CODEC_PCM16);
    expect(packet.byteLength).toBe(AUDIO_HEADER_SIZE + 960 * 2);
    // MOR-4312: the raw capture request and the captured samples reaching the
    // frame sink (the WebSocket send) in one pass.
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: expect.objectContaining({
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      }),
    });
    expect(header.getInt16(AUDIO_HEADER_SIZE, true)).toBe(Math.round(0.25 * 0x7fff));
    m.stop();
    expect(context.close).toHaveBeenCalled();
  });

  describe('secure context (MOR-2845)', () => {
    afterEach(() => {
      // jsdom on localhost reports a secure context; pin that back so the
      // fast-pool neighbours are unaffected by the insecure-context case.
      Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
    });

    it('blames the connection, not the browser, on a plain-HTTP page', async () => {
      Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true });
      expect(await new TxMic(vi.fn()).start()).toBe('TX MIC: insecure context');
    });

    it('still blames the browser when a secure context lacks getUserMedia', async () => {
      Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
      Object.defineProperty(globalThis, 'navigator', {
        value: {},
        writable: true, configurable: true,
      });
      expect(await new TxMic(vi.fn()).start()).toBe('TX MIC: microphone capture not supported');
    });
  });

  it('uses legacy WebKit getUserMedia for PCM16 fallback', async () => {
    delete (globalThis as any).AudioEncoder;
    delete (globalThis as any).MediaStreamTrackProcessor;
    const stream = { getTracks: () => [mockTrack], getAudioTracks: () => [mockTrack] };
    const legacyGetUserMedia = vi.fn((_constraints, success) => success(stream));
    Object.defineProperty(globalThis, 'navigator', {
      value: { webkitGetUserMedia: legacyGetUserMedia },
      writable: true,
      configurable: true,
    });
    const context = {
      sampleRate: SAMPLE_RATE,
      destination: {},
      createMediaStreamSource: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() })),
      createScriptProcessor: vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn(), onaudioprocess: null })),
      close: vi.fn().mockResolvedValue(undefined),
    };
    (globalThis as any).webkitAudioContext = vi.fn(function () { return context; });

    const m = new TxMic(vi.fn());

    expect(TxMic.supported()).toBe(true);
    expect(await m.start()).toBeNull();
    expect(legacyGetUserMedia).toHaveBeenCalledWith(
      {
        audio: {
        channelCount: CHANNELS, sampleRate: SAMPLE_RATE,
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
      },
      },
      expect.any(Function),
      expect.any(Function),
    );
    m.stop();
  });
});
