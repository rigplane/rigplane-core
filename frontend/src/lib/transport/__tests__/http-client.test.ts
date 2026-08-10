import { describe, it, expect, vi, afterEach } from 'vitest';

// ─── Tests ───────────────────────────────────────────────────────────────────

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
    [true, 'lan', Number.MAX_SAFE_INTEGER, ['audio', 'tx', 'mod_input_routing']],
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
    ['a source on the USB route', { audioTx: true, audioTxRoute: 'usb', audioTxRequiredModInputSource: 5 }],
    ['a source on the ACC route', { audioTx: true, audioTxRoute: 'acc', audioTxRequiredModInputSource: 5 }],
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

  it('accepts both a declared VFO readback contract and an older absent field', async () => {
    const { fetchCapabilities } = await import('../http-client');
    mockCapabilities(makeCapabilities({ vfoReadback: 'selected_unselected' }));
    await expect(fetchCapabilities()).resolves.toMatchObject({
      vfoReadback: 'selected_unselected',
    });

    mockCapabilities(makeCapabilities());
    await expect(fetchCapabilities()).resolves.not.toHaveProperty('vfoReadback');
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
    ['$.vfoReadback', { vfoReadback: 'relative' }],
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

// MOR-1409 A10 — retirement of the HTTP /state polling machinery
// (startPolling, fetchState, the ETag/revision-arbitration helpers). Pins
// the reduced export surface.
describe('public export surface (A10)', () => {
  it('no longer exports startPolling, fetchState, setPollingMultiplier, clearEtag, or HTTP_ERROR_THRESHOLD', async () => {
    const mod: Record<string, unknown> = await import('../http-client');
    expect('startPolling' in mod).toBe(false);
    expect('fetchState' in mod).toBe(false);
    expect('setPollingMultiplier' in mod).toBe(false);
    expect('clearEtag' in mod).toBe(false);
    expect('HTTP_ERROR_THRESHOLD' in mod).toBe(false);
  });

  it('still exports fetchCapabilities and fetchInfo unchanged', async () => {
    const mod = await import('../http-client');
    expect(typeof mod.fetchCapabilities).toBe('function');
    expect(typeof mod.fetchInfo).toBe('function');
  });
});
