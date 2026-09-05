import type { FilterPassbandViewModel, RadioViewModel } from './radio-view-model';

export type PbtField = 'pbtInner' | 'pbtOuter';
export type PbtObservationMarker = Readonly<{
  /** Source is explanatory only; `value` is one shared monotonic domain. */
  source: 'snapshot' | 'field';
  value: number;
}>;
export type PbtFieldEvidence =
  | Readonly<{ status: 'fresh'; marker: PbtObservationMarker }>
  | Readonly<{ status: 'stale'; marker?: PbtObservationMarker }>
  | Readonly<{ status: 'unavailable' }>;
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
const validMarker = (marker: PbtObservationMarker): boolean => Number.isFinite(marker.value) && marker.value >= 0;
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
  display: { state: 'stale', value: field.value },
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
  if (!passband) return Object.freeze({ state: EMPTY_PBT_PRESENTATION, view: canonical });
  const sameBoundary = boundaryEquals(previous.boundary, evidence.boundary);
  const state: NextPbtPresentationState = {
    boundary: evidence.boundary === null ? null : snapshotBoundary(evidence.boundary), pbtInner: null, pbtOuter: null,
  };
  let projected: FilterPassbandViewModel | null = null;
  for (const field of FIELDS) {
    const current = passband[field];
    const retained = sameBoundary ? previous[field] : null;
    const incoming = evidence.fields[field];
    const maskDisplay = () => {
      if (!current.display) return;
      projected ??= { ...passband };
      projected[field] = {
        reading: { status: 'unknown' },
        availability: { ...current.availability, operational: false },
        display: current.availability.structural
          ? { state: 'unknown', reason: evidence.boundary === null ? 'identity-unresolved' : 'invalid-evidence' }
          : { state: 'unsupported' },
      };
    };
    if (!current.availability.structural || evidence.boundary === null) {
      maskDisplay();
      continue;
    }
    const currentValue = current.reading.status === 'known' && Number.isFinite(current.reading.value)
      ? current.reading.value : null;
    const display = current.display;
    const displayValue = display?.state === 'current' || display?.state === 'stale' ? display.value : null;
    const displayAccepted = displayValue !== null && Number.isFinite(displayValue)
      && incoming.status !== 'unavailable' && incoming.marker !== undefined && validMarker(incoming.marker)
      && (incoming.status === 'fresh' ? display?.state === 'current' : display?.state === 'stale')
      && (!retained || incoming.marker.value > retained.marker.value);
    const accepted = !display && incoming.status === 'fresh'
      && validMarker(incoming.marker)
      && current.availability.operational
      && currentValue !== null
      && (!retained || incoming.marker.value > retained.marker.value);
    if (displayAccepted) {
      state[field] = snapshotRetained(displayValue, incoming.marker);
      if (display?.state === 'stale') {
        projected ??= { ...passband };
        projected[field] = retainedField(state[field]!);
      }
    } else if (accepted) {
      state[field] = snapshotRetained(currentValue, incoming.marker);
    } else if (retained) {
      state[field] = retained;
      projected ??= { ...passband };
      projected[field] = retainedField(retained);
    } else {
      maskDisplay();
    }
  }
  const nextView = projected === null ? canonical : Object.freeze({ ...canonical, filterPassband: Object.freeze(projected) });
  return Object.freeze({ state: Object.freeze(state), view: nextView });
}
