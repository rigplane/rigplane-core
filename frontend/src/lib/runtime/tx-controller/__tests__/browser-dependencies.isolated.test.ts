import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
const h = vi.hoisted(() => ({
  deliveries: new Set<(event: CommandDeliveryEvent) => void>(),
  sessions: new Set<(event: ControlSessionTransition) => void>(),
  messages: new Set<(message: { type?: string; name?: string; data?: unknown }) => void>(),
  sessionState: 'disconnected' as 'disconnected' | 'connected',
  appliedRevision: 0,
  notify: vi.fn(),
  send: vi.fn((_name: string, _params: Record<string, unknown>, _id?: string) => true),
  start: vi.fn(async () => null), stop: vi.fn(), submit: vi.fn(async () => 'accepted' as const),
  setTot: vi.fn(async () => {}), ids: 0, stale: true, remainingMs: null as number | null,
  refresh: vi.fn(async () => {}),
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => ({ available: false }), managedTransmitIsStale: () => h.stale,
  managedTransmitRemainingMs: () => h.remainingMs, refreshManagedTransmit: h.refresh,
  invalidateManagedTransmit: vi.fn(), submitManagedTransmit: h.submit,
  setManagedTransmitTot: h.setTot, managedTransmitAppliedRevision: () => h.appliedRevision,
}));
vi.mock('$lib/types/protocol', () => ({ makeCommandId: () => `cmd-${++h.ids}` }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  onTxAudioDied: () => () => {},
  startManagedTx: h.start, stopLocalAudio: h.stop,
}) }));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: h.send,
  emitLocalNotification: h.notify,
  onCommandDelivery: (fn: (event: CommandDeliveryEvent) => void) => {
    h.deliveries.add(fn); return () => h.deliveries.delete(fn);
  },
  onControlSessionTransition: (fn: (event: ControlSessionTransition) => void) => {
    h.sessions.add(fn); return () => h.sessions.delete(fn);
  },
  onMessage: (fn: (message: { type?: string; name?: string; data?: unknown }) => void) => {
    h.messages.add(fn); return () => h.messages.delete(fn);
  },
  getControlSession: () => ({ state: h.sessionState, epoch: 4 }),
}));
import { createManagedBrowserDependencies } from '../browser-dependencies';
import { ManagedTransmitError } from '$lib/transport/managed-transmit-client';
const emit = (event: CommandDeliveryEvent) => [...h.deliveries].forEach((fn) => fn(event));
const emitMessage = (message: { type?: string; name?: string; data?: unknown }) => {
  [...h.messages].forEach((fn) => fn(message));
};
beforeEach(() => {
  vi.useFakeTimers(); h.deliveries.clear(); h.sessions.clear(); h.messages.clear();
  h.sessionState = 'disconnected'; h.appliedRevision = 0;
  h.send.mockReset().mockReturnValue(true);
  h.start.mockClear(); h.stop.mockClear(); h.submit.mockClear(); h.ids = 0;
  h.notify.mockClear(); h.setTot.mockClear(); h.stale = true; h.remainingMs = null;
  h.refresh.mockClear();
});
describe('managed browser TX dependencies', () => {
  it.each(['transmit_on', 'force_off'] as const)('keeps a typed 409 for %s as an admission rejection and refreshes', async (operation) => {
    h.submit.mockRejectedValueOnce(new ManagedTransmitError('tx_held_by_other', 409));
    const browser = createManagedBrowserDependencies();
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      await expect(browser.dependencies.submit(operation)).resolves.toBe('rejected');
      expect(h.refresh).toHaveBeenCalledExactlyOnceWith();
      if (operation === 'transmit_on') {
        browser.dependencies.onLatchRefusal?.();
        expect(h.notify).toHaveBeenCalledExactlyOnceWith('error', expect.any(String), 'txHeldByOther');
      } else {
        expect(h.notify).not.toHaveBeenCalled();
      }
    } finally { browser.dispose(); warn.mockRestore(); }
  });
  it.each([
    ['tx_held_by_other', 'txHeldByOther'], ['release_in_progress', 'txReleaseInProgress'],
    ['authority_unavailable', 'txAuthorityUnavailable'], ['read_only', 'txReadOnly'],
    ['controller_refused', 'txControllerRefused'], ['radio_refused', 'txRadioRefused'],
    ['future_refusal', 'txStartRefused'],
  ])('presents HTTP refusal %s through the locale mechanism and retains its code', (code, toast) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const browser = createManagedBrowserDependencies();
    try {
      browser.dependencies.onLatchRefusal?.(new ManagedTransmitError(code, 409));
      expect(h.notify).toHaveBeenCalledExactlyOnceWith('error', expect.any(String), toast);
      expect(warn).toHaveBeenCalledWith(`[tx] Latch start refused: ${code}`);
    } finally { browser.dispose(); warn.mockRestore(); }
  });
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
    expect(h.notify).toHaveBeenCalledExactlyOnceWith('error', expect.any(String), 'controllerLost');
  });

  it('keeps the real refusal reason when the command gate refuses on a live session (MOR-4651)', async () => {
    const browser = createManagedBrowserDependencies();
    h.sessionState = 'connected';
    h.send.mockReturnValue(false); // sendCommand's degraded-radio-health refusal
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      await expect(browser.dependencies.sendPtt('ptt_on')).resolves.toBe('rejected');
    } finally {
      warn.mockRestore();
    }
    // The session is up, so the sticky controllerLost notification and its
    // control_link_unavailable log line must not fire on top of the health
    // gate's own warning.
    expect(h.notify).not.toHaveBeenCalled();
    expect(warn).not.toHaveBeenCalledWith('[tx] PTT start refused: control_link_unavailable');
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

  it('owns a presentation-only countdown ticker and clears it on dispose', () => {
    const browser = createManagedBrowserDependencies();
    h.stale = false;
    h.remainingMs = 900;
    const seen: Array<number | null> = [];
    browser.dependencies.onPresentationTick?.(() => seen.push(h.remainingMs));
    expect(vi.getTimerCount()).toBe(1);

    vi.advanceTimersByTime(250);
    h.stale = true;
    h.remainingMs = null;
    vi.advanceTimersByTime(250);
    expect(seen).toEqual([900, null]);
    expect(browser.dependencies.snapshot()).toMatchObject({ fresh: false, remainingMs: null });

    browser.dispose();
    vi.advanceTimersByTime(1_000);
    expect([seen, vi.getTimerCount()]).toEqual([[900, null], 0]);
  });

  it('propagates only live-session managed_transmit_changed authority signals', () => {
    const browser = createManagedBrowserDependencies();
    expect(h.messages.size).toBe(0);
    const signals: number[] = [];
    const off = browser.dependencies.onAuthorityChanged?.(() => signals.push(signals.length));
    expect(h.messages.size).toBe(1);

    h.sessionState = 'connected';
    emitMessage({ type: 'event', name: 'managed_transmit_changed', data: {} });
    emitMessage({ type: 'event', name: 'freq_changed', data: {} });
    emitMessage({ type: 'state_update', data: {} });
    expect(signals.length).toBe(1);

    h.sessionState = 'disconnected';
    emitMessage({ type: 'event', name: 'managed_transmit_changed', data: {} });
    expect(signals.length).toBe(1);

    h.sessionState = 'connected';
    emitMessage({ type: 'event', name: 'managed_transmit_changed', data: {} });
    expect(signals.length).toBe(2);

    off?.();
    emitMessage({ type: 'event', name: 'managed_transmit_changed', data: {} });
    expect(signals.length).toBe(2);
  });

  it('releases the authority signal subscription on dispose', () => {
    const browser = createManagedBrowserDependencies();
    browser.dependencies.onAuthorityChanged?.(() => {});
    expect(h.messages.size).toBe(1);
    browser.dispose();
    expect(h.messages.size).toBe(0);
  });
});
