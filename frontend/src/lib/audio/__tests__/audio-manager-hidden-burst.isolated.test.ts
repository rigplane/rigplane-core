// Wire contract: the `audio_start` request and the RX frame bytes built
// here come from tests/fixtures/audio_rx_session_wire.json, shared with the
// paired handler test tests/test_web_audio_rx_wire_fixture.py. That Python
// test drives the REAL /api/v1/audio handler with the same request and
// verifies the emitted binary frame against the same fixture, so a server
// header-format regression fails there instead of leaving this spec green on
// fabricated replies (owner's paired-handler test rule).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SAMPLE_RATE } from '../constants';
import wire from '../../../../../tests/fixtures/audio_rx_session_wire.json';

const rxWire = wire.rx_frame_pcm16_mono_20ms;
const rxRequestWire = wire.audio_start_rx;

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('../../stores/audio.svelte', () => ({
  setRxEnabled: vi.fn(),
  setTxEnabled: vi.fn(),
  setTxCodecFallback: vi.fn(),
  setRxError: vi.fn(),
  setTxError: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
}));

// Same shape as audio-manager.isolated.test.ts, but ../rx-player is left
// real so the hidden-burst scenario drives the real audio session.
vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = vi.fn().mockResolvedValue(null);
    stop = vi.fn();
    applyServerCodec = vi.fn(() => ({ switched: false, error: null }));
    get active() { return true; }
    get lastCaptureFailureCategory() { return null; }
    get captureEpoch() { return 0; }
    get codec() { return null; }
    static supported() { return true; }
    constructor(
      _send: (data: ArrayBuffer) => void,
      _onCaptureDied?: (reason: string) => void,
    ) {}
  },
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

  /** Simulate the server dropping this socket: fires onclose so the manager's reconnect path runs. */
  serverClose(code = 1006, reason = 'rearm') {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
}

let ctx: any;

// Fake AudioContext from rx-player.test.ts: controllable currentTime,
// tracked sources with start/stop/addEventListener.
function installFakeAudioContext(): void {
  const srcs: any[] = [];
  ctx = {
    state: 'running', sampleRate: SAMPLE_RATE, currentTime: 0, destination: {},
    resume: vi.fn().mockResolvedValue(undefined),
    close: vi.fn().mockResolvedValue(undefined),
    createGain: vi.fn(() => ({ gain: { value: 1 }, connect: vi.fn() })),
    createStereoPanner: vi.fn(() => ({ pan: { value: 0 }, connect: vi.fn() })),
    createChannelSplitter: vi.fn(() => ({ connect: vi.fn() })),
    createWaveShaper: vi.fn(() => ({ curve: null, oversample: 'none', connect: vi.fn() })),
    createBuffer: vi.fn((channels: number, frameCount: number, rate: number) => {
      const planes = Array.from({ length: channels }, () => new Float32Array(frameCount));
      return {
        duration: frameCount / rate, length: frameCount, numberOfChannels: channels,
        getChannelData: (channel: number) => planes[channel],
      };
    }),
    _srcs: srcs,
    createBufferSource: vi.fn(() => {
      const src = {
        buffer: null, connect: vi.fn(), start: vi.fn(), stop: vi.fn(),
        addEventListener: vi.fn(), removeEventListener: vi.fn(),
      };
      srcs.push(src);
      return src;
    }),
  };
  vi.stubGlobal('AudioContext', function () { return ctx; } as any);
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

// One 20 ms mono PCM16 RX frame built from the shared wire fixture: header
// bytes from `headerHexWithSequence0`, payload from the payload rule
// (`sample[i] = i % 100` as int16 LE). Only the sequence field varies.
let rxSequence = 0;
function rxFrame(): ArrayBuffer {
  const header = hexToBytes(rxWire.headerHexWithSequence0);
  new DataView(header.buffer).setUint16(2, rxSequence & 0xffff, true);
  rxSequence += 1;
  const samples = rxWire.payload.samples;
  const buf = new ArrayBuffer(header.length + samples * 2);
  new Uint8Array(buf).set(header, 0);
  const view = new DataView(buf);
  for (let i = 0; i < samples; i++) {
    view.setInt16(header.length + i * 2, i % 100, true);
  }
  return buf;
}

/** The audio request the manager sent must match the shared fixture: the
 *  stable subset equal, and the key set exactly stable + variable keys. */
function expectRxSubscription(sent: unknown[]): void {
  const stable = rxRequestWire.stable as Record<string, unknown>;
  const expectedKeys = [...Object.keys(stable), ...rxRequestWire.variableKeys].sort();
  const match = sent
    .map((m) => JSON.parse(String(m)) as Record<string, unknown>)
    .find((m) => Object.keys(stable).every((k) => m[k] === stable[k]));
  expect(match).toBeDefined();
  expect(Object.keys(match as Record<string, unknown>).sort()).toEqual(expectedKeys);
}

function setHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
}

