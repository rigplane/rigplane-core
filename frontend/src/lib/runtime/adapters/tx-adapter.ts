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

export interface PttObservationMarker {
  authorityEpoch: number;
  pttObservationSeq: number | null;
  pttLastObservedMonotonic: number | null;
}

export interface AuthoritativePttObservation extends PttObservationMarker {
  ptt: boolean;
  pttObserved: boolean;
  pttFreshness: 'fresh' | 'stale' | 'expired' | 'unknown';
  pttSource: string;
}

export interface ConfirmedOffRestoreInput {
  barrier: PttObservationMarker;
  observation: AuthoritativePttObservation;
}

interface LocalAudioAttempt {
  generation: number;
  lifecycle: 'starting' | 'running';
  stopRequested: boolean;
}

let nextLocalAudioGeneration = 0;
let activeLocalAudioAttempt: LocalAudioAttempt | null = null;

function markerIsStrictlyNewer(
  barrier: PttObservationMarker,
  observation: AuthoritativePttObservation,
): boolean {
  if (observation.authorityEpoch !== barrier.authorityEpoch) return false;

  if (
    barrier.pttObservationSeq !== null ||
    observation.pttObservationSeq !== null
  ) {
    return (
      barrier.pttObservationSeq !== null &&
      observation.pttObservationSeq !== null &&
      observation.pttObservationSeq > barrier.pttObservationSeq
    );
  }

  return (
    barrier.pttLastObservedMonotonic !== null &&
    observation.pttLastObservedMonotonic !== null &&
    observation.pttLastObservedMonotonic >
      barrier.pttLastObservedMonotonic
  );
}

function canRestoreModAfterConfirmedOff({
  barrier,
  observation,
}: ConfirmedOffRestoreInput): boolean {
  return (
    observation.ptt === false &&
    observation.pttObserved &&
    observation.pttFreshness === 'fresh' &&
    (observation.pttSource === 'radio-readback' ||
      observation.pttSource === 'backend-observation') &&
    markerIsStrictlyNewer(barrier, observation)
  );
}

async function startTx(): Promise<string | null> {
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

  autoSetLanModInputForTx();
  armModInputTxGuard();

  let err: string | null;
  try {
    err = await runtime.startTx();
  } catch (error) {
    if (attempt.stopRequested) runtime.stopTx();
    if (activeLocalAudioAttempt?.generation === attempt.generation) {
      activeLocalAudioAttempt = null;
    }
    throw error;
  }

  if (attempt.stopRequested) {
    runtime.stopTx();
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

function stopLocalAudio(): void {
  const attempt = activeLocalAudioAttempt;
  if (!attempt || attempt.stopRequested) return;

  attempt.stopRequested = true;
  runtime.stopTx();
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
    startTx,
    stopLocalAudio,
    restoreModAfterConfirmedOff,
  };
}
