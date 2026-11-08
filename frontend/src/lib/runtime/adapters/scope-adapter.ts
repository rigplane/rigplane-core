/**
 * Runtime-owned ingress for binary scope frames (MOR-4270). This file is the
 * declared `scope_metadata` owner: every read of receiver/mode/span metadata
 * from a `ScopeFrame` lives here, downstream consumers receive the qualified
 * `ScopeGeometry` facts and the slot key instead.
 */

import type {
  CurrentScopeContext,
  ScopeFrame,
  ScopeFreshness,
  ScopeGeometry,
  ScopeParseError,
  ScopeParseResult,
} from '$lib/runtime/scope-frame';

export type {
  CurrentScopeContext, ScopeFrame, ScopeFreshness, ScopeGeometry,
  ScopeParseError, ScopeParseResult,
} from '$lib/runtime/scope-frame';

export function parseCurrentScopeFrame(buffer: ArrayBuffer, context: CurrentScopeContext): ScopeParseResult {
  if (buffer.byteLength < 56) return { ok: false, reason: 'short header' };
  const wire = new DataView(buffer);
  if (wire.getUint8(0) !== 2) return { ok: false, reason: 'message type' };
  const source = (['radio', 'audio_fft', 'iq_fft'] as const)[wire.getUint8(1)];
  if (source === undefined) return { ok: false, reason: 'source' };
  const flags = wire.getUint8(2);
  if ((flags & ~3) !== 0) return { ok: false, reason: 'flags' };
  const identityLength = wire.getUint8(3);
  const width = wire.getUint16(6, true);
  const pixelOffset = 56 + identityLength;
  const hasPixels = (flags & 2) !== 0;
  if (width === 0 || buffer.byteLength !== pixelOffset + (hasPixels ? width : 0)) {
    return { ok: false, reason: 'pixel length' };
  }
  const identity = new Uint8Array(buffer, 56, identityLength);
  if (identityLength === 0 || identity.some(byte => byte < 33 || byte > 126)) {
    return { ok: false, reason: 'source id' };
  }
  const sentTime = wire.getBigInt64(16, true);
  if (sentTime < -1n || sentTime > BigInt(Number.MAX_SAFE_INTEGER)) return { ok: false, reason: 'sent time' };
  const startHz = wire.getFloat64(24, true);
  const endHz = wire.getFloat64(32, true);
  const coverageStartHz = wire.getFloat64(40, true);
  const coverageEndHz = wire.getFloat64(48, true);
  if (![startHz, endHz, coverageStartHz, coverageEndHz].every(Number.isFinite)
    || startHz < 0 || endHz <= startHz
    || coverageStartHz < startHz || coverageEndHz < coverageStartHz || coverageEndHz > endHz) {
    return { ok: false, reason: 'frequency range' };
  }
  const receiver = wire.getUint16(4, true);
  return { ok: true, frame: Object.freeze({
    source, sourceId: String.fromCharCode(...identity), epoch: wire.getUint32(8, true),
    receiver: receiver === 65535 ? null : receiver, seq: wire.getUint32(12, true),
    sentAtMs: sentTime === -1n ? null : Number(sentTime),
    viewStartHz: startHz, viewEndHz: endHz, coverageStartHz, coverageEndHz,
    width, pixels: hasPixels ? new Uint8Array(buffer, pixelOffset, width) : null,
    outOfRange: (flags & 1) !== 0,
  }) };
}

import type { ScopeGranted, ScopeSubscription } from '../scope-view';

/** Qualify the server's negotiation facts before they enter stream geometry. */
export function parseScopeGranted(message: unknown): ScopeGranted | null {
  if (message === null || typeof message !== 'object') return null;
  const value = message as Record<string, unknown>;
  const view = (input: unknown): input is ScopeGranted['view'] => {
    if (input === null || typeof input !== 'object') return false;
    const candidate = input as Record<string, unknown>;
    return typeof candidate.startHz === 'number' && Number.isFinite(candidate.startHz) && candidate.startHz >= 0
      && typeof candidate.endHz === 'number' && Number.isFinite(candidate.endHz) && candidate.endHz > candidate.startHz;
  };
  if (value.type !== 'granted' || typeof value.sourceId !== 'string' || !/^[!-~]{1,255}$/.test(value.sourceId)
    || !(value.receiver === null || (Number.isInteger(value.receiver) && (value.receiver as number) >= 0 && (value.receiver as number) < 65535))
    || !view(value.view) || !(value.coverage === null || view(value.coverage))
    || !Number.isInteger(value.width) || (value.width as number) < 1 || (value.width as number) > 65535
    || typeof value.fps !== 'number' || !Number.isFinite(value.fps) || value.fps < 0) return null;
  if (value.coverage && (value.coverage.startHz < value.view.startHz || value.coverage.endHz > value.view.endHz)) return null;
  return { type: 'granted', sourceId: value.sourceId, receiver: value.receiver as number | null,
    view: { ...value.view }, coverage: value.coverage === null ? null : { ...value.coverage }, width: value.width as number, fps: value.fps };
}

export function scopeGrantedGeometry(grant: ScopeGranted, context: CurrentScopeContext): ScopeGeometry {
  return { source: context.source, sourceId: grant.sourceId, receiver: grant.receiver,
    mode: context.mode ?? 0, width: grant.width, view: grant.view,
    coverage: grant.coverage ?? { startHz: grant.view.startHz, endHz: grant.view.startHz },
    outOfRange: false, hasPixels: false, freshness: 'stale' };
}

