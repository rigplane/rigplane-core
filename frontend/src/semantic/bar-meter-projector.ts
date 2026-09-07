import { t } from '$lib/i18n';
import {
  alcLevel,
  compLevel,
  formatAlc,
  formatAmps,
  formatCompDb,
  formatPowerWatts,
  formatSwr,
  formatVolts,
  hasSwrRatioScale,
  idLevel,
  isAlcFault,
  isSwrFault,
  normalizePower,
  swrLevel,
  vdLevel,
} from '../components-v2/panels/meter-utils';
import { projectTxMeterDisplay } from './tx-meter-display';
import type {
  DisplayObservedMeterField,
  MeterRfState,
  MeterSourceIdentity,
  MeterValueDomain,
  MetersViewModel,
  RadioViewModel,
} from './radio-view-model';

export type BarMeterKey = Exclude<keyof MetersViewModel, 'rfState' | 'signal' | 'swr'>;
export type LevelMeterKey = Exclude<keyof MetersViewModel, 'rfState' | 'signal'>;
export type LevelMeterState = 'unsupported' | 'idle' | 'current' | 'stale' | 'unknown';
export type LevelMeterEvidence =
  | Readonly<{ state: 'current' | 'stale'; value: number }>
  | Readonly<{ state: 'unsupported' | 'idle' | 'unknown' }>;

export interface LevelMeterProjection<Key extends LevelMeterKey = LevelMeterKey> {
  readonly key: Key;
  readonly label: string;
  readonly relevant: boolean;
  readonly observed: boolean;
  readonly state: LevelMeterState;
  readonly evidence: LevelMeterEvidence;
  readonly domain: MeterValueDomain | undefined;
  readonly motionFraction: number | null;
  readonly displayText: string;
  readonly stateText: string;
  readonly accessibleDescription: string | undefined;
  readonly fault: boolean;
  readonly showPeak: boolean;
  readonly source: MeterSourceIdentity | null | undefined;
  readonly gauge: boolean;
}

export type BarMeterProjection = LevelMeterProjection<BarMeterKey>;
export type SwrMeterProjection = LevelMeterProjection<'swr'> & {
  readonly ratioScale: boolean;
};

type MeterDefinition<Key extends LevelMeterKey> = readonly [
  Key,
  string,
  (value: number, domain?: MeterValueDomain) => number | null,
  (value: number, domain?: MeterValueDomain) => string,
  boolean,
];

const BAR_DEFINITIONS = [
  ['power', 'Po', normalizePower, formatPowerWatts, true],
  ['alc', 'ALC', alcLevel, formatAlc, true],
  ['drainCurrent', 'Id', idLevel, formatAmps, true],
  ['drainVoltage', 'Vd', vdLevel, formatVolts, false],
  ['compression', 'COMP', compLevel, formatCompDb, false],
] as const satisfies readonly MeterDefinition<BarMeterKey>[];

const SWR_DEFINITION = [
  'swr', 'SWR', swrLevel, formatSwr, false,
] as const satisfies MeterDefinition<'swr'>;

const FAULT_CHECKS: Partial<
  Record<LevelMeterKey, (value: number, domain?: MeterValueDomain) => boolean>
> = {
  alc: isAlcFault,
  swr: isSwrFault,
};

const observed = (field: DisplayObservedMeterField): boolean =>
  field.availability.operational && field.reading.status === 'known';

const reading = (field: DisplayObservedMeterField): number =>
  field.reading.status === 'known' ? field.reading.value : 0;

