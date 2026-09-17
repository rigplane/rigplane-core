/**
 * filter-controls — pure filter-math helpers shared between runtime props
 * and UI wiring layers.
 *
 * No imports from `components-v2/*`. Safe to use in `$lib/runtime/`.
 */

// PBT raw <-> display conversion. The Hz converters live on the measured
// lattice below (MOR-2497); `PbtRange`/`pbtRangeFromCaps` remain as the
// structural gate that decides whether a caps payload declares a usable
// `controls.pbt_inner` entry at all.
import { getControlRange } from '$lib/stores/capabilities.svelte';
import { decodeControlDomain, encodeControlDomain } from '$lib/radio/control-domain';
import { exactDecimalInteger, exactDecimalNumber } from '$lib/types/exact-decimal';
import type {
  Capabilities, ControlDomain, ControlRange as CapabilityControlRange,
  FilterModeConfig, FilterSegmentConfig,
} from '$lib/types/capabilities';

const FILTER_WIDTH_MIN = 50;
const FILTER_WIDTH_MAX = 3600;
const FILTER_WIDTH_STEP = 50;

export type PbtRange = { rawCenter: number; displayMin: number; displayMax: number };

/** Legacy numeric consumers must not inspect discriminated exact domains. */
function isLegacyControlRange(control: unknown): control is CapabilityControlRange {
  return control !== null && typeof control === 'object' && !('mapping' in control);
}

/**
 * Derives a `PbtRange` explicitly from a `Capabilities` object's own
 * `controls.pbt_inner` entry (MOR-1284 F1) — the structural gate saying
 * this caps payload declares a usable PBT range. Callers still holding one
 * (MOR-2497, after the Hz converters moved to the measured lattice):
 * `panel-commands.ts`'s write-path guards, `panel-adapters.ts`'s
 * `pbtStructural` and derived IF-shift availability gate,
 * `radio-view-model-adapter.ts`'s `hasPbtRange`,
 * `scope-passband-display.ts`'s `validScale`, and
 * `toAudioSpectrumProps`'s renderer `pbtRange` prop. Returns `undefined`
 * when the caps object carries no usable range: absent, never fabricated.
 *
 * MOR-1291: "usable" requires each of `raw_center`/`display_min`/
 * `display_max` to be a finite number (rejecting `NaN`/`Infinity`, not just
 * `undefined`), plus `raw_center !== 0` and `display_max !== 0`. A caps
 * payload with a malformed `pbt_inner` entry is treated the same as one
 * with no entry at all: `undefined`, never a fabricated or garbage value.
 */
export function pbtRangeFromCaps(caps: Capabilities | null | undefined): PbtRange | undefined {
  const ctrl = caps?.controls?.pbt_inner;
  if (!isLegacyControlRange(ctrl)) return undefined;
  const { raw_center: rawCenter, display_min: displayMin, display_max: displayMax } = ctrl;
  if (
    typeof rawCenter !== 'number' || !Number.isFinite(rawCenter) || rawCenter === 0
    || typeof displayMin !== 'number' || !Number.isFinite(displayMin)
    || typeof displayMax !== 'number' || !Number.isFinite(displayMax) || displayMax === 0
  ) {
    return undefined;
  }
  return { rawCenter, displayMin, displayMax };
}

// Measured IC PBT passband-edge lattice (MOR-2497).
//
// Every number in this block was measured on the bench on 2026-09-17 by
// sweeping `set_pbt_inner(raw)` over every raw 0..255 and reading the value
// the radio snapped each write back to, on an IC-7610 over LAN, at widths
// 500/250/350/3600 Hz (step 50) and AM 6000 Hz (step 200):
//   positions = 2 * floor(filterWidthHz / (2 * stepHz)) + 1 lattice points,
//   raw(i)    = floor((i + 0.5) * 256 / positions),  i = 0..positions-1,
//   Hz(i)     = (i - (positions - 1)/2) * stepHz.
// The centre position is ALWAYS raw 128 (floor(256/2) with i at the middle
// index), matching the "0128=center" the IC-7610 CI-V Reference Guide
// documents; an earlier width/step + 1 model with a margin in from each end
// reproduced the 3600/1800/500 sweeps but fails the 250/350/AM sweeps the
// test file pins. One edge spans +/-floor(filterWidthHz/(2*stepHz))*stepHz
// -- +/-100 Hz at width 250, NOT +/-125 -- so a width that is an odd
// multiple of the step does NOT reach its own half-width. The 50 Hz step is
// pinned against the radio's own display (front panel read `SFT +900`/`BW
// 1.8` at raw 254, filter 3600 -- a 25 Hz step would have read `SFT +450`).

/** PBT lattice step measured on the IC-7610 (LAN) on 2026-09-16, against the
 *  radio's own front-panel display (MOR-2497). The IC-705 and IC-9700 were
 *  not on the bench, so this value is a parameter of the conversion below,
 *  not a literal inside it — a caller applying it to an unmeasured rig is
 *  making an inference, and the call site is where that inference must be
 *  visible. */
