import { getContext, setContext } from 'svelte';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { createBrowserTxControllerDependencies } from './browser-dependencies';
import { TxController } from './controller';
import type { AppAuthorityProjection } from './app-authority';
import type { TxGuard, TxIntent, TxState } from './model';
type Intent = Exclude<TxIntent, null>; type DeepReadonly<T> = T extends object ? { readonly [K in keyof T]: DeepReadonly<T[K]> } : T;
type StateView = DeepReadonly<TxState>; type Listener = (state: StateView) => void; type LifecycleSource = (release: () => void) => () => void;
export type AppTxController = Readonly<{
  snapshot(): StateView; subscribe(listener: Listener): () => void;
  start(sourceId: string, leaseId: string, intent: Intent): void; resetFault(): void;
  setIntent(sourceId: string, guard: Readonly<TxGuard>, intent: Intent): void; release(sourceId: string, guard: Readonly<TxGuard>): void;
}>;
export type AppTxControllerHost = Readonly<{ refreshAuthority(): void; release(): Promise<void>; dispose(): void }>;
export interface AppTxControllerHostBindings { registerPreDisconnectBarrier(barrier: () => Promise<void>): () => void; lifecycleReleaseSource: LifecycleSource }
const contextKey = Symbol('AppTxController'); const noop = () => {};
function immutableCopy<T>(value: T): DeepReadonly<T> {
  if (value === null || typeof value !== 'object') return value as DeepReadonly<T>;
  const copy = Array.isArray(value) ? value.map(immutableCopy) : Object.fromEntries(Object.entries(value).map(([key, child]) => [key, immutableCopy(child)]));
  return Object.freeze(copy) as DeepReadonly<T>;
}
export function getAppTxController(): AppTxController {
  const controller = getContext<AppTxController | undefined>(contextKey); if (!controller) throw new Error('App TxController host is not provided'); return controller;
}
export function provideAppTxControllerHost(bindings: AppTxControllerHostBindings): AppTxControllerHost {
  if (getContext<AppTxController | undefined>(contextKey)) throw new Error('App TxController host is already provided');
  const browser = createBrowserTxControllerDependencies();
  let session: ControlSessionTransition = { state: 'disconnected', epoch: 0 };
  let authority = browser.projectAuthority(session);
  const controller = new TxController(authority.epoch, authority.ptt.marker, browser.dependencies);
  const subscriptions = new Set<() => void>();
  let disposed = false; let inFlight: Promise<void> | null = null;
  let offSession = noop; let offBarrier = noop; let offLifecycle = noop;
  const applyAuthority = (next: AppAuthorityProjection) => {
    const offCommandId = browser.dependencies.commandId('off');
    if (next.epoch > controller.snapshot().authorityEpoch)
      controller.dispatch({ type: 'epoch', epoch: next.epoch, baseline: next.ptt.marker, offCommandId });
    controller.dispatch({ type: 'authority', epoch: next.epoch, ptt: next.ptt, eligibility: next.eligibility, offCommandId });
  };
  const refreshAuthority = () => {
    if (disposed) return;
    authority = browser.projectAuthority(session);
    if (controller.snapshot().guard) applyAuthority(authority);
  };
  const release = (): Promise<void> => {
    if (inFlight) return inFlight;
    if (!disposed) {
      const guard = controller.snapshot().guard;
      if (guard) try { controller.dispatch({
        type: 'release', guard, commandId: browser.dependencies.commandId('off'),
      }); } catch { /* teardown must remain bounded */ }
    }
    let bounded!: Promise<void>;
    bounded = Promise.resolve().finally(() => { if (inFlight === bounded) inFlight = null; });
    return (inFlight = bounded);
  };
  const facade = Object.freeze<AppTxController>({
    snapshot: () => immutableCopy(controller.snapshot()),
    subscribe: (listener) => {
      if (disposed) return noop;
      const stop = controller.subscribe((state) => listener(immutableCopy(state))); let active = true;
      const remove = () => { if (active) { active = false; subscriptions.delete(remove); stop(); } };
      subscriptions.add(remove); return remove;
    },
    start: (sourceId, leaseId, intent) => { if (!disposed) controller.dispatch(
      { type: 'start', sourceId, leaseId, intent, eligibility: authority.eligibility, ptt: authority.ptt }); },
    setIntent: (sourceId, guard, intent) => { if (!disposed) controller.dispatch(
      { type: 'intent', sourceId, guard: { ...guard }, intent }); },
    release: (sourceId, guard) => { if (!disposed) controller.dispatch(
      { type: 'release', sourceId, guard: { ...guard }, commandId: browser.dependencies.commandId('off') }); },
    resetFault: () => { if (!disposed) controller.dispatch({ type: 'reset-fault' }); },
  });
  const dispose = () => {
    if (disposed) return;
    void release(); disposed = true;
    for (const cleanup of [offBarrier, offSession, offLifecycle, ...subscriptions]) try { cleanup(); } catch { /* continue */ }
    subscriptions.clear(); try { browser.dispose(); } catch { /* continue */ }
  };
  try {
    offSession = browser.subscribeSession((next, transition) => {
      if (disposed) return;
      session = transition; authority = next; applyAuthority(next);
    });
    offBarrier = bindings.registerPreDisconnectBarrier(release);
    offLifecycle = browser.bindLifecycleRelease(bindings.lifecycleReleaseSource, () => { void release(); });
    setContext(contextKey, facade);
    return Object.freeze({ refreshAuthority, release, dispose });
  } catch (error) { dispose(); throw error; }
}
