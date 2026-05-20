import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { TxMic } from '../tx-mic';
import { SAMPLE_RATE, CHANNELS, TX_BITRATE, AUDIO_HEADER_SIZE, CODEC_PCM16 } from '../constants';

let mockTrack: any, mockEncoder: any, mockReader: any;
beforeEach(() => {
  delete (globalThis as any).AudioContext;
  delete (globalThis as any).webkitAudioContext;
  mockTrack = { stop: vi.fn(), kind: 'audio' };
  mockReader = { read: vi.fn(() => Promise.resolve({ done: true })), cancel: vi.fn().mockResolvedValue(undefined) };
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
afterEach(() => {
  delete (globalThis as any).AudioEncoder;
  delete (globalThis as any).MediaStreamTrackProcessor;
  delete (globalThis as any).AudioContext;
  delete (globalThis as any).webkitAudioContext;
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
  it('requests mic with correct constraints', async () => {
    const m = new TxMic(vi.fn()); await m.start();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: { channelCount: CHANNELS, sampleRate: SAMPLE_RATE, echoCancellation: true, noiseSuppression: true },
    });
    expect(m.active).toBe(true); m.stop();
  });
  it('handles permission denied', async () => {
    (navigator.mediaDevices.getUserMedia as any).mockRejectedValueOnce(new Error('denied'));
    expect(await new TxMic(vi.fn()).start()).toContain('permission denied');
  });
  it('configures encoder correctly', async () => {
    const m = new TxMic(vi.fn()); await m.start();
    expect(mockEncoder.configure).toHaveBeenCalledWith({
      codec: 'opus', sampleRate: SAMPLE_RATE, numberOfChannels: CHANNELS, bitrate: TX_BITRATE,
    });
    m.stop();
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
    m.stop();
    expect(context.close).toHaveBeenCalled();
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
        audio: { channelCount: CHANNELS, sampleRate: SAMPLE_RATE, echoCancellation: true, noiseSuppression: true },
      },
      expect.any(Function),
      expect.any(Function),
    );
    m.stop();
  });
});