export const PBT_MEASURED_STEP_HZ = 50;

type PbtLattice = Readonly<{
  positions: number;
}>;

/** The lattice the radio snaps PBT writes onto, or `null` when the declared
 *  filter width cannot form one: zero, negative, `NaN`/infinite, not an
 *  integer multiple of `stepHz`, or more positions than the 0..255 wire
 *  range can keep on distinct raws (adjacent positions would collide). A
 *  width equal to one step forms the honest single-position lattice (P = 1):
 *  only the centre is reachable. */
function pbtLattice(filterWidthHz: number, stepHz: number): PbtLattice | null {
  if (!Number.isFinite(filterWidthHz) || !Number.isFinite(stepHz) || stepHz <= 0) return null;
  const steps = filterWidthHz / stepHz;
  if (!Number.isSafeInteger(steps) || steps < 1) return null;
  const positions = 2 * Math.floor(filterWidthHz / (2 * stepHz)) + 1;
  if (positions > 256) return null;
  return { positions };
}

/** Raw wire value of lattice position `i`. */
function latticeRaw(lattice: PbtLattice, i: number): number {
  return Math.floor(((i + 0.5) * 256) / lattice.positions);
}

/** Nearest lattice position to `raw`; an exact tie (a raw sitting halfway
 *  between two reachable points) resolves toward the centre — the radio's own
 *  snap rule at a half-lattice distance was not part of the bench sweep, and
 *  the centre-side reading never invents a larger edge displacement than the
 *  raw must represent. */
function nearestLatticePosition(lattice: PbtLattice, raw: number): number {
  const last = lattice.positions - 1;
  const ideal = ((raw + 0.5) * lattice.positions) / 256 - 0.5;
  const lo = Math.max(0, Math.min(last, Math.floor(ideal)));
  const hi = Math.max(0, Math.min(last, Math.ceil(ideal)));
  const dLo = Math.abs(raw - latticeRaw(lattice, lo));
  const dHi = Math.abs(raw - latticeRaw(lattice, hi));
  if (dLo !== dHi) return dLo < dHi ? lo : hi;
  const centre = last / 2;
  return Math.abs(lo - centre) <= Math.abs(hi - centre) ? lo : hi;
}

/** Hz of a PBT raw value on the measured lattice, or `null` for a degenerate
 *  filter width (`pbtLattice`) or a raw the wire cannot carry (non-finite or
 *  outside 0..255). A raw between lattice points — unreachable in a readback,
 *  since the radio snaps — is snapped per `nearestLatticePosition`. */
export function measuredPbtRawToHz(raw: number, filterWidthHz: number, stepHz: number): number | null {
  const lattice = pbtLattice(filterWidthHz, stepHz);
  if (lattice === null || !Number.isFinite(raw) || raw < 0 || raw > 255) return null;
  const i = nearestLatticePosition(lattice, raw);
  return (i - (lattice.positions - 1) / 2) * stepHz;
}

/** Raw wire value for a PBT offset in Hz on the measured lattice, or `null`
 *  for a degenerate filter width (`pbtLattice`) or a non-finite `hz`. An `hz`
 *  between lattice points snaps to the nearest position — positions are
 *  exactly `stepHz` apart in Hz, so only an exact half-step ties, and the tie
 *  resolves toward the centre for the same reason as in
 *  `nearestLatticePosition`. An `hz` beyond the lattice clamps to the extreme
 *  reachable raw. */
export function measuredPbtHzToRaw(hz: number, filterWidthHz: number, stepHz: number): number | null {
  const lattice = pbtLattice(filterWidthHz, stepHz);
  if (lattice === null || !Number.isFinite(hz)) return null;
  const last = lattice.positions - 1;
  const centre = last / 2;
  const exact = hz / stepHz + centre;
  const lo = Math.floor(exact);
  const hi = Math.ceil(exact);
  const dLo = exact - lo;
  const dHi = hi - exact;
  const i = dLo !== dHi
    ? (dLo < dHi ? lo : hi)
    : (Math.abs(lo - centre) <= Math.abs(hi - centre) ? lo : hi);
  return latticeRaw(lattice, Math.max(0, Math.min(last, i)));
}

/** The slider/display domain of the measured twin-PBT lattice — the ONE
 *  derivation of PBT control bounds (MOR-2497). One edge spans
 *  +/-floor(filterWidthHz/(2*stepHz))*stepHz — the same measured span
 *  `measuredPbtRawToHz`/`measuredPbtHzToRaw` convert on and
 *  `mapIfShiftToPbt` clamps its writes to — with the lattice's own step
 *  spacing. Returns `null` exactly when `pbtLattice` forms no lattice
 *  (zero, negative, non-finite, or a width that is not a whole multiple of
 *  the step): callers in the props/adapter layers treat that as "no domain
 *  key" and keep their existing `hasPbt` gating — never a NaN bound, never
 *  the retired fabricated +/-1200. */
