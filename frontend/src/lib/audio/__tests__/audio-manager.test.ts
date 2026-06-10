import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const rxStart = vi.fn();
const rxStop = vi.fn();
const rxFlush = vi.fn();
const rxSetJitterBounds = vi.fn();
const rxStats = vi.fn(() => ({ underruns: 3, bufferDepthMs: 140, droppedFrames: 2 }));
const txStart = vi.fn().mockResolvedValue(null);
const txStop = vi.fn();

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    start = rxStart;
    stop = rxStop;
    flush = rxFlush;
    setJitterBounds = rxSetJitterBounds;
    stats = rxStats;
    setFocus = vi.fn();
    setSplitStereo = vi.fn();
    setChannelGainDb = vi.fn();
    get focus() { return 'main'; }
    get splitStereo() { return true; }
    get mainGainDb() { return 0; }
    get subGainDb() { return 0; }
    set volume(_value: number) {}
  },
}));

vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = txStart;
    stop = txStop;
    static supported() { return true; }
  },
}));

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('../../stores/audio.svelte', () => ({
  setRxEnabled: vi.fn(),
  setTxEnabled: vi.fn(),
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
}

describe('AudioManager websocket subscriptions', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  it('sends rx audio_start when startRx is called after config already opened the websocket', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.setAudioConfig({ focus: 'main', split_stereo: true });
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    audioManager.startRx();

    expect(ws.sent).toContain(JSON.stringify({
      type: 'audio_start',
      direction: 'rx',
      preferred_rx_codec: 'pcm16',
    }));
  });

  it('sends rx audio_stop before closing an open websocket', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    audioManager.stopRx();

    expect(ws.sent).toContain(JSON.stringify({ type: 'audio_stop', direction: 'rx' }));
  });

  it('requests opus when AudioDecoder is available', async () => {
    vi.stubGlobal('AudioDecoder', class {});
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    expect(ws.sent).toContain(JSON.stringify({
      type: 'audio_start',
      direction: 'rx',
      preferred_rx_codec: 'opus',
    }));
  });

  it('requests pcm16 inside the Tauri shell even when AudioDecoder is available', async () => {
    vi.stubGlobal('AudioDecoder', class {});
    vi.stubGlobal('__TAURI_INTERNALS__', {});
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    expect(ws.sent).toContain(JSON.stringify({
      type: 'audio_start',
      direction: 'rx',
      preferred_rx_codec: 'pcm16',
    }));
  });
});

describe('AudioManager audio_stats uplink (MOR-585)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllGlobals();
    vi.useFakeTimers();
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function statsMessages(ws: FakeWebSocket): unknown[] {
    return ws.sent
      .map((s) => JSON.parse(s as string))
      .filter((m) => m.type === 'audio_stats');
  }

  it('sends periodic audio_stats with player counters while RX is active', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    vi.advanceTimersByTime(1500);
    expect(statsMessages(ws)).toEqual([{
      type: 'audio_stats',
      underruns: 3,
      buffer_depth_ms: 140,
      dropped_frames: 2,
    }]);

    vi.advanceTimersByTime(3000);
    expect(statsMessages(ws).length).toBe(3);  // low rate: one per 1.5 s

    audioManager.stopRx();
  });

  it('does not send audio_stats when only TX is active', async () => {
    const { audioManager } = await import('../audio-manager');

    await audioManager.startTx();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    ws.sent = [];

    vi.advanceTimersByTime(5000);
    expect(statsMessages(ws)).toEqual([]);

    audioManager.stopTx();
  });

  it('clears the stats timer on close — no timer leak after stopRx', async () => {
    const { audioManager } = await import('../audio-manager');

    audioManager.startRx();
    const ws = FakeWebSocket.instances[0];
    ws.open();

    audioManager.stopRx();
    ws.sent = [];
    vi.advanceTimersByTime(10000);
    expect(statsMessages(ws)).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });
});
