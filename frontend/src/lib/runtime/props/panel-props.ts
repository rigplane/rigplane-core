/**
 * panel-props — pure state→props mappers for the runtime layer.
 *
 * Duplicate of `components-v2/wiring/state-adapter` mappers, created as a
 * stepping stone to eliminate the `lib/runtime` → `components-v2` dependency
 * (epic #959, issue #996).
 *
 * RULES:
 *  - NO imports from `components-v2/*`
 *  - Import filter helpers from `$lib/radio/filter-controls`
 *  - Import types from `$lib/types/*`
 */

import type { ServerState, ReceiverState } from '$lib/types/state';
import type { Capabilities, FilterModeConfig } from '$lib/types/capabilities';
import { deriveIfShift, pbtRawToHz } from '$lib/radio/filter-controls';
import { isFieldAvailable, getFieldAvailability } from '$lib/state/field-status';

/* ── Private helpers ─────────────────────────────────────────── */

function activeRx(state: ServerState): ReceiverState {
  return state.active === 'SUB' ? state.sub : state.main;
}

function activeReceiverKey(state: ServerState): 'main' | 'sub' {
  return state.active === 'SUB' ? 'sub' : 'main';
}

function hasCap(caps: Capabilities | null, name: string): boolean {
  return caps?.capabilities?.includes(name) ?? false;
}

function topFieldAvailable(state: ServerState | null, field: string): boolean {
  return isFieldAvailable(state, field);
}

function activeFieldAvailable(state: ServerState | null, field: string): boolean {
  if (!state) return false;
  return isFieldAvailable(state, `${activeReceiverKey(state)}.${field}`);
}

function activeFieldShown(state: ServerState | null, field: string): boolean {
  if (!state) return false;
  return (
    getFieldAvailability(state, `${activeReceiverKey(state)}.${field}`) !== 'missing'
  );
}

/* ── VFO ─────────────────────────────────────────────────────── */

export interface VfoStateProps {
  receiver: 'main' | 'sub';
  freq: number;
  mode: string;
  filter: string;
  sValue: number;
  isActive: boolean;
  badges: Record<string, boolean | string>;
  rit?: { active: boolean; offset: number };
}

export function toVfoProps(
  state: ServerState | null,
  receiver: 'main' | 'sub',
): VfoStateProps {
  if (!state) {
    return {
      receiver,
      freq: 14074000,
      mode: 'USB',
      filter: 'FIL1',
      sValue: 0,
      isActive: receiver === 'main',
      badges: {},
    };
  }

  const rx = state[receiver];
  if (!rx) {
    return {
      receiver,
      freq: 14074000,
      mode: 'USB',
      filter: 'FIL1',
      sValue: 0,
      isActive: receiver === 'main',
      badges: {},
    };
  }
  const isActive = (state.active === 'SUB') === (receiver === 'sub');

  // Always show all possible badges, active state determines if they light up
  const badges: Record<string, boolean | string> = {
    'NB': rx.nb ?? false,
    'NR': rx.nr ?? false,
    'DIGI-SEL': rx.digisel ?? false,
    'IP+': rx.ipplus ?? false,
    'ANF': rx.autoNotch ?? false,
    'NOTCH': rx.manualNotch ?? false,
    'ATT': rx.att > 0,
    'PRE': rx.preamp > 0,
    'RFG': (rx.rfGain ?? 255) < 255,
    'SQL': (rx.squelch ?? 0) > 0,
    'ATU': (state.tunerStatus ?? 0) > 0,
  };

  // Dynamic badges (only show when active)
  if (rx.dataMode) badges['DATA'] = true;
  if (state.split) badges['SPLIT'] = true;
  if ((state.tunerStatus ?? 0) === 2) badges['TUNE'] = true;

  const filters = ['FIL1', 'FIL2', 'FIL3'];
  const fil = rx.filter ?? 1;
  const filterLabel = filters[fil - 1] ?? `FIL${fil}`;

  return {
    receiver,
    freq: rx.freqHz ?? 14074000,
    mode: rx.mode ?? 'USB',
    filter: filterLabel,
    sValue: rx.sMeter ?? 0,
    isActive,
    badges,
    rit: state.ritOn
      ? { active: true, offset: state.ritFreq ?? 0 }
      : undefined,
  };
}