export function measuredPbtDisplayDomain(
  filterWidthHz: number,
  stepHz: number,
): ControlDisplayDomain | null {
  const lattice = pbtLattice(filterWidthHz, stepHz);
  if (lattice === null) return null;
  const span = ((lattice.positions - 1) / 2) * stepHz;
  // span 0 (width == step, P = 1) must give min 0, not -0.
  return { min: span === 0 ? 0 : -span, max: span, step: stepHz, origin: 0 };
}

// Generic control display <-> CI-V wire conversion (MOR-490 / MOR-498)
// Some IC-7610 controls expose a CI-V wire value on a different scale than the
// front-panel / slider display (e.g. NR level wire 0-255 vs display 0-15; NB
// depth wire 0-9 vs display 1-10).  Read the range from capabilities if
// available.  A per-control default remains only where a legacy scale predates
// published control domains (`nr_level`); every other control resolves through
// `resolveControlContract` and has no value at all when the radio publishes
// none.
//
// NOTE: when the wire scale is wider than the display scale the mapping
// quantises wire steps onto fewer physical steps, so individual steps may need
// hardware fine-tuning if the operator sees off-by-one step drift.
type ControlRange = {
  rawMin: number;
  rawMax: number;
  displayMin: number;
  displayMax: number;
};

const CONTROL_DEFAULTS: Record<string, ControlRange> = {
  nr_level: { rawMin: 0, rawMax: 255, displayMin: 0, displayMax: 15 },
};

function controlRange(key: string, fallback: ControlRange): ControlRange {
  try {
    const ctrl = getControlRange(key);
    if (
      ctrl &&
      ctrl.display_min !== undefined &&
      ctrl.display_max !== undefined &&
      ctrl.display_max > ctrl.display_min
    ) {
      return {
        rawMin: ctrl.raw_min,
        rawMax: ctrl.raw_max,
        displayMin: ctrl.display_min,
        displayMax: ctrl.display_max,
      };
    }
  } catch {
    // capabilities store not available (e.g. in tests)
  }
  return fallback;
}

/** Same shape as the internal (unexported) `ControlRange`, exported under its
 *  own name (MOR-1290) so `controlRangeFromCaps`'s return type — and the
 *  optional `range` parameter it feeds `controlRawToDisplay` — can be named
 *  by callers outside this module, the same reason `PbtRange` (MOR-1284) is
 *  exported rather than the conversion functions staying un-parameterisable. */
export type ControlDisplayRange = ControlRange;

/** One resolved raw<->display conversion for a single `controls.<key>` entry
 *  (MOR-2475). `resolveControlContract` yields it from either an exact
 *  `ControlDomain` (decoded/encoded through `control-domain.ts`) or a legacy
 *  band (this module's proportional conversion), so NR level, manual-notch
 *  frequency and NB depth all share one conversion path. */
export type ControlContract = Readonly<{
  rawToDisplay: (raw: number) => number | null;
  displayToRaw: (display: number) => number | null;
  displayDomain: ControlDisplayDomain | null;
  acceptsRaw: (raw: number) => boolean;
  hasControl: boolean;
  receivers: number | null;
}>;

/** The NR-level contract (MOR-1733) is the shared `ControlContract` whose
 *  `hasControl` flag is exposed as `hasNr` — the name `projectNrLevel` and
 *  `panel-commands.ts` gate `adjustable` and command dispatch on. */
export type NrLevelContract = Readonly<Omit<ControlContract, 'hasControl'> & { hasNr: boolean }>;

export type NrLevelDisplayDomain = Readonly<{
  min: number;
  max: number;
  step: number;
  origin: number;
}>;

export type ControlDisplayDomain = NrLevelDisplayDomain;

export type NrLevelProjection = Readonly<{
  value: number | null;
  domain: NrLevelDisplayDomain | null;
  adjustable: boolean;
}>;

/** The `controls` entries served by the shared contract path (MOR-2475). */
export type ControlDomainKey = 'nr_level' | 'manual_notch_freq' | 'nb_depth';

type ControlSpec = Readonly<{
  /** Capability tag whose declaration makes the control adjustable; `null`
   *  for a control gated only on its published `controls` entry. */
  capability: string | null;
  /** Legacy fallback band used when the radio publishes no exact domain;
   *  `null` for a control with no legacy scale to fall back to. */
  defaults: ControlRange | null;
}>;

const CONTROL_SPECS: Readonly<Record<ControlDomainKey, ControlSpec>> = {
  nr_level: { capability: 'nr', defaults: CONTROL_DEFAULTS.nr_level },
  manual_notch_freq: { capability: null, defaults: null },
  nb_depth: { capability: null, defaults: null },
};

