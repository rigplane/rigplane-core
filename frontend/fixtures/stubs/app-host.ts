/**
 * MOR-1070 stub for `$lib/runtime/tx-controller/app-host`.
 *
 * Same surface the component tests mock (`snapshot` / `subscribe` / `start` /
 * `setIntent` / `release` / `resetFault`), so the wiring's real lease identity
 * discipline runs unmodified — the calls are recorded rather than sent.
 */
import { harness, record, type TxSnapshot } from '../harness-state';

export function getAppTxController() {
  return {
    snapshot: (): TxSnapshot => harness.tx,
    subscribe: (listener: (next: TxSnapshot) => void): (() => void) => {
      harness.listeners.add(listener);
      return () => { harness.listeners.delete(listener); };
    },
    start: (...args: unknown[]): void => record('tx.start', args),
    setIntent: (...args: unknown[]): void => record('tx.setIntent', args),
    release: (...args: unknown[]): void => record('tx.release', args),
    resetFault: (...args: unknown[]): void => record('tx.resetFault', args),
  };
}
