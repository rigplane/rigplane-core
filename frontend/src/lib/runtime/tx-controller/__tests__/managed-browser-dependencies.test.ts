import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';

const h = vi.hoisted(() => ({
  deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(),
  send: vi.fn(() => true), refresh: vi.fn(async () => {}), invalidate: vi.fn(),
  start: vi.fn(async () => null), stop: vi.fn(), audioDied: null as null | (() => void), ids: 0,
}));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `managed-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  startManagedTx: h.start, stopLocalAudio: h.stop,
  onTxAudioDied: (handler: () => void) => { h.audioDied = handler; return () => { h.audioDied = null; }; },
}) }));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => null, managedTransmitIsStale: () => true,
  managedTransmitRemainingMs: () => null,
  refreshManagedTransmit: h.refresh, invalidateManagedTransmit: h.invalidate,
  submitManagedTransmit: vi.fn(async () => 'accepted'),
}));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  onCommandDelivery: (handler: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(handler); return () => h.deliveries.delete(handler);
  },
  onControlSessionTransition: (handler: (event: ControlSessionTransition) => void) => {
    h.sessions.add(handler); return () => h.sessions.delete(handler);
  },
}));
import { createManagedBrowserDependencies } from '../browser-dependencies';

const emit = (event: CommandDeliveryEvent) => [...h.deliveries].forEach((handler) => handler(event));

describe('managed browser dependencies', () => {
  beforeEach(() => {
    h.deliveries.clear(); h.sessions.clear(); h.ids = 0;
    h.send.mockReset().mockReturnValue(true); h.refresh.mockReset().mockResolvedValue(undefined);
    h.invalidate.mockReset(); h.start.mockClear(); h.stop.mockClear(); h.audioDied = null;
  });

  it('waits for a terminal server response and ignores transport-only/foreign delivery', async () => {
    h.refresh.mockImplementation(() => new Promise<void>(() => {}));
    const browser = createManagedBrowserDependencies();
    const result = browser.dependencies.sendPtt('ptt_on');
    expect(h.send).toHaveBeenCalledWith('ptt_on', {}, 'managed-1');
    emit({ commandId: 'managed-1', kind: 'transport-sent', originalEpoch: 1, eventEpoch: 1 });
    emit({ commandId: 'foreign', kind: 'response-ok', originalEpoch: 1, eventEpoch: 1 });
    let settled = false; void result.then(() => { settled = true; }); await Promise.resolve();
    expect(settled).toBe(false);
    emit({ commandId: 'managed-1', kind: 'response-ok', originalEpoch: 1, eventEpoch: 1 });
    await expect(result).resolves.toBe('accepted');
    expect(h.refresh).toHaveBeenCalledTimes(1);
    expect(h.deliveries.size).toBe(0);
  });

  it('reports terminal rejection/offline refusal and settles pending commands on dispose', async () => {
    const browser = createManagedBrowserDependencies();
    const rejected = browser.dependencies.sendPtt('ptt_on');
    emit({ commandId: 'managed-1', kind: 'response-error', originalEpoch: 1, eventEpoch: 1 });
    await expect(rejected).resolves.toBe('rejected');
    h.send.mockReturnValue(false);
    await expect(browser.dependencies.sendPtt('ptt_off')).resolves.toBe('rejected');
    h.send.mockReturnValue(true);
    const pending = browser.dependencies.sendPtt('ptt_on');
    browser.dispose(); browser.dispose();
    await expect(pending).resolves.toBe('rejected');
    expect([h.deliveries.size, h.sessions.size]).toEqual([0, 0]);
  });
});