const nrLevelContracts = new WeakMap<ControlDisplayRange, NrLevelContract>();
const EMPTY_CONTROL_CONTRACT: ControlContract = {
  rawToDisplay: () => null,
  displayToRaw: () => null,
  displayDomain: null,
  acceptsRaw: () => false,
  hasControl: false,
  receivers: null,
};
function emptyControlContract(receivers: number | null = null): ControlContract {
  return receivers === null ? EMPTY_CONTROL_CONTRACT : { ...EMPTY_CONTROL_CONTRACT, receivers };
}
const CAPS_KEYS = ['capabilities', 'receivers', 'controls'] as const;
const EXACT_CONTROL_KEYS = [
  'mapping', 'raw_step', 'raw_origin', 'display_step', 'display_origin',
  'quantization', 'restoration', 'display_center', 'lookup',
] as const;
const CONTROL_METADATA_KEYS = [
  'raw_min', 'raw_max', 'raw_center', 'display_min', 'display_max',
  'display_unit', 'style', ...EXACT_CONTROL_KEYS,
] as const;

function snapshotControlRecord(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const ownKeys = Reflect.ownKeys(value);
  const snapshot: Record<string, unknown> = {};
  for (const key of keys) {
    const owns = ownKeys.includes(key);
    if (Reflect.has(value, key) !== owns) return null;
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!owns) {
      if (descriptor !== undefined) return null;
    } else {
      if (!descriptor || !('value' in descriptor)) return null;
      const read = Reflect.get(value, key);
      if (!Object.is(read, descriptor.value)) return null;
      snapshot[key] = read;
    }
  }
  return snapshot;
}

function exactControlDomain(control: Record<string, unknown>): ControlDomain | null {
  return EXACT_CONTROL_KEYS.some((key) => Object.hasOwn(control, key))
    ? control as unknown as ControlDomain : null;
}

function legacyControlRange(
  control: unknown, defaults: ControlRange | null,
): ControlDisplayRange | null {
  if (!isLegacyControlRange(control)
    || !Number.isSafeInteger(control.raw_min) || !Number.isSafeInteger(control.raw_max)
    || control.raw_min >= control.raw_max) return null;
  const hasDisplayMin = control.display_min !== undefined;
  const hasDisplayMax = control.display_max !== undefined;
  if (hasDisplayMin !== hasDisplayMax) return null;
  if (!hasDisplayMin) return defaults;
  if (!Number.isFinite(control.display_min) || !Number.isFinite(control.display_max)
    || (control.display_max as number) <= (control.display_min as number)) return null;
  return {
    rawMin: control.raw_min,
    rawMax: control.raw_max,
    displayMin: control.display_min as number,
    displayMax: control.display_max as number,
  };
}

function exactControlContract(
  domain: ControlDomain, hasControl: boolean, receivers: number,
): ControlContract {
  const displayDomain = exactControlDisplayDomain(domain);
  return {
    hasControl, receivers,
    displayDomain,
    acceptsRaw: (raw) => {
      try {
        return displayDomain !== null && decodeControlDomain(domain, raw) !== null;
      } catch {
        return false;
      }
    },
    rawToDisplay: (raw) => {
      try {
        const exact = decodeControlDomain(domain, raw);
        if (exact === null) return null;
        const display = Number(exact);
        return Number.isSafeInteger(display)
          && exactDecimalInteger(display) === exact
          && encodeControlDomain(domain, exact) === raw
          ? display : null;
      } catch {
        return null;
      }
    },
    displayToRaw: (display) => {
      try {
        if (!Number.isSafeInteger(display)) return null;
        const exact = exactDecimalInteger(display);
        const raw = encodeControlDomain(domain, exact);
        return raw !== null && decodeControlDomain(domain, raw) === exact ? raw : null;
      } catch {
        return null;
      }
    },
  };
}

function exactControlDisplayDomain(domain: ControlDomain): NrLevelDisplayDomain | null {
  try {
    const values = [
      domain.display_min,
      domain.display_max,
      domain.display_step,
      domain.display_origin,
    ].map((exact) => {
      const value = Number(exact);
      return Number.isFinite(value) && exactDecimalNumber(value) === exact ? value : null;
    });
    if (values.some((value) => value === null)) return null;
    const probe = decodeControlDomain(domain, domain.raw_origin);
    if (probe === null || encodeControlDomain(domain, probe) !== domain.raw_origin) return null;
    const [min, max, step, origin] = values as number[];
    return { min: min!, max: max!, step: step!, origin: origin! };
  } catch {
    return null;
  }
}

