/**
 * RadioViewModel → RendererViewModel projection (MOR-1243).
 *
 * The seam this module owns (named in contract.ts's doc comment): the
 * semantic-UI contract `RadioViewModel` (MOR-1062 — `{status:'unknown'}`
 * unions everywhere a radio fact might be unobserved) projects onto the
 * design-language renderer contract `RendererViewModel` (MOR-1072 — flat
 * primitives only). Two hard rules from the ticket:
 *
 *   1. Unknownness is PRESERVED, never collapsed. Every unknown/not-observed
 *      branch (`activeReceiver`, a VFO/txTarget slot, `split`, `dualWatch`,
 *      `txTarget`, `txPermit`) surfaces as the literal string `'unknown'` —
 *      a value no known branch of any of those fields can ever produce (no
 *      receiver, slot id, or permit status is spelled "unknown") — instead
 *      of a fabricated look-alike default (e.g. `'MAIN'`).
 *
 *   2. Capability objects and module paths cannot pass through. This
 *      function never spreads an input object into `fields`; every value
 *      written there is named explicitly and passed through `primitive()`,
 *      which throws `ProjectionError` rather than emit a non-primitive. So
 *      (a) an extra key on a malformed/future `RadioViewModel` that this
 *      function never reads is dropped by construction, and (b) a
 *      capability-shaped value smuggled into a slot this contract expects
 *      to be a flat primitive (e.g. a VFO's `mode`) is rejected at the
 *      source rather than forwarded for `isRendererViewModel` to catch.
 *      That gate (contract.ts) is the structural backstop; this function is
 *      the mechanism.
 *
 * Pure: only the two contracts' TYPES are imported (nested shapes are
 * derived via indexed-access types, not separate imports) — no runtime
 * dependency on either contract module, or on anything else. Same input
 * always produces a deep-equal output.
 */
import type { RadioViewModel } from '../../semantic/radio-view-model';
import type { RendererViewModel } from './contract';

type Primitive = string | number | boolean | null;
type ActiveRx = RadioViewModel['activeReceiver'];
type BooleanFact = RadioViewModel['split'];
type VfoSlot = RadioViewModel['vfos'][number]['slot'];
type VfoViewModel = RadioViewModel['vfos'][number];
type TxTargetViewModel = RadioViewModel['txTarget'];
type TxPermit = RadioViewModel['txPermit'];
type Availability = RadioViewModel['scope']['hardwareScope'];
type DisabledReason = RadioViewModel['disabledReasons'][number];

/** The explicit unknown-rendering primitive (rule 1) — distinct from every real value any projected field can otherwise hold. */
const UNKNOWN = 'unknown';

/** Thrown when a value that must be a flat primitive is not one at runtime — rule 2's mechanism, independent of the isRendererViewModel gate. */
export class ProjectionError extends Error {}

function primitive(value: unknown, path: string): Primitive {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value;
  }
  throw new ProjectionError(`Cannot project ${path}: expected a flat primitive, got ${typeof value}`);
}

function projectSlot(slot: VfoSlot): string {
  if (slot.kind === 'slotted') return primitive(slot.id, 'slot.id') as string;
  if (slot.kind === 'relative') return primitive(slot.role, 'slot.role') as string;
  return slot.kind; // 'unslotted' | 'unknown' — both already explicit, distinct strings
}

function projectActiveReceiver(rx: ActiveRx): string {
  return rx.status === 'known' ? (primitive(rx.receiver, 'activeReceiver.receiver') as string) : UNKNOWN;
}

function projectBooleanFact(fact: BooleanFact): Primitive {
  return fact.status === 'known' ? primitive(fact.value, 'booleanFact.value') : UNKNOWN;
}

function projectVfo(vfo: VfoViewModel, index: number): Record<string, Primitive> {
  const p = `vfo${index}`;
  return {
    [`${p}Receiver`]: primitive(vfo.receiver, `${p}.receiver`),
    [`${p}Slot`]: projectSlot(vfo.slot),
    [`${p}Label`]: primitive(vfo.label, `${p}.label`),
    [`${p}FrequencyHz`]: primitive(vfo.frequencyHz, `${p}.frequencyHz`),
    [`${p}Mode`]: primitive(vfo.mode, `${p}.mode`),
    [`${p}Filter`]: primitive(vfo.filter, `${p}.filter`),
    [`${p}Active`]: primitive(vfo.isActive, `${p}.isActive`),
    [`${p}TxTarget`]: primitive(vfo.isTxTarget, `${p}.isTxTarget`),
  };
}

function projectTxTarget(tx: TxTargetViewModel): Record<string, Primitive> {
  if (tx.status === 'unknown') {
    return {
      txTargetStatus: UNKNOWN,
      txTargetReceiver: UNKNOWN,
      txTargetSlot: UNKNOWN,
      txTargetFrequencyHz: null,
      txTargetUnknownReason: primitive(tx.reason, 'txTarget.reason'),
    };
  }
  return {
    txTargetStatus: 'known',
    txTargetReceiver: primitive(tx.receiver, 'txTarget.receiver'),
    txTargetSlot: projectSlot(tx.slot),
    txTargetFrequencyHz: primitive(tx.frequencyHz, 'txTarget.frequencyHz'),
    txTargetUnknownReason: null,
  };
}

function projectTxPermit(permit: TxPermit): Record<string, Primitive> {
  if (permit.status === 'allowed') {
    return { txPermitStatus: 'allowed', txPermitBand: primitive(permit.band, 'txPermit.band'), txPermitReason: null };
  }
  // 'denied' | 'unknown' both carry a `reason` and no `band` — permit.status
  // (already one of the contract's three literal strings) passes straight
  // through, so an 'unknown' permit surfaces as exactly that, not 'denied'.
  return { txPermitStatus: permit.status, txPermitBand: null, txPermitReason: primitive(permit.reason, 'txPermit.reason') };
}

function projectAvailability(avail: Availability, prefix: string): Record<string, Primitive> {
  return {
    [`${prefix}Structural`]: primitive(avail.structural, `${prefix}.structural`),
    [`${prefix}Operational`]: primitive(avail.operational, `${prefix}.operational`),
  };
}

function projectDisabledReason(reason: DisabledReason, index: number): Record<string, Primitive> {
  const p = `disabledReason${index}`;
  return {
    [`${p}Field`]: primitive(reason.field, `${p}.field`),
    [`${p}Code`]: primitive(reason.code, `${p}.code`),
  };
}

/** Projects a validator-clean `RadioViewModel` onto a `RendererViewModel`. Throws `ProjectionError` if a value expected to be a flat primitive is not one at runtime. */
export function projectRadioViewModel(model: RadioViewModel): RendererViewModel {
  const fields: Record<string, Primitive> = {
    topologyId: primitive(model.topologyId, 'topologyId'),
    vfoScheme: primitive(model.vfoScheme, 'vfoScheme'),
    activeReceiver: projectActiveReceiver(model.activeReceiver),
    split: projectBooleanFact(model.split),
    dualWatch: projectBooleanFact(model.dualWatch),
    vfoCount: model.vfos.length,
    disabledReasonsCount: model.disabledReasons.length,
    ...Object.assign({}, ...model.vfos.map(projectVfo)),
    ...projectTxTarget(model.txTarget),
    ...projectTxPermit(model.txPermit),
    ...projectAvailability(model.scope.hardwareScope, 'hardwareScope'),
    ...projectAvailability(model.scope.audioFftScope, 'audioFftScope'),
    ...Object.assign({}, ...model.disabledReasons.map(projectDisabledReason)),
  };
  return { kind: 'radio', fields };
}
