import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

const document = (configuredSeconds: number | null): ManagedTransmitDocument => ({
  schemaVersion: 1,
  sampledAt: '2026-09-04T00:00:00Z',
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

  it('replaces the projection only with the validated setTot response', async () => {
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
});