/**
 * Project a `controls.<name>` capabilities entry onto the numeric display
 * domain a slider/keyboard surface needs: `{min, max, step, origin}`
 * (MOR-1682). DATA-DRIVEN: an exact `ControlDomain` contributes its own
 * display lattice, validated by the same decode/encode origin round-trip
 * `exactControlDisplayDomain` applies to NR level; a legacy `ControlRange`
 * with finite `display_min < display_max` contributes those bounds with the
 * entry's own `decode_quantum` as the step when it is a positive integer,
 * otherwise the CALLER's fallback step anchored at `display_min` (the caller
 * knows its own today-behaviour step; this helper never invents one).
 * Anything else — absent entry, legacy entry without usable display bounds,
 * an exact domain that fails the round-trip — returns `null`, and the
 * surface keeps its own explicit fallback. Later MOR-1681/MOR-1680
 * consumers pass their own fallback steps; no per-control constants live
 * here.
 */
export function controlDisplayDomain(
  control: CapabilityControlRange | ControlDomain | null | undefined,
  fallbackStep: number,
): ControlDisplayDomain | null {
  if (control === null || control === undefined) return null;
  if (!isLegacyControlRange(control)) return exactControlDisplayDomain(control);
  const { display_min: min, display_max: max } = control;
  if (typeof min !== 'number' || !Number.isFinite(min)
    || typeof max !== 'number' || !Number.isFinite(max) || max <= min) return null;
  const step = Number.isSafeInteger(control.decode_quantum) && (control.decode_quantum as number) > 0
    ? control.decode_quantum as number
    : fallbackStep;
  return { min, max, step, origin: min };
}

function legacyControlContract(
  key: ControlDomainKey, range: ControlDisplayRange,
  hasControl: boolean, receivers: number | null,
): ControlContract {
  return {
    hasControl, receivers,
    displayDomain: {
      min: range.displayMin,
      max: range.displayMax,
      step: 1,
      origin: range.displayMin,
    },
    acceptsRaw: (raw) =>
      Number.isSafeInteger(raw) && raw >= range.rawMin && raw <= range.rawMax,
    rawToDisplay: (raw) => controlRawToDisplay(key, raw, CONTROL_DEFAULTS[key], range),
    displayToRaw: (display) => controlDisplayToRaw(key, display, CONTROL_DEFAULTS[key], range),
  };
}

/**
 * Resolve the raw<->display conversion for one `controls.<key>` entry from the
 * radio's published capabilities (MOR-2475). An exact `ControlDomain` is
 * decoded and encoded through `control-domain.ts`; a legacy band keeps this
 * module's proportional conversion; an entry the radio does not publish
 * resolves to the empty contract, or to that control's own legacy default
 * where one exists (NR level).
 */
export function resolveControlContract(
  caps: Capabilities | null | undefined,
  key: ControlDomainKey,
): ControlContract {
  const spec = CONTROL_SPECS[key];
  try {
    if (caps === null || caps === undefined) {
      return spec.defaults
        ? legacyControlContract(key, spec.defaults, false, null)
        : emptyControlContract();
    }
    const snapshot = snapshotControlRecord(caps, CAPS_KEYS);
    if (!snapshot || !Array.isArray(snapshot.capabilities)
      || Object.getPrototypeOf(snapshot.capabilities) !== Array.prototype
      || !Number.isSafeInteger(snapshot.receivers) || (snapshot.receivers as number) < 1) {
      return emptyControlContract();
    }
    const capabilities = Array.from(snapshot.capabilities);
    if (!capabilities.every((name) => typeof name === 'string')) return emptyControlContract();
    const declared = spec.capability === null ? false : capabilities.includes(spec.capability);
    const receivers = snapshot.receivers as number;
    if (snapshot.controls === undefined) {
      return spec.defaults
        ? legacyControlContract(key, spec.defaults, declared, receivers)
        : emptyControlContract(receivers);
    }
    const controls = snapshotControlRecord(snapshot.controls, [key]);
    if (!controls) return emptyControlContract();
    if (!Object.hasOwn(controls, key)) {
      return spec.defaults
        ? legacyControlContract(key, spec.defaults, declared, receivers)
        : emptyControlContract(receivers);
    }
    const control = snapshotControlRecord(controls[key], CONTROL_METADATA_KEYS);
    if (!control) return emptyControlContract(receivers);
    const hasControl = spec.capability === null ? true : declared;
    const exact = exactControlDomain(control);
    if (exact) return exactControlContract(exact, hasControl, receivers);
    const legacy = legacyControlRange(control, spec.defaults);
    return legacy
      ? legacyControlContract(key, legacy, hasControl, receivers)
      : emptyControlContract(receivers);
  } catch {
    return emptyControlContract();
  }
}

/** Resolve NR display/readback and command encoding from the shared contract. */
export function resolveNrLevelContract(
  caps: Capabilities | null | undefined,
): NrLevelContract {
  const { hasControl, ...conversion } = resolveControlContract(caps, 'nr_level');
  return { ...conversion, hasNr: hasControl };
}

