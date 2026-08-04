/**
 * Radio semantic view-model contract (MOR-1062).
 *
 * The seam between adapters (which consume runtime state and capabilities —
 * see `lib/runtime/adapters/*`) and semantic UI (which renders only this
 * shape and nothing else — no transport, store, or manufacturer knowledge).
 * A design language may change how a fact looks; it may never change which
 * facts exist. See docs/plans/2026-07-25-ui-composition-architecture-v3.md
 * and the MOR-977 shared semantic skeleton (Linear comment, 2026-08-03).
 *
 * `ActiveRx` and the scheme-conditioned slot/target shapes below mirror the
 * exact identity types frozen by the MOR-988 "Accepted capability and
 * presentation semantics" decision (§3.2, §4) — this contract reuses that
 * vocabulary rather than reinventing it. Every "unknown"/"denied" branch is
 * intentional and must survive round-tripping — collapsing it into a
 * boolean or a default is the failure mode this contract exists to prevent.
 */
import type { VfoScheme } from '$lib/types/capabilities';
import type { FrequencyPermit } from '$lib/utils/tx-permit';
import { invalid, record, exactKeys, str } from './validator-primitives';

export type ReceiverId = 'MAIN' | 'SUB';
export type VfoSlotId = 'A' | 'B';

/**
 * Whether a VFO/target position has an addressable A/B slot at all, distinct
 * from whether that slot was actually observed. `unslotted` = the scheme has
 * no A/B concept here (`single`, `ab_shared`); `unknown` = a slotted scheme
 * (`ab`, `main_sub`) whose slot could not be observed — MOR-988 §3.2/§4:
 * missing/stale never synthesizes `A`.
 */
export type VfoSlot =
  | { kind: 'slotted'; id: VfoSlotId }
  | { kind: 'unslotted' }
  | { kind: 'unknown' };

/** MOR-988 §3.2 `ActiveRx`, verbatim: an adapter with no observation must never fabricate 'MAIN'. */
export type ActiveRx =
  | { status: 'known'; receiver: ReceiverId }
  | { status: 'unknown' };

/** A boolean radio fact (`split`, `dualWatch`) that can itself be unobserved. */
export type BooleanFact =
  | { status: 'known'; value: boolean }
  | { status: 'unknown' };

/**
 * Structural = the radio model supports this. Operational = usable right
 * now, given live capability AND field-observed state. MOR-977 two-level
 * gating: a control that fails the STRUCTURAL half is absent (nothing to
 * render); a control that fails only the OPERATIONAL half stays present but
 * disabled, degrading to an explicit unknown/disabled state rather than a
 * guessed default — MOR-988 §11.3: "Old v1 servers lacking additive fields
 * degrade to `unknown`/disabled in v3, not guessed behavior."
 */
export interface Availability {
  structural: boolean;
  operational: boolean;
}

export interface VfoViewModel {
  receiver: ReceiverId;
  slot: VfoSlot;
  label: string;
  frequencyHz: number | null;
  mode: string | null;
  filter: string | null;
  isActive: boolean;
  isTxTarget: boolean;
}

export type TxTargetViewModel =
  | { status: 'known'; receiver: ReceiverId; slot: VfoSlot; frequencyHz: number | null }
  | { status: 'unknown'; reason: 'not-observed' | 'stale' | 'unsupported' | 'contradiction' };

export interface ScopeAvailabilityViewModel {
  hardwareScope: Availability;
  audioFftScope: Availability;
}

export type DisabledReasonCode =
  | 'capability-unavailable'
  | 'field-not-observed'
  | 'tx-target-unknown'
  | 'out-of-band';

export interface DisabledReason {
  field: string;
  code: DisabledReasonCode;
}

export interface RadioViewModel {
  topologyId: string;
  vfoScheme: VfoScheme;
  activeReceiver: ActiveRx;
  vfos: readonly VfoViewModel[];
  /** Orthogonal wire booleans (state.ts `split`/`dualWatch`; independent CI-V
   *  commands) — both may be true, false, or unobserved independently. */
  split: BooleanFact;
  dualWatch: BooleanFact;
  txTarget: TxTargetViewModel;
  txPermit: FrequencyPermit;
  scope: ScopeAvailabilityViewModel;
  disabledReasons: readonly DisabledReason[];
}

