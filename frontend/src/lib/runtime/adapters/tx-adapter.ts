/**
 * TX adapter — provides audio TX lifecycle callbacks for PTT components.
 *
 * Replaces direct audioManager imports in TxPanel and MobileRadioLayout.
 */

import { runtime } from '../frontend-runtime';
import { armModInputTxGuard } from './mod-input-tx-guard.svelte';

export function getTxAudioControl() {
  return {
    startTx: () => {
      // MOR-617: preflight the MOD-input source at the moment of keying.
      // Warn-only — never blocks or delays the actual TX audio start.
      armModInputTxGuard();
      return runtime.startTx();
    },
    stopTx: () => runtime.stopTx(),
  };
}
