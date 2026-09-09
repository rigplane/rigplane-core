/**
 * Pure per-receiver `RadioViewModel` slicing for the dual-receiver-cockpit's
 * two channel strips (MOR-1067), and — since MOR-2425 / R58 — the per-SLOT
 * slicing beside it (`slotsOf`/`forSlot`). Carries none of the TX-lease weight
 * `SemanticRadioSurfaces.svelte` owns — filtering only, never touched by or
 * touching TX state, so this is not a second TX code path. `receiversOf`
 * never fabricates a receiver absent from `view.vfos` (MOR-988 §3.2): an
 * empty view model yields none. `isActiveStrip` is true only on a POSITIVELY observed match — an
 * `unknown` activeReceiver marks every strip inactive, never a guessed one.
 * `isOperationalStrip` (MOR-1256) is the structural/operational counterpart:
 * a receiver stays in `receiversOf`/`vfos` when only STRUCTURALLY present
 * (MOR-977 — the strip renders), but reads back the adapter's per-receiver
 * `disabledReasons` entry to say whether it is also usable right now.
 */
import type { ReceiverId, RadioViewModel, VfoSlot } from '../../semantic/radio-view-model';

export function receiversOf(view: RadioViewModel): readonly ReceiverId[] {
  const seen: ReceiverId[] = [];
  for (const vfo of view.vfos) {
    if (!seen.includes(vfo.receiver)) seen.push(vfo.receiver);
  }
  return seen;
}

/** Every fact except `vfos` stays the shared/global value, unchanged. */
export function forReceiver(view: RadioViewModel, receiver: ReceiverId): RadioViewModel {
  return { ...view, vfos: view.vfos.filter((vfo) => vfo.receiver === receiver) };
}

/**
 * One column of a two-column deck, under owner ruling R58 (2026-09-09): a
 * deck column is a SLOT, not a receiver. On a two-receiver radio a slot IS a
 * receiver, so the second slot is SUB and keeps its own instruments. On a
 * one-receiver radio a slot is one of that receiver's VFO positions, so the
 * second slot is the unselected VFO — which has no receiver of its own and
 * therefore no receiver instruments (`ownsReceiverInstruments`).
 */
export interface StripSlot {
  /** Stable `{#each}` key. */
  readonly key: string;
  readonly receiver: ReceiverId;
  /** Indices into `view.vfos`. Like `receiversOf`, this never names a
   *  position absent from `view.vfos` (MOR-988 §3.2). */
  readonly entries: readonly number[];
  /** Whether this is the FIRST slot backed by `receiver`, and so the one
   *  that carries that receiver's indicator row and S-meter. */
  readonly ownsReceiverInstruments: boolean;
  /** Whether another slot of this deck is backed by the same receiver — true
   *  only where one receiver backs more than one column. */
  readonly sharesReceiver: boolean;
}

function slotIdentity(slot: VfoSlot): string {
  if (slot.kind === 'slotted') return slot.id;
  if (slot.kind === 'relative') return slot.role;
  return slot.kind;
}

/**
 * The deck slots `view.vfos` actually carries, left column first.
 *
 * Two receivers: one slot each, in the model's own order — R58 fixes SUB as
 * the second slot, so this does not reorder behind the active receiver.
 * One receiver: one slot per VFO position, the receiver's active/selected
 * one first (R58: the left column is the active slot). `isActiveSlot` is at
 * most true once per receiver (`radio-view-model.ts`'s validator), and false
 * on every position when the slot was never observed — the order is then the
 * model's own, never a guess.
 */
export function slotsOf(view: RadioViewModel): readonly StripSlot[] {
  const receivers = receiversOf(view);
  if (receivers.length > 1) {
    return receivers.map((receiver) => ({
      key: receiver,
      receiver,
      entries: view.vfos.flatMap((vfo, index) => (vfo.receiver === receiver ? [index] : [])),
      ownsReceiverInstruments: true,
      sharesReceiver: false,
    }));
  }
  const indices = view.vfos.map((_vfo, index) => index);
  const ordered = [
    ...indices.filter((index) => view.vfos[index].isActiveSlot),
    ...indices.filter((index) => !view.vfos[index].isActiveSlot),
  ];
  return ordered.map((index, position) => ({
    key: `${view.vfos[index].receiver}-${slotIdentity(view.vfos[index].slot)}`,
    receiver: view.vfos[index].receiver,
    entries: [index],
    ownsReceiverInstruments: position === 0,
    sharesReceiver: ordered.length > 1,
  }));
}

/**
 * `forReceiver`'s per-slot counterpart: every fact except `vfos` and
 * `receiverIndicators` stays the shared/global value, unchanged.
 *
 * The indicators are the exception because `VfoSurface`'s `indicatorReceiver`
 * prop cannot express "none" — omitting it there means "every indicator" —
 * so a slot that owns no receiver instruments has to be handed none.
 */
export function forSlot(view: RadioViewModel, slot: StripSlot): RadioViewModel {
  const vfos = slot.entries.map((index) => view.vfos[index]);
  if (view.receiverIndicators === undefined) return { ...view, vfos };
  return {
    ...view,
    vfos,
    receiverIndicators: slot.ownsReceiverInstruments
      ? view.receiverIndicators.filter((indicator) => indicator.receiver === slot.receiver)
      : [],
  };
}

/**
 * `isActiveStrip`'s per-slot counterpart: at most ONE column carries the
 * active mark (R58). A column whose receiver backs no other column keeps
 * today's rule — the mark follows the active RECEIVER, so a two-receiver deck
 * is unchanged; where two columns share a receiver, the mark goes to the one
 * holding that receiver's active position.
 */
export function isActiveSlotStrip(view: RadioViewModel, slot: StripSlot): boolean {
  if (!isActiveStrip(view, slot.receiver)) return false;
  return !slot.sharesReceiver || slot.entries.some((index) => view.vfos[index].isActiveSlot);
}

export function isActiveStrip(view: RadioViewModel, receiver: ReceiverId): boolean {
  return view.activeReceiver.status === 'known' && view.activeReceiver.receiver === receiver;
}

/**
 * True unless the adapter reported this exact receiver as operationally
 * unavailable (`radio-view-model-adapter.ts`'s `receiver.<ID>` disabledReason
 * — the `dual-rx-unavailable` diagnostic). Matches on the FULL field name,
 * not a prefix: an unrelated receiver's reason must never gate this one.
 */
export function isOperationalStrip(view: RadioViewModel, receiver: ReceiverId): boolean {
  return !view.disabledReasons.some((reason) => reason.field === `receiver.${receiver}`);
}
