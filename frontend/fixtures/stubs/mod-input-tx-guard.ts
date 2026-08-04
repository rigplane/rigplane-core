/**
 * MOR-1070 stub for `$lib/runtime/adapters/mod-input-tx-guard.svelte`.
 *
 * The real adapter reads the radio/capability stores and the command bus; the
 * banner it drives is one of the three conditional, zone-less controls named
 * in the MOR-1070 acceptance package's gate item (b), so the harness needs to
 * be able to turn it on deliberately (`zoneless-controls` fixture).
 */
import { harness, record, type ModGuardProps } from '../harness-state';

export function deriveModInputTxGuardProps(): ModGuardProps {
  return harness.modGuard;
}

export function getModInputTxGuardHandlers() {
  return {
    onSetLan: (): void => record('modGuard.setLan', []),
    onDismiss: (): void => record('modGuard.dismiss', []),
  };
}
