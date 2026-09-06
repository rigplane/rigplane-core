import {
  alcLevel,
  compLevel,
  formatAlc,
  formatAmps,
  formatCompDb,
  formatPowerWatts,
  formatVolts,
  idLevel,
  isAlcFault,
  normalizePower,
  vdLevel,
} from '../components-v2/panels/meter-utils';
import { projectTxMeterDisplay } from './tx-meter-display';
import type {
  DisplayObservedMeterField,
  MeterRfState,
  MeterSourceIdentity,
  MetersViewModel,
  RadioViewModel,
} from './radio-view-model';

export type BarMeterKey = Exclude<keyof MetersViewModel, 'rfState' | 'signal' | 'swr'>;

export interface BarMeterProjection {
  readonly key: BarMeterKey;
  readonly label: string;
  readonly relevant: boolean;
  readonly observed: boolean;
  readonly motionFraction: number | null;
  readonly displayText: string;
  readonly accessibleDescription: string | undefined;
  readonly fault: boolean;
  readonly showPeak: boolean;
  readonly source: MeterSourceIdentity | null | undefined;
  readonly gauge: boolean;
}

type BarDefinition = readonly [
  BarMeterKey,
  string,
  (value: number) => number,
  (value: number) => string,
  boolean,
];

const BAR_DEFINITIONS = [
  ['power', 'Po', normalizePower, formatPowerWatts, true],
  ['alc', 'ALC', alcLevel, formatAlc, true],
  ['drainCurrent', 'Id', idLevel, formatAmps, true],
  ['drainVoltage', 'Vd', vdLevel, formatVolts, false],
  ['compression', 'COMP', compLevel, formatCompDb, false],
] as const satisfies readonly BarDefinition[];

const FAULT_CHECKS: Partial<Record<BarMeterKey, (value: number) => boolean>> = {
  alc: isAlcFault,
};

const observed = (field: DisplayObservedMeterField): boolean =>
  field.availability.operational && field.reading.status === 'known';

const reading = (field: DisplayObservedMeterField): number =>
  field.reading.status === 'known' ? field.reading.value : 0;

export function projectTxMeterPresentation(
  field: DisplayObservedMeterField,
  rfState: MeterRfState,
) {
  const projected = projectTxMeterDisplay(field, rfState);
  if (!projected.supported) return { value: null, text: '?', description: 'Not observed' };
  const { relevance, observation } = projected;
  if (relevance === 'idle') {
    return { value: null, text: 'IDLE', description: 'Not measuring in RX' };
  }
  const cue = relevance === 'indeterminate' ? 'RF relevance indeterminate. ' : '';
  return {
    value: observation.state === 'current' ? observation.value : null,
    text: observation.state === 'stale' ? 'STALE' : observation.state === 'current'
      ? (relevance === 'indeterminate' ? ' ?' : '') : '?',
    description: cue + (observation.state === 'stale' ? 'Stale observation'
      : observation.state === 'current' ? 'Current observation' : 'Not observed'),
  };
}

export function projectBarMeters(view: RadioViewModel): readonly BarMeterProjection[] {
  const meters = view.meters;
  if (!meters) return [];
  const compressorOn = view.txAux?.compressor.reading.status === 'known'
    && view.txAux.compressor.reading.value === true;

  return BAR_DEFINITIONS.flatMap(([key, label, level, format, peakEligible]) => {
    const field = meters[key];
    if (!field.availability.structural || (key === 'compression' && !compressorOn)) return [];

    const tx = key === 'power' || key === 'alc'
      ? projectTxMeterPresentation(field, meters.rfState)
      : null;
    const isObserved = tx ? tx.value !== null : observed(field);
    const value = tx ? tx.value ?? 0 : reading(field);
    const gauge = isObserved || tx !== null;
    const displayText = gauge
      ? tx ? (isObserved ? format(value) + tx.text : tx.text) : format(value)
      : `${label} ?`;

    return [{
      key,
      label,
      relevant: field.relevant,
      observed: isObserved,
      motionFraction: isObserved ? level(value) : null,
      displayText,
      accessibleDescription: tx
        ? `${label}: ${tx.description}${isObserved ? `. ${format(value)}` : ''}`
        : undefined,
      fault: isObserved && field.relevant && (FAULT_CHECKS[key]?.(value) ?? false),
      showPeak: peakEligible && isObserved,
      source: field.source,
      gauge,
    }];
  });
}