export function projectTxMeterPresentation(
  field: DisplayObservedMeterField,
  rfState: MeterRfState,
): {
  readonly state: LevelMeterState;
  readonly evidence: LevelMeterEvidence;
  readonly value: number | null;
  readonly text: string;
  readonly description: string;
} {
  const projected = projectTxMeterDisplay(field, rfState);
  if (!projected.supported) {
    return {
      state: 'unsupported', evidence: { state: 'unsupported' },
      value: null, text: '?', description: 'Not observed',
    };
  }
  const { relevance, observation } = projected;
  if (relevance === 'idle') {
    return {
      state: 'idle', evidence: { state: 'idle' },
      value: null, text: '', description: t('core.meter.state.idle'),
    };
  }
  // R29/R32: a stale reading keeps its last value on the scale (digits and
  // fill), same as a current one. Anything else (never observed, or a
  // stray non-numeric state) is an empty scale: no value, no placeholder
  // glyph — the accessible description names that too.
  const cue = relevance === 'indeterminate' ? 'RF relevance indeterminate. ' : '';
  const retained = observation.state === 'current' || observation.state === 'stale';
  const evidence: LevelMeterEvidence = retained
    ? { state: observation.state, value: observation.value }
    : { state: observation.state };
  return {
    state: observation.state,
    evidence,
    value: retained ? observation.value : null,
    text: observation.state === 'current' && relevance === 'indeterminate' ? ' ?' : '',
    description: cue + (observation.state === 'stale' ? 'Stale observation'
      : observation.state === 'current' ? 'Current observation' : t('core.meter.state.noReading')),
  };
}

function projectLevelMeter<Key extends LevelMeterKey>(
  definition: MeterDefinition<Key>,
  field: DisplayObservedMeterField,
  rfState: MeterRfState,
  txObserved: boolean,
): LevelMeterProjection<Key> {
  const [key, label, level, format, peakEligible] = definition;
  const tx = txObserved ? projectTxMeterPresentation(field, rfState) : null;
  const isObserved = tx ? tx.value !== null : observed(field);
  const value = tx ? tx.value ?? 0 : reading(field);
  // MOR-2425 (R32): every structurally-present meter draws its gauge frame
  // — an empty scale (zero fill, no digits) when there is no reading, never
  // a hidden/placeholder span. Callers only reach this once
  // `field.availability.structural` is already true.
  const formatted = isObserved ? format(value, field.domain) : '';
  const stateText = tx?.text ?? '';
  const displayText = isObserved ? formatted + stateText : stateText;
  const motionFraction = isObserved ? level(value, field.domain) : null;
  const state: LevelMeterState = tx?.state ?? (isObserved ? 'current' : 'unknown');
  const evidence: LevelMeterEvidence = tx?.evidence
    ?? (isObserved ? { state: 'current', value } : { state: 'unknown' });

  return {
    key,
    label,
    relevant: field.relevant,
    observed: isObserved,
    state,
    evidence,
    domain: field.domain,
    motionFraction,
    displayText,
    stateText,
    accessibleDescription: tx
      ? `${label}: ${tx.description}${isObserved ? `. ${formatted}` : ''}`
      : undefined,
    fault: isObserved && field.relevant
      && (FAULT_CHECKS[key]?.(value, field.domain) ?? false),
    showPeak: peakEligible && isObserved && motionFraction !== null
      && (field.domain === undefined || field.domain.kind === 'engineering'),
    source: field.source,
    // Always true: `projectBarMeters`/`projectSwrMeter` only call this once
    // `field.availability.structural` holds — an unsupported meter never
    // reaches here, it simply has no tile.
    gauge: true,
  };
}

export function projectBarMeters(view: RadioViewModel): readonly BarMeterProjection[] {
  const meters = view.meters;
  if (!meters) return [];
  const compressorOn = view.txAux?.compressor.reading.status === 'known'
    && view.txAux.compressor.reading.value === true;

  return BAR_DEFINITIONS.flatMap((definition) => {
    const [key] = definition;
    const field = meters[key];
    if (!field.availability.structural || (key === 'compression' && !compressorOn)) return [];

    return [projectLevelMeter(
      definition, field, meters.rfState, key === 'power' || key === 'alc',
    )];
  });
}

export function projectSwrMeter(view: RadioViewModel): SwrMeterProjection | null {
  const meters = view.meters;
  if (!meters || !meters.swr.availability.structural) return null;
  return {
    ...projectLevelMeter(SWR_DEFINITION, meters.swr, meters.rfState, true),
    ratioScale: hasSwrRatioScale(meters.swr.domain),
  };
}
