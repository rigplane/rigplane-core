import type { DisplayObservation, DisplayObservedMeterField, MeterRfState } from './radio-view-model';

export type TxMeterDisplay = { readonly supported: false } | {
  readonly supported: true;
  readonly relevance: 'idle' | 'relevant' | 'indeterminate';
  readonly observation: DisplayObservation<number>;
};

export function projectTxMeterDisplay(
  field: DisplayObservedMeterField | undefined, rfState: MeterRfState,
): TxMeterDisplay {
  if (!field?.availability.structural) return { supported: false };
  const observation = field.display ?? (
    field.availability.operational && field.reading.status === 'known'
      ? { state: 'current', value: field.reading.value } as const
      : { state: 'unknown', reason: 'not-observed' } as const
  );
  return {
    supported: true,
    relevance: rfState === 'receiving' && !field.relevant ? 'idle'
      : rfState === 'transmitting' && field.relevant ? 'relevant' : 'indeterminate',
    observation,
  };
}
