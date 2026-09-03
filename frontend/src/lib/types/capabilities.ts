// Capabilities — mirrors backend /api/v1/capabilities schema
import {
  compareExactDecimals, exactDecimalInteger, exactDecimalNumber, exactDecimalOnLattice,
  exactDecimalString, type ExactDecimal,
} from './exact-decimal';

export interface Band {
  name: string;
  start: number;
  end: number;
  default: number;
  bsrCode?: number;
}

export interface FreqRange {
  start: number;
  end: number;
  label: string;
  bands?: Band[];
}

export interface ScopeConfig {
  centerMode: boolean;
  amplitudeMax: number;
  defaultSpan: number;
}

export interface AudioConfig {
  sampleRate: number;
  channels: number;
  codecs: string[];
  jitterFloorMs?: number;
  jitterCeilingMs?: number;
}

export interface FilterSegmentConfig {
  hzMin: number;
  hzMax: number;
  stepHz: number;
  indexMin: number;
}

export interface FilterModeConfig {
  defaults: number[];
  fixed: boolean;
  stepHz?: number;
  minHz?: number;
  maxHz?: number;
  segments?: FilterSegmentConfig[];
  table?: number[];
}

export interface KeyboardBindingConfig {
  id: string;
  action: string;
  sequence: string[];
  section: string;
  label?: string;
  description?: string;
  modifiers?: string[];
  repeatable?: boolean;
  params?: Record<string, unknown>;
}

export interface KeyboardConfig {
  leaderKey: string;
  leaderTimeoutMs: number;
  altHints: boolean;
  helpTitle: string;
  bindings: KeyboardBindingConfig[];
}

export interface ControlRange {
  range_min?: number;
  range_max?: number;
  raw_min: number;
  raw_max: number;
  raw_center?: number;
  display_min?: number;
  display_max?: number;
  display_unit?: string;
  style?: string;
}

export type ControlQuantization =
  | 'nearest_ties_down'
  | 'nearest_ties_up'
  | 'floor'
  | 'ceil'
  | 'reject';
export type ControlRestoration = 'exact' | 'unavailable';

export interface ControlLookupPoint {
  readonly raw: number;
  readonly display: ExactDecimal;
}

interface ControlDomainBase {
  readonly range_min?: number;
  readonly range_max?: number;
  readonly raw_min: number;
  readonly raw_max: number;
  readonly raw_step: number;
  readonly raw_origin: number;
  readonly display_min: ExactDecimal;
  readonly display_max: ExactDecimal;
  readonly display_step: ExactDecimal;
  readonly display_origin: ExactDecimal;
  readonly display_unit: string;
  readonly style?: string;
  readonly quantization: ControlQuantization;
  readonly restoration: ControlRestoration;
}

export interface ScalarControlDomain extends ControlDomainBase {
  readonly mapping: 'identity' | 'linear';
  readonly raw_center?: never;
  readonly display_center?: never;
  readonly lookup?: never;
}

export interface CenteredControlDomain extends ControlDomainBase {
  readonly mapping: 'centered';
  readonly raw_center: number;
  readonly display_center: ExactDecimal;
  readonly lookup?: never;
}

export interface LookupControlDomain extends ControlDomainBase {
  readonly mapping: 'lookup';
  readonly raw_center?: never;
  readonly display_center?: never;
  readonly lookup: readonly ControlLookupPoint[];
}

export type ControlDomain = ScalarControlDomain | CenteredControlDomain | LookupControlDomain;

export type VfoScheme = 'single' | 'ab' | 'ab_shared' | 'main_sub';
export type VfoReadback = 'absolute' | 'selected_unselected' | 'none';

export interface WebRtcCapabilities {
  available: boolean;
  enabled: boolean;
}

export interface TxBand {
  name: string;
  start: number;
  end: number;
}