/** Project NR-level readback without changing the legacy renderer-facing value. */
export function projectNrLevel(
  caps: Capabilities | null | undefined,
  raw: number | null | undefined,
  readable: boolean,
): NrLevelProjection {
  const contract = resolveNrLevelContract(caps);
  const domain = contract.displayDomain;
  if (!readable || raw === null || raw === undefined || !contract.acceptsRaw(raw)) {
    return { value: null, domain, adjustable: false };
  }
  const value = contract.rawToDisplay(raw);
  const valid = domain !== null && value !== null;
  return {
    value: valid ? value : null,
    domain,
    adjustable: valid && contract.hasNr,
  };
}

/**
 * Derives a `ControlDisplayRange` explicitly from a `Capabilities` object's
 * own `controls[key]` entry (MOR-1290, following the `pbtRangeFromCaps`
 * precedent, MOR-1284 F1) — the same shape `controlRange()` reads from the
 * capabilities STORE singleton, but sourced from an argument a caller
 * already has in hand rather than a module-global. Returns `undefined` when
 * the caps object carries no usable range for `key`, so `controlRawToDisplay`
 * falls through to its own store-lookup default.
 */
export function controlRangeFromCaps(
  key: string, caps: Capabilities | null | undefined,
): ControlDisplayRange | undefined {
  const ctrl = caps?.controls?.[key];
  if (!isLegacyControlRange(ctrl)) return undefined;
  if (
    ctrl.display_min !== undefined &&
    ctrl.display_max !== undefined &&
    ctrl.display_max > ctrl.display_min
  ) {
    return {
      rawMin: ctrl.raw_min, rawMax: ctrl.raw_max,
      displayMin: ctrl.display_min, displayMax: ctrl.display_max,
    };
  }
  return undefined;
}

/**
 * `controlRangeFromCaps(key, caps)`, falling back to this module's own
 * `CONTROL_DEFAULTS[key]` when caps carries no range for `key` at all
 * (MOR-1290 F1, verify round 1). `controlRangeFromCaps` alone still has one
 * honest "I don't know" outcome — `undefined` — and a caller that passes
 * that straight into `controlRawToDisplay`'s optional `range` falls through
 * to that function's OWN store lookup, making the result a function of
 * module-global state again for exactly the caps-omits-the-key case. This
 * wrapper closes that residual: every caller gets a CONCRETE range either
 * way, so passing its result as `range` never reaches the store — the
 * conversion becomes a pure function of `(raw, caps)` with no residual
 * dependency. Only defined for keys with a `CONTROL_DEFAULTS` entry
 * (`nr_level` today); throws for any other key so a typo fails
 * loudly rather than silently degrading to `undefined` mid-computation.
 */
export function controlRangeFromCapsOrDefault(
  key: string, caps: Capabilities | null | undefined,
): ControlDisplayRange {
  const fallback = CONTROL_DEFAULTS[key];
  if (!fallback) throw new Error(`controlRangeFromCapsOrDefault: no CONTROL_DEFAULTS entry for '${key}'`);
  if (key === 'nr_level') {
    const contract = resolveNrLevelContract(caps);
    let selected = fallback;
    try {
      selected = controlRangeFromCaps(key, caps) ?? fallback;
    } catch {
      // The resolver already classified trapped metadata as invalid.
    }
    const range = { ...selected };
    nrLevelContracts.set(range, contract);
    return range;
  }
  const range = { ...(controlRangeFromCaps(key, caps) ?? fallback) };
  return range;
}

/**
 * `range`, when supplied (MOR-1290), is used INSTEAD of the capabilities
 * STORE lookup — pass `controlRangeFromCaps(key, caps)` from a caller that
 * already holds a `caps` argument so the conversion is a pure function of
 * that argument rather than a hidden dependency on module-global store
 * state. Every EXISTING call site omits `range` and keeps today's
 * store-lookup behavior unchanged — this parameter is strictly additive. */
function controlRawToDisplay(
  key: string, raw: number, fallback: ControlRange, range?: ControlDisplayRange,
): number {
  const { rawMin, rawMax, displayMin, displayMax } = range ?? controlRange(key, fallback);
  const span = rawMax - rawMin;
  if (span <= 0) return displayMin;
  const display = Math.round(((raw - rawMin) / span) * (displayMax - displayMin) + displayMin);
  return Math.max(displayMin, Math.min(displayMax, display));
}

/** Convert a slider display value to the raw CI-V wire value for `key`. */
function controlDisplayToRaw(
  key: string, display: number, fallback: ControlRange, range?: ControlDisplayRange,
): number {
  const { rawMin, rawMax, displayMin, displayMax } = range ?? controlRange(key, fallback);
  const span = displayMax - displayMin;
  if (span <= 0) return rawMin;
  const raw = Math.round(((display - displayMin) / span) * (rawMax - rawMin) + rawMin);
  return Math.max(rawMin, Math.min(rawMax, raw));
}

/** Convert a raw 0-255 NR wire value to the 0-15 display value. `range`
 *  (MOR-1290) is strictly additive — see `controlRawToDisplay`. */
