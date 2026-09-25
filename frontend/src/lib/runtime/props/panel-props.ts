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
import type { Capabilities, ControlDomain, FilterModeConfig } from '$lib/types/capabilities';
import {
  controlDisplayDomain,
  deriveIfShift,
  measuredPbtDisplayDomain,
  measuredPbtRawToHz,
  nrRawToDisplay,
  pbtRangeFromCaps,
  projectNrLevel,
  resolveControlContract,
} from '$lib/radio/filter-controls';
import type { NrLevelProjection, ControlDisplayDomain, PbtRange } from '$lib/radio/filter-controls';
import { decodeControlDomain, encodeControlDomain } from '$lib/radio/control-domain';
import { isFieldAvailable, isFieldRead } from '$lib/state/field-status';
import { modInputStateKey } from '$lib/radio/mod-input';

/* ── Private helpers ─────────────────────────────────────────── */

function activeRx(state: ServerState): ReceiverState {
  return state.active === 'SUB' ? state.sub : state.main;
}

function activeReceiverKey(state: ServerState): 'main' | 'sub' {
  return state.active === 'SUB' ? 'sub' : 'main';
}

function hasCap(caps: Capabilities | null, name: string): boolean {
  try {
    return caps?.capabilities?.includes(name) ?? false;
  } catch {
    return false;
  }
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
  return isFieldRead(state, `${activeReceiverKey(state)}.${field}`);
}