/* ── VFO Ops (split / swap / etc.) ──────────────────────────── */

export interface VfoOpsProps {
  splitActive: boolean;
  txVfo: 'main' | 'sub';
  dualWatch: boolean;
  mainSubTracking: boolean;
}

export function toVfoOpsProps(
  state: ServerState | null,
  _caps: Capabilities | null,
): VfoOpsProps {
  const split = state?.split ?? false;
  const txVfo: 'main' | 'sub' = split ? 'sub' : 'main';

  return {
    splitActive: split,
    txVfo,
    dualWatch: state?.dualWatch ?? false,
    mainSubTracking: state?.mainSubTracking ?? false,
  };
}

/* ── RF Front End ────────────────────────────────────────────── */

export interface PreOption {
  value: number;
  label: string;
}

export interface RfFrontEndProps {
  rfGain: number;
  squelch: number;
  att: number;
  pre: number;
  digiSel: boolean;
  ipPlus: boolean;
  rfGainAvailable: boolean;
  squelchAvailable: boolean;
  attAvailable: boolean;
  preAvailable: boolean;
  digiSelAvailable: boolean;
  ipPlusAvailable: boolean;
  attValues: number[];
  attLabels: Record<string, string>;
  preValues: number[];
  preOptions: PreOption[];
  showRfGain: boolean;
  showSquelch: boolean;
  showAtt: boolean;
  showPre: boolean;
  preDisabled: boolean;
  preDisabledReason: string;
  showDigiSel: boolean;
  showIpPlus: boolean;
}

function formatPreLabel(level: number, labels: Record<string, string>): string {
  const key = String(level);
  if (key in labels) return labels[key];
  return level === 0 ? 'OFF' : `P${level}`;
}

export function toRfFrontEndProps(
  state: ServerState | null,
  caps: Capabilities | null,
): RfFrontEndProps {
  const rx = state ? activeRx(state) : null;
  const attValues = caps?.attValues ?? [0, 6, 12, 18];
  const attLabels = caps?.attLabels ?? {};
  const preValues = caps?.preValues ?? [0, 1, 2];
  const preLabels = caps?.preLabels ?? {};
  const rfGainAvailable = activeFieldShown(state, 'rfGain');
  const squelchAvailable = activeFieldShown(state, 'squelch');
  const attAvailable = activeFieldShown(state, 'att');
  const preAvailable = activeFieldShown(state, 'preamp');
  const digiSelAvailable = activeFieldAvailable(state, 'digisel');
  const ipPlusAvailable = activeFieldAvailable(state, 'ipplus');
  // IC-7610 hardware mutex: PREAMP and DIGI-SEL are mutually exclusive — the radio
  // ignores a PREAMP set while DIGI-SEL is ON. Mirror the radio by disabling the PRE
  // control so it does not light optimistically (MOR-479). Sourced from the profile
  // rule rigs/ic7610.toml [[rules]] kind="disables" when_active="digisel"
  // disables=["preamp"]; targeted here rather than plumbed through capabilities
  // (rules are not yet serialized to the client).
  const preDisabled = rx?.digisel ?? false;
  return {
    rfGain: rx?.rfGain ?? 255,
    squelch: rx?.squelch ?? 0,
    att: rx?.att ?? 0,
    digiSel: rx?.digisel ?? false,
    ipPlus: rx?.ipplus ?? false,
    pre: rx?.preamp ?? 0,
    rfGainAvailable,
    squelchAvailable,
    attAvailable,
    preAvailable,
    digiSelAvailable,
    ipPlusAvailable,
    attValues,
    attLabels,
    preValues,
    preOptions: preValues.map((value) => ({
      value,
      label: formatPreLabel(value, preLabels),
    })),
    showRfGain: hasCap(caps, 'rf_gain') && rfGainAvailable,
    showSquelch: hasCap(caps, 'squelch') && squelchAvailable,
    showAtt: hasCap(caps, 'attenuator') && attAvailable,
    showPre: hasCap(caps, 'preamp') && preAvailable,
    preDisabled,
    preDisabledReason: preDisabled ? 'DIGI-SEL is ON — turn it off to use the preamp' : '',
    showDigiSel: hasCap(caps, 'digisel') && digiSelAvailable,
    showIpPlus: hasCap(caps, 'ip_plus') && ipPlusAvailable,
  };
}