/** The stream accepts pixels and grants only from its selected subscription identity. */
export function scopeMatchesSubscription(
  facts: { readonly sourceId: string; readonly receiver: number | null }, subscription: ScopeSubscription | null,
): boolean {
  return subscription === null || (facts.sourceId === subscription.sourceId && facts.receiver === subscription.receiver);
}

/** The one LCD/SDR silence boundary. A frame is stale at this exact age. */
export const SCOPE_FRAME_SILENCE_MS = 500;

export function scopeSlotKey(sourceId: string, receiver: number | null): string {
  return receiver === null ? `${sourceId}:*` : `${sourceId}:${receiver}`;
}

/** Display metadata derived by the ingress owner; pixel payload stays on the frame sink. */
export function scopeFrameDisplayFacts(frame: ScopeFrame): Pick<ScopeGeometry, 'view' | 'width' | 'hasPixels' | 'outOfRange'> {
  return {
    view: { startHz: frame.viewStartHz, endHz: frame.viewEndHz },
    width: frame.width, hasPixels: frame.pixels !== null, outOfRange: frame.outOfRange,
  };
}

/** One ingress pass: the typed frame, its qualified geometry and the slot key it belongs to. */
export type ScopeIngressResult =
  | { readonly ok: true; readonly frame: ScopeFrame; readonly geometry: ScopeGeometry; readonly slotKey: string }
  | { readonly ok: false; readonly reason: ScopeParseError };

export function ingressScopeFrame(buffer: ArrayBuffer, context: CurrentScopeContext): ScopeIngressResult {
  const parsed = parseCurrentScopeFrame(buffer, context);
  if (!parsed.ok) return parsed;
  const frame = parsed.frame;
  return {
    ok: true,
    frame,
    geometry: {
      source: frame.source, sourceId: frame.sourceId, receiver: frame.receiver,
      ...scopeFrameDisplayFacts(frame),
      mode: context.mode ?? 0,
      coverage: { startHz: frame.coverageStartHz, endHz: frame.coverageEndHz },
      freshness: 'fresh',
    },
    slotKey: scopeSlotKey(frame.sourceId, frame.receiver),
  };
}

import type { Capabilities, FilterModeConfig, FilterSegmentConfig } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { FilterWidthDisplay, ScopeControlsViewModel } from '../../../semantic/radio-view-model';
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
  /** MOR-3677: the modeFilter group's composed width display (the spectrum
   *  chip's source), or null when the model carries no modeFilter group.
   *  Deliberately NOT part of `digest` beyond its state kind — a held-stale
   *  known reading must not churn the digest every freshness tick. */
  readonly filterWidthDisplay: FilterWidthDisplay | null;
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
  return status?.observed === true
    && (status.freshness === 'fresh' || status.freshness === 'stale')
    && (status.availability === 'available' || status.availability === 'stale');
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

function knownSemanticReading<T>(
  field: { readonly reading: { readonly status: 'known'; readonly value: T } | { readonly status: 'unknown' } }
    | undefined,
): T | null {
  return field?.reading.status === 'known' ? field.reading.value : null;
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
  // MOR-3677: the composed display verdict rides beside the Hz — the
  // spectrum chip reads it, geometry still gates on `filterWidthHz`.
  const filterWidthDisplay = modeFilter?.filterWidthDisplay ?? null;
  const dataMode = nonnegativeInteger(dataFact) ? dataFact : null;
  // Native IF shift remains an independently observed raw fact. PBT-only
  // radios instead expose a semantic shift derived from their two
  // `strictlySeen` PBT facts (fresh or held stale; R40 overturns the
  // stale half of MOR-1649); no raw
  // IF-shift observation exists for that radio shape.
  const ifShiftFact = caps.capabilities.includes('if_shift')
    ? knownReading(state, `${key}.ifShift`, passband?.ifShift)
    : strictlySeen(state, `${key}.pbtInner`) && strictlySeen(state, `${key}.pbtOuter`)
      ? knownSemanticReading(passband?.ifShift)
      : null;
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
    // Only the display's state KIND belongs to the digest identity: the
    // stale flag flips on every freshness tick (MOR-3677).
    filterWidthState: filterWidthDisplay === null ? null : filterWidthDisplay.state,
    filterShape: finiteNumber(knownReading(state, `${key}.filterShape`, passband?.filterShape))
      ? knownReading(state, `${key}.filterShape`, passband?.filterShape) as number : null,
    ifShiftHz: finiteNumber(ifShiftFact) ? ifShiftFact : null,
    pbtInnerHz: finiteNumber(knownReading(state, `${key}.pbtInner`, passband?.pbtInner))
      ? knownReading(state, `${key}.pbtInner`, passband?.pbtInner) as number : null,
    pbtOuterHz: finiteNumber(knownReading(state, `${key}.pbtOuter`, passband?.pbtOuter))
      ? knownReading(state, `${key}.pbtOuter`, passband?.pbtOuter) as number : null,
    dataMode,
    rule,
    scopeControls: model.scopeControls ? immutableClone(model.scopeControls) : null,
  };
  return immutableClone({ ...core, filterWidthDisplay, digest: JSON.stringify(core) });
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
