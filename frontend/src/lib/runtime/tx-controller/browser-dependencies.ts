import { getTxAudioControl } from '$lib/runtime/adapters/tx-adapter';
import {
  invalidateManagedTransmit, managedTransmitIsStale, managedTransmitSnapshot,
  refreshManagedTransmit, submitManagedTransmit,
} from '$lib/stores/managed-transmit.svelte';
import { makeCommandId } from '$lib/types/protocol';
import {
  onCommandDelivery, onControlSessionTransition, sendCommand,
  type CommandDeliveryEvent, type ControlSessionTransition,
} from '$lib/transport/ws-client';
import { projectManagedTransmit } from './model';
import type { PttIntentOperation, TxControllerDependencies } from './controller';

type Unsubscribe = () => void;
const noop = () => {};
const terminalOutcome = (event: CommandDeliveryEvent): 'accepted' | 'rejected' | null => {
  if (event.kind === 'response-ok') return 'accepted';
  if (event.kind === 'response-error' || event.kind === 'error') return 'rejected';
  return null;
};

export function createBrowserTxControllerDependencies() {
  const audio = getTxAudioControl();
  const cleanups = new Set<Unsubscribe>();
  let disposed = false;
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
  const sendPtt = (operation: PttIntentOperation): Promise<'accepted' | 'rejected'> => {
    if (disposed) return Promise.resolve('rejected');
    const commandId = makeCommandId();
    return new Promise((resolve) => {
      let remove = noop;
      remove = track(onCommandDelivery((event) => {
        if (event.commandId !== commandId) return;
        const outcome = terminalOutcome(event);
        if (outcome === null) return;
        remove();
        void refreshManagedTransmit().catch(() => invalidateManagedTransmit());
        resolve(outcome);
      }));
      const submitted = sendCommand(operation, {}, commandId);
      if (!submitted && operation === 'ptt_on') {
        remove();
        resolve('rejected');
      }
    });
  };
  const dependencies: TxControllerDependencies = {
    snapshot: () => projectManagedTransmit(managedTransmitSnapshot(), managedTransmitIsStale()),
    refresh: () => refreshManagedTransmit(),
    invalidate: invalidateManagedTransmit,
    sendPtt,
    submit: (operation) => submitManagedTransmit(operation),
    startAudio: () => disposed ? Promise.resolve('TX browser dependencies disposed') : audio.startTx(),
    stopLocalAudio: () => audio.stopLocalAudio(),
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
      for (const remove of [...cleanups]) remove();
      cleanups.clear();
    },
  };
}
