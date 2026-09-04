import { describe, expect, it } from 'vitest';
import { projectManagedTx } from '../managed-state';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

const document = (intent: 'rx' | 'transmit' | 'ptt', releaseRequired = false): ManagedTransmitDocument => ({
  schemaVersion: 1, sampledAt: '2026-09-04T00:00:00Z',
  managedTransmit: { status: 'available', intent: intent === 'ptt' ? { kind: 'ptt', owner: 'session' } : { kind: intent },
    releaseRequired, lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
  txObservation: { observedPtt: 'off' },
});

describe('managed TX projection', () => {
  it('fails stale state closed without claiming RX or browser ownership', () => {
    expect(projectManagedTx(document('rx'), true)).toMatchObject({
      fresh: false, radioTx: 'unknown', intent: null,
    });
    expect(projectManagedTx(document('rx'), true)).not.toHaveProperty('mayOwnKey');
  });
  it('does not call normal active release debt a releasing phase', () => {
    expect(projectManagedTx(document('ptt', true), false).phase).toBe('key-confirm-pending');
    expect(projectManagedTx(document('transmit', true), false).phase).toBe('key-confirm-pending');
    expect(projectManagedTx(document('rx', true), false).phase).toBe('releasing');
  });
});
