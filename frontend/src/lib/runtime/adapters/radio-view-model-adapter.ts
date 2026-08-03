/**
 * Radio view-model adapter — the live seam behind the semantic VFO and RX/TX
 * surfaces (MOR-1065).
 *
 * Maps REAL runtime state + capabilities onto the MOR-1062 radio semantic
 * view-model shape. Unknown-preservation is the whole job: an unobserved
 * active receiver, A/B slot, split, dual-watch or TX target must reach the
 * surfaces as the contract's explicit `unknown` branch — never as a
 * fabricated `'MAIN'` / `'A'` / `false` (MOR-988 §3.2, §4, §11.3). Every
 * radio fact below is gated on the backend's own field status, so an old or
 * partially-observed server degrades to `unknown`/disabled rather than to a
 * guessed default.
 *
 * The contract type is imported TYPE-ONLY. eslint invariant 1 (MOR-1061 F2)
 * bans `lib/runtime/** -> semantic/**`, but the v3 ADR's own dependency
 * diagram puts the view model on the ADAPTER side of that seam, so the
 * adapters zone carries a narrow, recorded `allowTypeImports` exception (see
 * `eslint.config.js`; MOR-1065 review ruling 2). Value imports from
 * `semantic/` stay blocked — pinned by `architecture-boundaries.test.ts`.
 * Annotating the return type here is what makes contract drift a compile
 * error at the PRODUCER (and, because the returned object literal is checked
 * against the annotation, an extra field is an error too);
 * `__tests__/radio-view-model-adapter.test.ts` additionally runs every
 * emitted model through the real `validateRadioViewModel`.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import {
  derivePresentationCapabilities, type ReceiverId, type VfoSlotId,
} from './presentation-capabilities';
import { deriveTxCapabilities } from './tx-capabilities';

type Slot = { kind: 'slotted'; id: VfoSlotId } | { kind: 'unslotted' } | { kind: 'unknown' };
type Fact = { status: 'known'; value: boolean } | { status: 'unknown' };
type Reason = {
  field: string;
  code: 'capability-unavailable' | 'field-not-observed' | 'tx-target-unknown' | 'out-of-band';
};
type Readable = {
  freqHz?: number; mode?: string; filter?: number | null; filterNum?: number | null;
};
type Position = { slot: Slot; base: string; filterKey: 'filter' | 'filterNum'; src: Readable | null };

const RECEIVER_KEY = { MAIN: 'main', SUB: 'sub' } as const;
const SLOT_KEY = { A: 'vfoA', B: 'vfoB' } as const;

/** Observed + fresh + available — the same three-part gate the TX authority uses. */
function seen(state: ServerState | null, path: string): boolean {
  const status = state?.fieldStatus?.[path];
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
}

function boolFact(state: ServerState | null, path: string, value: unknown): Fact {
  return seen(state, path) && typeof value === 'boolean'
    ? { status: 'known', value }
    : { status: 'unknown' };
}

/** Per-position readings; each degrades to `null` on its own, unobserved field. */
function readings(
  state: ServerState | null, base: string, filterKey: 'filter' | 'filterNum', src: Readable | null,
): { frequencyHz: number | null; mode: string | null; filter: string | null } {
  const hz = src?.freqHz;
  const mode = src?.mode;
  const filter = filterKey === 'filter' ? src?.filter : src?.filterNum;
  return {
    frequencyHz: seen(state, `${base}freqHz`) && typeof hz === 'number' && Number.isFinite(hz)
      ? hz : null,
    mode: seen(state, `${base}mode`) && typeof mode === 'string' && mode !== '' ? mode : null,
    filter: seen(state, `${base}${filterKey}`) && typeof filter === 'number'
      ? `FIL${filter}` : null,
  };
}

const sameSlot = (a: Slot, b: Slot): boolean =>
  a.kind === 'slotted' && b.kind === 'slotted' ? a.id === b.id : a.kind === b.kind;

/**
 * `null` when there is nothing safe to render at all: capabilities have not
 * loaded, or they describe a topology that contradicts itself
 * (`derivePresentationCapabilities` diagnoses that, and a contradictory
 * topology must not be guessed into one of the four canonical shapes).
 */
