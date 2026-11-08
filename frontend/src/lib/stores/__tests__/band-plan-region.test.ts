// MOR-4300: a band-plan region change changes what the capabilities
// payload publishes (region-gated band-selector rows — display only,
// 4 m), so a successful `selectBandPlanRegion` asks the capabilities
// refresh OWNER — ws-client's `invalidateCapabilities` — to re-read the
// document. That path already owns session identity, provider
// generation, superseded-request handling and retry/backoff; this store
// keeps no second fetch path (owner rule: no parallel mechanisms). A
// failed region POST must not invalidate. What the refresh does with
// the re-read (storing the document, discarding reads superseded by a
// newer invalidation or a newer provider generation) is pinned in
// src/lib/transport/__tests__/ws-client.isolated.test.ts.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const invalidateCapabilities = vi.hoisted(() => vi.fn());

vi.mock('$lib/transport/ws-client', () => ({ invalidateCapabilities }));

describe('band-plan region store: capabilities invalidation on region change (MOR-4300)', () => {
  let store: typeof import('../band-plan-region.svelte');

  beforeEach(async () => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn());
    invalidateCapabilities.mockReset();
    store = await import('../band-plan-region.svelte');
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('invalidates the session capabilities exactly once after a successful region switch', async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok: true, json: async () => ({}) }) as Response,
    );

    await expect(store.selectBandPlanRegion('IARU-R1')).resolves.toBe(true);

    expect(invalidateCapabilities).toHaveBeenCalledTimes(1);
    expect(store.getBandPlanRegion()).toBe('IARU-R1');
  });

  it('does not invalidate when the region switch fails', async () => {
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      async () => ({ ok: false } as Response),
    );

    await expect(store.selectBandPlanRegion('IARU-R1')).resolves.toBe(false);

    expect(invalidateCapabilities).not.toHaveBeenCalled();
    expect(store.getBandPlanRegion()).toBeNull();
  });
});
