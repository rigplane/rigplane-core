// Capabilities — mirrors backend /api/v1/capabilities schema

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
  raw_min: number;
  raw_max: number;
  raw_center?: number;
  display_min?: number;
  display_max?: number;
  display_unit?: string;
  style?: string;
}

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
  scopeSource?: string | null;  // "hardware", "audio_fft", or null
  audioFftAvailable?: boolean;  // true when audio FFT scope is available (even with hardware scope)
  scopeConfig?: ScopeConfig;
  audioConfig: AudioConfig;
  webrtc: WebRtcCapabilities;
  controls?: Record<string, ControlRange>;
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

export function validateCapabilities(value: unknown): Capabilities {
  const raw = requireRecord(value, '$');

  requireString(raw.model, '$.model');
  requireBoolean(raw.scope, '$.scope');
  requireBoolean(raw.audio, '$.audio');
  requireBoolean(raw.tx, '$.tx');
  requireStringArray(raw.capabilities, '$.capabilities');
  requireInteger(raw.receivers, '$.receivers', true);

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

  return raw as unknown as Capabilities;
}
