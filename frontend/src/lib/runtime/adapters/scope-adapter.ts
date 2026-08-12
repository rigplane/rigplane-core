/** Runtime-owned decoder for binary audio-scope frames. */

export interface ScopeFrame {
  readonly receiver: number;
  readonly mode: number;
  readonly startFreq: number;
  readonly endFreq: number;
  readonly pixels: Uint8Array;
}

export function parseScopeFrame(buf: ArrayBuffer): ScopeFrame | null {
  const view = new DataView(buf);
  if (view.byteLength < 16 || view.getUint8(0) !== 0x01) return null;
  const receiver = view.getUint8(1);
  const mode = view.getUint8(2);
  const startFreq = view.getUint32(3, true);
  const endFreq = view.getUint32(7, true);
  const pixelCount = view.getUint16(14, true);
  if (16 + pixelCount > view.byteLength) return null;
  return Object.freeze({
    receiver,
    mode,
    startFreq,
    endFreq,
    pixels: new Uint8Array(buf, 16, pixelCount),
  });
}

import type { Capabilities, FilterModeConfig, FilterSegmentConfig } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ScopeControlsViewModel } from '../../../semantic/radio-view-model';
import { resolveFilterModeConfig } from '$lib/runtime/props/panel-props';
import { toRadioViewModel } from './radio-view-model-adapter';

type DeepReadonly<T> = T extends readonly (infer U)[] ? readonly DeepReadonly<U>[]
  : T extends object ? { readonly [K in keyof T]: DeepReadonly<T[K]> } : T;

export type SpectrumFilterRule =
  | Readonly<{ kind: 'table'; minHz: number; maxHz: number; values: readonly number[] }>
  | Readonly<{
    kind: 'segments'; minHz: number; maxHz: number;
    segments: readonly Readonly<FilterSegmentConfig>[];
  }>
  | Readonly<{ kind: 'step'; minHz: number; maxHz: number; stepHz: number }>;

export interface SpectrumAuthority {
  readonly providerGeneration: number;
  readonly receiver: 0 | 1;
  readonly frequencyHz: number | null;
  readonly mode: string | null;
  readonly filter: string | null;
  readonly filterWidthHz: number | null;
  readonly filterShape: number | null;
  readonly ifShiftHz: number | null;
  readonly pbtInnerHz: number | null;
  readonly pbtOuterHz: number | null;
  readonly dataMode: number | null;
  readonly rule: SpectrumFilterRule | null;
  readonly scopeControls: DeepReadonly<ScopeControlsViewModel> | null;
  readonly digest: string;
}

const positiveInteger = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) > 0;
const nonnegativeInteger = (value: unknown): value is number =>
  Number.isSafeInteger(value) && (value as number) >= 0;
const finiteNumber = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const strictlySeen = (state: ServerState, path: string): boolean => {
  const status = state.fieldStatus?.[path];
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
};

function immutableClone<T>(value: T): DeepReadonly<T> {
  if (Array.isArray(value)) return Object.freeze(value.map(immutableClone)) as DeepReadonly<T>;
  if (value !== null && typeof value === 'object') {
    const clone = Object.fromEntries(Object.entries(value).map(([key, item]) => [key, immutableClone(item)]));
    return Object.freeze(clone) as DeepReadonly<T>;
  }
  return value as DeepReadonly<T>;
}

function validSegments(segments: readonly FilterSegmentConfig[], minHz: number, maxHz: number): boolean {
  if (segments.length === 0 || segments[0].hzMin !== minHz
    || segments[segments.length - 1].hzMax !== maxHz) return false;
  let priorHzMax = 0;
  let priorIndexMax = -1;
  for (const segment of segments) {
    if (!positiveInteger(segment.hzMin) || !positiveInteger(segment.hzMax)
      || !positiveInteger(segment.stepHz) || !nonnegativeInteger(segment.indexMin)
      || segment.hzMin > segment.hzMax
      || (segment.hzMax - segment.hzMin) % segment.stepHz !== 0
      || segment.hzMin <= priorHzMax || segment.indexMin <= priorIndexMax) return false;
    priorHzMax = segment.hzMax;
    priorIndexMax = segment.indexMin + ((segment.hzMax - segment.hzMin) / segment.stepHz);
    if (!Number.isSafeInteger(priorIndexMax)) return false;
  }
  return true;
}

