/**
 * Compatibility tests for the data-channel attach acknowledgement
 * (MOR-4478): the server under review will send one extra text frame
 * `{"type":"attached","v":1,"channel":"audio"}` as the first frame on the
 * audio channel. Today's shipped frontend must ignore it: no state change,
 * no error, no reconnect, and normal handling of the following audio
 * format acknowledgement and RX audio frames.
 *
 * Drives the real audio-socket message path in audio-manager.ts exactly
 * the way audio-manager.isolated.test.ts does (FakeWebSocket through the
 * real WebSocketTransport text/binary split).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RxPlaybackDiagnostics, RxPlayer } from '../rx-player';

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
const txStart = vi.fn().mockResolvedValue(null);
const txStop = vi.fn();
const txApplyServerCodec = vi.fn(() => ({ switched: false, error: null }));

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    start = rxStart;
    stop = rxStop;
    flush = rxFlush;
    feed = rxFed;
    setJitterBounds = rxSetJitterBounds;
    stats = rxStats;
    diagnosticSnapshot = rxDiagnosticSnapshot;
  },
}));

vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = txStart;
    stop = txStop;
    applyServerCodec = txApplyServerCodec;
    get active() { return true; }
    static supported() { return true; }
  },
}));

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
  subscribeCapabilities: vi.fn(() => () => {}),
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

  function serverText(socket: FakeWebSocket, message: Record<string, unknown>) {
    socket.onmessage?.({ data: JSON.stringify(message) });
  }

  async function rig() {
    const { audioManager } = await import('../audio-manager');
    const audio = await import('../../stores/audio.svelte');
    audioManager.setControllerBinding({
      remote: () => true, ready: () => true, epoch: () => 7, current: () => true,
      protocols: () => [`rigplane-controller-v1.${'a'.repeat(64)}`],
    });
    return { audioManager, setRxError: vi.mocked(audio.setRxError) };
  }

  it('ignores {"type":"attached"} then feeds the format ack and RX frames normally', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const { audioManager, setRxError } = await rig();
    const starting = audioManager.startTx();
    await vi.advanceTimersByTimeAsync(0);
    audioManager.startRx();
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const sentBefore = [...socket.sent];

    // The attach ack is the first frame the changed server sends.
    serverText(socket, { type: 'attached', v: 1, channel: 'audio' });
    expect(audioManager.rxEnabled).toBe(true);
    expect(audioManager.txEnabled).toBe(true);
    expect(audioManager.txCodecFallback).toBe(false);
    expect(setRxError).not.toHaveBeenCalledWith(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket.readyState).toBe(FakeWebSocket.OPEN);
    expect(socket.sent).toEqual(sentBefore);
    expect(rxFed).not.toHaveBeenCalled();

    // Normal handling afterwards: the TX format ack resolves the pending start.
    serverText(socket, { type: 'audio_tx_format', codec: 'opus', sample_rate: 48000 });
    expect(await starting).toBeNull();
    expect(txApplyServerCodec).toHaveBeenCalledWith('opus', 48000);
    expect(audioManager.txCodecFallback).toBe(false);

    // And an RX binary audio frame reaches the player untouched.
    const frame = new ArrayBuffer(512);
    socket.onmessage?.({ data: frame });
    expect(rxFed).toHaveBeenCalledTimes(1);
    expect(rxFed.mock.lastCall?.[0]).toBe(frame);

    expect(errorSpy).not.toHaveBeenCalled();
  });
});