export function nrRawToDisplay(raw: number): number;
export function nrRawToDisplay(raw: number, range: ControlDisplayRange): number | undefined;
export function nrRawToDisplay(raw: number, range?: ControlDisplayRange): number | undefined {
  const contract = range ? nrLevelContracts.get(range) : undefined;
  if (contract) return contract.rawToDisplay(raw) ?? undefined;
  return controlRawToDisplay('nr_level', raw, CONTROL_DEFAULTS.nr_level, range);
}

export function clampFilterWidth(
  value: number,
  maxHz: number = FILTER_WIDTH_MAX,
  stepHz: number = FILTER_WIDTH_STEP,
): number {
  const clamped = Math.max(FILTER_WIDTH_MIN, Math.min(maxHz, value));
  return Math.round(clamped / stepHz) * stepHz;
}

/**
 * Snap `value` onto the step grid of a single [hzMin, hzMax, stepHz]
 * segment, clamping into range first. Ties (`value` sits exactly halfway
 * between two grid points) resolve to the LOWER point — the same
 * `snapStep` convention `scope-adapter.ts`'s `snapSpectrumFilterWidth`
 * uses (see the cross-reference there). Tie-break must stay aligned with
 * `snapSpectrumFilterWidth` / `snapStep` — the spectrum-panel passband-drag
 * path and this slider/preset path must never disagree about which Hz a
 * midpoint value belongs to.
 */
function snapWithinSegment(value: number, hzMin: number, hzMax: number, stepHz: number): number {
  const bounded = Math.max(hzMin, Math.min(hzMax, value));
  const lower = hzMin + Math.floor((bounded - hzMin) / stepHz) * stepHz;
  const upper = Math.min(hzMax, lower + stepHz);
  return bounded - lower <= upper - bounded ? lower : upper;
}

/**
 * Quantize a raw filter-width value (Hz) to the nearest value the radio's
 * OWN capability-declared width rule actually accepts (MOR-1518). The
 * IC-7300's `USB`/`LSB`/`CW`/`RTTY` rules split into two `segments` with
 * DIFFERENT step sizes either side of 500/600 Hz (50 Hz below, 100 Hz above
 * — `rigs/ic7300.toml`'s `[filters.width.USB]`). A single fixed step
 * produces exactly the illegal mid-drag widths the live bench reported
 * (1050/2150/3150 Hz) once the drag crosses into the coarser upper segment
 * — the backend's `filter_hz_to_index` (`src/rigplane/commands/_codec.py`)
 * then rejects them with "Filter width N is not aligned to N Hz steps",
 * the reported sticky-toast spray.
 *
 * `rule` is meant to be `resolveFilterModeConfig`'s own per-mode output —
 * the SAME resolved config `panel-commands.ts`'s `validResolvedFilterWidth`
 * checks against — so quantization and validation are never out of step.
 *
 * DATA-DRIVEN, NEVER A FABRICATED CEILING: `rule` absent, `fixed`,
 * `table`-shaped, or declaring neither `segments` nor a complete
 * `minHz`/`maxHz`/`stepHz` triple all pass `value` through UNCHANGED — this
 * function never invents a step, and never imposes this module's own
 * `FILTER_WIDTH_MIN`/`MAX`/`STEP` (an IC-7610-shaped default other helpers
 * in this file use, e.g. `clampFilterWidth`) on a radio or mode that
 * declared nothing. A radio that offers a WIDER range than that default
 * (e.g. an FTX-1 table/step mode with `filterWidthMax` above 3600 Hz) must
 * keep reaching every value it already could reach pre-MOR-1518 — silently
 * capping it at a borrowed IC-7610 ceiling would be a worse defect than the
 * unaligned-value bug this function fixes. `fixed`/`table` rules are the
 * same story (e.g. the IC-7300's FM `[filters.width.FM]` is `fixed = true`
 * with `defaults = [15000, 10000, 7000]`, well above 3600 Hz); table-mode
 * widths also already have their own nearest-entry snap
 * (`FilterPanel.svelte`'s `hzToTableIndex`/`tableIndexToHz`), so staying a
 * no-op for them is not a regression either.
 *
 * Segment selection mirrors `snapSpectrumFilterWidth`'s own `'segments'`
 * branch: `value` is snapped against EVERY declared segment's own grid
 * (`snapWithinSegment`, clamping into that segment's own bounds first), and
 * the overall nearest candidate wins — ties preferring the LOWER candidate.
 * This is what correctly handles a value that lands in a GAP BETWEEN two
 * segments (the IC-7300's own 500→600 Hz gap: filter index 9 is 500 Hz,
 * index 10 jumps straight to 600 Hz — there is no legal width in between):
 * each segment's clamped candidate is compared by raw distance, same as any
 * other candidate. Malformed segment entries (non-finite bounds,
 * non-positive step, `hzMin > hzMax`) are dropped before selection; if that
 * leaves nothing usable, this also passes `value` through unchanged.
 */