export interface Capabilities {
  [extension: string]: unknown;
  model: string;
  scope: boolean;
  audio: boolean;
  tx: boolean;
  audioTx?: boolean;
  audioTxRoute?: 'lan' | 'usb' | 'acc' | null;
  audioTxRequiredModInputSource?: number | null;
  capabilities: string[];
  receivers: number;
  vfoScheme: VfoScheme;
  /** Provider identity semantics; absent on older compatible servers. */
  vfoReadback?: VfoReadback;
  freqRanges: FreqRange[];
  modes: string[];
  filters: string[];
  filterWidthMin?: number;   // Min filter width in Hz (default 50)
  filterWidthMax?: number;   // Max filter width in Hz (default 9999)
  filterConfig?: Record<string, FilterModeConfig>;
  attValues?: number[];   // Attenuator dB steps (e.g. [0,20] for IC-7300, [0,6,12,18] for IC-7610)
  attLabels?: Record<string, string>;  // Attenuator labels (e.g. {"0":"OFF","6":"6dB"})
  preValues?: number[];   // Preamp levels: 0 = off, 1 = P1, 2 = P2, etc.
  preLabels?: Record<string, string>;  // Preamp labels (e.g. {"0":"OFF","1":"P1","2":"P2"})
  agcModes?: number[];    // AGC mode values (e.g. [1,2,3] = FAST/MID/SLOW)
  agcLabels?: Record<string, string>;  // AGC mode labels (e.g. {"1":"FAST","2":"MID","3":"SLOW"})
  /** RF/SQL control model (MOR-1447 leg 2): "separate" (default, two
   *  independent controls) or "combined" (Icom-style single RF/SQL knob).
   *  Absent on older servers — treat as "separate". */
  rfSqlControlModel?: 'separate' | 'combined';
  dataModeCount?: number;
  dataModeLabels?: Record<string, string>;
  keyboard?: KeyboardConfig | null;
  antennas?: number;      // Number of antenna ports
  hasRxAntenna?: boolean;
  scopeSource?: string | null;  // "hardware", "audio_fft", or null
  audioFftAvailable?: boolean;  // true when audio FFT scope is available (even with hardware scope)
  scopeConfig?: ScopeConfig;
  audioConfig: AudioConfig;
  webrtc: WebRtcCapabilities;
  controls?: Readonly<Record<string, ControlRange | ControlDomain>>;
  txBands: TxBand[] | null;
  meterCalibrations?: Record<string, MeterCalPoint[]>;
  meterRedlines?: Record<string, number>;
}

export interface MeterCalPoint {
  raw: number;
  actual: number;
  label: string;
}

function invalid(path: string, expected: string): never {
  throw new TypeError(`Invalid capabilities payload at ${path}: expected ${expected}`);
}

function requireRecord(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    invalid(path, 'an object');
  }
  return value as Record<string, unknown>;
}

function requireString(value: unknown, path: string): void {
  if (typeof value !== 'string') invalid(path, 'a string');
}

function requireBoolean(value: unknown, path: string): void {
  if (typeof value !== 'boolean') invalid(path, 'a boolean');
}

function requireStringArray(value: unknown, path: string): void {
  if (!Array.isArray(value)) invalid(path, 'an array');
  value.forEach((item, index) => requireString(item, `${path}[${index}]`));
}

function requireInteger(value: unknown, path: string, positive = false): void {
  if (
    typeof value !== 'number'
    || !Number.isInteger(value)
    || !Number.isFinite(value)
    || (positive && value <= 0)
  ) {
    invalid(path, positive ? 'a positive integer' : 'an integer');
  }
}

function requireFiniteNumber(value: unknown, path: string): void {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    invalid(path, 'a finite number');
  }
}

const DOMAIN_REQUIRED_KEYS = [
  'raw_min', 'raw_max', 'raw_step', 'raw_origin',
  'display_min', 'display_max', 'display_step', 'display_origin', 'display_unit',
  'mapping', 'quantization', 'restoration',
] as const;
const DOMAIN_KEYS = new Set([
  ...DOMAIN_REQUIRED_KEYS, 'style', 'range_min', 'range_max',
  'raw_center', 'display_center', 'lookup',
]);
const EXPLICIT_DOMAIN_KEYS = new Set([
  'raw_step', 'raw_origin', 'display_step', 'display_origin', 'display_center',
  'mapping', 'quantization', 'restoration', 'lookup',
]);

function finiteNumber(value: unknown, path: string): number {
  requireFiniteNumber(value, path);
  return value as number;
}

