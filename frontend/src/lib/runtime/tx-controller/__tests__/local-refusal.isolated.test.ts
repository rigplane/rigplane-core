import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';

const h = vi.hoisted(() => ({
  contexts: new Map<unknown, unknown>(),
  sessions: new Set<(session: ControlSessionTransition) => void>(),
  messages: new Set<(message: unknown) => void>(),
  session: { state: 'disconnected', epoch: 0 } as ControlSessionTransition,
  get: vi.fn<() => Promise<ManagedTransmitDocument>>(),
  command: vi.fn<() => Promise<'accepted' | 'rejected'>>(),
  start: vi.fn<() => Promise<string | null>>(), stop: vi.fn(),
  notify: vi.fn(), send: vi.fn(),
}));
vi.mock('svelte', () => ({
  getContext: (key: unknown) => h.contexts.get(key),
  setContext: (key: unknown, value: unknown) => { h.contexts.set(key, value); return value; },
}));
vi.mock('$lib/transport/managed-transmit-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/managed-transmit-client')>(),
  ManagedTransmitClient: class {
    snapshot = h.get;
    command = h.command;
  },
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  startManagedTx: h.start, stopLocalAudio: h.stop, onTxAudioDied: () => () => {},
}) }));
vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: () => h.session, emitLocalNotification: h.notify, sendCommand: h.send,
  onCommandDelivery: () => () => {},
  onControlSessionTransition: (fn: (session: ControlSessionTransition) => void) => {
    h.sessions.add(fn); return () => h.sessions.delete(fn);
  },
  onMessage: (fn: (message: unknown) => void) => {
    h.messages.add(fn); return () => h.messages.delete(fn);
  },
}));

import { createManagedBrowserDependencies } from '../browser-dependencies';
import { ManagedTxController } from '../managed-controller';
import { getManagedAppTxController, provideManagedAppTxHost } from '../managed-app-host';
import { ManagedTransmitError } from '$lib/transport/managed-transmit-client';
import {
  invalidateManagedTransmit, managedTransmitIsStale, receiveManagedTransmitSnapshot,
} from '$lib/stores/managed-transmit.svelte';
import { setLocale } from '$lib/i18n';