function normalizeFilterRule(config: FilterModeConfig | null): SpectrumFilterRule | null {
  if (!config || config.fixed || (config.table !== undefined && config.segments !== undefined)) return null;
  if (config.table !== undefined) {
    const values = config.table;
    if (values.length === 0 || values.some((value, index) =>
      !positiveInteger(value) || (index > 0 && value <= values[index - 1]))) return null;
    const minHz = values[0];
    const maxHz = values[values.length - 1];
    if ((config.minHz !== undefined && config.minHz !== minHz)
      || (config.maxHz !== undefined && config.maxHz !== maxHz)) return null;
    return { kind: 'table', minHz, maxHz, values: [...values] };
  }
  if (config.segments !== undefined) {
    if (!positiveInteger(config.minHz) || !positiveInteger(config.maxHz)
      || config.minHz > config.maxHz || !validSegments(config.segments, config.minHz, config.maxHz)) return null;
    return { kind: 'segments', minHz: config.minHz, maxHz: config.maxHz,
      segments: config.segments.map((segment) => ({ ...segment })) };
  }
  if (!positiveInteger(config.minHz) || !positiveInteger(config.maxHz)
    || !positiveInteger(config.stepHz) || config.minHz > config.maxHz
    || (config.maxHz - config.minHz) % config.stepHz !== 0) return null;
  return { kind: 'step', minHz: config.minHz, maxHz: config.maxHz, stepHz: config.stepHz };
}

function knownReading<T>(
  state: ServerState,
  path: string,
  field: { readonly reading: { readonly status: 'known'; readonly value: T } | { readonly status: 'unknown' } }
    | undefined,
): T | null {
  return strictlySeen(state, path) && field?.reading.status === 'known' ? field.reading.value : null;
}

function activeVfoPath(receiver: 'MAIN' | 'SUB', slot: { readonly kind: string; readonly id?: string }): string {
  const key = receiver === 'SUB' ? 'sub' : 'main';
  return slot.kind === 'slotted' && (slot.id === 'A' || slot.id === 'B')
    ? `${key}.${slot.id === 'A' ? 'vfoA' : 'vfoB'}.` : `${key}.`;
}

