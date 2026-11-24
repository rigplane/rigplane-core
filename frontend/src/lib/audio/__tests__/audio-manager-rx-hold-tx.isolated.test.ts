/**
 * MOR-4591: receive playback is held for the whole transmission.
 *
 * The phone microphone is captured raw since MOR-4312 (no browser echo
 * canceller), so receive audio still queued in the jitter buffer at the PTT
 * press, or arriving while the radio keys, played from the speaker into the
 * open microphone and went out on the air as a short burst. The manager now
 * flushes the player at the press, drops receive frames while TX is wanted,
 * and re-primes the player when TX ends or fails to start.
 *
 * Wire contract: the `audio_start` request and every RX frame built here come
 * from tests/fixtures/audio_rx_session_wire.json, shared with the paired
 * handler test tests/test_web_audio_rx_wire_fixture.py (it drives the REAL
 * /api/v1/audio handler with the same request and verifies the emitted frame
 * against the same fixture), the same pairing as
 * audio-manager-hidden-burst.isolated.test.ts. The real AudioManager and the
 * real RxPlayer run over a fake AudioContext; only the microphone, the stores
 * and the socket are doubled, so the assertions observe what would be played.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SAMPLE_RATE } from '../constants';
import type { TxCaptureFailure, TxCodec, TxCodecSwitch, TxMic } from '../tx-mic';
import type * as audioStore from '../../stores/audio.svelte';
import type * as connectionStore from '../../stores/connection.svelte';
import type * as capabilitiesStore from '$lib/stores/capabilities.svelte';
import type { PttOperation } from '$lib/runtime/tx-controller/managed-controller';
import wire from '../../../../../tests/fixtures/audio_rx_session_wire.json';

type Runtime = typeof import('$lib/runtime/frontend-runtime').runtime;
const rxWire = wire.rx_frame_pcm16_mono_20ms;
const rxRequestWire = wire.audio_start_rx;

const txFailure = (code: TxCaptureFailure['code'], category: string, reason: string): TxCaptureFailure => ({
  code, category, reason, at: Date.now(),
});

const txStart = vi.fn((): Promise<TxCaptureFailure | null> => Promise.resolve(null));
const txStop = vi.fn();
const txApplyServerCodec = vi.fn(
  (_codec: TxCodec, _sampleRate: number): TxCodecSwitch => ({ switched: false, error: null }),
);

// The members AudioManager consumes, taken from the producer's own class
// so a changed member fails type checking here.
type ConsumedTxMic = Pick<TxMic,
  'start' | 'stop' | 'active' | 'captureEpoch' | 'codec' | 'lastCaptureFailureCategory'
  | 'applyServerCodec'>;

vi.mock('../tx-mic', () => ({
  TxMic: class implements ConsumedTxMic {
    start = txStart;
    stop = txStop;
    applyServerCodec = txApplyServerCodec;
    get active() { return true; }
    get codec(): TxCodec | null { return 'opus'; }
    get captureEpoch(): number { return 0; }
    get lastCaptureFailureCategory(): string | null { return null; }
    static supported() { return true; }
  },
}));

// Store doubles typed against the producer modules' own exported surface.
const audioStoreDouble = {
  setRxEnabled: vi.fn((_v: boolean) => {}),
  setTxEnabled: vi.fn((_v: boolean) => {}),
  setTxCodecFallback: vi.fn((_v: boolean) => {}),
  setRxError: vi.fn((_v: boolean) => {}),
  setTxError: vi.fn((_v: boolean) => {}),
} satisfies Pick<typeof audioStore,
  'setRxEnabled' | 'setTxEnabled' | 'setTxCodecFallback' | 'setRxError' | 'setTxError'>;
const connectionStoreDouble = {
  setAudioConnected: vi.fn((_v: boolean) => {}),
} satisfies Pick<typeof connectionStore, 'setAudioConnected'>;
const capabilitiesStoreDouble = {
  getCapabilities: vi.fn((): null => null),
  subscribeCapabilities: vi.fn((): (() => void) => () => {}),
} satisfies Pick<typeof capabilitiesStore, 'getCapabilities' | 'subscribeCapabilities'>;

vi.mock('../../stores/connection.svelte', () => connectionStoreDouble);
vi.mock('../../stores/audio.svelte', () => audioStoreDouble);
vi.mock('$lib/stores/capabilities.svelte', () => capabilitiesStoreDouble);
type TxAudioRuntime = Pick<Runtime, 'startTx' | 'stopTx' | 'onTxAudioDied'>;
let audioManagerForRuntime: TxAudioRuntime;
vi.mock('$lib/runtime/frontend-runtime', () => {
  const runtime = {
    startTx: () => audioManagerForRuntime.startTx(),
    stopTx: (options) => audioManagerForRuntime.stopTx(options),
    onTxAudioDied: (callback) => audioManagerForRuntime.onTxAudioDied(callback),
  } satisfies TxAudioRuntime;
  return { runtime };
});
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({ armModInputTxGuard: vi.fn() }));
vi.mock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
  autoSetLanModInputForTx: vi.fn(), restoreModInputAfterTx: vi.fn(),
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

function fakeBufferSource() {
  return {
    buffer: null as Pick<AudioBuffer, 'getChannelData' | 'duration'> | null,
    connect: vi.fn((_node: unknown) => {}), start: vi.fn((_time: number) => {}), stop: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  };
}

// Fake AudioContext from rx-player.test.ts: controllable currentTime,
// tracked sources with start/stop/addEventListener.
function installFakeAudioContext(): void {
  const srcs: ReturnType<typeof fakeBufferSource>[] = [];
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
      const src = fakeBufferSource();
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


function sources(): ReturnType<typeof fakeBufferSource>[] { return ctx._srcs; }
/** Sources still scheduled to play: started by the player and not stopped. */
function audible(): number {
  return sources().filter((src) => src.stop.mock.calls.length === 0).length;
}