let sampled = 0;
const snapshot = (): ManagedTransmitDocument => ({
  schemaVersion: 1, sampledAt: new Date(Date.UTC(2026, 10, 28, 0, 0, ++sampled)).toISOString(),
  managedTransmit: {
    status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null,
    lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'off' },
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const cleanup: Array<() => void> = [];
function stack() {
  const browser = createManagedBrowserDependencies();
  const controller = new ManagedTxController(browser.dependencies);
  const refusal = vi.spyOn(browser.dependencies, 'onLatchRefusal');
  cleanup.push(() => { controller.dispose(); browser.dispose(); });
  return { browser, controller, refusal };
}
const session = (state: ControlSessionTransition['state'], epoch: number) => {
  h.session = { state, epoch };
  for (const listener of h.sessions) listener(h.session);
};

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  h.contexts.clear(); h.sessions.clear(); h.messages.clear();
  h.session = { state: 'connected', epoch: 4 };
  h.get.mockReset().mockImplementation(async () => snapshot());
  h.command.mockReset().mockResolvedValue('accepted');
  h.start.mockReset().mockResolvedValue(null);
  setLocale('en-US');
  invalidateManagedTransmit();
  receiveManagedTransmitSnapshot(snapshot());
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
});
afterEach(async () => {
  for (const stop of cleanup.splice(0)) stop();
  await vi.advanceTimersByTimeAsync(0);
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('local TX refusals (MOR-4934)', () => {
  it('refuses stale authority immediately, publishes it, and recovers without replaying ON', async () => {
    const recovery = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(recovery.promise);
    const { controller, refusal } = stack();
    const publish = vi.fn();
    controller.subscribe(publish);
    invalidateManagedTransmit();
    expect(controller.snapshot().fresh).toBe(true);

    controller.transmitOn();

    expect(refusal).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      operation: 'transmit_on', code: 'authority_unavailable',
    }));
    expect(h.notify).toHaveBeenCalledExactlyOnceWith('error', expect.stringContaining('Transmit control is unavailable'), 'txAuthorityUnavailable');
    expect(console.info).toHaveBeenCalledWith('[tx] Handled start refusal', {
      operation: 'transmit_on', reason: 'authority_unavailable', source: 'local',
      controlSession: { state: 'connected', epoch: 4 },
      cachedDocument: { status: 'available', stale: true, sampledAt: expect.any(String) },
      visibility: document.visibilityState,
    });
    expect(publish).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ fresh: false }));
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.start).not.toHaveBeenCalled();
    expect(h.command).not.toHaveBeenCalled();
    expect(h.send).not.toHaveBeenCalled();

    recovery.resolve(snapshot());
    await vi.waitFor(() => expect(controller.snapshot().fresh).toBe(true));
    expect(h.command).not.toHaveBeenCalled();
    controller.transmitOn();
    await vi.waitFor(() => expect(h.command).toHaveBeenCalledExactlyOnceWith('transmit_on'));
  });

  it('does not wait for an unresolved authority read on a fresh press', async () => {
    const recovery = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(recovery.promise);
    const { controller, refusal } = stack();
    const refreshing = controller.refresh();
    controller.transmitOn();
    await vi.waitFor(() => expect(h.command).toHaveBeenCalledWith('transmit_on'));
    expect(refusal).not.toHaveBeenCalled();
    recovery.resolve(snapshot());
    await refreshing;
  });

  it('reports unavailable authority documents without inventing a more specific stale cause', () => {
    receiveManagedTransmitSnapshot({
      ...snapshot(), managedTransmit: { status: 'unavailable', reason: 'authority_not_composed' },
    });
    const { controller, refusal } = stack();
    controller.transmitOn();
    expect(refusal.mock.calls[0][0]).toMatchObject({ code: 'authority_unavailable' });
    expect(console.info).toHaveBeenCalledWith('[tx] Handled start refusal', expect.objectContaining({
      cachedDocument: { status: 'unavailable', stale: true, sampledAt: expect.any(String) },
    }));
    expect(h.start).not.toHaveBeenCalled();
    expect(h.command).not.toHaveBeenCalled();
  });

  it('keeps ForceOFF and PTT release reachable while stale recovery is unresolved', async () => {
    const recovery = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(recovery.promise);
    const on = deferred<'accepted' | 'rejected'>();
    const { controller, browser } = stack();
    const sendPtt = vi.spyOn(browser.dependencies, 'sendPtt').mockImplementation((operation) =>
      operation === 'ptt_on' ? on.promise : Promise.resolve('accepted'));
    controller.pttOn();
    await vi.waitFor(() => expect(sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on'));
    invalidateManagedTransmit();
    controller.transmitOn();
    await controller.pttOff();
    await controller.forceOff();
    expect(sendPtt.mock.calls).toEqual([['ptt_on'], ['ptt_off']]);
    expect(h.command).toHaveBeenCalledExactlyOnceWith('force_off');
    on.resolve('accepted');
    recovery.resolve(snapshot());
    await vi.advanceTimersByTimeAsync(0);
  });

  it.each(['ptt_on', 'transmit_on'] as const)('reports microphone refusal for %s without sending ON', async (operation) => {
    h.start.mockResolvedValueOnce('Microphone access denied');
    const { controller, refusal } = stack();
    if (operation === 'ptt_on') controller.pttOn(); else controller.transmitOn();
    await vi.waitFor(() => expect(refusal).toHaveBeenCalledTimes(1));
    expect(refusal.mock.calls[0][0]).toMatchObject({ operation, code: 'tx_audio_unavailable' });
    expect(h.notify).toHaveBeenCalledExactlyOnceWith(
      'error', expect.stringContaining('Radio was not keyed'), 'txAudioStartRefused',
      { reason: 'Microphone access denied' },
    );
    expect(h.notify.mock.calls[0][1]).toContain('Microphone access denied');
    expect(console.info).toHaveBeenCalledWith('[tx] Handled start refusal', expect.objectContaining({
      operation, reason: 'tx_audio_unavailable', source: 'local',
    }));
    expect(h.command).not.toHaveBeenCalled();
    expect(h.send).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
    if (operation === 'transmit_on') {
      controller.transmitOn();
      await vi.waitFor(() => expect(h.command).toHaveBeenCalledExactlyOnceWith('transmit_on'));
    }
  });

  it('handles an unexpected audio exception without an unhandled rejection', async () => {
    h.start.mockRejectedValueOnce(new Error('capture failed'));
    const { controller, refusal } = stack();
    controller.transmitOn();
    await vi.waitFor(() => expect(refusal).toHaveBeenCalledTimes(1));
    expect(refusal.mock.calls[0][0]).toMatchObject({ code: 'tx_audio_unavailable' });
    expect(h.command).not.toHaveBeenCalled();
  });

  it('does not publish a microphone refusal from a cancelled attempt', async () => {
    const audio = deferred<string | null>();
    h.start.mockReturnValueOnce(audio.promise);
    const { controller, refusal } = stack();
    controller.transmitOn();
    await controller.forceOff();
    audio.resolve('Microphone access denied');
    await vi.advanceTimersByTimeAsync(0);
    expect(h.stop).toHaveBeenCalledTimes(1);
    expect(refusal).not.toHaveBeenCalled();
    expect(h.notify).not.toHaveBeenCalled();
    expect(h.command).toHaveBeenCalledExactlyOnceWith('force_off');
  });

  it('reports only the current press when two presses share a refused microphone preparation', async () => {
    const audio = deferred<string | null>();
    h.start.mockReturnValueOnce(audio.promise);
    const { controller, refusal } = stack();
    controller.transmitOn();
    controller.transmitOn();
    audio.resolve('Microphone access denied');
    await vi.advanceTimersByTimeAsync(0);
    expect(h.start).toHaveBeenCalledTimes(1);
    expect(refusal).toHaveBeenCalledTimes(1);
    expect(h.notify).toHaveBeenCalledTimes(1);
    expect(h.command).not.toHaveBeenCalled();
    expect(h.stop).toHaveBeenCalledTimes(1);
  });

  it.each([
    [new ManagedTransmitError('tx_held_by_other', 409), 'tx_held_by_other', 'txHeldByOther'],
    [new Error('offline'), 'tx_request_failed', 'txStartRefused'],
  ] as const)('preserves command refusal %s through the browser seam', async (error, reason, toast) => {
    h.command.mockRejectedValueOnce(error);
    const { controller } = stack();
    controller.transmitOn();
    await vi.waitFor(() => expect(h.notify).toHaveBeenCalledTimes(1));
    expect(h.notify).toHaveBeenCalledWith('error', expect.any(String), toast);
    expect(console.info).toHaveBeenCalledWith('[tx] Handled start refusal', expect.objectContaining({
      operation: 'transmit_on', reason, source: 'command',
    }));
  });

  it('does not consume a pending HTTP refusal when a local refusal is reported', async () => {
    const recovery = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(recovery.promise);
    h.command.mockRejectedValueOnce(new ManagedTransmitError('tx_held_by_other', 409));
    const { browser, controller } = stack();
    const command = browser.dependencies.submit('transmit_on');
    await vi.waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    invalidateManagedTransmit();
    controller.transmitOn();
    recovery.resolve(snapshot());
    await expect(command).resolves.toBe('rejected');
    browser.dependencies.onLatchRefusal?.();
    expect(h.notify.mock.calls.map((call) => call[2])).toEqual(['txAuthorityUnavailable', 'txHeldByOther']);
  });

  it('recovers a connected host after failed GET, ignores ordinary frames, and fences old-session results', async () => {
    const refreshes = vi.spyOn(ManagedTxController.prototype, 'refresh');
    h.session = { state: 'disconnected', epoch: 0 };
    h.get.mockRejectedValueOnce(new Error('snapshot GET failed'));
    const host = provideManagedAppTxHost({
      registerPreDisconnectBarrier: () => () => {}, lifecycleReleaseSource: () => () => {},
    });
    cleanup.push(() => host.dispose());
    const controller = getManagedAppTxController();
    host.refreshAuthority(3);
    session('connected', 4);
    await refreshes.mock.results[0].value;
    expect(controller.snapshot().fresh).toBe(false);
    expect(managedTransmitIsStale()).toBe(true);

    const oldRead = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(oldRead.promise);
    controller.transmitOn();
    expect(h.notify).toHaveBeenCalledWith('error', expect.any(String), 'txAuthorityUnavailable');
    expect(h.get).toHaveBeenCalledTimes(2);
    for (const name of ['audio_session', 'freq_changed']) {
      for (const listener of h.messages) listener({ type: 'event', name, data: {} });
    }
    for (const listener of h.messages) listener({ type: 'state_update', data: { ptt: false } });
    expect(h.get).toHaveBeenCalledTimes(2);
    expect(managedTransmitIsStale()).toBe(true);

    session('disconnected', 5);
    const newRead = deferred<ManagedTransmitDocument>();
    h.get.mockReturnValueOnce(newRead.promise);
    session('connected', 6);
    newRead.resolve(snapshot());
    await vi.waitFor(() => expect(controller.snapshot().fresh).toBe(true));
    const recovered = controller.snapshot();
    const unavailable: ManagedTransmitDocument = {
      ...snapshot(), managedTransmit: { status: 'unavailable', reason: 'authority_not_composed' },
    };
    oldRead.resolve(unavailable);
    await refreshes.mock.results[1].value;
    expect(h.get).toHaveBeenCalledTimes(3);
    expect(controller.snapshot()).toEqual(recovered);
    expect(managedTransmitIsStale()).toBe(false);
    expect(h.command).not.toHaveBeenCalled();
  });
});
