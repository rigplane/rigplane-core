import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { deriveIfShift, pbtRangeFromCaps, pbtRawToHz } from '$lib/radio/filter-controls';
import type { ScopeFramePresentation } from '../scope-frame-host';
import { qualifyDisplayObservation, qualifyRadioDisplayObservation } from './display-observation';
import { derivePresentationCapabilities, type ReceiverId } from './presentation-capabilities';
import { SCOPE_FRAME_SILENCE_MS, toSpectrumAuthority } from './scope-adapter';

type Scalar = number | string | boolean;
type Observation = Readonly<{ value: Scalar; marker: number }>;
type Observations = Readonly<Record<string, Observation>>;
type Markers = Readonly<Record<string, number>>;
export interface ScopePassbandTuple {
  readonly frequencyHz: number;
  readonly mode: string;
  readonly widthHz: number;
  readonly shiftHz: number;
  readonly frameMode: number;
  readonly startHz: number;
  readonly endHz: number;
}
export type ScopePassbandDisplay =
  | Readonly<{ state: 'current' | 'stale'; tuple: ScopePassbandTuple }>
  | Readonly<{ state: 'unknown'; reason: string }>
  | Readonly<{ state: 'unsupported' }>;
export interface ScopePassbandDisplayState {
  readonly display: ScopePassbandDisplay;
  readonly identity: string | null;
  readonly domain: string | null;
  readonly observations: Observations;
  readonly geometryPaths: readonly string[];
  readonly receipt: number;
  readonly floors: Readonly<{ domain: string | null; geometry: Markers; receipt: number }> | null;
}
export interface ScopePassbandDisplayInput {
  state: ServerState | null;
  caps: Capabilities | null;
  selection: { receiver: ReceiverId; slot: 'single' | 'A' | 'B' } | null;
  session: { state: 'disconnected' | 'connecting' | 'connected' | 'reconnecting'; epoch: number } | null;
  frame: ScopeFramePresentation | null;
}
const EMPTY_OBSERVATIONS: Observations = Object.freeze({});
const EMPTY_PATHS: readonly string[] = Object.freeze([]);
export const EMPTY_SCOPE_PASSBAND_DISPLAY: ScopePassbandDisplayState = Object.freeze({
  display: Object.freeze({ state: 'unknown', reason: 'not-observed' }),
  identity: null, domain: null, observations: EMPTY_OBSERVATIONS,
  geometryPaths: EMPTY_PATHS, receipt: 0, floors: null,
});
const nonnegative = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0;
const positive = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n > 0;
const integer = (n: unknown): n is number => nonnegative(n) && Number.isSafeInteger(n);
const modeName = (value: string | undefined) => value?.trim().toUpperCase() ?? '';
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]));
  return value;
}
function capabilityIdentity(caps: Capabilities): string {
  const tags = ['scope', 'dual_rx', 'filter_width', 'if_shift', 'pbt', 'data_mode'];
  return JSON.stringify(canonical({
    scheme: caps.vfoScheme, receivers: caps.receivers, readback: caps.vfoReadback,
    tags: tags.filter((tag) => caps.capabilities.includes(tag)),
    modes: caps.modes, filters: caps.filters, config: caps.filterConfig,
    min: caps.filterWidthMin, max: caps.filterWidthMax,
    pbt: caps.capabilities.includes('if_shift') ? undefined : caps.controls?.pbt_inner,
    shift: caps.capabilities.includes('if_shift') ? caps.controls?.if_shift : undefined,
  }));
}
interface Candidate { identity: string; tuple: ScopePassbandTuple; stale: boolean; strict: boolean }
interface Inspection {
  candidate: Candidate | null;
  domain: string | null;
  observations: Record<string, Observation>;
  geometryPaths: string[];
  receipt: number;
  reason: string;
  unsupported: boolean;
}
function inspect(input: ScopePassbandDisplayInput): Inspection {
  const { state, caps, selection, session, frame: presentation } = input;
  const envelope = presentation?.envelope;
  const result: Inspection = { candidate: null, domain: null, observations: {}, geometryPaths: [],
    receipt: integer(envelope?.acceptedSequence) ? envelope.acceptedSequence : 0,
    reason: 'identity-unresolved', unsupported: false };
  if (!state || !caps || !selection || state.stateContractVersion !== 1 || caps.stateContractVersion !== 1
    || !integer(state.providerGeneration) || state.providerGeneration !== caps.providerGeneration
    || (selection.receiver !== 'MAIN' && selection.receiver !== 'SUB')) return result;
  const { topology, scope } = derivePresentationCapabilities(caps);
  if (!topology || !topology.operationalReceivers.includes(selection.receiver)
    || state.active !== selection.receiver || !Array.isArray(caps.capabilities)
    || !caps.capabilities.every((tag) => typeof tag === 'string')) return result;
  const key = selection.receiver === 'MAIN' ? 'main' : 'sub';
  const rx = state[key];
  if (!rx) return result;
  result.domain = JSON.stringify([state.providerGeneration, selection.receiver]);
  const has = (tag: string) => caps.capabilities.includes(tag);
  const native = has('if_shift');
  const scale = native ? undefined : pbtRangeFromCaps(caps);
  const validScale = scale && scale.rawCenter > 0 && scale.displayMax > 0 && scale.displayMin < scale.displayMax;
  if (!scope.hardwareScopeAvailable || !has('filter_width') || !caps.filters?.length
    || (!native && (!has('pbt') || !validScale))) {
    result.unsupported = true; return result;
  }
  const geometryFields = native ? ['filterWidth', 'ifShift'] : ['filterWidth', 'pbtInner', 'pbtOuter'];
  result.geometryPaths = geometryFields.map((leaf) => `${key}.${leaf}`);
  let invalid = false;
  let stale = false;
  function read<T extends Scalar>(path: string, value: T | undefined, radio = false): T | undefined {
    const args = { state, caps, path, value, structural: true };
    const observation = radio ? qualifyRadioDisplayObservation(args)
      : qualifyDisplayObservation({ ...args, receiver: selection!.receiver });
    if (observation.state !== 'current' && observation.state !== 'stale') { invalid = true; return undefined; }
    stale ||= observation.state === 'stale';
    result.observations[path] = Object.freeze({ value: observation.value,
      marker: state!.fieldStatus![path].lastObservedMonotonic! });
    for (let prefix = path; prefix.includes('.');) {
      prefix = prefix.slice(0, prefix.lastIndexOf('.'));
      const parent = state!.fieldStatus?.[prefix];
      if (parent) result.observations[prefix] = Object.freeze({ value: true, marker: parent.lastObservedMonotonic! });
    }
    return observation.value;
  }
  const width = read(`${key}.filterWidth`, rx.filterWidth ?? undefined);
  const shift = native ? read(`${key}.ifShift`, rx.ifShift) : undefined;
  const inner = native ? undefined : read(`${key}.pbtInner`, rx.pbtInner);
  const outer = native ? undefined : read(`${key}.pbtOuter`, rx.pbtOuter);
  if (topology.structuralCount === 2) read('active', state.active, true);
  const slots = topology.slots[selection.receiver];
  const slotted = slots !== null;
  if (slotted) {
    const slot = read(`${key}.activeSlot`, rx.activeSlot);
    if ((selection.slot !== 'A' && selection.slot !== 'B') || slot !== selection.slot
      || !slots?.includes(selection.slot)) return result;
  } else if (selection.slot !== 'single') return result;
  const position = slotted ? (selection.slot === 'A' ? rx.vfoA : rx.vfoB) : rx;
  const base = slotted ? `${key}.${selection.slot === 'A' ? 'vfoA' : 'vfoB'}` : key;
  const frequency = read(`${base}.freqHz`, position?.freqHz);
  const mode = modeName(read(`${base}.mode`, position?.mode));
  const filter = read(`${base}.${slotted ? 'filterNum' : 'filter'}`,
    slotted ? (selection.slot === 'A' ? rx.vfoA?.filterNum : rx.vfoB?.filterNum) ?? undefined : rx.filter ?? undefined);
  if (slotted) {
    if (modeName(read(`${key}.mode`, rx.mode)) !== mode
      || read(`${key}.filter`, rx.filter ?? undefined) !== filter
      || read(`${key}.freqHz`, rx.freqHz) !== frequency) invalid = true;
  }
  const data = has('data_mode') ? read(`${key}.dataMode`, rx.dataMode) : 'structurally-unsupported';
  const shiftHz = native ? shift : validScale && inner !== undefined && outer !== undefined
    ? deriveIfShift(pbtRawToHz(inner, scale), pbtRawToHz(outer, scale)) : undefined;
  if (invalid || !positive(frequency) || !positive(width) || !mode || !caps.modes?.some((m) => modeName(m) === mode)
    || !integer(filter) || filter === 0 || !caps.filters.includes(`FIL${filter}`)
    || (has('data_mode') && !integer(data)) || typeof shiftHz !== 'number' || !Number.isFinite(shiftHz)) {
    result.reason = 'invalid-observation'; return result;
  }
  if (!session || session.state !== 'connected' || !integer(session.epoch) || session.epoch === 0) return result;
  result.reason = 'frame-unavailable';
  const authority = presentation?.authority;
  const resolution = presentation?.resolution;
  const frame = envelope?.frame;
  const receiver = selection.receiver === 'MAIN' ? 0 : 1;
  const age = authority && envelope ? authority.nowMonotonic - envelope.receivedAt : NaN;
  if (!envelope || !authority || !frame || resolution?.state !== 'live'
    || envelope.source !== 'hardware' || authority.source !== 'hardware'
    || envelope.receiver !== receiver || authority.receiver !== receiver || frame.receiver !== receiver
    || envelope.providerGeneration !== state.providerGeneration || authority.providerGeneration !== state.providerGeneration
    || !integer(envelope.transportEpoch) || envelope.transportEpoch === 0 || envelope.transportEpoch !== authority.transportEpoch
    || !integer(envelope.acceptedSequence) || envelope.acceptedSequence === 0
    || authority.transport !== 'connected' || !authority.demanded || !nonnegative(age) || age >= SCOPE_FRAME_SILENCE_MS
    || !nonnegative(envelope.receivedAt) || !integer(frame.mode) || frame.mode > 255
    || !Number.isFinite(frame.startFreq) || !Number.isFinite(frame.endFreq) || frame.endFreq <= frame.startFreq
    || resolution.frame.source !== 'hardware' || resolution.frame.receiver !== selection.receiver
    || resolution.frame.freshness !== 'fresh'
    || resolution.frame.startHz !== frame.startFreq || resolution.frame.endHz !== frame.endFreq) return result;
  const tuple = Object.freeze({ frequencyHz: frequency, mode, widthHz: width, shiftHz,
    frameMode: frame.mode, startHz: frame.startFreq, endHz: frame.endFreq });
  const strict = stale ? null : toSpectrumAuthority(state, caps);
  result.candidate = {
    tuple, stale, identity: JSON.stringify([state.providerGeneration, capabilityIdentity(caps), session.epoch,
      selection.receiver, selection.slot, frequency, mode, filter, data, 'hardware',
      envelope.transportEpoch, frame.mode, frame.startFreq, frame.endFreq]),
    strict: !!strict && strict.receiver === receiver && strict.frequencyHz === frequency
      && modeName(strict.mode ?? undefined) === mode && strict.filter === `FIL${filter}`
      && strict.filterWidthHz === width && strict.ifShiftHz === shiftHz
      && (!has('data_mode') || strict.dataMode === data),
  };
  return result;
}
function geometryMarkers(paths: readonly string[], observations: Observations): Markers {
  return Object.freeze(Object.fromEntries(paths.flatMap((path) =>
    observations[path] ? [[path, observations[path].marker]] : [])));
}
function mergeMarkers(...vectors: Markers[]): Markers {
  const merged: Record<string, number> = {};
  for (const vector of vectors) for (const [path, marker] of Object.entries(vector)) {
    merged[path] = Math.max(merged[path] ?? -1, marker);
  }
  return Object.freeze(merged);
}
function active(display: ScopePassbandDisplay): display is Extract<ScopePassbandDisplay, { tuple: ScopePassbandTuple }> {
  return display.state === 'current' || display.state === 'stale';
}
export function projectScopePassbandDisplay(
  previous: ScopePassbandDisplayState, input: ScopePassbandDisplayInput,
): ScopePassbandDisplayState {
  const next = inspect(input);
  const candidate = next.candidate;
  const sameDomain = next.domain !== null && previous.domain === next.domain;
  const changedIdentity = !!candidate && previous.identity !== null && candidate.identity !== previous.identity;
  const regression = sameDomain && Object.entries(next.observations).some(([path, observation]) => {
    const old = previous.observations[path];
    return old && (observation.marker < old.marker
      || (observation.marker === old.marker && observation.value !== old.value));
  });
  const changedGeometry = next.geometryPaths.some((path) =>
    next.observations[path]?.value !== previous.observations[path]?.value);
  const receiptRegression = candidate !== null && next.receipt < previous.receipt;
  const invalid = !candidate || regression || receiptRegression || (!candidate.stale && !candidate.strict)
    || (candidate.stale && changedGeometry);
  const hasTuple = active(previous.display);
  const domainChange = previous.floors !== null && next.domain !== null && previous.floors.domain !== next.domain;
  const retire = (hasTuple && (invalid || changedIdentity)) || changedIdentity || domainChange;
  let floors = previous.floors;
  if (retire) {
    const domain = next.domain ?? previous.domain;
    floors = Object.freeze({ domain, receipt: Math.max(previous.receipt, next.receipt),
      geometry: mergeMarkers(
        previous.floors?.domain === domain ? previous.floors.geometry : {},
        previous.domain === domain ? geometryMarkers(previous.geometryPaths, previous.observations) : {},
        next.domain === domain ? geometryMarkers(next.geometryPaths, next.observations) : {},
      ) });
  }
  const crossedFloors = !floors || (floors.domain === next.domain && next.receipt > floors.receipt
    && next.geometryPaths.every((path) => next.observations[path]
      && next.observations[path].marker > (floors.geometry[path] ?? -1)));
  const canRetain = hasTuple && !retire && !invalid;
  const canCapture = candidate && !candidate.stale && candidate.strict && !regression
    && !receiptRegression && !retire && crossedFloors;
  const display: ScopePassbandDisplay = canRetain || canCapture
    ? { state: candidate!.stale ? 'stale' : 'current',
      tuple: canRetain && !changedGeometry ? tupleOf(previous.display) : candidate!.tuple }
    : next.unsupported ? { state: 'unsupported' }
      : { state: 'unknown', reason: retire ? 'retired' : candidate?.stale ? 'first-stale'
        : candidate ? 'awaiting-evidence' : next.reason };
  return Object.freeze({
    display: Object.freeze(display), identity: candidate?.identity ?? previous.identity,
    domain: next.domain ?? previous.domain,
    observations: Object.freeze(regression && sameDomain ? { ...previous.observations } : { ...next.observations }),
    geometryPaths: Object.freeze([...next.geometryPaths]), receipt: Math.max(previous.receipt, next.receipt),
    floors: active(display) ? null : floors,
  });
}
function tupleOf(display: ScopePassbandDisplay): ScopePassbandTuple {
  if (!active(display)) throw new Error('Passband tuple unavailable');
  return display.tuple;
}