/* ── Filter ──────────────────────────────────────────────────── */

export function resolveFilterModeConfig(
  caps: Capabilities | null,
  mode: string | undefined,
  dataMode: number | undefined,
): FilterModeConfig | null {
  const filterConfig = caps?.filterConfig;
  const normalizedMode = mode?.toUpperCase();
  const candidates: string[] = [];

  if (normalizedMode) {
    if ((dataMode ?? 0) > 0) {
      candidates.push(`${normalizedMode}-D`);
    }
    candidates.push(normalizedMode);
    if (normalizedMode === 'USB' || normalizedMode === 'LSB') {
      if ((dataMode ?? 0) > 0) {
        candidates.push('SSB-D');
      }
      candidates.push('SSB');
    }
    if (normalizedMode === 'CW-R') {
      candidates.push('CW');
    }
    if (normalizedMode === 'RTTY-R') {
      candidates.push('RTTY');
    }
  }

  for (const candidate of candidates) {
    const config = filterConfig?.[candidate];
    if (config) {
      return config;
    }
  }
  return null;
}

export interface FilterProps {
  currentMode: string;
  currentFilter: number;
  filterShape: number;
  filterLabels: string[];
  filterWidth: number;
  filterWidthMin: number;
  filterWidthMax: number;
  filterConfig: FilterModeConfig | null;
  ifShift: number;
  hasPbt: boolean;
  pbtInner: number;
  pbtOuter: number;
}

export function toFilterProps(
  state: ServerState | null,
  caps: Capabilities | null,
): FilterProps {
  const rx = state ? activeRx(state) : null;
  const pbtInner = pbtRawToHz(rx?.pbtInner ?? 128);
  const pbtOuter = pbtRawToHz(rx?.pbtOuter ?? 128);
  const filterConfig = resolveFilterModeConfig(caps, rx?.mode, rx?.dataMode);
  return {
    currentMode: rx?.mode ?? 'USB',
    currentFilter: rx?.filter ?? 1,
    filterShape: rx?.filterShape ?? 0,
    filterLabels: caps?.filters ?? ['FIL1', 'FIL2', 'FIL3'],
    filterWidth: rx?.filterWidth ?? 2400,
    filterWidthMin:
      filterConfig?.minHz ??
      filterConfig?.table?.[0] ??
      caps?.filterWidthMin ??
      50,
    filterWidthMax:
      filterConfig?.maxHz ??
      (filterConfig?.table?.length
        ? filterConfig.table[filterConfig.table.length - 1]
        : undefined) ??
      caps?.filterWidthMax ??
      9999,
    filterConfig,
    ifShift: hasCap(caps, 'if_shift')
      ? (rx?.ifShift ?? 0)
      : deriveIfShift(pbtInner, pbtOuter),
    hasPbt: hasCap(caps, 'pbt'),
    pbtInner,
    pbtOuter,
  };
}

/* ── AGC ─────────────────────────────────────────────────────── */

export interface AgcProps {
  agcMode: number;
  agcModes: number[];
  agcLabels: Record<string, string>;
  hasAgc: boolean;
}

