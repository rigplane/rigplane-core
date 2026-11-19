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
 * Same module doubles as audio-manager-attach-ack.isolated.test.ts: every
 * double implements the Pick of its producer class that AudioManager
 * consumes, and the socket goes through the real WebSocketTransport.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RxAudioFocus, RxPlaybackDiagnostics, RxPlayer } from '../rx-player';
import type { TxCodec, TxCodecSwitch, TxMic } from '../tx-mic';
import type * as audioStore from '../../stores/audio.svelte';
import type * as connectionStore from '../../stores/connection.svelte';
import type * as capabilitiesStore from '$lib/stores/capabilities.svelte';

const rxStart = vi.fn();
const rxStop = vi.fn();
const rxFlush = vi.fn();
const rxFed = vi.fn();
const rxSetJitterBounds = vi.fn();
const rxConstruct = vi.fn();
const rxDiagnosticSnapshot = vi.fn((): ReturnType<RxPlayer['diagnosticSnapshot']> => ({
  preRouting: null, decodedStatus: 'fresh', decodedAgeMs: 0,
  volume: 1, focus: 'both', splitStereo: false, preGain: 1, mainGain: 1, subGain: 1,
  sinkSelection: 'unsupported',
  underruns: 0, bufferDepthMs: 0, droppedFrames: 0,
  playback,
}));

const playback: RxPlaybackDiagnostics = {
  schemaVersion: 1, contextState: 'running', sampleRate: 48000, receivedFrames: 0,
  validFrames: 0, decodedFrames: 0, pcmPeak: null, scheduledFrames: 0, suspendedDrops: 0,
  resumeOutcome: 'resolved', lastResumeError: null,
};
const rxStats = vi.fn((): ReturnType<RxPlayer['stats']> => ({
  underruns: 0, bufferDepthMs: 0, droppedFrames: 0, playback,
}));
const txStart = vi.fn((): Promise<string | null> => Promise.resolve(null));
const txStop = vi.fn();
const txApplyServerCodec = vi.fn(
  (_codec: TxCodec, _sampleRate: number): TxCodecSwitch => ({ switched: false, error: null }),
);

// The members AudioManager consumes, taken from the producer's own classes
// so a changed member fails type checking here (same binding as
// audio-manager.isolated.test.ts, review round 1).
type ConsumedRxPlayer = Pick<RxPlayer,
  'start' | 'stop' | 'flush' | 'feed' | 'setJitterBounds' | 'stats' | 'diagnosticSnapshot'
  | 'setFocus' | 'setSplitStereo' | 'setChannelGainDb'
  | 'focus' | 'splitStereo' | 'mainGainDb' | 'subGainDb' | 'volume'>;
type ConsumedTxMic = Pick<TxMic,
  'start' | 'stop' | 'active' | 'captureEpoch' | 'codec' | 'lastCaptureFailureCategory'
  | 'applyServerCodec'>;

vi.mock('../rx-player', () => ({
  RxPlayer: class implements ConsumedRxPlayer {
    constructor(enabled: boolean) { rxConstruct(enabled); }
    start = rxStart;
    stop = rxStop;
    flush = rxFlush;
    feed = rxFed;
    setJitterBounds = rxSetJitterBounds;
    stats = rxStats;
    diagnosticSnapshot = rxDiagnosticSnapshot;
    setFocus = vi.fn((_focus: RxAudioFocus) => {});
    setSplitStereo = vi.fn((_on: boolean) => {});
    setChannelGainDb = vi.fn((_channel: 'main' | 'sub', _db: number) => {});
    get focus(): RxAudioFocus { return 'both'; }
    get splitStereo() { return false; }
    get mainGainDb() { return 0; }
    get subGainDb() { return 0; }
    get volume() { return 1; }
    set volume(_value: number) {}
  },
}));

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

  send(data: unknown) { this.sent.push(data); }
  close() { this.readyState = 3; }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
}

describe('receive playback is held while transmitting (MOR-4591)', () => {
  beforeEach(() => {
    vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
  });
  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.unstubAllGlobals(); vi.useRealTimers();
    txStart.mockImplementation(() => Promise.resolve(null));
  });

  /** RX already playing on an open socket, as on a phone listening to the band. */
  async function listening() {
    const { audioManager } = await import('../audio-manager');
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).toHaveBeenCalledTimes(1);
    rxFed.mockClear(); rxFlush.mockClear();
    return { audioManager, socket };
  }

  it('flushes queued receive audio at the press and drops frames until TX ends', async () => {
    const { audioManager, socket } = await listening();
    let settle!: (error: string | null) => void;
    txStart.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve; }));

    const starting = audioManager.startTx();
    // At the press, before the microphone has even opened: the scheduled
    // receive audio is stopped so it cannot play into the microphone.
    expect(rxFlush).toHaveBeenCalledTimes(1);
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).not.toHaveBeenCalled();

    settle(null);
    expect(await starting).toBeNull();
    expect(audioManager.txEnabled).toBe(true);
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).not.toHaveBeenCalled();

    audioManager.stopTx();
    // Re-primed on release, so the first frame after TX is not an underrun.
    expect(rxFlush).toHaveBeenCalledTimes(2);
    const frame = new ArrayBuffer(64);
    socket.onmessage?.({ data: frame });
    expect(rxFed).toHaveBeenCalledTimes(1);
    expect(rxFed.mock.lastCall?.[0]).toBe(frame);
  });

  it('gives receive audio back when the microphone refuses to start', async () => {
    const { audioManager, socket } = await listening();
    txStart.mockImplementationOnce(() => Promise.resolve('TX MIC: permission denied'));
    expect(await audioManager.startTx()).toBe('TX MIC: permission denied');
    expect(audioManager.txEnabled).toBe(false);
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).toHaveBeenCalledTimes(1);
  });

  it('a superseded start does not end the hold of the newer press', async () => {
    const { audioManager, socket } = await listening();
    const settles: ((error: string | null) => void)[] = [];
    txStart.mockImplementation(() => new Promise((resolve) => { settles.push(resolve); }));
    const first = audioManager.startTx();
    audioManager.stopTx();
    const second = audioManager.startTx();
    settles[0](null);
    expect(await first).toBe('TX MIC: capture start cancelled');
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).not.toHaveBeenCalled();
    settles[1](null);
    expect(await second).toBeNull();
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).not.toHaveBeenCalled();
    audioManager.stopTx();
    socket.onmessage?.({ data: new ArrayBuffer(64) });
    expect(rxFed).toHaveBeenCalledTimes(1);
  });
});
