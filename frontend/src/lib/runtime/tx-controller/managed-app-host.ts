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
}>;
export type ManagedAppTxHost = Readonly<{
  refreshAuthority(): void;
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
  const refreshAuthority = () => { if (!disposed) void controller.refresh(); };
  const release = async () => { if (!disposed) await controller.releaseSession(); };
  const facade = Object.freeze<ManagedAppTxController>({
    snapshot: () => controller.snapshot(),
    subscribe: (listener) => disposed ? noop : controller.subscribe(listener),
    pttOn: () => { if (!disposed) controller.pttOn(); },
    pttOff: () => { if (!disposed) void controller.pttOff(); },
    transmitOn: () => { if (!disposed) controller.transmitOn(); },
    forceOff: () => { if (!disposed) void controller.forceOff(); },
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
      if (session.state === 'connected') refreshAuthority();
      else void controller.releaseSession().finally(() => controller.abandonSession());
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