/** Count nonzero samples whose connected source finished during this clock window. */
function renderedSignalSince(since: number): number {
  const [preGain, mainGain, , limiterInput] = ctx.createGain.mock.results.map(
    (result: { value: { gain: { value: number } } }) => result.value,
  );
  if (ctx.state !== 'running' || preGain.gain.value <= 0
    || mainGain.gain.value <= 0 || limiterInput.gain.value <= 0) return 0;
  let rendered = 0;
  for (const src of sources()) {
    const start = src.start.mock.calls[0]?.[0];
    if (start === undefined || src.buffer === null || src.stop.mock.calls.length > 0
      || src.connect.mock.calls[0]?.[0] !== preGain) continue;
    const end = start + src.buffer.duration;
    if (start >= since && end <= ctx.currentTime) {
      rendered += src.buffer.getChannelData(0).filter((sample) => sample !== 0).length;
    }
  }
  return rendered;
}

describe('receive playback is held while transmitting (MOR-4591)', () => {
  beforeEach(() => {
    vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers();
    rxSequence = 0;
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
    installFakeAudioContext();
  });
  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
    txStart.mockImplementation(() => Promise.resolve(null));
  });

  /** RX already playing on an open socket, as on a phone listening to the band. */
  async function listening(remote?: boolean) {
    const { audioManager } = await import('../audio-manager');
    if (remote !== undefined) {
      audioManager.setControllerBinding({
        remote: () => remote, ready: () => true, epoch: () => 7, current: (epoch) => epoch === 7,
        protocols: () => [`rigplane-controller-v1.${'a'.repeat(64)}`], role: () => 'primary',
      });
    }
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    expectRxSubscription(socket.sent);
    ctx.currentTime = 0;
    for (let i = 0; i < 5; i++) socket.onmessage?.({ data: rxFrame() });
    expect(sources()).toHaveLength(5);
    expect(audible()).toBe(5);
    return { audioManager, socket };
  }

  /** Frames received after this call: how many new sources got scheduled. */
  function scheduledAfter(socket: FakeWebSocket, frames = 3): number {
    const before = sources().length;
    for (let i = 0; i < frames; i++) socket.onmessage?.({ data: rxFrame() });
    return sources().length - before;
  }

  // The production Tower desktop bridge exposes a loopback WebSocket to
  // the WebView. Exercise that boundary, with the station and the bridge
  // represented by the fake socket. Both local backends use local binding.
  it.each([
    { route: 'Tower', remote: true, protocol: 'http:', host: '127.0.0.1:8080', skipped: 239 },
    { route: 'LAN-direct', remote: false, protocol: 'https:', host: 'station.test', skipped: 250 },
    { route: 'USB-local', remote: false, protocol: 'http:', host: '127.0.0.1:8080', skipped: 250 },
  ])('$route plays fresh RX within one second of a five-second PTT hold (MOR-4740)', async (route) => {
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
    vi.stubGlobal('location', { protocol: route.protocol, host: route.host });
    const { audioManager, socket } = await listening(route.remote);
    audioManagerForRuntime = audioManager;
    audioManager.setRxVolume(0.6);
    const origin = Date.now();
    const advance = async (ms: number) => {
      await vi.advanceTimersByTimeAsync(ms);
      ctx.currentTime = (Date.now() - origin) / 1000;
    };
    await advance(90);
    expect(renderedSignalSince(0)).toBeGreaterThan(0);

    const { getTxAudioControl } = await import('$lib/runtime/adapters/tx-adapter');
    const { ManagedTxController } = await import('$lib/runtime/tx-controller/managed-controller');
    const { projectManagedTx } = await import('$lib/runtime/tx-controller/managed-state');
    const audio = getTxAudioControl();
    let acceptOff!: (outcome: 'accepted') => void;
    const offAck = new Promise<'accepted'>((resolve) => { acceptOff = resolve; });
    const idle = { ...projectManagedTx(null, true), fresh: true, radioTx: 'off' as const };
    let state: ReturnType<typeof projectManagedTx> = idle;
    const sendPtt = vi.fn(async (operation: PttOperation): Promise<'accepted'> => {
      if (operation === 'ptt_on') {
        state = { ...idle, phase: 'active', intent: 'momentary', radioTx: 'on', txRisk: 'confirmed-on' };
      } else {
        await offAck;
        state = idle;
      }
      return 'accepted';
    });
    const controller = new ManagedTxController({
      snapshot: () => state,
      refresh: async () => {}, invalidate: () => {}, sendPtt,
      submit: async () => 'accepted', setTot: async () => {},
      startAudio: audio.startManagedTx, stopLocalAudio: audio.stopLocalAudio,
      onAudioDied: audio.onTxAudioDied,
    });

    try {
      controller.pttOn();
      await advance(0);
      expect(socket.sent).toContain(JSON.stringify({ type: 'audio_start', direction: 'tx' }));
      socket.onmessage?.({ data: JSON.stringify({ type: 'audio_tx_format', codec: 'opus', sample_rate: SAMPLE_RATE }) });
      await advance(0);
      expect(sendPtt.mock.calls).toEqual([['ptt_on']]);
      expect(audioManager.txEnabled).toBe(true);
      expect(controller.snapshot().radioTx).toBe('on');

      const pressedAt = ctx.currentTime;
      for (let i = route.skipped; i < 250; i++) {
        await advance(20);
        socket.onmessage?.({ data: rxFrame() });
      }
      await advance(route.skipped * 20);
      expect(renderedSignalSince(pressedAt)).toBe(0);
      expect(audible()).toBe(0);

      const releasedAt = ctx.currentTime;
      const releasing = controller.pttOff();
      expect(sendPtt.mock.calls).toEqual([['ptt_on'], ['ptt_off']]);
      expect(audioManager.txEnabled).toBe(false);
      expect(txStop).toHaveBeenCalledOnce();
      expect(socket.sent).toContain(JSON.stringify({ type: 'audio_stop', direction: 'tx' }));
      // RX can arrive before the terminal OFF response on the control socket.
      // Never send a separate PTT update on the audio channel.
      for (let i = 0; i < 10; i++) {
        await advance(20);
        socket.onmessage?.({ data: rxFrame() });
      }
      expect(renderedSignalSince(releasedAt)).toBeGreaterThan(0);
      acceptOff('accepted');
      await releasing;
      expect(controller.snapshot().radioTx).toBe('off');
      // The release window consumes fresh frames on the original socket and graph.
      expect(FakeWebSocket.instances).toEqual([socket]);
      expect(socket.readyState).toBe(FakeWebSocket.OPEN);
      expect(socket.sent).not.toContain(JSON.stringify({ type: 'audio_stop', direction: 'rx' }));
      expect(ctx.close).not.toHaveBeenCalled();
      expect(ctx.createGain.mock.results[0].value.gain.value).toBe(0.6);
    } finally {
      acceptOff('accepted');
      controller.dispose();
    }
  });

  it('flushes queued receive audio at the press and drops frames until TX ends', async () => {
    const { audioManager, socket } = await listening();
    let settle!: (failure: TxCaptureFailure | null) => void;
    txStart.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve; }));

    const starting = audioManager.startTx();
    // At the press, before the microphone has even opened: the queued
    // receive audio is stopped so it cannot play into the microphone.
    expect(audible()).toBe(0);
    expect(scheduledAfter(socket)).toBe(0);

    settle(null);
    expect(await starting).toBeNull();
    expect(audioManager.txEnabled).toBe(true);
    expect(scheduledAfter(socket)).toBe(0);

    audioManager.stopTx();
    // Playback resumes on release.
    expect(scheduledAfter(socket)).toBe(3);
    expect(audible()).toBe(3);
  });

  it('gives receive audio back when the microphone refuses to start', async () => {
    const { audioManager, socket } = await listening();
    const refused = txFailure('permission', 'NotAllowedError', 'permission denied');
    txStart.mockImplementationOnce(() => Promise.resolve(refused));
    expect(await audioManager.startTx()).toBe(refused.reason);
    expect(audioManager.txEnabled).toBe(false);
    expect(scheduledAfter(socket)).toBe(3);
  });

  it('a superseded start does not end the hold of the newer press', async () => {
    const { audioManager, socket } = await listening();
    const settles: ((failure: TxCaptureFailure | null) => void)[] = [];
    txStart.mockImplementation(() => new Promise((resolve) => { settles.push(resolve); }));
    const first = audioManager.startTx();
    audioManager.stopTx();
    const second = audioManager.startTx();
    settles[0](null);
    expect(await first).toBe('TX MIC: capture start cancelled');
    expect(scheduledAfter(socket)).toBe(0);
    settles[1](null);
    expect(await second).toBeNull();
    expect(scheduledAfter(socket)).toBe(0);
    audioManager.stopTx();
    expect(scheduledAfter(socket)).toBe(3);
  });
});
