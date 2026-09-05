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
  setTot: vi.fn<(configuredSeconds: number | null) => Promise<void>>(async () => {}),
  startAudio: vi.fn(async () => null), stopAudio: vi.fn(),
  state: null as unknown as ManagedTxState, audioDied: undefined as (() => void) | undefined,
  presentationTick: undefined as (() => void) | undefined, offPresentationTick: vi.fn(),
}));
vi.mock('svelte', () => ({
  getContext: (key: unknown) => h.contexts.get(key),
  setContext: (key: unknown, value: unknown) => { h.contexts.set(key, value); return value; },
}));
vi.mock('../browser-dependencies', () => ({ createManagedBrowserDependencies: h.factory }));
import { getManagedAppTxController, provideManagedAppTxHost } from '../managed-app-host';

const idle = (): ManagedTxState => ({
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null, faultDetail: null,
  fresh: true, releaseRequired: false, configuredSeconds: 180,
  remainingMs: null, lastOperation: null,
});
const bindings = () => ({
  registerPreDisconnectBarrier: (barrier: () => Promise<void>) => { h.barrier = barrier; return h.offBarrier; },
  lifecycleReleaseSource: (release: () => void) => { h.lifecycle = release; return h.offLifecycle; },
});
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

beforeEach(() => {
  h.contexts.clear(); h.session = undefined; h.lifecycle = undefined; h.barrier = undefined; h.audioDied = undefined;
  h.presentationTick = undefined;
  h.state = idle();
  for (const mock of [h.factory, h.offSession, h.offLifecycle, h.offBarrier, h.offAudio,
    h.disposeBrowser, h.refresh, h.invalidate, h.sendPtt, h.submit, h.setTot, h.offPresentationTick,
    h.startAudio, h.stopAudio]) mock.mockReset();
  h.refresh.mockResolvedValue(undefined); h.sendPtt.mockResolvedValue('accepted');
  h.submit.mockResolvedValue('accepted'); h.startAudio.mockResolvedValue(null);
  h.setTot.mockResolvedValue(undefined);
  h.factory.mockImplementation(() => {
    const dependencies: ManagedTxDependencies = {
      snapshot: () => h.state, refresh: h.refresh, invalidate: h.invalidate,
      sendPtt: h.sendPtt, submit: h.submit, setTot: h.setTot, startAudio: h.startAudio,
      stopLocalAudio: h.stopAudio,
      onAudioDied: (handler) => { h.audioDied = handler; return h.offAudio; },
      onPresentationTick: (handler) => { h.presentationTick = handler; return h.offPresentationTick; },
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
    expect(Object.keys(facade).sort()).toEqual(['forceOff', 'pttOff', 'pttOn', 'setTot', 'snapshot', 'subscribe', 'transmitOn']);
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

  it.each(['lifecycle', 'pre-disconnect', 'session-close', 'dispose'] as const)(
    '%s preserves another client\'s latched TRANSMIT', async (release) => {
    h.state = { ...idle(), phase: 'active', intent: 'latched', radioTx: 'on', releaseRequired: true };
    const host = provideManagedAppTxHost(bindings());
    if (release === 'lifecycle') h.lifecycle!();
    if (release === 'pre-disconnect') await h.barrier!();
    if (release === 'session-close') h.session!({ state: 'disconnected', epoch: 5 });
    if (release === 'dispose') host.dispose();
    await flush();
    host.dispose();
    await flush();
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.sendPtt).not.toHaveBeenCalled();
    expect(h.startAudio).not.toHaveBeenCalled();
    expect(h.stopAudio).not.toHaveBeenCalled();
  });

  it.each(['lifecycle', 'pre-disconnect', 'session-close', 'dispose'] as const)(
    '%s preserves this browser\'s admitted TRANSMIT while cleaning local audio', async (release) => {
    const host = provideManagedAppTxHost(bindings());
    getManagedAppTxController().transmitOn();
    await vi.waitFor(() => expect(h.submit).toHaveBeenCalledWith('transmit_on'));
    h.state = { ...idle(), phase: 'active', intent: 'latched', radioTx: 'on', releaseRequired: true };
    host.refreshAuthority();
    await flush();
    if (release === 'lifecycle') h.lifecycle!();
    if (release === 'pre-disconnect') await h.barrier!();
    if (release === 'session-close') h.session!({ state: 'disconnected', epoch: 5 });
    if (release === 'dispose') host.dispose();
    await flush();
    host.dispose();
    await flush();
    expect(h.submit.mock.calls.map(([operation]) => operation)).toEqual(['transmit_on']);
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
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on');
    expect(h.sendPtt.mock.calls.map(([operation]) => operation)).toEqual(['ptt_on', 'ptt_off']);
    expect(h.stopAudio).toHaveBeenCalledTimes(1);
  });

  it('makes disposed callbacks and facade actions inert', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    host.dispose();
    await flush();
    const counts = [h.refresh, h.sendPtt, h.submit, h.setTot, h.startAudio]
      .map((mock) => mock.mock.calls.length);
    h.session!({ state: 'connected', epoch: 6 }); h.lifecycle!(); await h.barrier!();
    facade.pttOn(); facade.pttOff(); facade.transmitOn(); facade.forceOff();
    await facade.setTot(240); host.refreshAuthority();
    await flush();
    expect([h.refresh, h.sendPtt, h.submit, h.setTot, h.startAudio]
      .map((mock) => mock.mock.calls.length)).toEqual(counts);
  });

  it('routes fractional and disabled TOT edits through the stable facade', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();

    await facade.setTot(240);
    await facade.setTot(1.5);
    await facade.setTot(null);
    await expect(facade.setTot(0)).rejects.toThrow(/positive finite/);
    await expect(facade.setTot(Number.NaN)).rejects.toThrow(/positive finite/);
    await expect(facade.setTot(Number.POSITIVE_INFINITY)).rejects.toThrow(/positive finite/);

    expect(h.setTot.mock.calls).toEqual([[240], [1.5], [null]]);
    expect(h.invalidate).not.toHaveBeenCalled();
    host.dispose();
    await flush();
  });

  it('publishes stale projection after a failed TOT write', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    let published: ManagedTxState | undefined;
    facade.subscribe((state) => { published = state; });
    h.setTot.mockRejectedValueOnce(new Error('write failed'));
    h.invalidate.mockImplementationOnce(() => {
      h.state = { ...h.state, fresh: false, configuredSeconds: null };
    });

    await expect(facade.setTot(240)).rejects.toThrow('write failed');

    expect(h.invalidate).toHaveBeenCalledTimes(1);
    expect(published).toMatchObject({ fresh: false, configuredSeconds: null });
    host.dispose();
    await flush();
  });

  it('republishes browser-projected countdown ticks and clears that subscription on disposal', async () => {
    const host = provideManagedAppTxHost(bindings());
    const facade = getManagedAppTxController();
    const seen: ManagedTxState[] = [];
    facade.subscribe((state) => seen.push(state));

    h.state = { ...idle(), remainingMs: 900 };
    h.presentationTick!();
    h.state = { ...h.state, fresh: false, remainingMs: null };
    h.presentationTick!();

    expect(seen.map(({ fresh, remainingMs }) => [fresh, remainingMs]))
      .toEqual([[true, 900], [false, null]]);
    host.dispose();
    await flush();
    expect(h.offPresentationTick).toHaveBeenCalledTimes(1);
  });
});
