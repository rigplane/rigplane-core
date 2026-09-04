import { getContext, setContext } from 'svelte';
import { createBrowserTxControllerDependencies } from './browser-dependencies';
import { TxController } from './controller';
import type { TxState } from './model';

type Listener = (state: Readonly<TxState>) => void;
export type AppTxController = Readonly<{
  snapshot(): Readonly<TxState>;
  subscribe(listener: Listener): () => void;
  pttOn(): void;
  pttOff(): void;
  transmitOn(): void;
  forceOff(): void;
  refresh(): void;
}>;
export type AppTxControllerHost = Readonly<{
  refreshAuthority(): void;
  release(): Promise<void>;
  dispose(): void;
}>;
export interface AppTxControllerHostBindings {
  registerPreDisconnectBarrier(barrier: () => Promise<void>): () => void;
  lifecycleReleaseSource(release: () => void): () => void;
}

const contextKey = Symbol('AppTxController');
const noop = () => {};

export function getAppTxController(): AppTxController {
  const controller = getContext<AppTxController | undefined>(contextKey);
  if (!controller) throw new Error('App TxController host is not provided');
  return controller;
}

export function provideAppTxControllerHost(bindings: AppTxControllerHostBindings): AppTxControllerHost {
  if (getContext<AppTxController | undefined>(contextKey)) throw new Error('App TxController host is already provided');
  const browser = createBrowserTxControllerDependencies();
  const controller = new TxController(browser.dependencies);
  let disposed = false;
  const refreshAuthority = () => { if (!disposed) void controller.refresh(); };
  const release = async () => { if (!disposed) await controller.releaseSession(); };
  const facade = Object.freeze<AppTxController>({
    snapshot: () => controller.snapshot(),
    subscribe: (listener) => disposed ? noop : controller.subscribe(listener),
    pttOn: () => { if (!disposed) controller.requestPtt(); },
    pttOff: () => { if (!disposed) void controller.releasePtt(); },
    transmitOn: () => { if (!disposed) controller.requestTransmit(); },
    forceOff: () => { if (!disposed) void controller.forceOff(); },
    refresh: refreshAuthority,
  });
  let offSession = noop;
  let offBarrier = noop;
  let offLifecycle = noop;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    for (const cleanup of [offBarrier, offSession, offLifecycle]) cleanup();
    controller.dispose();
    browser.dispose();
  };
  try {
    offSession = browser.subscribeSession((session) => {
      if (session.state === 'connected') refreshAuthority();
      else controller.abandonSession();
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
