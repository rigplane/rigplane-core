/**
 * Pure per-receiver `RadioViewModel` slicing for the dual-receiver-cockpit's
 * two channel strips (MOR-1067). Carries none of the TX-lease weight
 * `SemanticRadioSurfaces.svelte` owns — filtering only, never touched by or
 * touching TX state, so this is not a second TX code path. `receiversOf`
 * never fabricates a receiver absent from `view.vfos` (MOR-988 §3.2): a
 * single-receiver view model yields exactly one strip, an empty one yields
 * none. `isActiveStrip` is true only on a POSITIVELY observed match — an
 * `unknown` activeReceiver marks every strip inactive, never a guessed one.
 * `isOperationalStrip` (MOR-1256) is the structural/operational counterpart:
 * a receiver stays in `receiversOf`/`vfos` when only STRUCTURALLY present
 * (MOR-977 — the strip renders), but reads back the adapter's per-receiver
 * `disabledReasons` entry to say whether it is also usable right now.
 */
import type { ReceiverId, RadioViewModel } from '../../semantic/radio-view-model';

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
