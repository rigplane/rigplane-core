/**
 * TX adapter — provides audio TX lifecycle callbacks for PTT components.
 *
 * Replaces direct audioManager imports in TxPanel and MobileRadioLayout.
 */

import { runtime } from '../frontend-runtime';
import { setTxError } from '../../stores/audio.svelte';
import { armModInputTxGuard } from './mod-input-tx-guard.svelte';
import {
  autoSetLanModInputForTx,
  restoreModInputAfterTx,
} from './mod-input-auto.svelte';
import {
  canRestoreModAfterConfirmedOff,
  type ConfirmedOffRestoreInput,
} from './ptt-restore-gate';

export type {
  PttObservationMarker,
  AuthoritativePttObservation,
  ConfirmedOffRestoreInput,
} from './ptt-restore-gate';

interface LocalAudioAttempt {
  generation: number;
  lifecycle: 'starting' | 'running';
  stopRequested: boolean;
  stopOptions?: { automatic?: boolean };
}

let nextLocalAudioGeneration = 0;
let activeLocalAudioAttempt: LocalAudioAttempt | null = null;

async function startTx(autoSwitchModInput = true): Promise<string | null> {
  if (activeLocalAudioAttempt?.lifecycle === 'starting') {
    return 'TX audio start already in progress';
  }
  if (activeLocalAudioAttempt?.lifecycle === 'running') return null;

  const attempt: LocalAudioAttempt = {
    generation: ++nextLocalAudioGeneration,
    lifecycle: 'starting',
    stopRequested: false,
  };
  activeLocalAudioAttempt = attempt;

  // Managed TX snapshots do not yet carry the browser switch correlation
  // needed for a safe restore, so that path deliberately leaves MOD untouched.
  if (autoSwitchModInput) autoSetLanModInputForTx();
  armModInputTxGuard();

  let err: string | null;
  try {
    err = await runtime.startTx();
  } catch (error) {
    if (attempt.stopRequested) runtime.stopTx(attempt.stopOptions);
    if (activeLocalAudioAttempt?.generation === attempt.generation) {
      activeLocalAudioAttempt = null;
    }
    throw error;
  }

  if (attempt.stopRequested) {
    runtime.stopTx(attempt.stopOptions);
    if (activeLocalAudioAttempt?.generation === attempt.generation) {
      activeLocalAudioAttempt = null;
    }
    return err;
  }

  if (err) {
    if (activeLocalAudioAttempt?.generation === attempt.generation) {
      activeLocalAudioAttempt = null;
    }
    return err;
  }

  attempt.lifecycle = 'running';
  return null;
}

function stopLocalAudio(options?: { automatic?: boolean }): void {
  const attempt = activeLocalAudioAttempt;
  if (!options?.automatic) {
    setTxError(false);
    if (attempt?.stopRequested) attempt.stopOptions = options;
  }
  if (!attempt || attempt.stopRequested) return;

  attempt.stopRequested = true;
  attempt.stopOptions = options;
  runtime.stopTx(options);
  if (
    attempt.lifecycle === 'running' &&
    activeLocalAudioAttempt?.generation === attempt.generation
  ) {
    activeLocalAudioAttempt = null;
  }
}

function restoreModAfterConfirmedOff(input: ConfirmedOffRestoreInput): void {
  if (!canRestoreModAfterConfirmedOff(input)) return;
  restoreModInputAfterTx();
}

export function getTxAudioControl() {
  return {
    startTx: () => startTx(),
    startManagedTx: () => startTx(false),
    stopLocalAudio,
    restoreModAfterConfirmedOff,
    onTxAudioDied: (callback: () => void) => runtime.onTxAudioDied(callback),
  };
}
