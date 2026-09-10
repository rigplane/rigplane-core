import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

const document = (configuredSeconds: number | null, sampledAt = '2026-09-04T00:00:00Z'): ManagedTransmitDocument => ({
  schemaVersion: 1,
  sampledAt,
  managedTransmit: {
    status: 'available', intent: { kind: 'rx' }, releaseRequired: true,
    lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds, active: true, remainingMs: 100, expiresAt: 'x' },
  },
  txObservation: { observedPtt: 'off' },
});

describe('managed transmit store', () => {
  beforeEach(() => vi.resetModules());

  it('marks cached countdown stale and never changes server debt', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180), 10);
    expect(store.managedTransmitRemainingMs(20)).toBe(90);
    store.receiveManagedTransmitSnapshot({
      schemaVersion: 1, sampledAt: '2026-09-03T00:00:00Z',
      managedTransmit: { status: 'unavailable', reason: 'authority_not_composed' },
      txObservation: { observedPtt: 'unknown' },
    });
    expect(store.managedTransmitSnapshot()?.managedTransmit.status).toBe('available');
    store.invalidateManagedTransmit();
    expect(store.managedTransmitRemainingMs(20)).toBeNull();
    const snapshot = store.managedTransmitSnapshot();
    expect(snapshot?.managedTransmit.status).toBe('available');
    if (snapshot?.managedTransmit.status === 'available') {
      expect(snapshot.managedTransmit.releaseRequired).toBe(true);
    }
  });

  it('accepts the canonical setTot response through the freshness guard', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    const client = { setTot: vi.fn(async () => document(240)) };

    await store.setManagedTransmitTot(240, client);

    expect(client.setTot).toHaveBeenCalledExactlyOnceWith(240);
    const snapshot = store.managedTransmitSnapshot();
    expect(snapshot?.managedTransmit.status).toBe('available');
    if (snapshot?.managedTransmit.status === 'available') {
      expect(snapshot.managedTransmit.tot.configuredSeconds).toBe(240);
    }
  });

  it('does not let a stale setTot response overwrite a newer received snapshot', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    let resolvePut!: (value: ManagedTransmitDocument) => void;
    const client = { setTot: vi.fn(() => new Promise<ManagedTransmitDocument>((resolve) => { resolvePut = resolve; })) };

    const pending = store.setManagedTransmitTot(240, client);
    store.receiveManagedTransmitSnapshot(document(300, '2026-09-04T00:00:01Z'));
    resolvePut(document(240));
    await pending;

    const snapshot = store.managedTransmitSnapshot();
    expect(snapshot?.managedTransmit.status).toBe('available');
    if (snapshot?.managedTransmit.status === 'available') {
      expect(snapshot.managedTransmit.tot.configuredSeconds).toBe(300);
    }
  });

  it('makes a failed write stale without changing cached server truth', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    const client = { setTot: vi.fn(async () => { throw new Error('write failed'); }) };

    await expect(store.setManagedTransmitTot(null, client)).rejects.toThrow('write failed');

    expect(store.managedTransmitIsStale()).toBe(true);
    const snapshot = store.managedTransmitSnapshot();
    expect(snapshot?.managedTransmit.status).toBe('available');
    if (snapshot?.managedTransmit.status === 'available') {
      expect(snapshot.managedTransmit.tot.configuredSeconds).toBe(180);
    }
  });

  it('keeps an available projection fresh while a background refresh is pending', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    let resolveRead!: (value: ManagedTransmitDocument) => void;
    const client = { snapshot: vi.fn(() => new Promise<ManagedTransmitDocument>((resolve) => { resolveRead = resolve; })) };

    const pending = store.refreshManagedTransmit(client);

    expect(store.managedTransmitIsStale()).toBe(false);
    expect(store.managedTransmitSnapshot()?.txObservation.observedPtt).toBe('off');
    expect(client.snapshot).toHaveBeenCalledTimes(1);
    resolveRead(document(180, '2026-09-04T00:00:01Z'));
    await pending;
    expect(store.managedTransmitIsStale()).toBe(false);
  });

  it('fences a late refresh after hard invalidation', async () => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    let resolveRead!: (value: ManagedTransmitDocument) => void;
    const client = { snapshot: vi.fn(() => new Promise<ManagedTransmitDocument>((resolve) => { resolveRead = resolve; })) };

    const pending = store.refreshManagedTransmit(client);
    store.invalidateManagedTransmit();
    resolveRead(document(240, '2026-09-04T00:00:01Z'));
    await pending;

    expect(store.managedTransmitIsStale()).toBe(true);
    expect(store.managedTransmitSnapshot()?.managedTransmit.status).toBe('available');
    expect(store.managedTransmitSnapshot()?.sampledAt).toBe('2026-09-04T00:00:00Z');
  });

  it.each([
    ['HTTP failure', async () => { throw new Error('read failed'); }],
    ['unavailable document', async (): Promise<ManagedTransmitDocument> => ({
      schemaVersion: 1 as const, sampledAt: '2026-09-04T00:00:01Z',
      managedTransmit: { status: 'unavailable' as const, reason: 'authority_not_composed' },
      txObservation: { observedPtt: 'unknown' as const },
    })],
  ])('%s retires the cached available projection', async (_label, snapshot) => {
    const store = await import('../managed-transmit.svelte');
    store.receiveManagedTransmitSnapshot(document(180));
    const pending = store.refreshManagedTransmit({ snapshot });
    if (_label === 'HTTP failure') await expect(pending).rejects.toThrow('read failed');
    else await pending;
    expect(store.managedTransmitIsStale()).toBe(true);
  });
});
