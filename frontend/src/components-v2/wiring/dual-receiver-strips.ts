/**
 * Pure per-receiver `RadioViewModel` slicing for the dual-receiver-cockpit's
 * two channel strips (MOR-1067). Carries none of the TX-lease weight
 * `SemanticRadioSurfaces.svelte` owns — filtering only, never touched by or
 * touching TX state, so this is not a second TX code path. `receiversOf`
 * never fabricates a receiver absent from `view.vfos` (MOR-988 §3.2): a
 * single-receiver view model yields exactly one strip, an empty one yields
 * none. `isActiveStrip` is true only on a POSITIVELY observed match — an
 * `unknown` activeReceiver marks every strip inactive, never a guessed one.
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
