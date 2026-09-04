/**
 * MOR-1070 stub for the canonical managed App TX facade.
 *
 * It exposes no browser lease or reducer controls: fixture views consume only
 * the server-shaped snapshot and the four managed intents.
 */
import { harness, record, type TxSnapshot } from '../harness-state';

export function getManagedAppTxController() {
  return {
    snapshot: (): TxSnapshot => harness.tx,
    subscribe: (listener: (next: TxSnapshot) => void): (() => void) => {
      harness.listeners.add(listener);
      return () => { harness.listeners.delete(listener); };
    },
    pttOn: (): void => record('tx.pttOn', []),
    pttOff: (): void => record('tx.pttOff', []),
    transmitOn: (): void => record('tx.transmitOn', []),
    forceOff: (): void => record('tx.forceOff', []),
  };
}
