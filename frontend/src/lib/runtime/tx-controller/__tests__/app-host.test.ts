import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TxControllerDependencies } from '../controller';
import type { PttMarker, TxEvent, TxState } from '../model';
import type { ControlSessionTransition } from '$lib/transport/ws-client'; type SessionHandler = (projection: any, session: ControlSessionTransition) => void;
const h = vi.hoisted(() => ({
  contexts: new Map<unknown, unknown>(), factory: vi.fn(), controllers: 0,
  events: [] as string[], effects: [] as string[],
  session: undefined as SessionHandler | undefined,
  lifecycle: undefined as (() => void) | undefined, barrier: undefined as (() => Promise<void>) | undefined,
  offSession: vi.fn(), offLifecycle: vi.fn(), offBarrier: vi.fn(), disposeDependencies: vi.fn(),
}));
vi.mock('svelte', () => ({ getContext: (key: unknown) => h.contexts.get(key),
  setContext: (key: unknown, value: unknown) => { h.contexts.set(key, value); return value; } }));
vi.mock('../browser-dependencies', () => ({ createBrowserTxControllerDependencies: h.factory }));
vi.mock('../controller', async (original) => {
  const actual = await original<typeof import('../controller')>();
  return { ...actual, TxController: class extends actual.TxController {
    constructor(epoch: number, baseline: PttMarker, dependencies: TxControllerDependencies) { super(epoch, baseline, dependencies); h.controllers += 1; }
    override dispatch(event: TxEvent) { h.events.push(event.type); super.dispatch(event); }
  } };
});
import { getAppTxController, provideAppTxControllerHost } from '../app-host';
const projection = (session: ControlSessionTransition, seq: number) => ({
  epoch: session.epoch, facts: null, modInputSource: { status: 'unknown' as const },
  eligibility: { catPtt: session.state === 'connected', browserTxAudio: session.state === 'connected',
    controlLive: session.state === 'connected', permit: 'allowed' as const,
    target: { receiver: 'MAIN' as const, slot: 'A' as const, frequencyHz: 100 } },
  ptt: { value: false, observed: true, fresh: session.state === 'connected', source: 'radio-readback' as const,
    marker: { authorityEpoch: session.epoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq } },
});
const bindings = () => ({
  registerPreDisconnectBarrier: (barrier: () => Promise<void>) => { h.barrier = barrier; return h.offBarrier; },
  lifecycleReleaseSource: (release: () => void) => { h.lifecycle = release; return h.offLifecycle; },
});
beforeEach(() => {
  h.contexts.clear(); h.events.length = 0; h.effects.length = 0; h.controllers = 0;
  h.session = undefined; h.lifecycle = undefined; h.barrier = undefined;
  for (const mock of [h.factory, h.offSession, h.offLifecycle, h.offBarrier, h.disposeDependencies]) mock.mockReset();
  h.factory.mockImplementation(() => {
    let seq = 0; let id = 0;
    const dependencies: TxControllerDependencies = {
      startAudio: async () => { h.effects.push('audio'); return null; },
      sendPtt: (command) => { h.effects.push(command); }, stopLocalAudio: () => h.effects.push('stop'),
      restoreMod: vi.fn(), commandId: (command) => `${command}-${++id}`,
      schedule: vi.fn(() => ({})), cancel: vi.fn(),
      timeoutMs: { 'audio-start': 5_000, 'on-confirmation': 5_000, 'off-confirmation': 5_000 },
    };
    return {
      dependencies, projectAuthority: (session: ControlSessionTransition) => projection(session, session.state === 'connected' ? ++seq : seq),
      subscribeSession: (handler: SessionHandler) => {
        h.session = (_projection, session) => handler!(projection(session, session.state === 'connected' ? ++seq : seq), session); return h.offSession;
      },
      bindLifecycleRelease: (source: (release: () => void) => () => void, release: () => void) => source(release),
      dispose: h.disposeDependencies,
    };
  });
});
describe('App TxController host', () => {
  it('keeps import/context reads inert and provides one private owner with stable identity', () => {
    expect(() => getAppTxController()).toThrow(/not provided/); expect(h.factory).not.toHaveBeenCalled();
    const host = provideAppTxControllerHost(bindings()); const facade = getAppTxController();
    expect([h.factory.mock.calls.length, h.controllers]).toEqual([1, 1]);
    expect(Object.keys(facade).sort()).toEqual(['release', 'resetFault', 'setIntent', 'snapshot', 'start', 'subscribe']);
    expect(Object.keys(host).sort()).toEqual(['dispose', 'refreshAuthority', 'release']);
    expect(Object.isFrozen(facade)).toBe(true); expect(getAppTxController()).toBe(facade);
    expect(() => provideAppTxControllerHost(bindings())).toThrow(/already provided/);
    expect([h.factory.mock.calls.length, h.controllers]).toEqual([1, 1]);
  });
  it('orders session epoch first and makes every stale callback inert after robust disposal', () => {
    const host = provideAppTxControllerHost(bindings());
    h.session!(projection({ state: 'connected', epoch: 4 }, 1), { state: 'connected', epoch: 4 });
    expect(h.events).toEqual(['epoch', 'authority']);
    h.offBarrier.mockImplementationOnce(() => { throw new Error('cleanup'); });
    host.dispose(); host.dispose();
    expect([h.offBarrier, h.offSession, h.offLifecycle, h.disposeDependencies].map((fn) => fn.mock.calls.length)).toEqual([1, 1, 1, 1]);
    const count = h.events.length;
    h.session!(projection({ state: 'connected', epoch: 5 }, 2), { state: 'connected', epoch: 5 });
    h.lifecycle!(); host.refreshAuthority(); getAppTxController().start('stale', 'lease', 'momentary');
    expect(h.events).toHaveLength(count);
  });
  it('dispatches release synchronously, shares its bounded promise, and retains uncertain OFF state', async () => {
    const host = provideAppTxControllerHost(bindings()); const facade = getAppTxController();
    h.session!(projection({ state: 'connected', epoch: 1 }, 1), { state: 'connected', epoch: 1 });
    host.refreshAuthority();
    facade.start('desktop', 'lease', 'momentary');
    await Promise.resolve(); await Promise.resolve();
    expect(h.effects).toEqual(['audio', 'on']);
    const release = host.release();
    h.lifecycle!();
    expect(h.barrier!()).toBe(release); expect(h.effects).toEqual(['audio', 'on', 'off', 'stop']);
    expect(facade.snapshot()).toMatchObject({ phase: 'releasing', txRisk: 'uncertain',
      pendingOff: { originalEpoch: 1 }, radioTx: 'off' });
    await release; expect(facade.snapshot().phase).toBe('releasing');
  });
  it('detaches and deeply freezes snapshot and subscription views', async () => {
    const host = provideAppTxControllerHost(bindings()); const facade = getAppTxController();
    h.session!(projection({ state: 'connected', epoch: 1 }, 1), { state: 'connected', epoch: 1 }); host.refreshAuthority();
    let observed: TxState | undefined; facade.subscribe((state) => { observed = state as TxState; });
    facade.start('desktop', 'lease', 'momentary');
    const snapshot = facade.snapshot() as TxState; const emitted = observed!;
    expect(snapshot).not.toBe(emitted);
    expect([snapshot, snapshot.guard, snapshot.leaseTarget, emitted, emitted.guard, emitted.leaseTarget].every(Object.isFrozen)).toBe(true);
    for (const mutate of [() => { snapshot.authorityEpoch = 99; }, () => { snapshot.guard!.generation = 99; },
      () => { emitted.radioTx = 'on'; }, () => { emitted.pttMarker.authorityEpoch = 99; }, () => { emitted.leaseTarget!.frequencyHz = 999; }]) expect(mutate).toThrow(TypeError);
    await Promise.resolve(); await Promise.resolve();
    const later = facade.snapshot();
    expect(later).toMatchObject({ authorityEpoch: 1, radioTx: 'off', pttMarker: { authorityEpoch: 1 }, guard: { leaseId: 'lease', generation: 1, authorityEpoch: 1 },
      leaseTarget: { receiver: 'MAIN', slot: 'A', frequencyHz: 100 } });
    facade.release('desktop', later.guard!); expect(h.effects).toEqual(['audio', 'on', 'off', 'stop']);
  });
});