const RECEIVER_IDS: readonly ReceiverId[] = ['MAIN', 'SUB'];
const SLOT_IDS: readonly VfoSlotId[] = ['A', 'B'];
const VFO_SCHEMES: readonly VfoScheme[] = ['single', 'ab', 'ab_shared', 'main_sub'];
const DISABLED_REASON_CODES: readonly DisabledReasonCode[] = [
  'capability-unavailable', 'field-not-observed', 'tx-target-unknown', 'out-of-band',
];

function oneOf<T>(value: unknown, allowed: readonly T[], path: string): T {
  if (!allowed.includes(value as T)) invalid(path, allowed.join(' | '));
  return value as T;
}
function bool(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') invalid(path, 'a boolean');
  return value;
}
function nullableNumber(value: unknown, path: string): number | null {
  if (value !== null && (typeof value !== 'number' || !Number.isFinite(value))) {
    invalid(path, 'a finite number or null');
  }
  return value as number | null;
}
function nullableString(value: unknown, path: string): string | null {
  if (value !== null && typeof value !== 'string') invalid(path, 'a string or null');
  return value as string | null;
}

/**
 * Declares a fact group as optional (MOR-1264, decision (b) of the MOR-1262
 * decomposition's slice 0): present ⇒ validated strictly by `validate` (the
 * group runs its own `exactKeys`/shape checks, unchanged); absent (`value`
 * is `undefined`) ⇒ the field stays `undefined` on the returned model and
 * the family is structurally unavailable — the MOR-988 §11.3 degrade-to-
 * unknown doctrine applied at the group level, not a new relaxation.
 *
 * A group is optional *only* because its key is listed in the containing
 * `exactKeys(...)` allow-list alongside the required keys — `exactKeys`
 * itself is untouched, so a key absent from that list is still rejected as
 * an extra. This is what keeps "optional" bounded rather than "anything
 * goes". A future slice declares and reads its group with one line each,
 * e.g. (MOR-1244, `txAux`) — `validateTxAux` built from the same
 * `record`/`exactKeys`/`str` imported from `./validator-primitives`:
 *   exactKeys(v, [...requiredKeys, 'txAux'], '$');
 *   txAux: optionalGroup(v.txAux, '$.txAux', validateTxAux),
 */
export function optionalGroup<T>(
  value: unknown,
  path: string,
  validate: (value: unknown, path: string) => T,
): T | undefined {
  return value === undefined ? undefined : validate(value, path);
}

function validateVfoSlot(value: unknown, path: string): VfoSlot {
  const v = record(value, path);
  if (v.kind === 'slotted') {
    exactKeys(v, ['kind', 'id'], path);
    return { kind: 'slotted', id: oneOf(v.id, SLOT_IDS, `${path}.id`) };
  }
  if (v.kind === 'unslotted') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unslotted' };
  }
  if (v.kind === 'unknown') {
    exactKeys(v, ['kind'], path);
    return { kind: 'unknown' };
  }
  invalid(`${path}.kind`, `'slotted' | 'unslotted' | 'unknown'`);
}
function slotEqual(a: VfoSlot, b: VfoSlot): boolean {
  return a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id : a.kind === b.kind;
}

