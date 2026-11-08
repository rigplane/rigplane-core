// MOR-4300: a band-plan region change changes what the capabilities
// payload publishes (region-gated band-selector rows — display only,
// 4 m), so a successful `selectBandPlanRegion` re-reads capabilities
// once and stores them; a failed region POST must not re-read, and a
// failed re-read is swallowed like the store's other errors (the
// region switch itself still stands).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const fetchCapabilities = vi.hoisted(() => vi.fn());

vi.mock('$lib/transport/http-client', () => ({ fetchCapabilities }));

describe('band-plan region store: capabilities re-read on region change (MOR-4300)', () => {
  let store: typeof import('../band-plan-region.svelte');
  let caps: typeof import('../capabilities.svelte');

  beforeEach(async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn());
    fetchCapabilities.mockReset();
    store = await import('../band-plan-region.svelte');
    caps = await import('../capabilities.svelte');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function mockConfigPost(ok: boolean): void {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok, json: async () => ({}) }) as Response,
    );
  }

  it('re-reads capabilities once after a successful region switch and stores them', async () => {
    mockConfigPost(true);
    const payload = { stateContractVersion: 1, providerGeneration: 3 };
    fetchCapabilities.mockResolvedValue(payload);

    await expect(store.selectBandPlanRegion('IARU-R1')).resolves.toBe(true);

    await vi.waitFor(() => expect(caps.getCapabilities()).toBe(payload));
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(store.getBandPlanRegion()).toBe('IARU-R1');
  });

  it('does not re-read when the region switch fails', async () => {
    mockConfigPost(false);

    await expect(store.selectBandPlanRegion('IARU-R1')).resolves.toBe(false);

    expect(fetchCapabilities).not.toHaveBeenCalled();
    expect(caps.getCapabilities()).toBeNull();
  });

  it('swallows a failed capabilities re-read: the region switch still stands', async () => {
    mockConfigPost(true);
    fetchCapabilities.mockRejectedValue(new Error('capabilities offline'));

    await expect(store.selectBandPlanRegion('IARU-R1')).resolves.toBe(true);

    await vi.waitFor(() => expect(fetchCapabilities).toHaveBeenCalled());
    await Promise.resolve();
    await Promise.resolve();
    expect(store.getBandPlanRegion()).toBe('IARU-R1');
    expect(caps.getCapabilities()).toBeNull();
  });
});
