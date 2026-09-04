import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
const h = vi.hoisted(() => ({
  deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(), send: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
  start: vi.fn(async () => null), stop: vi.fn(), submit: vi.fn(async () => 'accepted' as const),
  setTot: vi.fn(async () => {}), ids: 0,
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => ({ available: false }), managedTransmitIsStale: () => true,
  managedTransmitRemainingMs: () => null, refreshManagedTransmit: vi.fn(async () => {}),
  invalidateManagedTransmit: vi.fn(), submitManagedTransmit: h.submit,
  setManagedTransmitTot: h.setTot,
}));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `cmd-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  onTxAudioDied: () => () => {},
  startManagedTx: h.start, stopLocalAudio: h.stop,
}) }));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  onCommandDelivery: (fn: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(fn); return () => h.deliveries.delete(fn);
  },
  onControlSessionTransition: (fn: (event: ControlSessionTransition) => void) => {
    h.sessions.add(fn); return () => h.sessions.delete(fn);
  },
}));
import { createManagedBrowserDependencies } from '../browser-dependencies';
const emit = (event: CommandDeliveryEvent) => [...h.deliveries].forEach((fn) => fn(event));
beforeEach(() => {
  vi.useFakeTimers(); h.deliveries.clear(); h.sessions.clear(); h.send.mockReset().mockReturnValue(true);
  h.start.mockClear(); h.stop.mockClear(); h.submit.mockClear(); h.ids = 0;
  h.setTot.mockClear();
});
describe('managed browser TX dependencies', () => {
  it('is dormant, delegates media, forwards sessions, and disposes idempotently', async () => {
    const browser = createManagedBrowserDependencies();
    expect([h.deliveries.size, h.sessions.size, h.start.mock.calls.length, vi.getTimerCount()]).toEqual([0, 0, 0, 0]);
    const seen: number[] = [];
    const off = browser.subscribeSession((session) => seen.push(session.epoch));
    [...h.sessions][0]({ state: 'connected', epoch: 5 });
    expect(seen).toEqual([5]);
    off();
    await expect(browser.dependencies.startAudio()).resolves.toBeNull();
    browser.dependencies.stopLocalAudio();
    expect([h.start.mock.calls.length, h.stop.mock.calls.length]).toEqual([1, 1]);
    browser.dispose(); browser.dispose();
    expect([h.sessions.size, h.deliveries.size, vi.getTimerCount()]).toEqual([0, 0, 0]);
  });

  it('registers before send and resolves only the matching terminal admission', async () => {
    const browser = createManagedBrowserDependencies();
    h.send.mockImplementation((_name, _params, id) => {
      expect(h.deliveries.size).toBe(1);
      emit({ commandId: id!, kind: 'response-ok', originalEpoch: 4, eventEpoch: 4 });
      return true;
    });
    await expect(browser.dependencies.sendPtt('ptt_on')).resolves.toBe('accepted');
    expect(h.send).toHaveBeenCalledWith('ptt_on', {}, 'cmd-1');
    expect(h.deliveries.size).toBe(0);
  });

  it('fails a refused ON closed and keeps ForceOFF on the distinct HTTP path', async () => {
    const browser = createManagedBrowserDependencies();
    h.send.mockReturnValue(false);
    await expect(browser.dependencies.sendPtt('ptt_on')).resolves.toBe('rejected');
    await expect(browser.dependencies.submit('force_off')).resolves.toBe('accepted');
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('force_off');
    expect(h.send).toHaveBeenCalledExactlyOnceWith('ptt_on', {}, 'cmd-1');
  });

  it('routes TOT edits through the canonical managed HTTP store path', async () => {
    const browser = createManagedBrowserDependencies();
    await browser.dependencies.setTot(240);
    await browser.dependencies.setTot(null);
    expect(h.setTot.mock.calls).toEqual([[240], [null]]);
  });

  it('uses no client confirmation timer and rejects a pending write on dispose', async () => {
    const browser = createManagedBrowserDependencies();
    const pending = browser.dependencies.sendPtt('ptt_off');
    expect(vi.getTimerCount()).toBe(0);
    browser.dispose();
    await expect(pending).resolves.toBe('rejected');
    expect(h.deliveries.size).toBe(0);
  });
});