function safeInteger(value: unknown, path: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    invalid(path, 'a safe integer');
  }
  return value;
}

function choice<T extends string>(value: unknown, path: string, values: readonly T[]): T {
  if (typeof value !== 'string' || !values.includes(value as T)) {
    invalid(path, values.join(' | '));
  }
  return value as T;
}

function validateRawAxis(
  low: number,
  high: number,
  step: number,
  origin: number,
  path: string,
): void {
  if (low >= high) invalid(path, 'min < max');
  if (step <= 0) invalid(`${path}_step`, '> 0');
  if (origin < low || origin > high) invalid(`${path}_origin`, 'inside its declared range');
  if (!exactDecimalOnLattice(exactDecimalInteger(low), exactDecimalInteger(origin), exactDecimalInteger(step))) invalid(`${path}_min`, 'a value on its declared lattice');
  if (!exactDecimalOnLattice(exactDecimalInteger(high), exactDecimalInteger(origin), exactDecimalInteger(step))) invalid(`${path}_max`, 'a value on its declared lattice');
}

function validateDisplayAxis(
  low: ExactDecimal, high: ExactDecimal, step: ExactDecimal, origin: ExactDecimal, path: string,
): void {
  if (compareExactDecimals(low, high) >= 0) invalid(path, 'min < max');
  if (compareExactDecimals(step, exactDecimalInteger(0)) <= 0) invalid(`${path}_step`, '> 0');
  if (compareExactDecimals(origin, low) < 0 || compareExactDecimals(origin, high) > 0) invalid(`${path}_origin`, 'inside its declared range');
  if (!exactDecimalOnLattice(low, origin, step)) invalid(`${path}_min`, 'a value on its declared lattice');
  if (!exactDecimalOnLattice(high, origin, step)) invalid(`${path}_max`, 'a value on its declared lattice');
}

function validateLookup(
  value: unknown,
  path: string,
  rawAxis: readonly [number, number, number, number],
  displayAxis: readonly [ExactDecimal, ExactDecimal, ExactDecimal, ExactDecimal],
  restoration: ControlRestoration,
  kind: DisplayKind,
): readonly ControlLookupPoint[] {
  if (!Array.isArray(value) || value.length === 0) invalid(path, 'a non-empty array');
  const points = value.map((item, index) => {
    const pointPath = `${path}[${index}]`;
    const point = requireRecord(item, pointPath);
    if (Object.keys(point).length !== 2 || !('raw' in point) || !('display' in point)) {
      invalid(pointPath, 'an object containing exactly raw and display');
    }
    const raw = safeInteger(point.raw, `${pointPath}.raw`);
    const display = readDisplay(point.display, `${pointPath}.display`, kind);
    if (raw < rawAxis[0] || raw > rawAxis[1]) invalid(`${pointPath}.raw`, 'inside its declared range');
    if (!exactDecimalOnLattice(exactDecimalInteger(raw), exactDecimalInteger(rawAxis[3]), exactDecimalInteger(rawAxis[2]))) invalid(`${pointPath}.raw`, 'a value on its declared lattice');
    if (compareExactDecimals(display, displayAxis[0]) < 0 || compareExactDecimals(display, displayAxis[1]) > 0) {
      invalid(`${pointPath}.display`, 'inside its declared range');
    }
    if (!exactDecimalOnLattice(display, displayAxis[3], displayAxis[2])) {
      invalid(`${pointPath}.display`, 'a value on its declared lattice');
    }
    return Object.freeze({ raw, display });
  });
  for (const field of ['raw', 'display'] as const) {
    const values = points.map((point) => point[field]);
    const order = (left: number | ExactDecimal, right: number | ExactDecimal) => field === 'raw'
      ? left as number - (right as number) : compareExactDecimals(left as ExactDecimal, right as ExactDecimal);
    if (values.some((item, index) => index > 0 && order(values[index - 1]!, item) === 0)) invalid(path, `unique ${field} values`);
    const increasing = values.every((item, index) => index === 0 || order(values[index - 1]!, item) < 0);
    const decreasing = values.every((item, index) => index === 0 || order(values[index - 1]!, item) > 0);
    if (values.length > 1 && !increasing && !decreasing) invalid(path, `strictly monotonic ${field} values`);
  }
  const expected = (BigInt(rawAxis[1]) - BigInt(rawAxis[0])) / BigInt(rawAxis[2]) + 1n;
  if (restoration === 'exact' && BigInt(points.length) !== expected) {
    invalid(path, 'complete raw lattice coverage for exact restoration');
  }
  return Object.freeze(points);
}

