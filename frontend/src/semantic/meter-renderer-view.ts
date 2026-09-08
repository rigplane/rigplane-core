import type {
  LevelMeterRendererView,
  MeterNumericEvidence,
  MeterDisplayDomain,
  SignalMeterEvidence,
  SignalMeterMark,
  SignalMeterRendererView,
  SignalMeterTick,
} from '../../component-kit-api/src/index';
import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
import { projectSignalMeter } from '../components-v2/meters/smeter-scale';
import type { MeterReading, MeterValueDomain } from './radio-view-model';
import type { StationLevelMeterFrame } from './StationMeterInstrumentHost.svelte';

function validFraction(value: number | null): boolean {
  return value === null || (Number.isFinite(value) && value >= 0 && value <= 1);
}

function displayDomain(domain: MeterValueDomain | undefined): MeterDisplayDomain {
  if (domain?.kind === 'engineering') {
    return Object.freeze({ kind: 'engineering', unit: domain.unit });
  }
  if (domain?.kind === 'raw') return Object.freeze({ kind: 'raw' });
  if (domain?.kind === 'unknown') return Object.freeze({ kind: 'unknown' });
  return Object.freeze({ kind: 'unknown' });
}

function evidence(
  reading: MeterReading,
  domain: MeterDisplayDomain,
): SignalMeterEvidence {
  return reading.status === 'known' && Number.isFinite(reading.value)
    ? Object.freeze({ state: 'current', value: reading.value, domain })
    : Object.freeze({ state: 'unknown', domain });
}

function marks(source: SignalMeterFrame['projection']['marks']): readonly SignalMeterMark[] {
  return Object.freeze(source.map((mark) => Object.freeze({
    actual: mark.actual,
    fraction: mark.fraction,
    text: mark.text,
  })));
}

function ticks(source: SignalMeterFrame['projection']['ticks']): readonly SignalMeterTick[] {
  return Object.freeze(source.map((tick) => Object.freeze({
    fraction: tick.fraction,
    kind: tick.kind,
  })));
}

/** Copy one Core-owned receiver frame into the smaller public, immutable renderer graph. */
export function toSignalMeterRendererView(
  frame: SignalMeterFrame,
  reading: MeterReading,
  domain: MeterValueDomain | undefined,
  relevant?: boolean,
): SignalMeterRendererView {
  const current = reading.status === 'known' && Number.isFinite(reading.value);
  const projection = domain === undefined
    ? projectSignalMeter(current ? reading.value : null, { kind: 'unknown' })
    : current ? frame.projection : projectSignalMeter(null, domain);
  const publicDomain = displayDomain(domain);
  const validStaticGeometry = validFraction(projection.crossoverFraction)
    && projection.marks.every((mark) => Number.isFinite(mark.actual) && validFraction(mark.fraction))
    && projection.ticks.every((tick) => validFraction(tick.fraction));
  const live = validStaticGeometry
    && current
    && projection.motionFraction !== null
    && validFraction(projection.motionFraction)
    && validFraction(frame.smoothedFraction)
    && validFraction(frame.peakFraction);
  return Object.freeze({
    kind: 'signal',
    evidence: evidence(reading, publicDomain),
    ...(relevant === undefined ? {} : { relevant }),
    scaleMode: validStaticGeometry ? projection.scaleMode : 'none',
    displayedFraction: live ? frame.smoothedFraction : null,
    peakFraction: live ? frame.peakFraction : null,
    primaryText: projection.primaryText,
    secondaryText: projection.secondaryText,
    accessibleDescription: projection.accessibleDescription,
    crossoverFraction: validStaticGeometry ? projection.crossoverFraction : null,
    marks: validStaticGeometry ? marks(projection.marks) : Object.freeze([]),
    ticks: validStaticGeometry ? ticks(projection.ticks) : Object.freeze([]),
  });
}

function levelEvidence(
  frame: StationLevelMeterFrame,
  domain: MeterDisplayDomain,
): MeterNumericEvidence {
  const evidence = frame.projection.evidence;
  if ((evidence.state === 'current' || evidence.state === 'stale')
    && Number.isFinite(evidence.value)) {
    return Object.freeze({ state: evidence.state, value: evidence.value, domain });
  }
  return Object.freeze({
    state: evidence.state === 'current' || evidence.state === 'stale'
      ? 'unknown' : evidence.state,
    domain,
  });
}

/** Copy one Core-owned station frame into the smaller public, immutable renderer graph. */
export function toLevelMeterRendererView(
  frame: StationLevelMeterFrame,
): LevelMeterRendererView {
  const projection = frame.projection;
  const domain = displayDomain(projection.domain);
  const publicEvidence = levelEvidence(frame, domain);
  const live = (publicEvidence.state === 'current' || publicEvidence.state === 'stale')
    && projection.motionFraction !== null
    && validFraction(projection.motionFraction)
    && validFraction(frame.motion.smoothedFraction);
  const base = {
    kind: 'level' as const,
    key: projection.key,
    label: projection.label,
    evidence: publicEvidence,
    relevant: projection.relevant,
    observed: projection.observed,
    displayedFraction: live ? frame.motion.smoothedFraction : null,
    peakFraction: live && projection.showPeak && validFraction(frame.motion.peakFraction)
      ? frame.motion.peakFraction : null,
    displayText: projection.displayText,
    stateText: projection.stateText,
    ...(projection.accessibleDescription === undefined
      ? {} : { accessibleDescription: projection.accessibleDescription }),
    gauge: projection.gauge,
    fault: projection.fault,
    peakEnabled: projection.showPeak,
  };
  return projection.key === 'swr'
    ? Object.freeze({
        ...base,
        key: 'swr',
        ratioScale: (projection as StationLevelMeterFrame<'swr'>['projection']).ratioScale,
      })
    : Object.freeze(base) as LevelMeterRendererView;
}