export function quantizeFilterWidthToRule(
  value: number,
  rule: FilterModeConfig | null | undefined,
): number {
  if (!Number.isFinite(value)) return value;
  if (!rule || rule.fixed || rule.table) return value;

  let rawSegments: FilterSegmentConfig[];
  if (rule.segments && rule.segments.length > 0) {
    rawSegments = rule.segments;
  } else if (
    typeof rule.stepHz === 'number' && typeof rule.minHz === 'number' && typeof rule.maxHz === 'number'
  ) {
    rawSegments = [{ hzMin: rule.minHz, hzMax: rule.maxHz, stepHz: rule.stepHz, indexMin: 0 }];
  } else {
    return value; // no usable width rule declared — never synthesize one
  }

  const segments = rawSegments.filter((segment) =>
    Number.isFinite(segment.hzMin) && Number.isFinite(segment.hzMax)
    && Number.isFinite(segment.stepHz) && segment.stepHz > 0
    && segment.hzMin <= segment.hzMax);
  if (segments.length === 0) return value;

  return segments
    .map((segment) => snapWithinSegment(value, segment.hzMin, segment.hzMax, segment.stepHz))
    .reduce((best, candidate) =>
      Math.abs(value - candidate) < Math.abs(value - best)
        || (Math.abs(value - candidate) === Math.abs(value - best) && candidate < best)
        ? candidate
        : best);
}

/** IF shift derived from the two passband edges: their mean.
 *
 *  MOR-2497 step 2 removed a `clampToBipolarRange` here, which bounded the
 *  result to +/-1200 Hz. That bound truncated a state the radio actually
 *  reaches: measured on the IC-7610 over LAN on 2026-09-17 at a 3600 Hz
 *  filter, writing raw 254 to BOTH edges reads both back at 254, which is
 *  +1800 Hz each on the measured lattice, so the true shift is +1800 Hz and
 *  the clamp reported 1200. Raw 1 on both edges gives -1800 Hz the same way.
 *
 *  What bounds the result instead. Every display-side caller -- in
 *  `panel-adapters.ts`, `radio-view-model-adapter.ts`,
 *  `scope-passband-display.ts` and `panel-props.ts` -- converts both edges
 *  with `measuredPbtRawToHz` at ONE width and step, and one edge cannot
 *  leave +/-floor(width/(2*step))*step Hz there, so their mean cannot
 *  either (MOR-2497). Callers with no lattice convert nothing and never
 *  reach this helper.
 */
export function deriveIfShift(pbtInner: number, pbtOuter: number): number {
  return Math.round((pbtInner + pbtOuter) / 2);
}

/** Map an IF-shift request (Hz) onto the two twin-PBT passband edges (Hz),
 *  preserving the passband width (MOR-2500).
 *
 *  All three PBT arguments are Hz on the measured lattice, as
 *  `measuredPbtRawToHz` reads them at the CURRENT filter width; the caller
 *  converts the result back to raws with `measuredPbtHzToRaw` at the same
 *  width and step. The lattice reaches +/-floor(filterWidthHz/(2*stepHz))*
 *  stepHz -- +/-100 Hz at width 250, NOT +/-125 (the 2026-09-17 sweep) --
 *  so the bound is that MEASURED span, never filterWidthHz/2: a requested
 *  shift that would push an edge past the reachable span stops at the
 *  boundary instead, and never clamps one edge alone, which would silently
 *  narrow the passband. A requested shift between two lattice shifts snaps
 *  to the nearest whole `stepHz` multiple first, so both written edges stay
 *  exactly on the lattice and the width survives the move whole. Degenerate
 *  width/step inputs are the caller's refusal: `measuredPbtRawToHz` returns
 *  null for them before this helper is reached.
 *
 *  Pinned in `filter-controls.test.ts` ("keeps the passband width on the
 *  write path") and exercised end to end through `onIfShiftChange` in
 *  `panel-commands.intent.isolated.test.ts` (MOR-2500 describe). */
export function mapIfShiftToPbt(
  targetIfShiftHz: number,
  currentPbtInnerHz: number,
  currentPbtOuterHz: number,
  filterWidthHz: number,
  stepHz: number,
): { pbtInner: number; pbtOuter: number } {
  const halfSpan = Math.floor(filterWidthHz / (2 * stepHz)) * stepHz;
  const currentIfShift = deriveIfShift(currentPbtInnerHz, currentPbtOuterHz);
  const requested = Math.round((targetIfShiftHz - currentIfShift) / stepHz) * stepHz;
  const delta = Math.max(
    Math.max(-halfSpan - currentPbtInnerHz, -halfSpan - currentPbtOuterHz),
    Math.min(halfSpan - currentPbtInnerHz, halfSpan - currentPbtOuterHz, requested),
  );

  return {
    pbtInner: currentPbtInnerHz + delta,
    pbtOuter: currentPbtOuterHz + delta,
  };
}
