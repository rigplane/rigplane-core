import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ServerState } from '../../types/state';

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeState(revision: number): ServerStateWithObservation {
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: 1,
    observationSeq: revision,
    updatedAt: new Date().toISOString(),
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
    sub: {
      freqHz: 7000000,
      mode: 'LSB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    wsClients: { scope: 0, control: 1, audio: 0 },
  };
}

/** Flush the microtask queue without advancing fake timers. */
async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('fetchState', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns parsed ServerState on success', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { fetchState } = await import('../http-client');
    const result = await fetchState();
    expect(result).not.toBeNull();
    expect(result!.revision).toBe(1);
    expect(result!.main.freqHz).toBe(14074000);
  });

  it('throws on non-ok response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, headers: { get: () => null } });
    const { fetchState } = await import('../http-client');
    await expect(fetchState()).rejects.toThrow('fetchState: 503');
  });

  it('returns null on 304 Not Modified', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 304,
      headers: { get: () => '"1"' },
    });

    const { fetchState } = await import('../http-client');
    const result = await fetchState();
    expect(result).toBeNull();
  });
});

describe('fetchCapabilities', () => {
  afterEach(() => vi.restoreAllMocks());

  function makeCapabilities(overrides: Record<string, unknown> = {}) {
    return {
      model: 'IC-7610',
      scope: true,
      audio: true,
      tx: true,
      capabilities: ['scope', 'dual_rx'],
      receivers: 2,
      vfoScheme: 'main_sub',
      freqRanges: [{
        start: 100000,
        end: 60000000,
        label: 'HF',
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000, bsrCode: 5 }],
      }],
      modes: ['USB', 'LSB'],
      filters: ['FIL1'],
      audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
      webrtc: { available: true, enabled: false },
      txBands: [{ name: '20m', start: 14000000, end: 14350000 }],
      ...overrides,
    };
  }

  function mockCapabilities(payload: unknown) {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(payload),
    });
  }

  it('returns the same validated raw object with additive extensions intact', async () => {
    const caps = makeCapabilities({ extension: { future: true } });
    mockCapabilities(caps);
    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result).toBe(caps);
    expect(result.extension).toEqual({ future: true });
  });

  it.each([
    [false, null, null, ['scope', 'audio', 'tx']],
    [true, 'usb', null, ['scope', 'audio', 'tx']],
    [true, 'lan', 5, ['scope', 'audio', 'tx', 'mod_input_routing']],
    [true, 'acc', Number.MAX_SAFE_INTEGER, ['audio', 'tx', 'mod_input_routing']],
    [true, 'lan', null, ['audio', 'tx', 'mod_input_routing']],
  ])('accepts and preserves a complete TX-audio tuple', async (
    audioTx,
    audioTxRoute,
    audioTxRequiredModInputSource,
    capabilities,
  ) => {
    const caps = makeCapabilities({
      audioTx,
      audioTxRoute,
      audioTxRequiredModInputSource,
      capabilities,
      futureTxFact: { additive: true },
    });
    mockCapabilities(caps);
    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result).toBe(caps);
    expect(result.futureTxFact).toEqual({ additive: true });
  });

  it('keeps an old capability document absent and never synthesizes audioTx', async () => {
    const caps = makeCapabilities({
      capabilities: ['audio', 'tx', 'voice_tx', 'mod_input_routing'],
    });
    mockCapabilities(caps);
    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result).toBe(caps);
    expect(Object.hasOwn(result, 'audioTx')).toBe(false);
  });

  it.each([
    [{ audioTx: true, audioTxRoute: 'lan' }],
    [{ audioTx: true, audioTxRequiredModInputSource: null }],
    [{ audioTxRoute: 'lan', audioTxRequiredModInputSource: null }],
  ])('rejects a partial TX-audio tuple', async (overrides) => {
    mockCapabilities(makeCapabilities(overrides));
    const { fetchCapabilities } = await import('../http-client');
    await expect(fetchCapabilities()).rejects.toThrow('TX-audio capability group');
  });

  it.each([
    ['$.audioTx', { audioTx: 'yes', audioTxRoute: 'lan', audioTxRequiredModInputSource: null }],
    ['$.audioTxRoute', { audioTx: true, audioTxRoute: 'network', audioTxRequiredModInputSource: null }],
    ['$.audioTxRoute', { audioTx: true, audioTxRoute: 1, audioTxRequiredModInputSource: null }],
    ['$.audioTxRequiredModInputSource', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: '5' }],
    ['$.audioTxRequiredModInputSource', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: -1 }],
    ['$.audioTxRequiredModInputSource', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: 1.5 }],
    ['$.audioTxRequiredModInputSource', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: Number.POSITIVE_INFINITY }],
    ['$.audioTxRequiredModInputSource', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: Number.MAX_SAFE_INTEGER + 1 }],
  ])('rejects malformed TX-audio field %s', async (path, overrides) => {
    mockCapabilities(makeCapabilities({
      capabilities: ['audio', 'tx', 'mod_input_routing'],
      ...overrides,
    }));
    const { fetchCapabilities } = await import('../http-client');
    await expect(fetchCapabilities()).rejects.toThrow(path);
  });

  it.each([
    ['false with a route', { audioTx: false, audioTxRoute: 'lan', audioTxRequiredModInputSource: null }],
    ['false with a source', { audioTx: false, audioTxRoute: null, audioTxRequiredModInputSource: 5 }],
    ['true with a null route', { audioTx: true, audioTxRoute: null, audioTxRequiredModInputSource: null }],
    ['a source without MOD routing', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: 5, capabilities: ['audio', 'tx'] }],
    ['a false audio scalar', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: null, audio: false }],
    ['a missing audio tag', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: null, capabilities: ['tx'] }],
    ['a false TX scalar', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: null, tx: false }],
    ['a missing TX tag', { audioTx: true, audioTxRoute: 'lan', audioTxRequiredModInputSource: null, capabilities: ['audio'] }],
  ])('rejects contradictory TX-audio facts: %s', async (_label, overrides) => {
    mockCapabilities(makeCapabilities({
      capabilities: ['audio', 'tx', 'mod_input_routing'],
      ...overrides,
    }));
    const { fetchCapabilities } = await import('../http-client');
    await expect(fetchCapabilities()).rejects.toThrow('contradictory TX-audio capability facts');
  });

  it.each([
    ['single', 1],
    ['ab', 1],
    ['ab_shared', 2],
    ['main_sub', 2],
  ])('accepts the %s topology', async (vfoScheme, receivers) => {
    mockCapabilities(makeCapabilities({ vfoScheme, receivers }));
    const { fetchCapabilities } = await import('../http-client');
    await expect(fetchCapabilities()).resolves.toMatchObject({ vfoScheme, receivers });
  });

  it.each([
    ['null', null],
    ['an explicit empty list', []],
  ])('accepts txBands as %s without normalizing it', async (_label, txBands) => {
    const caps = makeCapabilities({ txBands });
    mockCapabilities(caps);
    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result).toBe(caps);
    expect(result.txBands).toBe(txBands);
  });

  it('accepts finite numeric range values without imposing ordering rules', async () => {
    const freqRanges = [{
      start: 2.5,
      end: -1,
      label: 'Synthetic',
      bands: [{ name: 'Synthetic', start: 3.5, end: -2, default: 0.5, bsrCode: -1 }],
    }];
    const caps = makeCapabilities({ freqRanges });
    mockCapabilities(caps);
    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result).toBe(caps);
    expect(result.freqRanges).toBe(freqRanges);
  });

  it.each([
    ['$.receivers', { receivers: undefined }],
    ['$.receivers', { receivers: true }],
    ['$.receivers', { receivers: 0 }],
    ['$.receivers', { receivers: 1.5 }],
    ['$.vfoScheme', { vfoScheme: undefined }],
    ['$.vfoScheme', { vfoScheme: 'unknown' }],
    ['$.vfoScheme', { vfoScheme: 'single', receivers: 2 }],
    ['$.freqRanges[0]', { freqRanges: [null] }],
    ['$.freqRanges[0].start', { freqRanges: [{}] }],
    ['$.freqRanges[0].start', { freqRanges: [{ start: true, end: 2, label: 'HF' }] }],
    ['$.freqRanges[0].start', { freqRanges: [{ start: Number.NaN, end: 2, label: 'HF' }] }],
    ['$.freqRanges[0].end', { freqRanges: [{ start: 1, end: false, label: 'HF' }] }],
    ['$.freqRanges[0].end', { freqRanges: [{ start: 1, end: Number.POSITIVE_INFINITY, label: 'HF' }] }],
    ['$.freqRanges[0].label', { freqRanges: [{ start: 1, end: 2, label: false }] }],
    ['$.freqRanges[0].bands', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: {} }] }],
    ['$.freqRanges[0].bands[0]', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [null] }] }],
    ['$.freqRanges[0].bands[0].name', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{}] }] }],
    ['$.freqRanges[0].bands[0].start', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{ name: '20m', start: true, end: 4, default: 3 }] }] }],
    ['$.freqRanges[0].bands[0].end', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{ name: '20m', start: 2, end: false, default: 3 }] }] }],
    ['$.freqRanges[0].bands[0].default', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{ name: '20m', start: 2, end: 4, default: true }] }] }],
    ['$.freqRanges[0].bands[0].default', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{ name: '20m', start: 2, end: 4, default: Number.NEGATIVE_INFINITY }] }] }],
    ['$.freqRanges[0].bands[0].bsrCode', { freqRanges: [{ start: 1, end: 2, label: 'HF', bands: [{ name: '20m', start: 2, end: 4, default: 3, bsrCode: false }] }] }],
    ['$.audioConfig.channels', { audioConfig: { sampleRate: 48000, channels: true, codecs: ['opus'] } }],
    ['$.audioConfig.codecs[0]', { audioConfig: { sampleRate: 48000, channels: 1, codecs: [1] } }],
    ['$.webrtc.available', { webrtc: { available: 1, enabled: false } }],
    ['$.webrtc.enabled', { webrtc: { available: true } }],
    ['$.txBands', { txBands: undefined }],
    ['$.txBands[0].name', { txBands: [{ name: 20, start: 1, end: 2 }] }],
    ['$.txBands[0].start', { txBands: [{ name: '20m', start: true, end: 2 }] }],
    ['$.txBands[0].end', { txBands: [{ name: '20m', start: 1, end: false }] }],
    ['$.txBands[0].end', { txBands: [{ name: '20m', start: 1, end: 2.5 }] }],
  ])('rejects malformed required field %s', async (path, overrides) => {
    mockCapabilities(makeCapabilities(overrides));
    const { fetchCapabilities } = await import('../http-client');
    await expect(fetchCapabilities()).rejects.toThrow(path);
  });
});

