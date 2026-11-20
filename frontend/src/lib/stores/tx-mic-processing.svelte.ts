/**
 * TX microphone processing mode (MOR-4596): the one switch for the browser's
 * voice-call processing on the capture that feeds a transmitter.
 *
 * - `raw` (default): echo cancellation, noise suppression and automatic gain
 *   control all off (MOR-4312). The radio's own speech processing shapes the
 *   audio.
 * - `browser`: all three on, for operators whose microphone or room needs it.
 *
 * Kept per browser in localStorage, like the other client-side TX preference
 * (`AUTO_LAN_PREF_KEY` in `runtime/adapters/mod-input-auto.svelte.ts`). It is
 * read when a capture starts (`TxMic.start`), so a change applies from the
 * next PTT and never touches a capture that is already running.
 */

export type TxMicProcessing = 'raw' | 'browser';

/** localStorage key of the preference ('raw' / 'browser'). */
export const TX_MIC_PROCESSING_KEY = 'rigplane:tx-mic-processing';

export const TX_MIC_PROCESSING_DEFAULT: TxMicProcessing = 'raw';

function readStored(): TxMicProcessing {
  if (typeof localStorage === 'undefined') return TX_MIC_PROCESSING_DEFAULT;
  try {
    return localStorage.getItem(TX_MIC_PROCESSING_KEY) === 'browser' ? 'browser' : 'raw';
  } catch {
    return TX_MIC_PROCESSING_DEFAULT;
  }
}

let mode = $state<TxMicProcessing>(readStored());

export function getTxMicProcessing(): TxMicProcessing {
  return mode;
}

export function setTxMicProcessing(next: TxMicProcessing): void {
  mode = next;
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(TX_MIC_PROCESSING_KEY, next);
  } catch {
    /* persistence is best-effort */
  }
}

/** Re-read the stored value (a page reload, in tests). */
export function reloadTxMicProcessingForTests(): void {
  mode = readStored();
}
