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
import {
  deriveIfShift,
  nbDepthRawToDisplay,
  nrRawToDisplay,
  pbtRawToHz,
} from '$lib/radio/filter-controls';
import { isFieldAvailable, getFieldAvailability } from '$lib/state/field-status';
import { modInputStateKey } from '$lib/radio/mod-input';

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

function fieldObserved(state: ServerState | null, field: string): boolean {
  const status = state?.fieldStatus?.[field];
  return status?.observed === true
    && status.freshness === 'fresh'
    && status.availability === 'available';
}

/**
 * Whether a single-RX A/B surface must remain in Selected/Unselected mode.
 *
 * Current servers declare the provider readback contract explicitly. For an
 * older compatible capabilities payload, absence of any observed absolute A
 * and B slot facts is the conservative signal that literal A/B identity is
 * not available. State values alone are deliberately insufficient because
 * legacy receiver defaults can contain fabricated-looking slot objects.
 */
export function relativeVfoIdentityUnknown(
  state: ServerState | null,
  caps: Capabilities | null,
  receiverKey: 'main' | 'sub' = 'main',
): boolean {
  if (!state || !caps || caps.receivers !== 1 || caps.vfoScheme !== 'ab') return false;
  if (fieldObserved(state, `${receiverKey}.activeSlot`)) return false;
  if (caps.vfoReadback === 'selected_unselected') return true;
  if (caps.vfoReadback !== undefined) return false;

  const hasObservedAbsoluteSlots = (['vfoA', 'vfoB'] as const).every((slotKey) =>
    (['freqHz', 'mode', 'filterNum', 'dataMode'] as const).some((leaf) =>
      fieldObserved(state, `${receiverKey}.${slotKey}.${leaf}`),
    ),
  );
  return !hasObservedAbsoluteSlots;
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
      // MOR-1409 A11: no fabricated 14.074 MHz / USB / FIL1 stand-ins — an
      // unobserved VFO stays unknown. `freq`/`mode`/`filter` keep their
      // `number`/`string` contract, so the sentinel is a value that can
      // never be mistaken for a real reading (`NaN` never equals a real
      // frequency; `'---'` never equals a real mode/filter label — see
      // `toVfoControlProps`'s pre-existing use of the same convention).
      freq: Number.NaN,
      mode: '---',
      filter: '---',
      sValue: 0,
      isActive: receiver === 'main',
      badges: {},
    };
  }

  const rx = state[receiver];
  if (!rx) {
    return {
      receiver,
      freq: Number.NaN,
      mode: '---',
      filter: '---',
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
    'RFG': (rx.rfGain ?? 1) < 1,
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
    freq: rx.freqHz ?? Number.NaN,
    mode: rx.mode ?? '---',
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
    rfGain: rx?.rfGain ?? 1.0,
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
  hasFilterShape: boolean;
  filterLabels: string[];
  filterWidth: number;
  filterWidthMin: number;
  filterWidthMax: number;
  filterConfig: FilterModeConfig | null;
  ifShift: number;
  hasIfShift: boolean;
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
    // MOR-1409 A11: no fabricated USB / three-filter FIL1-FIL3 catalog
    // stand-in. `filterLabels` is a capability-derived choice set (like
    // `toAgcProps`'s `agcModes`) — unknown capabilities means an empty, not
    // invented, catalog.
    //
    // MOR-1409 A12 (adjudication 5245697359, Core #2317): `filterWidth` no
    // longer fabricates a 2400 Hz stand-in. A11 deferred this fix — a NaN
    // sentinel renders as the literal "NaNkHz" in FilterPanel.svelte's BW
    // readout (:207) and settings modal (:299) — a formatted-display
    // consumer, not a comparison consumer like `findActiveBand`. A12 is
    // granted FilterPanel.svelte as a fourth production file specifically
    // to add the consumer-boundary guard (`formatWidthDisplay`'s
    // `Number.isFinite` check), so the fabricated default can now be
    // removed here. See its `toAudioSpectrumProps` twin below.
    currentMode: rx?.mode ?? '---',
    currentFilter: rx?.filter ?? 1,
    filterShape: rx?.filterShape ?? 0,
    // MOR-1503: whether the radio has a REAL filter_shape command of its
    // own (Icom family, e.g. IC-7300). The FTX-1 declares no
    // `filter_shape` capability, so FilterPanel.svelte uses THIS flag to
    // decide whether to show the SHARP/SOFT shape buttons — a
    // capability-absent radio gets the section hidden instead of dead
    // buttons commanding a control the radio does not have (same class
    // as MOR-1494's IF-shift row).
    hasFilterShape: hasCap(caps, 'filter_shape'),
    filterLabels: caps?.filters ?? [],
    filterWidth: rx?.filterWidth ?? Number.NaN,
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
    // MOR-1494: whether the radio has a REAL if_shift command of its own.
    // Icom radios (PBT only, e.g. IC-7300) declare no `if_shift` capability
    // at all — `ifShift` above still computes a PBT-derived display value
    // for consumers that want it, but FilterPanel.svelte uses THIS flag to
    // decide whether to show the IF-shift control, so a capability-absent
    // radio gets the row hidden instead of a permanently-disabled control
    // with a synthetic reading (PBT Inner/Outer are the real controls there).
    hasIfShift: hasCap(caps, 'if_shift'),
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
  // MOR-1409 A11: an unobserved AGC field must not read back the MID (2)
  // default as if it had been confirmed — `agcMode` is gated on the same
  // field-availability check `hasAgc` already used to gate visibility, so
  // the two can no longer disagree about whether this value is real.
  const agcAvailable = activeFieldAvailable(state, 'agc');
  return {
    agcMode: agcAvailable ? (rx?.agc ?? Number.NaN) : Number.NaN,
    agcModes: caps?.agcModes ?? [],
    agcLabels: caps?.agcLabels ?? { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
    hasAgc: hasCap(caps, 'agc') && agcAvailable,
  };
}

/* ── RIT / XIT ───────────────────────────────────────────────── */

export interface RitXitProps {
  // MOR-1409 A12: no fabricated "off" reading for an unobserved RIT/XIT
  // state — `ritOn`/`ritTx` are real device state (like `mode`/`freqHz`),
  // not capability-availability flags. `ritActive`/`xitActive` keep their
  // `boolean` (not `boolean | null`) contract: `RitXitPanel.svelte`'s
  // `HardwareButton active={…}` prop is typed `boolean | undefined`, so
  // widening to `boolean | null` here breaks that (non-A12-owned)
  // consumer's compile — a fifth production file A12 is not granted. Both
  // fields stay gated on `hasRit`/`hasXit` (`RitXitPanel.svelte` never
  // renders a body for a cold/unsupported receiver regardless of this
  // field's raw value — plan §5), and `false` is the conservative/off
  // reading, the same non-fabrication class as `toCwProps`' internal
  // `mode ?? 'USB'` gate literal (plan §7 LOW item) — never a
  // plausible-looking *on* reading no one confirmed. `ritOffset`/
  // `xitOffset` still fix to the standard `NaN` sentinel.
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
    ritOffset: state?.ritFreq ?? Number.NaN,
    xitActive: state?.ritTx ?? false,
    xitOffset: state?.ritFreq ?? Number.NaN,
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
  /** Active DATA group's MOD-input source (IC-7610 enum, MOR-616); null until read. */
  modInputSource: number | null;
  /** Show the MOD-input control: data_mode cap + the active group has been observed. */
  hasModInput: boolean;
}

export function toModeProps(
  state: ServerState | null,
  caps: Capabilities | null,
): ModeProps {
  const rx = state ? activeRx(state) : null;
  // MOR-616: surface the MOD-input source of the active receiver's DATA
  // group (data_mode 0→DATA OFF, 1→D1, 2→D2, 3→D3). The control is hidden
  // until the backend has actually read the group (fieldStatus !== missing),
  // so radios with a data_mode capability but no MOD-input routing (e.g.
  // IC-7300) never render a dead dropdown.
  const modInputKey = modInputStateKey(rx?.dataMode ?? 0);
  return {
    // MOR-1409 A11: no fabricated USB stand-in for an unobserved mode.
    currentMode: rx?.mode ?? '---',
    // MOR-1409 A12 (expanded mandate, adjudication 5245697359, Core #2317):
    // no fabricated 10-mode invented catalog. `modes` is a
    // capability-derived choice set — same convention as `toAgcProps`'
    // `agcModes`/`toFilterProps`' `filterLabels` — unknown capabilities
    // means an empty, not invented, catalog.
    modes: caps?.modes ?? [],
    dataMode: rx?.dataMode ?? 0,
    hasDataMode: hasCap(caps, 'data_mode'),
    dataModeCount: caps?.dataModeCount ?? 0,
    dataModeLabels: caps?.dataModeLabels ?? { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' },
    modInputSource: state?.[modInputKey] ?? null,
    hasModInput:
      hasCap(caps, 'data_mode') &&
      state !== null &&
      getFieldAvailability(state, modInputKey) !== 'missing',
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
  hasNbDepth: boolean;
  hasNbWidth: boolean;
  /** Slider/scale ceiling for NB level: 255 (IC-7610) or 10 (FTX-1 native). */
  nbLevelMax: number;
  /** Render NB level as a percent (IC-7610 0-255) vs. raw integer (FTX-1 0-10). */
  nbLevelPercent: boolean;
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
  // MOR-502: NB depth/width exist only on rigs that expose an nb_depth control
  // range (IC-7610). FTX-1 (native 0-10 NB) and X6200 (nb_level only) must not
  // render phantom depth/width controls.
  const nbDepthRange = caps?.controls?.nb_depth ?? null;
  const hasNbDepth = nbDepthRange !== null;
  // The NB-level scale follows the nb_level control range: a 0-255 range
  // (IC-7610) renders as a percent; its absence means the native 0-10 raw
  // scale (FTX-1) and the label shows the raw integer.
  const nbLevelRange = caps?.controls?.nb_level ?? null;
  const nbLevelPercent = nbLevelRange !== null;
  const nbLevelMax = nbLevelRange?.raw_max ?? 10;
  return {
    nrMode: rx?.nr ? 1 : 0,
    // MOR-490: store holds the raw 0-255 wire value; the slider is 0-15.
    nrLevel: nrRawToDisplay(rx?.nrLevel ?? 0),
    nbActive: rx?.nb ?? false,
    nbLevel: rx?.nbLevel ?? 0,
    // MOR-498: store holds the 0-9 wire value; the slider is 1-10.
    nbDepth: nbDepthRawToDisplay(state?.nbDepth ?? 0),
    nbWidth: state?.nbWidth ?? 0,
    notchMode,
    notchFreq: state?.notchFilter ?? 0,
    manualNotchWidth: rx?.manualNotchWidth ?? 0,
    agcTimeConstant: rx?.agcTimeConstant ?? 0,
    hasNr: hasCap(caps, 'nr') && nrAvailable,
    hasNb: hasCap(caps, 'nb') && nbAvailable,
    hasNbDepth,
    hasNbWidth: hasNbDepth,
    nbLevelMax,
    nbLevelPercent,
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
    rfPower: state?.powerLevel ?? 0.5,
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
  // `twinPeak` keeps its `boolean` (not `boolean | null`) contract — see
  // `toRitXitProps`' header comment: `CwPanel.svelte`'s `HardwareButton
  // active={…}` prop is typed `boolean | undefined`, so widening breaks a
  // non-A12-owned consumer's compile. `false` is the conservative "off"
  // reading; `CwPanel.svelte` is gated on `hasCw` regardless.
  twinPeak: boolean;
  currentMode: string;
  apfDisabled: boolean;
  tpfDisabled: boolean;
  wpm: number;
  breakInActive: boolean;
  breakInDelay: number;
  sidetonePitch: number;
  sidetoneLevel: number;
  reversePaddle: boolean;
  // MOR-1409 A12: `keyerType` removed entirely (was `keyerType: 0`,
  // hardcoded, not even `??`-guarded). No `ServerState` field backs it and
  // no production `.svelte` consumer reads `CwProps.keyerType` — dead
  // output, deleted rather than sentineled (plan §3.3/§5).
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
  const mode = rx?.mode ?? 'USB';
  // Mode-gated CW filters (MOR-492): APF (Audio Peak Filter) is only meaningful
  // in CW/CW-R; TPF (Twin Peak Filter) only in RTTY/RTTY-R. Disable the control
  // outside its mode so it greys out and no-ops — mirrors the MOR-479 preamp
  // mutex. Includes the -R reverse variants in both predicates.
  const apfDisabled = !(mode === 'CW' || mode === 'CW-R');
  const tpfDisabled = !(mode === 'RTTY' || mode === 'RTTY-R');
  return {
    // MOR-1409 A12: no fabricated 600 Hz pitch / 12 wpm keying speed / 128
    // sidetone-level stand-ins for an unobserved CW receiver.
    cwPitch: state?.cwPitch ?? Number.NaN,
    keySpeed: state?.keySpeed ?? Number.NaN,
    breakIn: breakInVal,
    apfMode: rx?.apfTypeLevel ?? 0,
    twinPeak: rx?.twinPeakFilter ?? false,
    currentMode: mode,
    apfDisabled,
    tpfDisabled,
    wpm: state?.keySpeed ?? Number.NaN,
    breakInActive: breakInVal > 0,
    breakInDelay: state?.breakInDelay ?? 0,
    sidetonePitch: state?.cwPitch ?? Number.NaN,
    sidetoneLevel: state?.monitorGain ?? Number.NaN,
    reversePaddle: (state?.dashRatio ?? 0) < 0,
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
  hasTx: boolean;
}

export function toMeterProps(
  state: ServerState | null,
  caps: Capabilities | null,
): MeterProps {
  const rx = state ? activeRx(state) : null;
  // MOR-1409 A12: no fabricated zero-meter reading for an unobserved
  // receiver — a real S0/zero-power/zero-SWR reading is indistinguishable
  // from "never read" without this fix. No production `.svelte` file was
  // found to call `toMeterProps` repo-wide (`MetersDockPanel.svelte`, the
  // live desktop meter component, reads raw `radioState` fields directly,
  // bypassing this function entirely — plan §5) — zero golden/display risk
  // either way; this is a direct honesty fix at the projection layer.
  return {
    sValue: rx?.sMeter ?? Number.NaN,
    signal: rx?.sMeter ?? Number.NaN,
    rfPower: state?.powerMeter ?? Number.NaN,
    swr: state?.swrMeter ?? Number.NaN,
    alc: state?.alcMeter ?? Number.NaN,
    comp: state?.compMeter ?? Number.NaN,
    vd: state?.vdMeter ?? Number.NaN,
    id: state?.idMeter ?? Number.NaN,
    txActive: state?.ptt ?? false,
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
  // MOR-1409 A12: no fabricated 0.5 normalized AF-level stand-in for an
  // unobserved receiver in local mode. `RxAudioPanel.svelte` (this field's
  // only production consumer) is gated on `hasAfLevel || hasLiveAudio`.
  const afLevel =
    monitorMode === 'live'
      ? audioState.volume / 100
      : (rx?.afLevel ?? Number.NaN);
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
  // MOR-1409 A11: `BandSelector.svelte` (unowned by any gate in the
  // program — see the A11 re-anchor plan §4) feeds this straight into
  // `findActiveBand(currentFreq, freqRanges)` to highlight a HAM band tab.
  // Never a fixed 20-meter-band frequency stand-in, where the old default
  // would resolve to a real "20m" tab for an operator no one has
  // identified — `currentFreq` keeps its `number` contract (no fourth
  // production file touched to widen it), but `NaN` cannot satisfy
  // `freq >= band.start && freq <= band.end` for any real band.
  return {
    currentFreq: state ? activeRx(state).freqHz ?? Number.NaN : Number.NaN,
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
    // MOR-1409 A11: no fabricated single-antenna default — `antennaCount`
    // drives a button-generation loop in the (unowned-by-A11) antenna
    // panel, so `0` (never render an antenna button) is the honest "we
    // don't know how many antenna ports this radio has" value, exactly as
    // an empty capability-derived choice set is for `toAgcProps`/
    // `toFilterProps`.
    antennaCount: caps?.antennas ?? 0,
    hasRxAntenna: hasCap(caps, 'rx_antenna'),
  };
}

/* ── Scan Panel ──────────────────────────────────────────────── */

export interface ScanProps {
  // `scanning` keeps its `boolean` (not `boolean | null`) contract — see
  // `toRitXitProps`' header comment: `ScanPanel.svelte`'s `HardwareButton
  // active={…}` prop is typed `boolean | undefined`, so widening breaks a
  // non-A12-owned consumer's compile. `false` is the conservative "not
  // scanning" reading. `scanType`/`scanResumeMode` still fix to `NaN` —
  // pure comparison consumers (button `active` matching against a fixed
  // value list), golden-safe (plan §5).
  scanning: boolean;
  scanType: number;
  scanResumeMode: number;
}

export function toScanProps(state: ServerState | null): ScanProps {
  return {
    scanning: state?.scanning ?? false,
    scanType: state?.scanType ?? Number.NaN,
    scanResumeMode:
      state?.scanResumeMode === undefined || state?.scanResumeMode === null
        ? Number.NaN
        : state.scanResumeMode & 0x0f,
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
    // MOR-1409 A12: twin of `toFilterProps.filterWidth` above — same fix,
    // same rationale, guarded at the same FilterPanel.svelte consumer
    // boundary. The `AudioSpectrumPanel`/`AudioSpectrumCanvas` consumer
    // path is a numeric/animation consumer (comparison-safe), not
    // string-formatted.
    filterWidth: rx?.filterWidth ?? Number.NaN,
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
  /** False only during a relative Selected/Unselected bootstrap epoch. */
  vfoIdentityKnown: boolean;
}

export function toMemoryPanelProps(
  state: ServerState | null,
  caps: Capabilities | null = null,
): MemoryPanelProps {
  const rx = state ? activeRx(state) : null;
  const receiverKey = state?.active === 'SUB' ? 'sub' : 'main';
  // MOR-1409 A12: no fabricated 0 Hz / empty-string stand-ins for an
  // unobserved active receiver. Same `NaN`/`'---'` non-fabricating-sentinel
  // convention `toVfoProps`/`toFilterProps` already use for the same
  // field shapes — `MemoryPanel.svelte`'s "store VFO → channel" action only
  // reads these on an explicit user click, never during initial render.
  return {
    activeFreqHz: rx?.freqHz ?? Number.NaN,
    activeMode: rx?.mode ?? '---',
    vfoIdentityKnown: !relativeVfoIdentityUnknown(state, caps, receiverKey),
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
