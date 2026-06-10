/**
 * TX adapter — provides audio TX lifecycle callbacks for PTT components.
 *
 * Replaces direct audioManager imports in TxPanel and MobileRadioLayout.
 */

import { runtime } from '../frontend-runtime';
import { armModInputTxGuard } from './mod-input-tx-guard.svelte';
import {
  autoSetLanModInputForTx,
  restoreModInputAfterTx,
} from './mod-input-auto.svelte';

export function getTxAudioControl() {
  return {
    startTx: async (): Promise<string | null> => {
      // MOR-618: opt-in auto-set runs first — its optimistic LAN patch
      // preempts the MOR-617 warning (the guard then sees LAN and stays
      // quiet). No-op while the toggle is OFF (default).
      autoSetLanModInputForTx();
      // MOR-617: preflight the MOD-input source at the moment of keying.
      // Warn-only — never blocks or delays the actual TX audio start.
      armModInputTxGuard();
      const err = await runtime.startTx();
      // TX audio failed to start — undo the auto-set right away
      // (no-op when auto made no change).
      if (err) restoreModInputAfterTx();
      return err;
    },
    stopTx: () => {
      runtime.stopTx();
      // MOR-618: put the remembered MOD-input source back (no-op unless
      // the auto-set changed it at TX start).
      restoreModInputAfterTx();
    },
  };
}
