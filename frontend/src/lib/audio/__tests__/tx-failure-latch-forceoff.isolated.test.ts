import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const rxStart = vi.fn();
const rxStop = vi.fn();

vi.mock('../rx-player', () => ({
  RxPlayer: class {
    constructor(_enabled: boolean) {}
    start = rxStart;
    stop = rxStop;
    flush = vi.fn();
    feed = vi.fn();
    setJitterBounds = vi.fn();
    stats = vi.fn(() => ({ underruns: 0, bufferDepthMs: 0, droppedFrames: 0 }));
    diagnosticSnapshot = vi.fn(() => null);
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

const txStart = vi.fn().mockResolvedValue(null);
const txStop = vi.fn();
let txCaptureDied: ((reason: string) => void) | null = null;

vi.mock('../tx-mic', () => ({
  TxMic: class {
    start = txStart;
    stop = txStop;
    applyServerCodec = vi.fn(() => ({ switched: false, error: null }));
    get active() { return true; }
    get lastCaptureFailureCategory() { return null; }
    static supported() { return true; }
    constructor(
      _send: (data: ArrayBuffer) => void,
      onCaptureDied?: (reason: string) => void,
    ) {
      txCaptureDied = onCaptureDied ?? null;
    }
  },
}));

vi.mock('../../stores/connection.svelte', () => ({
  setAudioConnected: vi.fn(),
}));

vi.mock('../../runtime/adapters/mod-input-tx-guard.svelte', () => ({
  armModInputTxGuard: vi.fn(),
}));

vi.mock('../../runtime/adapters/mod-input-auto.svelte', () => ({
  autoSetLanModInputForTx: vi.fn(),
  restoreModInputAfterTx: vi.fn(),
}));

/** Forwards the adapter seam to the REAL AudioManager (installed per test),
 *  preserving the automatic/explicit origin argument. */
const h = vi.hoisted(() => ({
  manager: null as null | {
    startTx(): Promise<string | null>;
    stopTx(options?: { automatic?: boolean }): void;
    onTxAudioDied(callback: () => void): () => void;
  },
}));

vi.mock('../../runtime/frontend-runtime', () => ({
  runtime: {
    startTx: () => h.manager!.startTx(),
    stopTx: (options?: { automatic?: boolean }) => h.manager!.stopTx(options),
    onTxAudioDied: (callback: () => void) => h.manager!.onTxAudioDied(callback),
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
}

describe('TX failure latch across automatic ForceOff (MOR-3753)', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    txStart.mockResolvedValue(null);
    txCaptureDied = null;
    h.manager = null;
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:5173' });
  });

  afterEach(async () => {
    const { audioManager } = await import('../audio-manager');
    audioManager.destroy();
    vi.unstubAllGlobals();
  });

  async function rig() {
    const { audioManager } = await import('../audio-manager');
    const store = await import('../../stores/audio.svelte');
    const { getTxAudioControl } = await import('../../runtime/adapters/tx-adapter');
    const { ManagedTxController } = await import('../../runtime/tx-controller/managed-controller');
    h.manager = audioManager;
    const audio = getTxAudioControl();
    let keyed = false;
    let resolveForceOff!: () => void;
    const forceOffGate = new Promise<void>((resolve) => { resolveForceOff = resolve; });
    const submit = vi.fn(async (operation: 'transmit_on' | 'force_off') => {
      if (operation === 'transmit_on') {
        keyed = true;
        return 'accepted' as const;
      }
      // Deferred: lets the test observe the latched state before ForceOff
      // completes, then prove the latch survives completion.
      await forceOffGate;
      keyed = false;
      return 'accepted' as const;
    });
    const controller = new ManagedTxController({
      snapshot: () => ({
        phase: keyed ? 'active' : 'idle',
        intent: keyed ? 'momentary' : null,
        radioTx: keyed ? 'on' : 'off',
        txRisk: keyed ? 'confirmed-on' : 'none',
        fault: null,
        faultDetail: null,
        fresh: true,
        releaseRequired: false,
        configuredSeconds: 180,
        remainingMs: null,
        lastOperation: null,
      }),
      refresh: async () => {},
      invalidate: vi.fn(),
      sendPtt: vi.fn(async () => 'accepted' as const),
      submit,
      setTot: async () => {},
      startAudio: () => audio.startManagedTx(),
      stopLocalAudio: (options?: { automatic?: boolean }) => audio.stopLocalAudio(options),
      onAudioDied: (handler: () => void) => audioManager.onTxAudioDied(handler),
    });
    return {
      audioManager, store, audio, controller, submit,
      keyed: () => keyed,
      resolveForceOff: () => resolveForceOff(),
    };
  }

  /** Keys TX through the real chain, kills mid-TX capture, and leaves the
   *  automatic ForceOff parked at its deferred submit. */
  async function driveLatchedFailure(r: Awaited<ReturnType<typeof rig>>) {
    r.controller.transmitOn();
    await vi.waitFor(() => expect(r.submit).toHaveBeenCalledWith('transmit_on'));
    const socket = FakeWebSocket.instances.at(-1)!;
    socket.open();
    socket.onmessage?.({
      data: JSON.stringify({ type: 'audio_tx_format', codec: 'opus', sample_rate: 48000 }),
    });
    expect(r.keyed()).toBe(true);
    expect(r.audioManager.txEnabled).toBe(true);

    txCaptureDied?.('TX MIC: capture failed (unit test)');
    await vi.waitFor(() => expect(r.submit).toHaveBeenCalledWith('force_off'));
    // Latched before the automatic cleanup completes …
    expect(r.store.getAudioState().txError).toBe(true);
    expect(r.audioManager.txEnabled).toBe(false);
    return socket;
  }

  async function finishAutomaticCleanup(r: Awaited<ReturnType<typeof rig>>) {
    const completion = r.controller.forceOff({ automatic: true });
    r.resolveForceOff();
    await completion;
    expect(r.keyed()).toBe(false);
    expect(r.audioManager.txEnabled).toBe(false);
    expect(r.store.getAudioState().txError).toBe(true);
    expect(r.submit.mock.calls).toEqual([['transmit_on'], ['force_off']]);
    expect(txStop).toHaveBeenCalled();
  }

  it('preserves failure after automatic cleanup and clears through user PTT stop', async () => {
    const r = await rig();
    try {
      await driveLatchedFailure(r);
      await finishAutomaticCleanup(r);
      await r.controller.pttOff();
      expect(r.store.getAudioState().txError).toBe(false);
      expect(r.submit.mock.calls).toEqual([['transmit_on'], ['force_off']]);
    } finally { r.controller.dispose(); }
  });

  it('manual ForceOFF after automatic cleanup clears failure through the adapter', async () => {
    const r = await rig();
    try {
      await driveLatchedFailure(r);
      await finishAutomaticCleanup(r);
      await r.controller.forceOff();
      expect(r.store.getAudioState().txError).toBe(false);
      expect(r.submit.mock.calls).toEqual([['transmit_on'], ['force_off'], ['force_off']]);
      expect(r.keyed()).toBe(false);
    } finally { r.controller.dispose(); }
  });

  it('manual ForceOFF joining automatic cleanup clears failure without another command', async () => {
    const r = await rig();
    try {
      await driveLatchedFailure(r);
      const manual = r.controller.forceOff();
      r.resolveForceOff();
      await manual;
      expect(r.store.getAudioState().txError).toBe(false);
      expect(r.submit.mock.calls).toEqual([['transmit_on'], ['force_off']]);
      expect(r.keyed()).toBe(false);
    } finally { r.controller.dispose(); }
  });

  it('retry through controller and adapter clears failure and starts a new attempt', async () => {
    const r = await rig();
    try {
      await driveLatchedFailure(r);
      await finishAutomaticCleanup(r);
      r.controller.transmitOn();
      await vi.waitFor(() => expect(r.submit.mock.calls).toEqual([
        ['transmit_on'], ['force_off'], ['transmit_on'],
      ]));
      expect(r.store.getAudioState().txError).toBe(false);
      expect(r.audioManager.txEnabled).toBe(true);
    } finally { r.controller.dispose(); }
  });

  /** Momentary-PTT variant of rig() with the PTT channel visible. */
  async function momentaryRig() {
    const { audioManager } = await import('../audio-manager');
    const store = await import('../../stores/audio.svelte');
    const { getTxAudioControl } = await import('../../runtime/adapters/tx-adapter');
    const { ManagedTxController } = await import('../../runtime/tx-controller/managed-controller');
    h.manager = audioManager;
    const audio = getTxAudioControl();
    let pttActive = false;
    let lastOperation: 'ptt_on' | 'force_receive' | null = null;
    const sendPtt = vi.fn(async (operation: 'ptt_on' | 'ptt_off') => {
      pttActive = operation === 'ptt_on';
      lastOperation = pttActive ? 'ptt_on' : 'force_receive';
      return 'accepted' as const;
    });
    const controller = new ManagedTxController({
      snapshot: () => ({
        phase: pttActive ? 'active' : 'idle', intent: pttActive ? 'momentary' : null,
        radioTx: pttActive ? 'on' : 'off', txRisk: pttActive ? 'confirmed-on' : 'none',
        fault: null, faultDetail: null, fresh: true, releaseRequired: pttActive,
        configuredSeconds: 180, remainingMs: null, lastOperation,
      }),
      refresh: async () => {},
      invalidate: vi.fn(),
      sendPtt,
      submit: vi.fn(async () => 'accepted' as const),
      setTot: async () => {},
      startAudio: () => audio.startManagedTx(),
      stopLocalAudio: (options?: { automatic?: boolean }) => audio.stopLocalAudio(options),
      onAudioDied: (handler: () => void) => audioManager.onTxAudioDied(handler),
    });
    return { audioManager, store, controller, sendPtt };
  }

  it('a socket fault under momentary PTT survives automatic releaseSession; the user stop clears it (MOR-3753)', async () => {
    const r = await momentaryRig();
    try {
      r.controller.pttOn();
      await vi.waitFor(() => expect(r.audioManager.txEnabled).toBe(true));
      const socket = FakeWebSocket.instances.at(-1)!;
      socket.open();
      socket.onmessage?.({
        data: JSON.stringify({ type: 'audio_tx_format', codec: 'opus', sample_rate: 48000 }),
      });
      await vi.waitFor(() => expect(r.sendPtt).toHaveBeenCalledWith('ptt_on'));
      await vi.waitFor(() => expect(r.controller.snapshot()).toMatchObject({
        phase: 'active', intent: 'momentary', radioTx: 'on', releaseRequired: true,
      }));
      expect(r.audioManager.txEnabled).toBe(true);

      // Real manager socket fault under live demand latches the failure.
      socket.onerror?.({});
      expect(r.store.getAudioState().txError).toBe(true);

      // Automatic session release (pre-disconnect barrier / pagehide class).
      await r.controller.releaseSession();
      expect(r.controller.snapshot()).toMatchObject({
        phase: 'idle', intent: null, radioTx: 'off', releaseRequired: false,
      });
      expect(r.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
      expect(r.audioManager.txEnabled).toBe(false);
      expect(r.store.getAudioState().txError).toBe(true);
      expect(txStop).toHaveBeenCalledOnce();

      // The user's explicit PTT stop acknowledges the fault.
      await r.controller.pttOff();
      expect(r.store.getAudioState().txError).toBe(false);
      expect(r.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
      expect(txStop).toHaveBeenCalledOnce();
    } finally { r.controller.dispose(); }
  });
});