type DisplayKind = 'number' | 'string';

function readDisplay(value: unknown, path: string, kind: DisplayKind): ExactDecimal {
  if (kind === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value)) invalid(path, 'a finite number');
    return exactDecimalNumber(value);
  }
  const decimal = exactDecimalString(value);
  if (!decimal) invalid(path, 'a canonical fixed-point decimal string');
  return decimal;
}

function displayKind(raw: Record<string, unknown>, mapping: string, path: string): DisplayKind {
  const values: unknown[] = [raw.display_min, raw.display_max, raw.display_step, raw.display_origin];
  if (mapping === 'centered') values.push(raw.display_center);
  if (mapping === 'lookup' && Array.isArray(raw.lookup)) {
    raw.lookup.forEach((point) => { if (point && typeof point === 'object' && !Array.isArray(point)) values.push((point as Record<string, unknown>).display); });
  }
  const kinds = new Set(values.map((value) => typeof value));
  if (kinds.size !== 1 || (!kinds.has('number') && !kinds.has('string'))) invalid(path, 'a homogeneous all-number or all-canonical-string display domain');
  return kinds.has('number') ? 'number' : 'string';
}

function parseControlDomain(raw: Record<string, unknown>, path: string): ControlDomain {
  const missing = DOMAIN_REQUIRED_KEYS.filter((key) => !(key in raw));
  if (missing.length) invalid(path, `required fields (${missing.join(', ')})`);

  const mapping = choice(raw.mapping, `${path}.mapping`, ['identity', 'linear', 'centered', 'lookup']);
  const quantization = choice(raw.quantization, `${path}.quantization`, [
    'nearest_ties_down', 'nearest_ties_up', 'floor', 'ceil', 'reject',
  ]);
  const restoration = choice(raw.restoration, `${path}.restoration`, ['exact', 'unavailable']);
  const displays = displayKind(raw, mapping, path);
  const rawAxis = [
    safeInteger(raw.raw_min, `${path}.raw_min`), safeInteger(raw.raw_max, `${path}.raw_max`),
    safeInteger(raw.raw_step, `${path}.raw_step`), safeInteger(raw.raw_origin, `${path}.raw_origin`),
  ] as const;
  const displayAxis = [
    readDisplay(raw.display_min, `${path}.display_min`, displays), readDisplay(raw.display_max, `${path}.display_max`, displays),
    readDisplay(raw.display_step, `${path}.display_step`, displays), readDisplay(raw.display_origin, `${path}.display_origin`, displays),
  ] as const;
  if (typeof raw.display_unit !== 'string' || !raw.display_unit.trim()) {
    invalid(`${path}.display_unit`, 'a non-empty string');
  }
  validateRawAxis(...rawAxis, `${path}.raw`);
  validateDisplayAxis(...displayAxis, `${path}.display`);
  if ('range_min' in raw || 'range_max' in raw) {
    if (!('range_min' in raw) || !('range_max' in raw)
      || safeInteger(raw.range_min, `${path}.range_min`) !== rawAxis[0]
      || safeInteger(raw.range_max, `${path}.range_max`) !== rawAxis[1]) {
      invalid(path, 'legacy range equal to explicit raw bounds');
    }
  }
  if (raw.style !== undefined) {
    choice(raw.style, `${path}.style`, ['toggle', 'stepped', 'selector', 'toggle_and_level', 'level_is_toggle']);
  }

  const domain: Record<string, unknown> = { ...raw, ...Object.fromEntries(['min', 'max', 'step', 'origin'].map((field, index) => [`display_${field}`, displayAxis[index]!])), mapping, quantization, restoration };
  if (mapping === 'identity' && rawAxis.some((item, index) => compareExactDecimals(exactDecimalInteger(item), displayAxis[index]!) !== 0)) {
    invalid(path, 'identical raw and display domains for identity mapping');
  }
  if (mapping === 'centered') {
    const rawCenter = safeInteger(raw.raw_center, `${path}.raw_center`);
    const displayCenter = readDisplay(raw.display_center, `${path}.display_center`, displays);
    domain.display_center = displayCenter;
    if (rawCenter < rawAxis[0] || rawCenter > rawAxis[1] || !exactDecimalOnLattice(exactDecimalInteger(rawCenter), exactDecimalInteger(rawAxis[3]), exactDecimalInteger(rawAxis[2]))) {
      invalid(`${path}.raw_center`, 'a value in range on its declared lattice');
    }
    if (compareExactDecimals(displayCenter, displayAxis[0]) < 0 || compareExactDecimals(displayCenter, displayAxis[1]) > 0
      || !exactDecimalOnLattice(displayCenter, displayAxis[3], displayAxis[2])) {
      invalid(`${path}.display_center`, 'a value in range on its declared lattice');
    }
  } else if ('raw_center' in raw || 'display_center' in raw) {
    invalid(path, 'center fields only for centered mapping');
  }
  if (mapping === 'lookup') {
    domain.lookup = validateLookup(raw.lookup, `${path}.lookup`, rawAxis, displayAxis, restoration, displays);
  } else if ('lookup' in raw) {
    invalid(`${path}.lookup`, 'only for lookup mapping');
  }
  return Object.freeze(domain) as unknown as ControlDomain;
}

