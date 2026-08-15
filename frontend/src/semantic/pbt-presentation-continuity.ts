import type { FilterPassbandViewModel, RadioViewModel } from './radio-view-model';

export type PbtField = 'pbtInner' | 'pbtOuter';
export type PbtObservationMarker = Readonly<{
  /** Source is explanatory only; `value` is one shared monotonic domain. */
  source: 'snapshot' | 'field';
  value: number;
}>;
export type PbtFieldEvidence =
  | Readonly<{ status: 'fresh'; marker: PbtObservationMarker }>
  | Readonly<{ status: 'stale' | 'unavailable' }>;
export interface PbtPresentationEvidence {
  /** Every change fences retained presentation values. */
  readonly boundary: Readonly<{
    providerGeneration: number;
    receiver: string;
    controlSession: string;
    epoch: number;
  }> | null;
  readonly fields: Readonly<Record<PbtField, PbtFieldEvidence>>;
}

type RetainedPbt = Readonly<{ value: number; marker: PbtObservationMarker }>;
type NextPbtPresentationState = {
  boundary: PbtPresentationEvidence['boundary'];
  pbtInner: RetainedPbt | null;
  pbtOuter: RetainedPbt | null;
};
export interface PbtPresentationState {
  readonly boundary: PbtPresentationEvidence['boundary'];
  readonly pbtInner: RetainedPbt | null;
  readonly pbtOuter: RetainedPbt | null;
}
export const EMPTY_PBT_PRESENTATION: Readonly<PbtPresentationState> = Object.freeze({
  boundary: null, pbtInner: null, pbtOuter: null,
});

const FIELDS = ['pbtInner', 'pbtOuter'] as const;
const boundaryEquals = (
  left: PbtPresentationEvidence['boundary'], right: PbtPresentationEvidence['boundary'],
): boolean => left !== null && right !== null
  && left.providerGeneration === right.providerGeneration
  && left.receiver === right.receiver
  && left.controlSession === right.controlSession
  && left.epoch === right.epoch;
const validMarker = (marker: PbtObservationMarker): boolean => Number.isSafeInteger(marker.value);
const snapshotBoundary = (
  boundary: NonNullable<PbtPresentationEvidence['boundary']>,
): NonNullable<PbtPresentationEvidence['boundary']> => Object.freeze({ ...boundary });
const snapshotMarker = (marker: PbtObservationMarker): PbtObservationMarker =>
  Object.freeze({ source: marker.source, value: marker.value });
const snapshotRetained = (value: number, marker: PbtObservationMarker): RetainedPbt =>
  Object.freeze({ value, marker: snapshotMarker(marker) });
const retainedField = (field: RetainedPbt): FilterPassbandViewModel[PbtField] => ({
  reading: { status: 'known', value: field.value },
  availability: { structural: true, operational: false },
});

/**
 * Pure, composition-local presentation reducer. A retained value is always
 * operational:false: it never becomes radio truth or a commandable control.
 * Snapshot and field markers share one caller-supplied monotonic domain, so a
 * source transition is safe only when its marker is strictly newer.
 */
export function projectPbtPresentation(
  previous: Readonly<PbtPresentationState>, canonical: RadioViewModel,
  evidence: PbtPresentationEvidence,
): Readonly<{ state: Readonly<PbtPresentationState>; view: RadioViewModel }> {
  const passband = canonical.filterPassband;
  if (!passband || evidence.boundary === null) {
    return Object.freeze({ state: EMPTY_PBT_PRESENTATION, view: canonical });
  }
  const sameBoundary = boundaryEquals(previous.boundary, evidence.boundary);
  const state: NextPbtPresentationState = {
    boundary: snapshotBoundary(evidence.boundary), pbtInner: null, pbtOuter: null,
  };
  let projected: FilterPassbandViewModel | null = null;
  for (const field of FIELDS) {
    const current = passband[field];
    const retained = sameBoundary ? previous[field] : null;
    const incoming = evidence.fields[field];
    if (!current.availability.structural) continue;
    const currentValue = current.reading.status === 'known' && Number.isFinite(current.reading.value)
      ? current.reading.value : null;
    const accepted = incoming.status === 'fresh'
      && validMarker(incoming.marker)
      && current.availability.operational
      && currentValue !== null
      && (!retained || incoming.marker.value > retained.marker.value);
    if (accepted) {
      state[field] = snapshotRetained(currentValue, incoming.marker);
    } else if (retained) {
      state[field] = retained;
      projected ??= { ...passband };
      projected[field] = retainedField(retained);
    }
  }
  const nextView = projected === null ? canonical : Object.freeze({ ...canonical, filterPassband: Object.freeze(projected) });
  return Object.freeze({ state: Object.freeze(state), view: nextView });
}
