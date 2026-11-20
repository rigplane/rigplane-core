/**
 * Compatibility tests for the data-channel attach acknowledgement
 * (MOR-4478): the server under review will send one extra text frame
 * `{"type": "attached", "v": 1, "channel": "audio"}` as the first frame on the
 * audio channel. Today's shipped frontend must ignore it: no state change,
 * no error, no reconnect, and normal handling of the following audio
 * format acknowledgement and RX audio frames.
 *
 * Drives the real audio-socket message path in audio-manager.ts exactly
 * the way audio-manager.isolated.test.ts does (FakeWebSocket through the
 * real WebSocketTransport text/binary split).
 *
 * Producer contracts: the ack frame and the audio TX-format ack are
 * sent verbatim from the committed api-channel golden documents (built
 * by the server producer and replayed through the real handlers by
 * tests/test_api_channel_golden.py), and every
 * module double implements the Pick of its producer class that
 * AudioManager consumes, so a changed member fails svelte-check here.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RxAudioFocus, RxPlaybackDiagnostics, RxPlayer } from '../rx-player';
import type { TxCodec, TxCodecSwitch, TxMic } from '../tx-mic';
import type * as audioStore from '../../stores/audio.svelte';
import type * as connectionStore from '../../stores/connection.svelte';
import type * as capabilitiesStore from '$lib/stores/capabilities.svelte';
import apiChannelGolden from '../../transport/api-channel-golden.json';
import apiChannelWire from '../../transport/api-channel-wire.json';

/** The producer-built golden document for this channel's attach ack. */
const attachAckAudio = apiChannelGolden.documents.attach_ack_audio;
/** The committed wire contract's own `type` row for the attach ack. */
const wireAttachAckType = JSON.parse(String(
  apiChannelWire.payloads.attach_ack.fields.find(([name]) => name === 'type')?.[1] ?? 'null',
));
/** The producer-built golden document for the audio TX-format ack. */
const txFormatAudio = apiChannelGolden.documents.audio_tx_format_opus;

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

describe('audio channel attach acknowledgement compatibility (MOR-4478)', () => {
  beforeEach(() => {
    vi.resetModules(); vi.clearAllMocks(); vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'https:', host: 'station.test' });
  });
  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.useRealTimers();
  });

  async function rig() {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    audioManager.setControllerBinding({
      remote: () => true, ready: () => true, epoch: () => 7, current: () => true,
      protocols: () => [`rigplane-controller-v1.${'a'.repeat(64)}`],
      role: () => 'primary',
    });
    return { audioManager, setRxError: vi.mocked(audio.setRxError) };
  }

  it('ignores the golden {"type":"attached"} frame then feeds the format ack and RX frames normally', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { audioManager, setRxError } = await rig();
    const starting = audioManager.startTx();
    await vi.advanceTimersByTimeAsync(0);
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const sentBefore = [...socket.sent];

    // The attach ack is the first frame the changed server sends: the
    // golden document's text verbatim, exactly the bytes the producer
    // emits. A golden file that stops describing a server-to-client text
    // frame whose type is the wire contract's attach-ack discriminator
    // fails loudly here.
    expect(attachAckAudio.variant).toBe('text');
    expect(attachAckAudio.direction).toBe('server->client');
    const ackFrame = JSON.parse(attachAckAudio.text) as Record<string, unknown>;
    expect(ackFrame.type).toBe(wireAttachAckType);
    expect(ackFrame.channel).toBe('audio');
    socket.onmessage?.({ data: attachAckAudio.text });
    expect(audioManager.rxEnabled).toBe(true);
    expect(audioManager.txEnabled).toBe(true);
    expect(audioManager.txCodecFallback).toBe(false);
    expect(setRxError).not.toHaveBeenCalledWith(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    expect(socket.sent).toEqual(sentBefore);
    expect(rxFed).not.toHaveBeenCalled();

    // Normal handling afterwards: the golden TX-format ack — the exact
    // bytes the audio handler's own typed producer sends (built by the
    // server, replayed through the real stack by
    // tests/test_api_channel_golden.py) — resolves the pending start.
    // A golden file that stops describing a server-to-client text frame
    // whose type is the audio TX-format discriminator fails loudly
    // here; codec and sample rate are read from the document, never
    // re-typed as literals.
    expect(txFormatAudio.variant).toBe('text');
    expect(txFormatAudio.direction).toBe('server->client');
    const txFormatFrame = JSON.parse(txFormatAudio.text) as Record<string, unknown>;
    expect(txFormatFrame.type).toBe('audio_tx_format');
    socket.onmessage?.({ data: txFormatAudio.text });
    expect(await starting).toBeNull();
    expect(txApplyServerCodec).toHaveBeenCalledWith(
      txFormatFrame.codec, txFormatFrame.sample_rate,
    );
    expect(audioManager.txCodecFallback).toBe(false);

    // And an RX binary audio frame reaches the player untouched once the
    // transmission ends (MOR-4591 holds receive playback while TX is on).
    audioManager.stopTx();
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    const frame = new ArrayBuffer(512);
    socket.onmessage?.({ data: frame });
    expect(rxFed).toHaveBeenCalledTimes(1);
    expect(rxFed.mock.lastCall?.[0]).toBe(frame);

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