export function toAgcProps(
  state: ServerState | null,
  caps: Capabilities | null,
): AgcProps {
  const rx = state ? activeRx(state) : null;
  return {
    agcMode: rx?.agc ?? 2,
    agcModes: caps?.agcModes ?? [1, 2, 3],
    agcLabels: caps?.agcLabels ?? { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
    hasAgc: hasCap(caps, 'agc') && activeFieldAvailable(state, 'agc'),
  };
}

/* ── RIT / XIT ───────────────────────────────────────────────── */

export interface RitXitProps {
  ritActive: boolean;
  ritOffset: number;
  xitActive: boolean;
  xitOffset: number;
  hasRit: boolean;
  hasXit: boolean;
}

export function toRitXitProps(
  state: ServerState | null,
  caps: Capabilities | null,
): RitXitProps {
  return {
    ritActive: state?.ritOn ?? false,
    ritOffset: state?.ritFreq ?? 0,
    xitActive: state?.ritTx ?? false,
    xitOffset: state?.ritFreq ?? 0,
    hasRit: hasCap(caps, 'rit'),
    hasXit: hasCap(caps, 'xit'),
  };
}

/* ── Mode Panel ──────────────────────────────────────────────── */

export interface ModeProps {
  currentMode: string;
  modes: string[];
  dataMode: number;
  hasDataMode: boolean;
  dataModeCount: number;
  dataModeLabels: Record<string, string>;
}

export function toModeProps(
  state: ServerState | null,
  caps: Capabilities | null,
): ModeProps {
  const rx = state ? activeRx(state) : null;
  return {
    currentMode: rx?.mode ?? 'USB',
    modes: caps?.modes ?? [
      'USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R',
    ],
    dataMode: rx?.dataMode ?? 0,
    hasDataMode: hasCap(caps, 'data_mode'),
    dataModeCount: caps?.dataModeCount ?? 0,
    dataModeLabels: caps?.dataModeLabels ?? { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' },
  };
}

/* ── DSP Panel ───────────────────────────────────────────────── */

export interface DspProps {
  nrMode: number;
  nrLevel: number;
  nbActive: boolean;
  nbLevel: number;
  nbDepth: number;
  nbWidth: number;
  notchMode: 'off' | 'auto' | 'manual';
  notchFreq: number;
  manualNotchWidth: number;
  agcTimeConstant: number;
  hasNr: boolean;
  hasNb: boolean;
  hasNotch: boolean;
  hasAutoNotch: boolean;
  hasAgcTime: boolean;
}

export function toDspProps(
  state: ServerState | null,
  caps: Capabilities | null,
): DspProps {
  const rx = state ? activeRx(state) : null;

  let notchMode: 'off' | 'auto' | 'manual' = 'off';
  if (rx?.autoNotch) notchMode = 'auto';
  else if (rx?.manualNotch) notchMode = 'manual';

  const nbAvailable = activeFieldAvailable(state, 'nb');
  const nrAvailable = activeFieldAvailable(state, 'nr');
  const manualNotchAvailable = activeFieldAvailable(state, 'manualNotch');
  const autoNotchAvailable = activeFieldAvailable(state, 'autoNotch');
  return {
    nrMode: rx?.nr ? 1 : 0,
    nrLevel: rx?.nrLevel ?? 0,
    nbActive: rx?.nb ?? false,
    nbLevel: rx?.nbLevel ?? 0,
    nbDepth: state?.nbDepth ?? 0,
    nbWidth: state?.nbWidth ?? 0,
    notchMode,
    notchFreq: state?.notchFilter ?? 0,
    manualNotchWidth: rx?.manualNotchWidth ?? 0,
    agcTimeConstant: rx?.agcTimeConstant ?? 0,
    hasNr: hasCap(caps, 'nr') && nrAvailable,
    hasNb: hasCap(caps, 'nb') && nbAvailable,
    hasNotch: (hasCap(caps, 'notch') || caps === null) && manualNotchAvailable,
    hasAutoNotch: (hasCap(caps, 'notch') || caps === null) && autoNotchAvailable,
    hasAgcTime: activeFieldAvailable(state, 'agcTimeConstant'),
  };
}

/* ── TX Panel ────────────────────────────────────────────────── */

export interface TxProps {
  txActive: boolean;
  rfPower: number;
  micGain: number;
  atuActive: boolean;
  atuTuning: boolean;
  voxActive: boolean;
  compActive: boolean;
  compLevel: number;
  monActive: boolean;
  monLevel: number;
  driveGain: number;
  hasTx: boolean;
  hasTuner: boolean;
  hasMonitor: boolean;
  txActiveAvailable: boolean;
  rfPowerAvailable: boolean;
  micGainAvailable: boolean;
  atuAvailable: boolean;
  voxAvailable: boolean;
  compAvailable: boolean;
  compLevelAvailable: boolean;
  monAvailable: boolean;
  monLevelAvailable: boolean;
  driveGainAvailable: boolean;
}

export function toTxProps(
  state: ServerState | null,
  caps: Capabilities | null,
): TxProps {
  const txActiveAvailable = topFieldAvailable(state, 'ptt');
  const rfPowerAvailable = topFieldAvailable(state, 'powerLevel');
  const micGainAvailable = topFieldAvailable(state, 'micGain');
  const atuAvailable = topFieldAvailable(state, 'tunerStatus');
  const voxAvailable = topFieldAvailable(state, 'voxOn');
  const compAvailable = topFieldAvailable(state, 'compressorOn');
  const compLevelAvailable = topFieldAvailable(state, 'compressorLevel');
  const monAvailable = topFieldAvailable(state, 'monitorOn');
  const monLevelAvailable = topFieldAvailable(state, 'monitorGain');
  const driveGainAvailable = topFieldAvailable(state, 'driveGain');
  return {
    txActive: state?.ptt ?? false,
    rfPower: state?.powerLevel ?? 128,
    micGain: state?.micGain ?? 128,
    atuActive: (state?.tunerStatus ?? 0) > 0,
    atuTuning: (state?.tunerStatus ?? 0) === 2,
    voxActive: state?.voxOn ?? false,
    compActive: state?.compressorOn ?? false,
    compLevel: state?.compressorLevel ?? 0,
    monActive: state?.monitorOn ?? false,
    monLevel: state?.monitorGain ?? 128,
    driveGain: state?.driveGain ?? 128,
    hasTx: caps?.tx ?? false,
    hasTuner: hasCap(caps, 'tuner') && atuAvailable,
    hasMonitor: hasCap(caps, 'monitor') && monAvailable,
    txActiveAvailable,
    rfPowerAvailable,
    micGainAvailable,
    atuAvailable,
    voxAvailable,
    compAvailable,
    compLevelAvailable,
    monAvailable,
    monLevelAvailable,
    driveGainAvailable,
  };
}

/* ── CW Panel ────────────────────────────────────────────────── */

export interface CwProps {
  cwPitch: number;
  keySpeed: number;
  breakIn: number;
  apfMode: number;
  twinPeak: boolean;
  currentMode: string;
  wpm: number;
  breakInActive: boolean;
  breakInDelay: number;
  sidetonePitch: number;
  sidetoneLevel: number;
  reversePaddle: boolean;
  keyerType: number;
  hasCw: boolean;
  hasBreakIn: boolean;
  hasApf: boolean;
  hasTwinPeak: boolean;
}

export function toCwProps(
  state: ServerState | null,
  caps: Capabilities | null,
): CwProps {
  const rx = state ? activeRx(state) : null;
  const breakInVal = state?.breakIn ?? 0;
  return {
    cwPitch: state?.cwPitch ?? 600,
    keySpeed: state?.keySpeed ?? 12,
    breakIn: breakInVal,
    apfMode: rx?.apfTypeLevel ?? 0,
    twinPeak: rx?.twinPeakFilter ?? false,
    currentMode: rx?.mode ?? 'USB',
    wpm: state?.keySpeed ?? 12,
    breakInActive: breakInVal > 0,
    breakInDelay: state?.breakInDelay ?? 0,
    sidetonePitch: state?.cwPitch ?? 600,
    sidetoneLevel: state?.monitorGain ?? 128,
    reversePaddle: (state?.dashRatio ?? 0) < 0,
    keyerType: 0,
    hasCw: hasCap(caps, 'cw'),
    hasBreakIn: hasCap(caps, 'break_in'),
    hasApf: hasCap(caps, 'apf'),
    hasTwinPeak: hasCap(caps, 'twin_peak'),
  };
}

/* ── Meter Panel ─────────────────────────────────────────────── */

export interface MeterProps {
  sValue: number;
  signal: number;
  rfPower: number;
  swr: number;
  alc: number;
  comp: number;
  vd: number;
  id: number;
  txActive: boolean;
  meterSource: string;
  hasTx: boolean;
}

export function toMeterProps(
  state: ServerState | null,
  caps: Capabilities | null,
): MeterProps {
  const rx = state ? activeRx(state) : null;
  return {
    sValue: rx?.sMeter ?? 0,
    signal: rx?.sMeter ?? 0,
    rfPower: state?.powerMeter ?? 0,
    swr: state?.swrMeter ?? 0,
    alc: state?.alcMeter ?? 0,
    comp: state?.compMeter ?? 0,
    vd: state?.vdMeter ?? 0,
    id: state?.idMeter ?? 0,
    txActive: state?.ptt ?? false,
    meterSource: (state as { meterSource?: string } | null)?.meterSource ?? 'S',
    hasTx: caps?.tx ?? false,
  };
}

/* ── RX Audio Panel ──────────────────────────────────────────── */

export interface RxAudioProps {
  monitorMode: 'local' | 'live' | 'mute';
  afLevel: number;
  /** Radio AF-level control capability; independent from browser live audio. */
  hasAfLevel: boolean;
  hasLiveAudio: boolean;
  /** Audio-WS connection health — used to render a "link lost" indicator. */
  isAudioConnected: boolean;
  /** Capability flag — gates the dual-receiver routing sub-control. */
  hasDualReceiver: boolean;
}

export interface AudioUiState {
  muted: boolean;
  rxEnabled: boolean;
  volume: number;
}

export function toRxAudioProps(
  state: ServerState | null,
  caps: Capabilities | null,
  audioState: AudioUiState,
  audioConnected: boolean,
): RxAudioProps {
  const rx = state ? activeRx(state) : null;
  const hasLiveAudio = hasCap(caps, 'audio');
  const hasAfLevel = hasCap(caps, 'af_level') || hasLiveAudio;
  const monitorMode = audioState.muted
    ? 'mute'
    : audioState.rxEnabled && hasLiveAudio
      ? 'live'
      : 'local';
  const afLevel =
    monitorMode === 'live'
      ? Math.round((audioState.volume / 100) * 255)
      : (rx?.afLevel ?? 128);
  const hasDualReceiver = caps?.capabilities?.includes('dual_rx') ?? false;
  return {
    monitorMode,
    afLevel,
    hasAfLevel,
    hasLiveAudio,
    isAudioConnected: audioConnected,
    hasDualReceiver,
  };
}

/* ── Band Selector ───────────────────────────────────────────── */

export interface BandSelectorProps {
  currentFreq: number;
}

export function toBandSelectorProps(
  state: ServerState | null,
): BandSelectorProps {
  return {
    currentFreq: state ? activeRx(state).freqHz ?? 14074000 : 14074000,
  };
}

/* ── Antenna ────────────────────────────────────────────────── */

export interface AntennaProps {
  txAntenna: number;
  rxAnt: boolean;
  antennaCount: number;
  hasRxAntenna: boolean;
}

export function toAntennaProps(
  state: ServerState | null,
  caps: Capabilities | null,
): AntennaProps {
  const txAntenna = state?.txAntenna ?? 1;
  const rxAnt =
    txAntenna === 2
      ? (state?.rxAntenna2 ?? false)
      : (state?.rxAntenna1 ?? false);

  return {
    txAntenna,
    rxAnt,
    antennaCount: caps?.antennas ?? 1,
    hasRxAntenna: hasCap(caps, 'rx_antenna'),
  };
}

/* ── Scan Panel ──────────────────────────────────────────────── */

export interface ScanProps {
  scanning: boolean;
  scanType: number;
  scanResumeMode: number;
}

export function toScanProps(state: ServerState | null): ScanProps {
  return {
    scanning: state?.scanning ?? false,
    scanType: state?.scanType ?? 0,
    scanResumeMode: (state?.scanResumeMode ?? 0) & 0x0f,
  };
}

/* ── Audio Spectrum Panel ────────────────────────────────────── */

export interface AudioSpectrumProps {
  filterWidth: number;
  filterWidthMax: number;
  pbtInner: number;
  pbtOuter: number;
  manualNotch: boolean;
  notchFreq: number;
  contour: number;
  contourFreq: number;
}

export function toAudioSpectrumProps(
  state: ServerState | null,
  caps: Capabilities | null,
): AudioSpectrumProps {
  const rx = state ? activeRx(state) : null;
  const filterConfig = resolveFilterModeConfig(caps, rx?.mode, rx?.dataMode);
  const filterWidthMax = filterConfig?.table?.length
    ? filterConfig.table[filterConfig.table.length - 1]
    : (filterConfig?.maxHz ?? caps?.filterWidthMax ?? 4000);

  return {
    filterWidth: rx?.filterWidth ?? 2400,
    filterWidthMax,
    pbtInner: rx?.pbtInner ?? 128,
    pbtOuter: rx?.pbtOuter ?? 128,
    manualNotch: rx?.manualNotch ?? false,
    notchFreq: state?.notchFilter ?? 0,
    contour: rx?.contour ?? 0,
    // contourFreq is not yet exposed in ServerState; default to centre.
    contourFreq: 128,
  };
}

/* ── Memory Panel ────────────────────────────────────────────── */

export interface MemoryPanelProps {
  /** Active receiver frequency (Hz) — used by "store VFO → channel". */
  activeFreqHz: number;
  /** Active receiver mode — used by "store VFO → channel". */
  activeMode: string;
}

export function toMemoryPanelProps(state: ServerState | null): MemoryPanelProps {
  const rx = state ? activeRx(state) : null;
  return {
    activeFreqHz: rx?.freqHz ?? 0,
    activeMode: rx?.mode ?? '',
  };
}

/* ── Amber Telemetry Strip ───────────────────────────────────── */

export interface AmberTelemetryProps {
  vdRaw: number | null;
  idRaw: number | null;
}

export function toAmberTelemetryProps(state: ServerState | null): AmberTelemetryProps {
  // No temp field: the IC-7610 exposes no temperature over CI-V and
  // `ServerState` carries none, so the dead TEMP tile was dropped (MOR-483).
  return {
    vdRaw: state?.vdMeter ?? null,
    idRaw: state?.idMeter ?? null,
  };
}

/* ── VFO Control Panel ───────────────────────────────────────── */

export interface VfoControlProps {
  mode: string;
  isCwMode: boolean;
  breakInMode: number;
  hasDualRx: boolean;
  hasSplit: boolean;
  hasRit: boolean;
  hasTuner: boolean;
  hasCw: boolean;
  hasBreakIn: boolean;
}

export function toVfoControlProps(
  state: ServerState | null,
  caps: Capabilities | null,
): VfoControlProps {
  const rx = state ? activeRx(state) : null;
  const mode = rx?.mode ?? '---';
  return {
    mode,
    isCwMode: mode === 'CW' || mode === 'CW-R',
    breakInMode: state?.breakIn ?? 0,
    hasDualRx: hasCap(caps, 'dual_rx'),
    hasSplit: hasCap(caps, 'split'),
    hasRit: hasCap(caps, 'rit'),
    hasTuner: hasCap(caps, 'tuner'),
    hasCw: hasCap(caps, 'cw'),
    hasBreakIn: hasCap(caps, 'break_in'),
  };
}
