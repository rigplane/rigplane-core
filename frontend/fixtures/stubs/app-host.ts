/**
 * MOR-1070 stub for the managed-app-host TX facade.
 *
 * Fixture views consume the server-shaped snapshot; the four managed intent
 * methods only record delivery calls.
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