export function toSpectrumAuthority(
  state: ServerState | null,
  caps: Capabilities | null,
): SpectrumAuthority | null {
  const generation = state?.providerGeneration;
  const capsGeneration = caps?.providerGeneration;
  if (!state || !caps || state.stateContractVersion !== 1 || caps.stateContractVersion !== 1
    || !nonnegativeInteger(generation) || !nonnegativeInteger(capsGeneration)
    || generation !== capsGeneration) return null;
  const expectedReceivers = caps.vfoScheme === 'single' || caps.vfoScheme === 'ab' ? 1
    : caps.vfoScheme === 'ab_shared' || caps.vfoScheme === 'main_sub' ? 2 : 0;
  if (expectedReceivers === 0 || caps.receivers !== expectedReceivers) return null;
  const model = toRadioViewModel(state, caps);
  if (!model || model.activeReceiver.status !== 'known'
    || model.activeReceiver.receiver !== state.active) return null;
  const receiverName = model.activeReceiver.receiver;
  if (receiverName === 'SUB'
    && (caps.receivers !== 2 || !caps.capabilities.includes('dual_rx') || !state.sub)) return null;
  const activeVfos = model.vfos.filter((vfo) => vfo.isActive);
  if (activeVfos.length !== 1 || activeVfos[0].receiver !== receiverName) return null;
  const activeVfo = activeVfos[0];
  const receiver: 0 | 1 = receiverName === 'SUB' ? 1 : 0;
  const key = receiver === 1 ? 'sub' : 'main';
  const base = activeVfoPath(receiverName, activeVfo.slot);
  const frequencyHz = strictlySeen(state, `${base}freqHz`) && finiteNumber(activeVfo.frequencyHz)
    ? activeVfo.frequencyHz : null;
  const mode = strictlySeen(state, `${base}mode`) && typeof activeVfo.mode === 'string'
    && activeVfo.mode.length > 0 ? activeVfo.mode : null;
  const filter = strictlySeen(state, `${base}${activeVfo.slot.kind === 'slotted' ? 'filterNum' : 'filter'}`)
    && typeof activeVfo.filter === 'string' ? activeVfo.filter : null;
  const modeFilter = model.modeFilter;
  const passband = model.filterPassband;
  const modeFact = knownReading(state, `${key}.mode`, modeFilter?.currentMode);
  const widthFact = knownReading(state, `${key}.filterWidth`, modeFilter?.filterWidth);
  const dataFact = knownReading(state, `${key}.dataMode`, passband?.dataMode);
  const filterWidthHz = positiveInteger(widthFact) ? widthFact : null;
  const dataMode = nonnegativeInteger(dataFact) ? dataFact : null;
  const supportsData = caps.capabilities.includes('data_mode');
  const rule = mode !== null && modeFact === mode && filterWidthHz !== null
    && (!supportsData || dataMode !== null) && caps.capabilities.includes('filter_width')
    ? normalizeFilterRule(resolveFilterModeConfig(caps, mode, supportsData ? dataMode! : 0)) : null;
  const core = {
    providerGeneration: generation,
    receiver,
    frequencyHz,
    mode,
    filter,
    filterWidthHz,
    filterShape: finiteNumber(knownReading(state, `${key}.filterShape`, passband?.filterShape))
      ? knownReading(state, `${key}.filterShape`, passband?.filterShape) as number : null,
    ifShiftHz: finiteNumber(knownReading(state, `${key}.ifShift`, passband?.ifShift))
      ? knownReading(state, `${key}.ifShift`, passband?.ifShift) as number : null,
    pbtInnerHz: finiteNumber(knownReading(state, `${key}.pbtInner`, passband?.pbtInner))
      ? knownReading(state, `${key}.pbtInner`, passband?.pbtInner) as number : null,
    pbtOuterHz: finiteNumber(knownReading(state, `${key}.pbtOuter`, passband?.pbtOuter))
      ? knownReading(state, `${key}.pbtOuter`, passband?.pbtOuter) as number : null,
    dataMode,
    rule,
    scopeControls: model.scopeControls ? immutableClone(model.scopeControls) : null,
  };
  return immutableClone({ ...core, digest: JSON.stringify(core) });
}

// Tie prefers the LOWER grid point. MOR-1518: tie-break must stay aligned
// with `$lib/radio/filter-controls`'s `snapWithinSegment` /
// `quantizeFilterWidthToRule` — the slider/preset command-emission path —
// so a width value equidistant from two legal Hz values snaps the same way
// regardless of whether the operator dragged the spectrum passband edge
// (this file) or the Filter Width slider (the other file).
function snapStep(raw: number, minHz: number, maxHz: number, stepHz: number): number {
  const bounded = Math.max(minHz, Math.min(maxHz, raw));
  const lower = minHz + Math.floor((bounded - minHz) / stepHz) * stepHz;
  const upper = Math.min(maxHz, lower + stepHz);
  return bounded - lower <= upper - bounded ? lower : upper;
}

export function snapSpectrumFilterWidth(
  raw: number,
  rule: SpectrumFilterRule | null,
): number | null {
  if (!finiteNumber(raw) || !rule || !positiveInteger(rule.minHz) || !positiveInteger(rule.maxHz)
    || rule.minHz > rule.maxHz) return null;
  if (rule.kind === 'table') {
    if (rule.values.length === 0 || rule.values[0] !== rule.minHz
      || rule.values[rule.values.length - 1] !== rule.maxHz
      || rule.values.some((value, index) =>
        !positiveInteger(value) || (index > 0 && value <= rule.values[index - 1]))) return null;
    return rule.values.reduce((best, value) =>
      Math.abs(raw - value) < Math.abs(raw - best) ? value : best);
  }
  if (rule.kind === 'segments') {
    if (!validSegments(rule.segments, rule.minHz, rule.maxHz)) return null;
    return rule.segments.map((segment) => snapStep(raw, segment.hzMin, segment.hzMax, segment.stepHz))
      .reduce((best, value) => Math.abs(raw - value) < Math.abs(raw - best)
        || (Math.abs(raw - value) === Math.abs(raw - best) && value < best) ? value : best);
  }
  if (!positiveInteger(rule.stepHz) || (rule.maxHz - rule.minHz) % rule.stepHz !== 0) return null;
  return snapStep(raw, rule.minHz, rule.maxHz, rule.stepHz);
}