function startTimes(): number[] {
  return ctx._srcs.map((src: any) => src.start.mock.calls[0][0]);
}

async function startRxWithOpenSocket() {
  const { audioManager } = await import('../audio-manager');
  audioManager.startRx();
  const ws = FakeWebSocket.instances[0];
  ws.open();
  // The session subscribes to RX with the audio request before any frame.
  expectRxSubscription(ws.sent);
  return { audioManager, ws };
}

describe('hidden-tab burst through the real audio session (MOR-3965)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    rxSequence = 0;
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
    installFakeAudioContext();
  });

  afterEach(async () => {
    delete (document as any).hidden;
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('schedules a hidden 2 s burst with no gaps, then trims to the foreground ceiling on visible', async () => {
    const { audioManager, ws } = await startRxWithOpenSocket();
    setHidden(true);
    document.dispatchEvent(new Event('visibilitychange'));
    ctx.currentTime = 0;
    for (let i = 0; i < 100; i++) ws.onmessage?.({ data: rxFrame() });

    const stats = (audioManager as any).rxPlayer.stats();
    expect(stats.playback.scheduledFrames).toBe(100);
    expect(stats.droppedFrames).toBe(0);
    expect(stats.underruns).toBe(0);
    const starts = startTimes();
    expect(starts).toHaveLength(100);
    expect(starts[0]).toBeCloseTo(0.4, 5);
    for (let i = 1; i < starts.length; i++) {
      expect(starts[i] - starts[i - 1]).toBeCloseTo(0.02, 6);
    }

    setHidden(false);
    ctx.currentTime = 1.003;
    document.dispatchEvent(new Event('visibilitychange'));
    const limit = 1.003 + 0.3;
    // A buffer is trimmed when it has not started and would end beyond the
    // foreground ceiling; anything already started or ending inside it stays.
    let stopped = 0;
    for (let i = 0; i < starts.length; i++) {
      const notStarted = starts[i] > 1.003;
      const endsBeyond = starts[i] + 0.02 > limit;
      if (notStarted && endsBeyond) {
        expect(ctx._srcs[i].stop).toHaveBeenCalledTimes(1);
        stopped++;
      } else {
        expect(ctx._srcs[i].stop).not.toHaveBeenCalled();
      }
    }
    expect(stopped).toBe(55);
    expect((audioManager as any).rxPlayer.stats().droppedFrames).toBe(55);

    ws.onmessage?.({ data: rxFrame() });
    expect(ctx._srcs[100].start.mock.calls[0][0]).toBeCloseTo(1.3, 5);
    expect(ctx._srcs[100].stop).not.toHaveBeenCalled();
    expect((audioManager as any).rxPlayer.stats().underruns).toBe(0);
    audioManager.stopRx();
  });

  it('stops the pending hidden backlog on reconnect so old and new audio cannot overlap', async () => {
    const { audioManager, ws } = await startRxWithOpenSocket();
    setHidden(true);
    ctx.currentTime = 0;
    for (let i = 0; i < 100; i++) ws.onmessage?.({ data: rxFrame() });
    expect(ctx._srcs).toHaveLength(100);

    ws.serverClose();
    vi.advanceTimersByTime(600);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const fresh = FakeWebSocket.instances[1];
    fresh.open(); // AudioManager re-subscribes and calls rxPlayer.flush().
    // The reconnect re-subscribes to RX through the audio request.
    expectRxSubscription(fresh.sent);

    for (const src of ctx._srcs.slice(0, 100)) {
      expect(src.stop).toHaveBeenCalledTimes(1);
    }
    expect((audioManager as any).rxPlayer.stats().droppedFrames).toBe(0);

    ctx.currentTime = 5;
    fresh.onmessage?.({ data: rxFrame() });
    expect(ctx._srcs[100].start.mock.calls[0][0]).toBeCloseTo(5.4, 5);
    expect((audioManager as any).rxPlayer.stats().underruns).toBe(0);
    audioManager.stopRx();
  });

  it('drops the same burst past the foreground ceiling while visible', async () => {
    const { audioManager, ws } = await startRxWithOpenSocket();
    setHidden(false);
    ctx.currentTime = 0;
    for (let i = 0; i < 100; i++) ws.onmessage?.({ data: rxFrame() });
    // Foreground bounds are unchanged: starts 0.05..0.29 fit, the rest drop.
    expect((audioManager as any).rxPlayer.stats().playback.scheduledFrames).toBe(13);
    expect((audioManager as any).rxPlayer.stats().droppedFrames).toBe(87);
    audioManager.stopRx();
  });
});