describe('fetchInfo', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns parsed InfoResponse', async () => {
    const info = { version: '0.1.0', revision: 5, updatedAt: '2026-03-07T00:00:00Z', uptime: 42 };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(info),
    });

    const { fetchInfo } = await import('../http-client');
    const result = await fetchInfo();
    expect(result.version).toBe('0.1.0');
    expect(result.revision).toBe(5);
  });
});

describe('startPolling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('calls callback with state when revision advances', async () => {
    let rev = 1;
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({ ok: true, status: 200, headers: { get: () => '"' + (rev - 1) + '"' }, json: () => Promise.resolve(makeState(rev++)) }),
    );

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s));

    // first tick fires immediately (via void tick()), flush its microtasks
    await flushMicrotasks();

    expect(received.length).toBeGreaterThanOrEqual(1);
    expect(received[0].revision).toBe(1);

    stop();
  });

  it('skips callback when revision does not advance', async () => {
    const fixed = makeState(42);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"42"' },
      json: () => Promise.resolve(fixed),
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    // first tick: revision 42, callback called
    await flushMicrotasks();
    expect(received).toHaveLength(1);

    // advance to trigger second poll
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    // second poll: still revision 42 → skipped
    expect(received).toHaveLength(1);

    stop();
  });

  it('calls callback when only healthRevision advances', async () => {
    const first = makeState(42);
    first.healthRevision = 1;
    const second = makeState(42);
    second.healthRevision = 2;
    second.connection = { rigConnected: true, radioReady: false, controlConnected: true };
    second.radioHealth = {
      serverReachable: true,
      radioLink: 'connected',
      readiness: 'delayed',
      likelyCause: 'radio_not_responding',
      sinceMs: 1500,
      lastError: null,
    };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-${state.healthRevision}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].revision).toBe(42);
    expect(received[1].healthRevision).toBe(2);
    stop();
  });

  it('calls callback when only freshnessRevision advances', async () => {
    const first = makeState(42);
    first.freshnessRevision = 1;
    const second = makeState(42);
    second.freshnessRevision = 2;
    second.main.sMeter = 64;
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-${state.freshnessRevision}-0"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].stateRevision).toBe(42);
    expect(received[1].freshnessRevision).toBe(2);
    expect(received[1].main.sMeter).toBe(64);
    stop();
  });

  it('delivers stale fieldStatus metadata when freshnessRevision advances', async () => {
    const first = makeState(42);
    first.freshnessRevision = 1;
    first.fieldStatus = {
      'main.sMeter': {
        storePath: 'receiver.main.meters.s_meter',
        observed: true,
        freshness: 'fresh',
        availability: 'available',
      },
    };
    const second = makeState(42);
    second.freshnessRevision = 2;
    second.fieldStatus = {
      'main.sMeter': {
        storePath: 'receiver.main.meters.s_meter',
        observed: true,
        freshness: 'stale',
        availability: 'stale',
      },
    };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-${state.freshnessRevision}-stale-meter"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerStateWithObservation[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].stateRevision).toBe(42);
    expect(received[1].freshnessRevision).toBe(2);
    expect(received[1].fieldStatus?.['main.sMeter']).toEqual(
      second.fieldStatus['main.sMeter'],
    );
    stop();
  });

  it('calls callback when only observationSeq advances after a new ETag payload', async () => {
    const first = makeState(42);
    first.observationSeq = 1;
    first.fieldStatus = {
      'main.freqHz': {
        storePath: 'receiver.main.active.freq_mode.freq_hz',
        observed: true,
        freshness: 'fresh',
        availability: 'available',
        lastObservedMonotonic: 1,
        source: { provider: 'first' },
      },
    };
    const second = makeState(42);
    second.observationSeq = 2;
    second.fieldStatus = {
      'main.freqHz': {
        storePath: 'receiver.main.active.freq_mode.freq_hz',
        observed: true,
        freshness: 'fresh',
        availability: 'available',
        lastObservedMonotonic: 2,
        source: { provider: 'second' },
      },
    };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-1-h1-o${state.observationSeq}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerStateWithObservation[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].stateRevision).toBe(42);
    expect(received[1].freshnessRevision).toBe(1);
    expect(received[1].observationSeq).toBe(2);
    expect(received[1].fieldStatus?.['main.freqHz']).toEqual(
      second.fieldStatus['main.freqHz'],
    );
    stop();
  });

  it('calls callback when only wsClients changes and publicStateSeq advances', async () => {
    const first = makeState(42);
    first.observationSeq = 7;
    first.publicStateSeq = 1;
    first.wsClients = { scope: 0, control: 1, audio: 0 };
    const second = makeState(42);
    second.observationSeq = 7;
    second.publicStateSeq = 2;
    second.wsClients = { scope: 0, control: 2, audio: 0 };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-public-${state.publicStateSeq}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerStateWithObservation[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].stateRevision).toBe(42);
    expect(received[1].freshnessRevision).toBe(1);
    expect(received[1].observationSeq).toBe(7);
    expect(received[1].publicStateSeq).toBe(2);
    expect(received[1].wsClients?.control).toBe(2);
    stop();
  });

  it('skips equal-revision semantic state even when publicStateSeq advances', async () => {
    const first = makeState(42);
    first.publicStateSeq = 1;
    first.ptt = true;
    first.wsClients = { scope: 0, control: 1, audio: 0 };
    const second = makeState(42);
    second.publicStateSeq = 2;
    second.ptt = false;
    second.wsClients = { scope: 0, control: 2, audio: 0 };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-public-${state.publicStateSeq}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerStateWithObservation[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(1);
    expect(received[0].publicStateSeq).toBe(1);
    expect(received[0].wsClients?.control).toBe(1);
    expect(received[0].ptt).toBe(true);
    stop();
  });

  it('skips stale semantic state even when freshnessRevision advances', async () => {
    const first = makeState(42);
    first.freshnessRevision = 1;
    first.ptt = true;
    const second = makeState(41);
    second.freshnessRevision = 2;
    second.ptt = false;
    second.main.freqHz = 7100000;
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"${state.stateRevision}-${state.freshnessRevision}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(1);
    expect(received[0].stateRevision).toBe(42);
    expect(received[0].ptt).toBe(true);
    expect(received[0].main.freqHz).toBe(14074000);
    stop();
  });

  it('skips stale semantic state even when observationSeq advances', async () => {
    const first = makeState(42);
    first.observationSeq = 10;
    first.ptt = true;
    const second = makeState(41);
    second.observationSeq = 11;
    second.ptt = false;
    second.main.freqHz = 7100000;
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"${state.stateRevision}-o${state.observationSeq}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerStateWithObservation[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(1);
    expect(received[0].stateRevision).toBe(42);
    expect(received[0].observationSeq).toBe(10);
    expect(received[0].ptt).toBe(true);
    expect(received[0].main.freqHz).toBe(14074000);
    stop();
  });

  it('skips stale semantic state even when healthRevision advances', async () => {
    const first = makeState(42);
    first.healthRevision = 1;
    first.ptt = true;
    const second = makeState(41);
    second.healthRevision = 2;
    second.ptt = false;
    second.connection = { rigConnected: true, radioReady: false, controlConnected: true };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"${state.stateRevision}-h${state.healthRevision}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(1);
    expect(received[0].stateRevision).toBe(42);
    expect(received[0].ptt).toBe(true);
    expect(received[0].connection.radioReady).toBe(true);
    stop();
  });

  it('does not crash on fetch error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s));

    // flush — should not throw
    await flushMicrotasks();

    expect(received).toHaveLength(0);
    stop();
  });

  it('calls setHttpConnected(true) on successful poll', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { startPolling } = await import('../http-client');
    const { getHttpConnected } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {});
    await flushMicrotasks();

    expect(getHttpConnected()).toBe(true);
    stop();
  });

  it('clears reconnecting on successful poll', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { startPolling } = await import('../http-client');
    const { isReconnecting, setReconnecting } = await import('../../stores/connection.svelte');

    setReconnecting(true);
    const stop = startPolling(() => {});
    await flushMicrotasks();

    expect(isReconnecting()).toBe(false);
    stop();
  });

  it('calls setHttpConnected(false) after 3 consecutive errors', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { getHttpConnected } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);

    // Trigger 3 consecutive failures
    for (let i = 0; i < 3; i++) {
      await flushMicrotasks();
      vi.advanceTimersByTime(10);
    }
    await flushMicrotasks();

    expect(getHttpConnected()).toBe(false);
    stop();
  });

  it('classifies repeated HTTP failures as server_unreachable', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { getRadioHealth } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);

    for (let i = 0; i < 3; i++) {
      await flushMicrotasks();
      vi.advanceTimersByTime(10);
    }
    await flushMicrotasks();

    expect(getRadioHealth()?.likelyCause).toBe('server_unreachable');
    stop();
  });

  it('marks reconnecting when polling errors repeat', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { isReconnecting } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);
    await flushMicrotasks();

    expect(isReconnecting()).toBe(true);
    stop();
  });

  it('returns a stop function that halts polling', async () => {
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => '"' + callCount + '"' }, json: () => Promise.resolve(makeState(callCount)) });
    });

    const { startPolling } = await import('../http-client');
    const stop = startPolling(() => {}, 200);

    // first tick
    await flushMicrotasks();
    const countAfterFirst = callCount;
    expect(countAfterFirst).toBe(1);

    stop();

    // advance past interval — no new calls
    vi.advanceTimersByTime(400);
    await flushMicrotasks();

    expect(callCount).toBe(countAfterFirst);
  });

  it('does not pile up concurrent polls (inflight guard)', async () => {
    let resolvePending: (() => void) | undefined;
    let callCount = 0;

    globalThis.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      return new Promise((resolve) => {
        resolvePending = () =>
          resolve({ ok: true, status: 200, headers: { get: () => '"' + callCount + '"' }, json: () => Promise.resolve(makeState(callCount)) });
      });
    });

    const { startPolling } = await import('../http-client');
    const stop = startPolling(() => {}, 200);

    // first tick is inflight (not yet resolved)
    await flushMicrotasks();
    expect(callCount).toBe(1);

    // advance well past interval — second tick should not fire while first is pending
    vi.advanceTimersByTime(600);
    await flushMicrotasks();

    // still only 1 fetch outstanding due to inflight guard
    expect(callCount).toBe(1);

    resolvePending?.();
    stop();
  });
});