function fieldObserved(state: ServerState | null, field: string): boolean {
  const status = state?.fieldStatus?.[field];
  return status?.observed === true
    && (status.freshness === 'fresh' || status.freshness === 'stale')
    && (status.availability === 'available' || status.availability === 'stale');
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
    'ATT': (rx.att ?? 0) > 0,
    'PRE': (rx.preamp ?? 0) > 0,
    // Owner ruling 2026-09-23 (MOR-2546, extends #3591): RFG lights only
    // while RF gain is REDUCED, judged on the displayed percentage exactly
    // like the VFO deck's `levelFormatsBelowMax` (semantic/format-level.ts):
    // a raw 254/255 rounds to 100% and counts as maximum. This runtime-props
    // file must not import semantic/ (eslint lib/runtime isolation), so the
    // single-rounding comparison is inlined over the same snapshot-normalized
    // 0..1 fraction. `true` keeps the lit 'RFG' lamp; `''` empties it —
    // VfoPanel keeps the fixed-width lamp slot and hides an empty lamp from
    // assistive tech.
    'RFG': rx.rfGain != null && Math.round(rx.rfGain * 100) < 100 ? true : '',
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

// MOR-1529: exported so `AmberCockpit`/`AmberScope` (amber-lcd skin) can
// resolve their read-only preamp status token from the same profile-declared
// `[preamp.labels]` data this file already uses for `toRfFrontEndProps`'
// `preOptions`, instead of duplicating (or worse, re-hardcoding) the
// fallback logic.
export function formatPreLabel(level: number, labels: Record<string, string>): string {
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
  // MOR-1409 A11: an unknown/unobserved preamp capability must not present a
  // fabricated 3-level [0, 1, 2] IC-7300-shaped catalog — an X6200 profile
  // (declared [0, 1], no P.AMP2 stage) would otherwise get an invented
  // option it can't honor. Mirrors `toAgcProps`'s `agcModes: caps?.agcModes
  // ?? []` (MOR-1523 R1: this was the last un-converted twin of that fix).
  const preValues = caps?.preValues ?? [];
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

/** Whether the CURRENT mode has twin PBT, given the radio's published filter
 *  table. Twin PBT belongs to the mode, not only to the radio: the IC-7300
 *  Advanced Manual p.40 heads its Twin PBT section "SSB, CW, RTTY and AM
 *  modes", and the profile says which by declaring `pbtStepHz` per mode.
 *
 *  Two absences have to be told apart, and conflating them is the whole reason
 *  this is a function rather than one `!== undefined`:
 *
 *  - a payload where SOME mode declares a step and this one does not — the
 *    radio has no twin PBT here, so the controls go away;
 *  - a payload where NO mode declares one — an older server that does not
 *    publish the field at all. Reading that as "no PBT anywhere" would take the
 *    controls away from a radio that has them, so the radio-wide capability
 *    decides, exactly as it did before the field existed.
 *
 *  The second case is not hypothetical: the captured IC-7300 capabilities
 *  fixture in `adapters/__tests__/fixtures/` predates the field.
 *
 *  Exported for `adapters/radio-view-model-adapter.ts`, which gates the
 *  view-model's PBT structure on the same distinction — one definition, shared
 *  (MOR-2497). */
export function modeHasTwinPbt(
  caps: Capabilities | null,
  modeConfig: FilterModeConfig | null,
): boolean {
  if (modeConfig?.pbtStepHz !== undefined) return true;
  const published = Object.values(caps?.filterConfig ?? {});
  const anyModeDeclares = published.some((entry) => entry?.pbtStepHz !== undefined);
  return !anyModeDeclares;
}

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
  /**
   * The IF-shift control's display domain from the profile's
   * `controls.if_shift` entry (MOR-1681), or null when the radio publishes
   * nothing usable — `FilterPanel.svelte` keeps its own per-branch
   * today-behaviour constants for that case.
   */
  ifShiftDomain: ControlDisplayDomain | null;
  hasIfShift: boolean;
  hasPbt: boolean;
  /** PBT edge readings in Hz on the measured lattice, or `null` when none
   *  converts — no step for the mode (FM), an unobserved filter width, or a
   *  legacy payload that publishes no `pbtStepHz`. Never a value fabricated
   *  off the retired fixed +/-1200 scale (MOR-2497). */
  pbtInner: number | null;
  pbtOuter: number | null;
  /** The measured twin-PBT slider domain at the observed width and the
   *  mode's step (`measuredPbtDisplayDomain`, MOR-2497), or `null` when no
   *  lattice forms — `FilterPanel.svelte` then keeps its own explicit
   *  today-behaviour constants, the same fallback contract `ifShiftDomain`
   *  established (MOR-1681). */
  pbtDomain: ControlDisplayDomain | null;
}

export function toFilterProps(
  state: ServerState | null,
  caps: Capabilities | null,
): FilterProps {
  const rx = state ? activeRx(state) : null;
  // MOR-2513: an unobserved mode/dataMode (null) resolves no config.
  const filterConfig = resolveFilterModeConfig(caps, rx?.mode ?? undefined, rx?.dataMode ?? undefined);
  // MOR-2497: PBT reads in Hz on the measured lattice — the mode's declared
  // step and the OBSERVED filter width — through the one derivation both
  // control surfaces share (`measuredPbtDisplayDomain`). No lattice (no step
  // for this mode, an unobserved width, a legacy payload) means no reading
  // and no domain, never the retired fabricated +/-1200 conversion.
  const pbtStepHz = filterConfig?.pbtStepHz;
  const pbtWidthHz = rx?.filterWidth;
  const pbtDomain = pbtStepHz !== undefined && typeof pbtWidthHz === 'number'
    ? measuredPbtDisplayDomain(pbtWidthHz, pbtStepHz)
    : null;
  const pbtHz = (raw: number | undefined): number | null => (
    raw === undefined || pbtStepHz === undefined || typeof pbtWidthHz !== 'number'
      ? null
      : measuredPbtRawToHz(raw, pbtWidthHz, pbtStepHz)
  );
  const pbtInner = pbtHz(rx?.pbtInner ?? undefined);
  const pbtOuter = pbtHz(rx?.pbtOuter ?? undefined);
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
      : (pbtInner !== null && pbtOuter !== null ? deriveIfShift(pbtInner, pbtOuter) : 0),
    // MOR-1681: the IF-shift range/step come from the profile's published
    // `controls.if_shift` entry when usable; the legacy fallback step is
    // the family's today UI step (25 Hz, the semantic row and non-table
    // panel rows). Null keeps FilterPanel on its own constants.
    ifShiftDomain: controlDisplayDomain(caps?.controls?.if_shift, 25),
    // MOR-1494: whether the radio has a REAL if_shift command of its own.
    // Icom radios (PBT only, e.g. IC-7300) declare no `if_shift` capability
    // at all — `ifShift` above still computes a PBT-derived display value
    // for consumers that want it, but FilterPanel.svelte uses THIS flag to
    // decide whether to show the IF-shift control, so a capability-absent
    // radio gets the row hidden instead of a permanently-disabled control
    // with a synthetic reading (PBT Inner/Outer are the real controls there).
    hasIfShift: hasCap(caps, 'if_shift'),
    // MOR-2497: twin PBT is a property of the MODE, not only of the radio.
    // The IC-7300 Advanced Manual p.40 heads its Twin PBT section "SSB, CW,
    // RTTY and AM modes", and the profile says so per mode by declaring a
    // `pbtStepHz` for the modes that have it and none for those that do not.
    // Gating on the radio-wide capability alone left the PBT controls live in
    // FM, where the radio has no passband tuning at all, so the panel offered
    // two controls that command nothing.
    hasPbt: hasCap(caps, 'pbt') && modeHasTwinPbt(caps, filterConfig),
    pbtInner,
    pbtOuter,
    pbtDomain,
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
    // MOR-1547: no fabricated IC-7610-shaped FAST/MID/SLOW dict when the
    // profile declares no agcLabels — `{}` lets `buildAgcOptions`
    // (agc-utils.ts) fall back to the honest raw mode number per-entry,
    // the same "no invented label" contract it already applies whenever a
    // caps-provided `agcLabels` is missing an individual entry.
    agcLabels: caps?.agcLabels ?? {},
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
  //
  // MOR-1574: A12 above fixed the fabricated *default* but left NO
  // fieldStatus gate at all — a radio that declares the `rit`/`xit`
  // capability but has never actually reported `ritOn`/`ritFreq`/`ritTx`
  // (the live ic7300 fixture's exact shape) still passed `hasRit`/`hasXit`
  // on capability presence alone, so the conservative-`false` reading above
  // rendered as a CONFIRMED "RIT OFF" instead of an honest "unknown".
  // `hasRit`/`hasXit` now gate on field availability too, mirroring
  // `toAgcProps`' `hasAgc: hasCap(caps, 'agc') && agcAvailable` exactly —
  // `ritActive`/`xitActive` stay the same plain-boolean raw values (still
  // protected by the now-honest visibility gate, same as `digiSel`/
  // `ipPlus` in `toRfFrontEndProps`), and `ritOffset`/`xitOffset` fix to
  // `NaN` whenever their backing field is not available, same idiom as
  // `agcMode`.
  ritActive: boolean;
  ritOffset: number;
  xitActive: boolean;
  xitOffset: number;
  hasRit: boolean;
  hasXit: boolean;
  /** Exact RIT control contract, when supplied by a normalized capability payload. */
  ritDomain: ControlDomain | null | undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validatedRitDomain(caps: Capabilities | null): ControlDomain | null | undefined {
  try {
    const controls = caps?.controls;
    if (controls === undefined) return undefined;
    if (!isRecord(controls)) return null;
    if (!Object.hasOwn(controls, 'rit')) return undefined;
    const candidate = controls.rit;
    if (!isRecord(candidate)) return null;
    const domain = candidate as ControlDomain;
    if (!['identity', 'linear', 'centered', 'lookup'].includes(domain.mapping)
      || !Number.isSafeInteger(domain.raw_origin)) return null;
    const display = decodeControlDomain(domain, domain.raw_origin);
    return display !== null && encodeControlDomain(domain, display) === domain.raw_origin ? domain : null;
  } catch {
    return null;
  }
}

export function toRitXitProps(
  state: ServerState | null,
  caps: Capabilities | null,
): RitXitProps {
  const ritOnAvailable = topFieldAvailable(state, 'ritOn');
  const ritFreqAvailable = topFieldAvailable(state, 'ritFreq');
  const ritTxAvailable = topFieldAvailable(state, 'ritTx');
  const props = {
    ritActive: state?.ritOn ?? false,
    ritOffset: ritFreqAvailable ? (state?.ritFreq ?? Number.NaN) : Number.NaN,
    xitActive: state?.ritTx ?? false,
    xitOffset: ritFreqAvailable ? (state?.ritFreq ?? Number.NaN) : Number.NaN,
    hasRit: hasCap(caps, 'rit') && ritOnAvailable,
    hasXit: hasCap(caps, 'xit') && ritTxAvailable,
  };
  Object.defineProperty(props, 'ritDomain', {
    value: validatedRitDomain(caps),
    enumerable: false,
  });
  return props as RitXitProps;
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
  /** Exact profile-declared MOD-input choices; empty when capability metadata is absent. */
  modInputChoices: readonly { readonly value: number; readonly label: string }[];
  /** Show the MOD-input control: data_mode cap + the active group has been observed. */
  hasModInput: boolean;
}

export function toModeProps(
  state: ServerState | null,
  caps: Capabilities | null,
): ModeProps {
  const rx = state ? activeRx(state) : null;
  // MOR-616: surface the MOD-input source of the active receiver's DATA
  // group (data_mode 0→DATA OFF, 1→D1, 2→D2, 3→D3). The control uses
  // `isFieldRead` as its availability compatibility gate. The helper rejects
  // the three explicit absence values while preserving the legacy no-entry
  // fallback; it does not establish observation evidence.
  const dataMode = rx?.dataMode;
  const validDataGroup = Number.isSafeInteger(dataMode) && (dataMode as number) >= 0
    && (dataMode as number) <= (caps?.dataModeCount ?? -1);
  const modInputKey = validDataGroup ? modInputStateKey(dataMode as number) : null;
  const modInputChoices = caps?.dataModeInputs ?? [];
  const modInputSource = modInputKey === null ? null : state?.[modInputKey] ?? null;
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
    modInputSource: modInputChoices.some(option => option.value === modInputSource)
      ? modInputSource : null,
    modInputChoices,
    hasModInput:
      hasCap(caps, 'data_mode') && modInputChoices.length > 0 && modInputKey !== null
      && state !== null && isFieldRead(state, modInputKey),
  };
}

/* ── DSP Panel ───────────────────────────────────────────────── */

/**
 * Manual-notch readback (MOR-2475): the same source selection the
 * view-model adapter applies, shared by `toDspProps`,
 * `toAudioSpectrumProps`, and the DSP feedback adapter
 * (`panel-adapters.ts: getDspControlFeedback`), which must echo the same
 * field the value side reads. Where the radio publishes a
 * `manual_notch_freq` domain and the field's status is usable, the
 * reading is display units decoded from `manualNotchFreq`; otherwise the
 * raw `notchFilter` code, unchanged. A raw position the domain rejects
 * reads as 0 — these props shapes have no unknown state (`?? 0`
 * throughout). The domain is reported whenever the radio publishes a
 * usable one, independent of which source produced `notchFreq`; callers
 * emit it as an absent key when null. `source` names the field that
 * produced `notchFreq`.
 */
export function manualNotchReading(
  state: ServerState | null,
  caps: Capabilities | null,
): {
  notchFreq: number;
  notchFreqDomain: ControlDisplayDomain | null;
  source: 'manualNotchFreq' | 'notchFilter';
} {
  const rx = state ? activeRx(state) : null;
  const contract = resolveControlContract(caps, 'manual_notch_freq');
  const domain = contract.displayDomain;
  const raw = rx?.manualNotchFreq;
  const prefer = domain !== null
    && activeFieldAvailable(state, 'manualNotchFreq')
    && typeof raw === 'number';
  return {
    notchFreq: prefer ? contract.rawToDisplay(raw as number) ?? 0 : rx?.notchFilter ?? 0,
    notchFreqDomain: domain,
    source: prefer ? 'manualNotchFreq' : 'notchFilter',
  };
}

export interface DspProps {
  nrMode: number;
  nrLevel: number;
  nrLevelProjection: NrLevelProjection;
  nbActive: boolean;
  nbLevel: number;
  nbDepth: number;
  nbWidth: number;
  notchMode: 'off' | 'auto' | 'manual';
  notchFreq: number;
  /** The manual-notch control's published display domain
   *  (`controls.manual_notch_freq` through `resolveControlContract`), present
   *  only when the radio declares a usable one — consumers keep their own
   *  constants when the key is absent, mirroring the view-model adapter's
   *  `dsp.notchFreqDomain`. */
  notchFreqDomain?: ControlDisplayDomain;
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
  const nrLevelAvailable = activeFieldAvailable(state, 'nrLevel');
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
  const { notchFreq, notchFreqDomain } = manualNotchReading(state, caps);
  // MOR-498: the store holds the wire value (IC-7610 raw 0-9, display 1-10);
  // the conversion comes from the published `controls.nb_depth` domain
  // through the shared contract. A radio publishing none (the `hasNbDepth`
  // gate below already hides the control) resolves to the empty contract and
  // gets no fabricated 1-10 reading.
  const nbDepthContract = resolveControlContract(caps, 'nb_depth');
  return {
    nrMode: rx?.nr ? 1 : 0,
    // MOR-490: store holds the raw 0-255 wire value; the slider is 0-15.
    nrLevel: nrRawToDisplay(rx?.nrLevel ?? 0),
    nrLevelProjection: projectNrLevel(caps, rx?.nrLevel, nrLevelAvailable),
    nbActive: rx?.nb ?? false,
    nbLevel: rx?.nbLevel ?? 0,
    nbDepth: nbDepthContract.rawToDisplay(state?.nbDepth ?? 0) ?? 0,
    nbWidth: state?.nbWidth ?? 0,
    notchMode,
    // notchFilter (MOR-1548): reclassified receiver-scoped.
    notchFreq,
    ...(notchFreqDomain !== null ? { notchFreqDomain } : {}),
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
  autoTuneAvailable: boolean;
  /**
   * The pitch control's display domain from the profile's `controls.cw_pitch`
   * entry (MOR-1682), or null when the radio publishes nothing usable —
   * `CwPanel.svelte` keeps its own 300/900/5 constants for that case.
   */
  cwPitchDomain: ControlDisplayDomain | null;
  /**
   * The key-speed control's display domain from the profile's
   * `controls.key_speed` entry (MOR-2475 F1), derived exactly like
   * `cwPitchDomain` above it; null when the radio publishes nothing
   * usable — `CwPanel.svelte` keeps its own 6/48/1 constants for that case.
   */
  keySpeedDomain: ControlDisplayDomain | null;
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
    autoTuneAvailable: hasCap(caps, 'cw')
      && hasCap(caps, 'audio')
      && caps?.audioFftAvailable === true,
    cwPitchDomain: controlDisplayDomain(caps?.controls?.cw_pitch, 5),
    keySpeedDomain: controlDisplayDomain(caps?.controls?.key_speed, 1),
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
  // from "never read" without this fix.
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
  /** Dual-receiver audio ROUTING is its own capability
   *  (`lan_dual_rx_audio_routing`, IC-7610 only today) — `dual_rx` alone
   *  (e.g. the FTX-1) must not mount a routing control whose values the
   *  server will never accept or report (MOR-2527). */
  hasAudioRouting: boolean;
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
  // MOR-2583: monitor MUTE lives on the server and survives a page reload,
  // while the client-side `muted` flag does not — so the server's word wins.
  // A payload without `monitorMute` (older server) keeps today's behaviour.
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
  const hasAudioRouting = hasCap(caps, 'lan_dual_rx_audio_routing');
  return {
    monitorMode,
    afLevel,
    hasAfLevel,
    hasLiveAudio,
    isAudioConnected: audioConnected,
    hasDualReceiver,
    hasAudioRouting,
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
  /** Native IF-shift in Hz when the active profile exposes that control.
   *  Non-finite means the structural fact has not been observed. */
  ifShift: number;
  pbtInner: number;
  pbtOuter: number;
  /** The radio's published PBT raw↔Hz range (`controls.pbt_inner` through
   *  `pbtRangeFromCaps`, MOR-1284 F1), present only when the radio declares
   *  a usable one — absent, not null, otherwise. The renderer converts
   *  `pbtInner`/`pbtOuter` through this range only; without one it draws no
   *  PBT overlay rather than fall back to the capabilities store. */
  pbtRange?: PbtRange;
  /** The current mode's twin-PBT lattice step (`filterConfig[mode].pbtStepHz`,
   *  #3519), present only when the resolved mode config declares one — absent
   *  means the mode has no twin PBT (FM) or the payload predates the field,
   *  and the renderer then has no honest raw→Hz conversion rather than
   *  assuming 50 Hz. */
  pbtStepHz?: number;
  manualNotch: boolean;
  notchFreq: number;
  /** The manual-notch control's published display domain, present only
   *  when the radio declares a usable one — same source selection as
   *  `DspProps.notchFreqDomain` (one shared helper). Absent, not null,
   *  when the radio publishes nothing usable. */
  notchFreqDomain?: ControlDisplayDomain;
  contour: number;
  contourFreq: number;
}

export function toAudioSpectrumProps(
  state: ServerState | null,
  caps: Capabilities | null,
): AudioSpectrumProps {
  const rx = state ? activeRx(state) : null;
  // MOR-2513: null mode/dataMode (unobserved) resolves no config.
  const filterConfig = resolveFilterModeConfig(caps, rx?.mode ?? undefined, rx?.dataMode ?? undefined);
  const filterWidthMax = filterConfig?.table?.length
    ? filterConfig.table[filterConfig.table.length - 1]
    : (filterConfig?.maxHz ?? caps?.filterWidthMax ?? 4000);
  const { notchFreq, notchFreqDomain } = manualNotchReading(state, caps);
  const pbtRange = pbtRangeFromCaps(caps);

  return {
    // MOR-1409 A12: twin of `toFilterProps.filterWidth` above — same fix,
    // same rationale, guarded at the same FilterPanel.svelte consumer
    // boundary. The `AudioSpectrumPanel`/`AudioSpectrumCanvas` consumer
    // path is a numeric/animation consumer (comparison-safe), not
    // string-formatted.
    filterWidth: rx?.filterWidth ?? Number.NaN,
    filterWidthMax,
    ifShift: hasCap(caps, 'if_shift') ? (rx?.ifShift ?? Number.NaN) : 0,
    pbtInner: rx?.pbtInner ?? 128,
    pbtOuter: rx?.pbtOuter ?? 128,
    ...(pbtRange !== undefined ? { pbtRange } : {}),
    ...(filterConfig?.pbtStepHz !== undefined ? { pbtStepHz: filterConfig.pbtStepHz } : {}),
    manualNotch: rx?.manualNotch ?? false,
    // notchFilter (MOR-1548): reclassified receiver-scoped.
    notchFreq,
    ...(notchFreqDomain !== null ? { notchFreqDomain } : {}),
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
