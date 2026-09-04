import { getTxAudioControl } from '$lib/runtime/adapters/tx-adapter';
import {
  invalidateManagedTransmit, managedTransmitIsStale, managedTransmitSnapshot,
  managedTransmitRemainingMs, refreshManagedTransmit, setManagedTransmitTot,
  submitManagedTransmit,
} from '$lib/stores/managed-transmit.svelte';
import { makeCommandId } from '$lib/types/protocol';
import {
  onCommandDelivery, onControlSessionTransition, sendCommand,
  type CommandDeliveryEvent, type ControlSessionTransition,
} from '$lib/transport/ws-client';
import type { ManagedTxDependencies, PttOperation } from './managed-controller';
import { projectManagedTx } from './managed-state';

type Unsubscribe = () => void;
const noop = () => {};
const PRESENTATION_TICK_MS = 250;
const terminalOutcome = (event: CommandDeliveryEvent): 'accepted' | 'rejected' | null => {
  if (event.kind === 'response-ok') return 'accepted';
  if (event.kind === 'response-error' || event.kind === 'error') return 'rejected';
  return null;
};

/** One browser transport owner: terminal WS PTT delivery plus canonical HTTP projection. */
export function createManagedBrowserDependencies() {
  const audio = getTxAudioControl();
  const cleanups = new Set<Unsubscribe>();
  const pending = new Set<(outcome: 'accepted' | 'rejected') => void>();
  const presentationListeners = new Set<() => void>();
  let presentationTimer: ReturnType<typeof setInterval> | null = null;
  let disposed = false;
  const stopPresentationTicker = () => {
    if (presentationTimer === null) return;
    clearInterval(presentationTimer);
    presentationTimer = null;
  };
  const startPresentationTicker = () => {
    if (disposed || presentationTimer !== null || presentationListeners.size === 0) return;
    presentationTimer = setInterval(() => {
      if (disposed) return;
      for (const listener of presentationListeners) listener();
    }, PRESENTATION_TICK_MS);
  };
  const onPresentationTick = (listener: () => void): Unsubscribe => {
    if (disposed) return noop;
    presentationListeners.add(listener);
    startPresentationTicker();
    return () => {
      presentationListeners.delete(listener);
      if (presentationListeners.size === 0) stopPresentationTicker();
    };
  };
  const track = (unsubscribe: Unsubscribe): Unsubscribe => {
    if (disposed) { unsubscribe(); return noop; }
    let active = true;
    const remove = () => {
      if (!active) return;
      active = false;
      cleanups.delete(remove);
      unsubscribe();
    };
    cleanups.add(remove);
    return remove;
  };
  const sendPtt = (operation: PttOperation): Promise<'accepted' | 'rejected'> => {
    if (disposed) return Promise.resolve('rejected');
    const commandId = makeCommandId();
    return new Promise((resolve) => {
      let settled = false;
      const finish = (outcome: 'accepted' | 'rejected') => {
        if (settled) return;
        settled = true;
        pending.delete(finish);
        resolve(outcome);
      };
      pending.add(finish);
      let remove = noop;
      remove = track(onCommandDelivery((event) => {
        if (event.commandId !== commandId) return;
        const outcome = terminalOutcome(event);
        if (outcome === null) return;
        remove();
        finish(outcome);
        void refreshManagedTransmit().catch(() => invalidateManagedTransmit());
      }));
      if (!sendCommand(operation, {}, commandId)) {
        remove();
        finish('rejected');
      }
    });
  };
  const dependencies: ManagedTxDependencies = {
    snapshot: () => projectManagedTx(
      managedTransmitSnapshot(), managedTransmitIsStale(), managedTransmitRemainingMs(),
    ),
    refresh: () => refreshManagedTransmit(),
    invalidate: invalidateManagedTransmit,
    sendPtt,
    submit: submitManagedTransmit,
    setTot: setManagedTransmitTot,
    onPresentationTick,
    startAudio: () => disposed
      ? Promise.resolve('TX browser dependencies disposed')
      : audio.startManagedTx(),
    stopLocalAudio: audio.stopLocalAudio,
    onAudioDied: (handler) => disposed ? noop : track(audio.onTxAudioDied(handler)),
  };
  return {
    dependencies,
    subscribeSession: (handler: (session: ControlSessionTransition) => void) => disposed
      ? noop
      : track(onControlSessionTransition((session) => { if (!disposed) handler(session); })),
    dispose: () => {
      if (disposed) return;
      disposed = true;
      stopPresentationTicker();
      presentationListeners.clear();
      for (const finish of pending) finish('rejected');
      pending.clear();
      for (const remove of [...cleanups]) remove();
      cleanups.clear();
    },
  };
}
