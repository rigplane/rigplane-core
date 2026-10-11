/**
 * Pure PTT restore gate for the TX adapter: both helpers are functions of
 * their arguments only — no runtime singleton, stores, or module-level state.
 */

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

export function markerIsStrictlyNewer(
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

export function canRestoreModAfterConfirmedOff({
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