export function toRadioViewModel(
  state: ServerState | null, caps: Capabilities | null,
): RadioViewModel | null {
  if (!caps) return null;
  const presentation = derivePresentationCapabilities(caps);
  const topology = presentation.topology;
  if (!topology) return null;

  const activeReceiver: { status: 'known'; receiver: ReceiverId } | { status: 'unknown' } =
    seen(state, 'active') && (state?.active === 'MAIN' || state?.active === 'SUB')
      ? { status: 'known', receiver: state.active }
      : { status: 'unknown' };

  // TX identity and permit come from the SAME derivation the App TX authority
  // uses (`deriveTxCapabilities`), so the surfaces cannot disagree with the
  // controller about what the radio would key.
  const observedTarget = seen(state, 'txTarget') && state
    ? state.txTarget
    : {
      status: 'unknown' as const,
      reason: state?.fieldStatus?.txTarget?.availability === 'stale'
        ? 'stale' as const : 'not-observed' as const,
    };
  const facts = deriveTxCapabilities(caps, {
    txTarget: observedTarget, modInputSource: { status: 'unknown' },
  });
  const target = facts.txTarget;
  const txTarget = target.status === 'known'
    ? {
      status: 'known' as const, receiver: target.receiver, frequencyHz: target.frequencyHz,
      slot: (target.slot === null
        ? { kind: 'unslotted' } : { kind: 'slotted', id: target.slot }) as Slot,
    }
    : { status: 'unknown' as const, reason: target.reason };
  const txPermit = facts.frequencyPermit;

  const vfos = topology.structuralReceivers.flatMap((receiver) => {
    const key = RECEIVER_KEY[receiver];
    const rx = state?.[key] ?? null;
    const slots = topology.slots[receiver] ?? null;
    // Gated like every other fact — and it MUST be: the backend defaults
    // `activeSlot` to "A" (`state_schema.py`), so an ungated read marks
    // MAIN A active on evidence the radio never provided.
    const activeSlot = seen(state, `${key}.activeSlot`)
      && (rx?.activeSlot === 'A' || rx?.activeSlot === 'B') ? rx.activeSlot : null;
    const positions: Position[] = slots === null
      ? [{ slot: { kind: 'unslotted' }, base: `${key}.`, filterKey: 'filter', src: rx }]
      : slots.every((id) => rx?.[SLOT_KEY[id]] != null)
        ? slots.map((id) => ({
          slot: { kind: 'slotted', id }, base: `${key}.${SLOT_KEY[id]}.`,
          filterKey: 'filterNum', src: rx?.[SLOT_KEY[id]] ?? null,
        }))
        // A slotted scheme whose slot view was never observed: ONE position of
        // unknown slot identity. Synthesising 'A' here is exactly the
        // fabrication MOR-988 §3.2 forbids.
        : [{ slot: { kind: 'unknown' }, base: `${key}.`, filterKey: 'filter', src: rx }];
    return positions.map(({ slot, base, filterKey, src }) => ({
      receiver,
      slot,
      label: slot.kind === 'slotted' ? `${receiver} ${slot.id}` : receiver,
      ...readings(state, base, filterKey, src),
      isActive: activeReceiver.status === 'known' && activeReceiver.receiver === receiver
        && (slot.kind !== 'slotted' || slot.id === activeSlot),
      isTxTarget: txTarget.status === 'known' && txTarget.receiver === receiver
        && sameSlot(txTarget.slot, slot),
    }));
  });

  const split = boolFact(state, 'split', state?.split);
  const dualWatch = boolFact(state, 'dualWatch', state?.dualWatch);
  // Structural = the model has it. Operational = usable right now: the
  // hardware scope needs an observed scope-control block, the audio FFT needs
  // a live state payload for the stream it rides on.
  const hardwareScope = {
    structural: presentation.scope.hardwareScopeAvailable,
    operational: presentation.scope.hardwareScopeAvailable && state?.scopeControls != null,
  };
  const audioFftScope = {
    structural: presentation.scope.audioFftAvailable,
    operational: presentation.scope.audioFftAvailable && state !== null,
  };

  const disabledReasons: Reason[] = [];
  if (activeReceiver.status === 'unknown') {
    disabledReasons.push({ field: 'activeReceiver', code: 'field-not-observed' });
  }
  if (split.status === 'unknown') disabledReasons.push({ field: 'split', code: 'field-not-observed' });
  if (dualWatch.status === 'unknown') {
    disabledReasons.push({ field: 'dualWatch', code: 'field-not-observed' });
  }
  if (txTarget.status === 'unknown') {
    disabledReasons.push({
      field: 'txTarget',
      code: txTarget.reason === 'unsupported' ? 'capability-unavailable' : 'field-not-observed',
    });
  }
  if (txPermit.status === 'denied') disabledReasons.push({ field: 'txPermit', code: 'out-of-band' });
  if (txPermit.status === 'unknown') {
    disabledReasons.push({
      field: 'txPermit',
      code: txPermit.reason === 'ranges-unconfigured' ? 'capability-unavailable' : 'tx-target-unknown',
    });
  }
  for (const [field, availability] of [
    ['scope.hardwareScope', hardwareScope], ['scope.audioFftScope', audioFftScope],
  ] as const) {
    if (!availability.structural) disabledReasons.push({ field, code: 'capability-unavailable' });
    else if (!availability.operational) disabledReasons.push({ field, code: 'field-not-observed' });
  }

  return {
    topologyId: `${topology.structuralCount}/${topology.scheme}`,
    vfoScheme: topology.scheme,
    activeReceiver,
    vfos,
    split,
    dualWatch,
    txTarget,
    txPermit,
    scope: { hardwareScope, audioFftScope },
    disabledReasons,
  };
}
