import { getContext, setContext } from 'svelte';
import { createManagedBrowserDependencies } from './browser-dependencies';
import { ManagedTxController } from './managed-controller';
import type { ManagedTxState } from './managed-state';

type Listener = (state: Readonly<ManagedTxState>) => void;
export type ManagedAppTxController = Readonly<{
  snapshot(): Readonly<ManagedTxState>;
  subscribe(listener: Listener): () => void;
  pttOn(): void;
  pttOff(): void;
  transmitOn(): void;
  forceOff(): void;
  setTot(configuredSeconds: number | null): Promise<void>;
}>;
export type ManagedAppTxHost = Readonly<{
  refreshAuthority(providerGeneration: number | null): void;
  release(): Promise<void>;
  dispose(): void;
}>;
export interface ManagedAppTxHostBindings {
  registerPreDisconnectBarrier(barrier: () => Promise<void>): () => void;
  lifecycleReleaseSource(release: () => void): () => void;
}

const contextKey = Symbol('ManagedAppTxController');
const noop = () => {};

export function getManagedAppTxController(): ManagedAppTxController {
  const controller = getContext<ManagedAppTxController | undefined>(contextKey);
  if (!controller) throw new Error('Managed App TX host is not provided');
  return controller;
}

export function provideManagedAppTxHost(bindings: ManagedAppTxHostBindings): ManagedAppTxHost {
  if (getContext<ManagedAppTxController | undefined>(contextKey)) {
    throw new Error('Managed App TX host is already provided');
  }
  const browser = createManagedBrowserDependencies();
  const controller = new ManagedTxController(browser.dependencies);
  let disposed = false;
  let controlSessionEpoch: number | null = null;
  let providerGeneration: number | null = null;
  let contextRevision = 0;
  let refreshInFlight: { revision: number; promise: Promise<void>; queued: boolean } | null = null;
  let sessionBoundaryRevision = 0;
  const startRefresh = () => {
    if (disposed) return;
    if (controlSessionEpoch === null || providerGeneration === null) return;
    if (refreshInFlight?.revision === contextRevision) {
      refreshInFlight.queued = true;
      return;
    }
    const revision = contextRevision;
    const promise = controller.refresh().finally(() => {
      if (refreshInFlight?.promise !== promise) return;
      const rerun = refreshInFlight.queued && contextRevision === revision;
      refreshInFlight = null;
      if (rerun) startRefresh();
    });
    refreshInFlight = { revision, promise, queued: false };
  };
  const refreshAuthority = (nextProviderGeneration: number | null) => {
    if (disposed) return;
    const next = Number.isSafeInteger(nextProviderGeneration) && nextProviderGeneration! >= 0
      ? nextProviderGeneration
      : null;
    if (providerGeneration !== next) {
      providerGeneration = next;
      contextRevision++;
      controller.invalidate();
    }
    startRefresh();
  };
  const release = async () => { if (!disposed) await controller.releaseSession(); };
  const facade = Object.freeze<ManagedAppTxController>({
    snapshot: () => controller.snapshot(),
    subscribe: (listener) => disposed ? noop : controller.subscribe(listener),
    pttOn: () => { if (!disposed) controller.pttOn(); },
    pttOff: () => { if (!disposed) void controller.pttOff(); },
    transmitOn: () => { if (!disposed) controller.transmitOn(); },
    forceOff: () => { if (!disposed) void controller.forceOff(); },
    setTot: (configuredSeconds) => disposed
      ? Promise.resolve()
      : controller.setTot(configuredSeconds),
  });
  let offSession = noop;
  let offBarrier = noop;
  let offLifecycle = noop;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const cleanup of [offBarrier, offSession, offLifecycle]) cleanup();
    void controller.releaseSession().finally(() => {
      controller.dispose();
      browser.dispose();
    });
  };
  try {
    offSession = browser.subscribeSession((session) => {
      const boundary = ++sessionBoundaryRevision;
      if (session.state === 'connected') {
        if (controlSessionEpoch !== session.epoch) {
          controlSessionEpoch = session.epoch;
          contextRevision++;
          controller.invalidate();
        }
        startRefresh();
      } else {
        controlSessionEpoch = null;
        contextRevision++;
        controller.invalidate();
        void controller.releaseSession().finally(() => {
          if (sessionBoundaryRevision === boundary && controlSessionEpoch === null) {
            controller.abandonSession();
          }
        });
      }
    });
    offBarrier = bindings.registerPreDisconnectBarrier(release);
    offLifecycle = bindings.lifecycleReleaseSource(() => { void release(); });
    setContext(contextKey, facade);
    return Object.freeze({ refreshAuthority, release, dispose });
  } catch (error) {
    dispose();
    throw error;
  }
}