function normalizeControls(value: unknown): Readonly<Record<string, ControlRange | ControlDomain>> | null {
  const controls = requireRecord(value, '$.controls');
  let changed = false;
  const normalized = Object.fromEntries(Object.entries(controls).map(([name, value]) => {
    const control = requireRecord(value, `$.controls.${name}`);
    const unknown = Object.keys(control).filter((key) => !DOMAIN_KEYS.has(key));
    if (unknown.length) invalid(`$.controls.${name}`, `no unknown keys (${unknown.join(', ')})`);
    const explicit = Object.keys(control).some((key) => EXPLICIT_DOMAIN_KEYS.has(key));
    changed ||= explicit;
    return [name, explicit ? parseControlDomain(control, `$.controls.${name}`) : value];
  }));
  return changed ? Object.freeze(normalized) as Readonly<Record<string, ControlRange | ControlDomain>> : null;
}

export function validateCapabilities(value: unknown): Capabilities {
  const raw = requireRecord(value, '$');

  requireString(raw.model, '$.model');
  requireBoolean(raw.scope, '$.scope');
  requireBoolean(raw.audio, '$.audio');
  requireBoolean(raw.tx, '$.tx');
  requireStringArray(raw.capabilities, '$.capabilities');
  requireInteger(raw.receivers, '$.receivers', true);
  if (Object.prototype.hasOwnProperty.call(raw, 'hasRxAntenna')) {
    requireBoolean(raw.hasRxAntenna, '$.hasRxAntenna');
  }

  const txAudioFields = [
    'audioTx',
    'audioTxRoute',
    'audioTxRequiredModInputSource',
  ] as const;
  const presentTxAudioFields = txAudioFields.filter((field) =>
    Object.prototype.hasOwnProperty.call(raw, field),
  );
  if (presentTxAudioFields.length !== 0 && presentTxAudioFields.length !== txAudioFields.length) {
    invalid('$', 'a complete or absent TX-audio capability group');
  }
  if (presentTxAudioFields.length === txAudioFields.length) {
    requireBoolean(raw.audioTx, '$.audioTx');
    const routes = ['lan', 'usb', 'acc'] as const;
    if (raw.audioTxRoute !== null && !routes.includes(raw.audioTxRoute as typeof routes[number])) {
      invalid('$.audioTxRoute', 'lan | usb | acc | null');
    }
    if (
      raw.audioTxRequiredModInputSource !== null
      && (
        typeof raw.audioTxRequiredModInputSource !== 'number'
        || !Number.isSafeInteger(raw.audioTxRequiredModInputSource)
        || raw.audioTxRequiredModInputSource < 0
      )
    ) {
      invalid('$.audioTxRequiredModInputSource', 'a safe non-negative integer or null');
    }

    const tags = raw.capabilities as string[];
    if (
      raw.audioTx === false
      && (raw.audioTxRoute !== null || raw.audioTxRequiredModInputSource !== null)
    ) {
      invalid('$.audioTx', 'non-contradictory TX-audio capability facts');
    }
    if (
      raw.audioTx === true
      && (
        raw.audioTxRoute === null
        || raw.audio !== true
        || raw.tx !== true
        || !tags.includes('audio')
        || !tags.includes('tx')
        || (
          raw.audioTxRequiredModInputSource !== null
          && (
            raw.audioTxRoute !== 'lan'
            || !tags.includes('mod_input_routing')
          )
        )
      )
    ) {
      invalid('$.audioTx', 'non-contradictory TX-audio capability facts');
    }
  }

  const schemes: readonly VfoScheme[] = ['single', 'ab', 'ab_shared', 'main_sub'];
  if (!schemes.includes(raw.vfoScheme as VfoScheme)) {
    invalid('$.vfoScheme', schemes.join(' | '));
  }
  const expectedReceivers = raw.vfoScheme === 'single' || raw.vfoScheme === 'ab' ? 1 : 2;
  if (raw.receivers !== expectedReceivers) {
    invalid('$.vfoScheme', `${String(raw.vfoScheme)} with receivers=${expectedReceivers}`);
  }
  if (Object.prototype.hasOwnProperty.call(raw, 'vfoReadback')) {
    const readbacks: readonly VfoReadback[] = ['absolute', 'selected_unselected', 'none'];
    if (!readbacks.includes(raw.vfoReadback as VfoReadback)) {
      invalid('$.vfoReadback', readbacks.join(' | '));
    }
  }

  if (!Array.isArray(raw.freqRanges)) invalid('$.freqRanges', 'an array');
  raw.freqRanges.forEach((value, rangeIndex) => {
    const rangePath = `$.freqRanges[${rangeIndex}]`;
    const range = requireRecord(value, rangePath);
    requireFiniteNumber(range.start, `${rangePath}.start`);
    requireFiniteNumber(range.end, `${rangePath}.end`);
    requireString(range.label, `${rangePath}.label`);
    if ('bands' in range) {
      if (!Array.isArray(range.bands)) invalid(`${rangePath}.bands`, 'an array');
      range.bands.forEach((value, bandIndex) => {
        const bandPath = `${rangePath}.bands[${bandIndex}]`;
        const band = requireRecord(value, bandPath);
        requireString(band.name, `${bandPath}.name`);
        requireFiniteNumber(band.start, `${bandPath}.start`);
        requireFiniteNumber(band.end, `${bandPath}.end`);
        requireFiniteNumber(band.default, `${bandPath}.default`);
        if ('bsrCode' in band) {
          requireFiniteNumber(band.bsrCode, `${bandPath}.bsrCode`);
        }
      });
    }
  });
  requireStringArray(raw.modes, '$.modes');
  requireStringArray(raw.filters, '$.filters');

  const audioConfig = requireRecord(raw.audioConfig, '$.audioConfig');
  requireInteger(audioConfig.sampleRate, '$.audioConfig.sampleRate', true);
  requireInteger(audioConfig.channels, '$.audioConfig.channels', true);
  requireStringArray(audioConfig.codecs, '$.audioConfig.codecs');

  const webrtc = requireRecord(raw.webrtc, '$.webrtc');
  requireBoolean(webrtc.available, '$.webrtc.available');
  requireBoolean(webrtc.enabled, '$.webrtc.enabled');

  if (raw.txBands !== null) {
    if (!Array.isArray(raw.txBands)) invalid('$.txBands', 'an array or null');
    raw.txBands.forEach((value, index) => {
      const band = requireRecord(value, `$.txBands[${index}]`);
      requireString(band.name, `$.txBands[${index}].name`);
      requireInteger(band.start, `$.txBands[${index}].start`);
      requireInteger(band.end, `$.txBands[${index}].end`);
    });
  }

  if ('controls' in raw) {
    const controls = normalizeControls(raw.controls);
    if (controls) return { ...raw, controls } as unknown as Capabilities;
  }

  return raw as unknown as Capabilities;
}