function validateActiveRx(value: unknown, path: string): ActiveRx {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver'], path);
    return { status: 'known', receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateBooleanFact(value: unknown, path: string): BooleanFact {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'value'], path);
    return { status: 'known', value: bool(v.value, `${path}.value`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status'], path);
    return { status: 'unknown' };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateAvailability(value: unknown, path: string): Availability {
  const v = record(value, path);
  exactKeys(v, ['structural', 'operational'], path);
  return { structural: bool(v.structural, `${path}.structural`), operational: bool(v.operational, `${path}.operational`) };
}

function validateVfo(value: unknown, path: string): VfoViewModel {
  const v = record(value, path);
  exactKeys(v, ['receiver', 'slot', 'label', 'frequencyHz', 'mode', 'filter', 'isActive', 'isTxTarget'], path);
  return {
    receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
    slot: validateVfoSlot(v.slot, `${path}.slot`),
    label: str(v.label, `${path}.label`),
    frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    mode: nullableString(v.mode, `${path}.mode`),
    filter: nullableString(v.filter, `${path}.filter`),
    isActive: bool(v.isActive, `${path}.isActive`),
    isTxTarget: bool(v.isTxTarget, `${path}.isTxTarget`),
  };
}

function validateTxTarget(value: unknown, path: string): TxTargetViewModel {
  const v = record(value, path);
  if (v.status === 'known') {
    exactKeys(v, ['status', 'receiver', 'slot', 'frequencyHz'], path);
    return {
      status: 'known',
      receiver: oneOf(v.receiver, RECEIVER_IDS, `${path}.receiver`),
      slot: validateVfoSlot(v.slot, `${path}.slot`),
      frequencyHz: nullableNumber(v.frequencyHz, `${path}.frequencyHz`),
    };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['not-observed', 'stale', 'unsupported', 'contradiction'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'known' | 'unknown'`);
}

function validateTxPermit(value: unknown, path: string): FrequencyPermit {
  const v = record(value, path);
  if (v.status === 'allowed') {
    exactKeys(v, ['status', 'band'], path);
    return { status: 'allowed', band: nullableString(v.band, `${path}.band`) };
  }
  if (v.status === 'denied') {
    exactKeys(v, ['status', 'reason'], path);
    return { status: 'denied', reason: oneOf(v.reason, ['outside-configured-ranges'] as const, `${path}.reason`) };
  }
  if (v.status === 'unknown') {
    exactKeys(v, ['status', 'reason'], path);
    return {
      status: 'unknown',
      reason: oneOf(v.reason, ['ranges-unconfigured', 'tx-target-unknown'] as const, `${path}.reason`),
    };
  }
  invalid(`${path}.status`, `'allowed' | 'denied' | 'unknown'`);
}

function validateDisabledReason(value: unknown, path: string): DisabledReason {
  const v = record(value, path);
  exactKeys(v, ['field', 'code'], path);
  return { field: str(v.field, `${path}.field`), code: oneOf(v.code, DISABLED_REASON_CODES, `${path}.code`) };
}

/** Runtime validator (repo idiom: throws TypeError with a `$.path`, see `validateCapabilities`).
 *  Also enforces two cross-field invariants (review cycle 1, V1): `txPermit`
 *  cannot be 'allowed' while `txTarget` is unknown (no fail-open), and
 *  `isTxTarget` can be true only on the VFO a known `txTarget` names. */
export function validateRadioViewModel(value: unknown): RadioViewModel {
  const v = record(value, '$');
  exactKeys(v, [
    'topologyId', 'vfoScheme', 'activeReceiver', 'vfos', 'split', 'dualWatch',
    'txTarget', 'txPermit', 'scope', 'disabledReasons',
  ], '$');
  if (!Array.isArray(v.vfos)) invalid('$.vfos', 'an array');
  if (!Array.isArray(v.disabledReasons)) invalid('$.disabledReasons', 'an array');
  const scope = record(v.scope, '$.scope');
  exactKeys(scope, ['hardwareScope', 'audioFftScope'], '$.scope');

  const vfos = v.vfos.map((vfo, i) => validateVfo(vfo, `$.vfos[${i}]`));
  const txTarget = validateTxTarget(v.txTarget, '$.txTarget');
  const txPermit = validateTxPermit(v.txPermit, '$.txPermit');

  if (txPermit.status === 'allowed' && txTarget.status === 'unknown') {
    invalid('$.txPermit', "'allowed' only when txTarget is known (fail-open otherwise)");
  }
  vfos.forEach((vfo, i) => {
    const matches = txTarget.status === 'known'
      && vfo.receiver === txTarget.receiver && slotEqual(vfo.slot, txTarget.slot);
    if (vfo.isTxTarget && !matches) {
      invalid(`$.vfos[${i}].isTxTarget`, 'true only on the VFO matching a known txTarget');
    }
  });

  return {
    topologyId: str(v.topologyId, '$.topologyId'),
    vfoScheme: oneOf(v.vfoScheme, VFO_SCHEMES, '$.vfoScheme'),
    activeReceiver: validateActiveRx(v.activeReceiver, '$.activeReceiver'),
    vfos,
    split: validateBooleanFact(v.split, '$.split'),
    dualWatch: validateBooleanFact(v.dualWatch, '$.dualWatch'),
    txTarget,
    txPermit,
    scope: {
      hardwareScope: validateAvailability(scope.hardwareScope, '$.scope.hardwareScope'),
      audioFftScope: validateAvailability(scope.audioFftScope, '$.scope.audioFftScope'),
    },
    disabledReasons: v.disabledReasons.map((r, i) => validateDisabledReason(r, `$.disabledReasons[${i}]`)),
  };
}
