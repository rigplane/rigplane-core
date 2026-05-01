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

export interface Capabilities {
  model: string;
  scope: boolean;
  audio: boolean;
  tx: boolean;
  capabilities: string[];
  receivers?: number;      // 1 = single, 2 = dual receiver
  vfoScheme?: string;      // "ab" or "main_sub"
  hasLan?: boolean;        // Radio has LAN connectivity
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
  dataModeCount?: number;
  dataModeLabels?: Record<string, string>;
  keyboard?: KeyboardConfig | null;
  antennas?: number;      // Number of antenna ports
  scopeSource?: string | null;  // "hardware", "audio_fft", or null
  audioFftAvailable?: boolean;  // true when audio FFT scope is available (even with hardware scope)
  scopeConfig?: ScopeConfig;
  audioConfig?: AudioConfig;
  controls?: Record<string, ControlRange>;
  txBands?: { name: string; start: number; end: number }[];
  meterCalibrations?: Record<string, MeterCalPoint[]>;
  meterRedlines?: Record<string, number>;
}

export interface MeterCalPoint {
  raw: number;
  actual: number;
  label: string;
}
