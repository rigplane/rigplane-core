/**
 * MOR-1070 stub for the managed-app-host TX facade.
 *
 * Fixture views consume the server-shaped snapshot; the four managed intent
 * methods only record delivery calls.
 */
import { harness, record, type TxSnapshot } from '../harness-state';

/** One fixture-wide read-only facade, matching production's single App root. */
const fixtureController = Object.freeze({
  snapshot: (): TxSnapshot => harness.tx,
  subscribe: (listener: (next: TxSnapshot) => void): (() => void) => {
    harness.listeners.add(listener);
    return () => { harness.listeners.delete(listener); };
  },
  pttOn: (): void => record('tx.pttOn', []),
  pttOff: (): void => record('tx.pttOff', []),
  transmitOn: (): void => record('tx.transmitOn', []),
  forceOff: (): void => record('tx.forceOff', []),
  /**
   * Offline-fixture seam only. Record the intent but do not manufacture an
   * optimistic setting, countdown, TX actuation, or timer. A real server
   * snapshot remains the sole source of canonical TOT state.
   */
  setTot: async (configuredSeconds: number | null): Promise<void> => {
    record('tx.setTot', [configuredSeconds]);
  },
});

export function getManagedAppTxController() {
  return fixtureController;
}
