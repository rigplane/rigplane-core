import type {
  LevelMeterRendererView,
  MeterNumericEvidence,
  MeterDisplayDomain,
  MeterScaleDomain,
  SignalMeterEvidence,
  SignalMeterMark,
  SignalMeterRendererView,
  SignalMeterTick,
} from '../../component-kit-api/src/index';
import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
import { projectSignalMeter } from '../components-v2/meters/smeter-scale';
import { finiteValue, observationValue, readingValue } from '../primitives/reading-text';
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

function signalEvidence(
  value: number | null,
  domain: MeterDisplayDomain,
): SignalMeterEvidence {
  return value !== null
    ? Object.freeze({ state: 'current', value, domain })
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
  // MOR-2688 S4d: the ONE is-there-a-value decision, through the reading
  // entry point — used for the evidence, the geometry choice and the
  // projector argument alike.
  const value = finiteValue(readingValue({ reading }));
  const projection = domain === undefined
    ? projectSignalMeter(value, { kind: 'unknown' })
    : value !== null ? frame.projection : projectSignalMeter(null, domain);
  const publicDomain = displayDomain(domain);
  const validStaticGeometry = validFraction(projection.crossoverFraction)
    && projection.marks.every((mark) => Number.isFinite(mark.actual) && validFraction(mark.fraction))
    && projection.ticks.every((tick) => validFraction(tick.fraction));
  const live = validStaticGeometry
    && value !== null
    && projection.motionFraction !== null
    && validFraction(projection.motionFraction)
    && validFraction(frame.smoothedFraction)
    && validFraction(frame.peakFraction);
  return Object.freeze({
    kind: 'signal',
    evidence: signalEvidence(value, publicDomain),
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

/** A fresh frozen copy, so the public graph never aliases a projector constant. */
function scaleDomain(scale: MeterScaleDomain | null): MeterScaleDomain | null {
  return scale === null ? null : Object.freeze({ min: scale.min, max: scale.max });
}

function levelEvidence(
  frame: StationLevelMeterFrame,
  domain: MeterDisplayDomain,
): MeterNumericEvidence {
  const evidence = frame.projection.evidence;
  // MOR-2688 S4d: the value enters through the observation entry point.
  // The STATUS output stays exactly as before (design audit R2) — a
  // current/stale observation keeps its state when it carries a finite
  // value, decays to `unknown` when it does not, and the passive states
  // (`unsupported`/`idle`/`unknown`) pass through untouched.
  const value = finiteValue(observationValue(evidence));
  if (evidence.state === 'current' || evidence.state === 'stale') {
    return value !== null
      ? Object.freeze({ state: evidence.state, value, domain })
      : Object.freeze({ state: 'unknown', domain });
  }
  return Object.freeze({ state: evidence.state, domain });
}

/** Copy one Core-owned station frame into the smaller public, immutable renderer graph. */
export function toLevelMeterRendererView(
  frame: StationLevelMeterFrame,
): LevelMeterRendererView {
  const projection = frame.projection;
  const domain = displayDomain(projection.domain);
  const publicEvidence = levelEvidence(frame, domain);
  const live = observationValue(publicEvidence) !== null
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
    scale: scaleDomain(projection.scale),
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
