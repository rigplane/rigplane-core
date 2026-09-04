import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import type { ManagedTxDependencies } from '../managed-controller';
import type { ManagedTxState } from '../managed-state';

type SessionHandler = (session: ControlSessionTransition) => void;
const h = vi.hoisted(() => ({
  contexts: new Map<unknown, unknown>(), factory: vi.fn(), session: undefined as SessionHandler | undefined,
  lifecycle: undefined as (() => void) | undefined, barrier: undefined as (() => Promise<void>) | undefined,
  offSession: vi.fn(), offLifecycle: vi.fn(), offBarrier: vi.fn(), offAudio: vi.fn(), disposeBrowser: vi.fn(),
  refresh: vi.fn(async () => {}), invalidate: vi.fn(),
  sendPtt: vi.fn<(operation: 'ptt_on' | 'ptt_off') => Promise<'accepted' | 'rejected'>>(async () => 'accepted'),
  submit: vi.fn<(operation: 'transmit_on' | 'force_off') => Promise<'accepted' | 'rejected'>>(async () => 'accepted'),
  startAudio: vi.fn(async () => null), stopAudio: vi.fn(),
  state: null as unknown as ManagedTxState, audioDied: undefined as (() => void) | undefined,
}));
vi.mock('svelte', () => ({
  getContext: (key: unknown) => h.contexts.get(key),
  setContext: (key: unknown, value: unknown) => { h.contexts.set(key, value); return value; },
}));
vi.mock('../browser-dependencies', () => ({ createManagedBrowserDependencies: h.factory }));
import { getManagedAppTxController, provideManagedAppTxHost } from '../managed-app-host';

const idle = (): ManagedTxState => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null,
  fresh: true, releaseRequired: false, remainingMs: null, lastOperation: null,
});
const bindings = () => ({
  registerPreDisconnectBarrier: (barrier: () => Promise<void>) => { h.barrier = barrier; return h.offBarrier; },
  lifecycleReleaseSource: (release: () => void) => { h.lifecycle = release; return h.offLifecycle; },
});
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

beforeEach(() => {
  h.contexts.clear(); h.session = undefined; h.lifecycle = undefined; h.barrier = undefined; h.audioDied = undefined;
  h.state = idle();
  for (const mock of [h.factory, h.offSession, h.offLifecycle, h.offBarrier, h.offAudio,
    h.disposeBrowser, h.refresh, h.invalidate, h.sendPtt, h.submit, h.startAudio, h.stopAudio]) mock.mockReset();
  h.refresh.mockResolvedValue(undefined); h.sendPtt.mockResolvedValue('accepted');
  h.submit.mockResolvedValue('accepted'); h.startAudio.mockResolvedValue(null);
  h.factory.mockImplementation(() => {
    const dependencies: ManagedTxDependencies = {
      snapshot: () => h.state, refresh: h.refresh, invalidate: h.invalidate,
      sendPtt: h.sendPtt, submit: h.submit, startAudio: h.startAudio,
      stopLocalAudio: h.stopAudio,
      onAudioDied: (handler) => { h.audioDied = handler; return h.offAudio; },
    };
    return {
      dependencies,
      subscribeSession: (handler: SessionHandler) => { h.session = handler; return h.offSession; },
      dispose: h.disposeBrowser,
    };
  });
});

describe('managed App TX host', () => {
  it('provides one frozen App-root owner with stable identity', () => {
    expect(() => getManagedAppTxController()).toThrow(/not provided/);
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    expect(h.factory).toHaveBeenCalledTimes(1);
    expect(Object.keys(facade).sort()).toEqual(['forceOff', 'pttOff', 'pttOn', 'snapshot', 'subscribe', 'transmitOn']);
    expect(Object.keys(host).sort()).toEqual(['dispose', 'refreshAuthority', 'release']);
    expect(Object.isFrozen(facade)).toBe(true);
    expect(getManagedAppTxController()).toBe(facade);
    expect(() => provideManagedAppTxHost(bindings())).toThrow(/already provided/);
    expect(h.factory).toHaveBeenCalledTimes(1);
  });

  it('registers one lifecycle owner, refreshes on connect, and unregisters exactly once', async () => {
    const host = provideManagedAppTxHost(bindings());
    expect([h.session, h.lifecycle, h.barrier].every(Boolean)).toBe(true);
    h.session!({ state: 'connected', epoch: 4 });
    await flush();
    expect(h.refresh).toHaveBeenCalledTimes(1);
    host.dispose(); host.dispose();
    await flush();
    expect([h.offBarrier, h.offSession, h.offLifecycle, h.offAudio, h.disposeBrowser]
      .map((mock) => mock.mock.calls.length)).toEqual([1, 1, 1, 1, 1]);
  });

  it('coalesces disconnect, lifecycle, and barrier release without replaying ON', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    facade.pttOn();
    await flush();
    expect(h.sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on');
    h.session!({ state: 'disconnected', epoch: 5 });
    h.lifecycle!();
    await h.barrier!();
    await flush();
    expect(h.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
    expect(h.stopAudio).toHaveBeenCalledTimes(1);
    expect(h.submit).not.toHaveBeenCalled();
    host.dispose();
  });

  it('destroy discharges a started TRANSMIT obligation with exactly one ForceOFF', async () => {
    const host = provideManagedAppTxHost(bindings());
    getManagedAppTxController().transmitOn();
    await vi.waitFor(() => expect(h.submit).toHaveBeenCalledWith('transmit_on'));
    host.dispose(); host.dispose();
    await vi.waitFor(() => expect(h.submit.mock.calls.map(([operation]) => operation))
      .toEqual(['transmit_on', 'force_off']));
    expect(h.sendPtt).not.toHaveBeenCalled();
    expect(h.stopAudio).toHaveBeenCalledTimes(1);
  });

  it('releases session PTT over WS before submitting latched TRANSMIT over HTTP', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    facade.pttOn();
    await vi.waitFor(() => expect(h.sendPtt).toHaveBeenCalledExactlyOnceWith('ptt_on'));
    facade.transmitOn();
    await vi.waitFor(() => expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on'));
    expect(h.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
    expect(h.sendPtt.mock.invocationCallOrder[1]).toBeLessThan(h.submit.mock.invocationCallOrder[0]!);
    host.dispose();
    await flush();
  });

  it('makes disposed callbacks and facade actions inert', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    host.dispose();
    await flush();
    const counts = [h.refresh, h.sendPtt, h.submit, h.startAudio].map((mock) => mock.mock.calls.length);
    h.session!({ state: 'connected', epoch: 6 }); h.lifecycle!(); await h.barrier!();
    facade.pttOn(); facade.pttOff(); facade.transmitOn(); facade.forceOff(); host.refreshAuthority();
    await flush();
    expect([h.refresh, h.sendPtt, h.submit, h.startAudio].map((mock) => mock.mock.calls.length)).toEqual(counts);
  });
});
