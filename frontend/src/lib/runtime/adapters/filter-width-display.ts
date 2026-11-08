/**
 * MOR-3677 — the filter-width DISPLAY derivation: one honest state for the
 * width row and the spectrum chip, composed from three qualified leaves of
 * the SAME evidence machinery every display fact uses
 * (`qualifyDisplayObservation`, `display-observation.ts`) — no parallel
 * tri-state mechanism.
 *
 * Why a composed value rather than reading `filterWidth` alone: the FTX-1
 * answers `SH0000` (its own mode default) and turns NARROW on without SH
 * following it, so "a width leaf exists" no longer decides between "here is
 * the Hz" and "there is no Hz to show". The states, checked in this order
 * (design §1 truth table):
 *
 *  1. `unsupported`      — no `filter_width` capability at all;
 *  2. `narrow`           — NARROW confirmed on AND the width leaf withheld
 *                          (the MOR-2803 declared-absent discard): the
 *                          per-mode NAR WIDTH owns the passband, and its Hz
 *                          is not readable, so no Hz may be shown. The
 *                          `availability !== 'available'` conjunct is the
 *                          NARROW-over-SH boundary itself: for the one
 *                          freshness tick where a held SH value is still
 *                          available, the reading stays `known` (owner-
 *                          accepted default, design Q8);
 *  3. `radio-default`    — `filterWidthRadioDefault` confirmed true (PR1's
 *                          wire leaf; code 000): the radio's own default,
 *                          no Hz exists;
 *  4. `known {hz, stale}`— a current-or-held width reading with a positive
 *                          integer value;
 *  5. `not-reported`     — the width leaf WAS observed but carries no Hz
 *                          (C4FM, an out-of-table code, or the flag leaf
 *                          still unpublished by a pre-PR1 backend — this
 *                          derivation must degrade honestly when PR2 merges
 *                          alone);
 *  6. `not-observed`     — anything else, including an unresolved identity
 *                          (fail closed, never a fabricated Hz).
 *
 * The contract type lives in `semantic/radio-view-model.ts`
 * (`ModeFilterViewModel.filterWidthDisplay`); this module re-exports it and
 * owns the derivation only.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { DisplayObservation, FilterWidthDisplay } from '../../../semantic/radio-view-model';
import { qualifyDisplayObservation } from './display-observation';
import type { ReceiverId } from './presentation-capabilities';

export type { FilterWidthDisplay };

const hasCap = (caps: Capabilities | null, name: string): boolean =>
  caps?.capabilities?.includes(name) ?? false;

/** Narrows a qualified observation to its current-or-held value branch. */
function withValue<T extends boolean | number | string>(
  observation: DisplayObservation<T>,
): observation is Extract<DisplayObservation<T>, { readonly state: 'current' | 'stale' }> {
  return observation.state === 'current' || observation.state === 'stale';
}

/**
 * The PR1 wire leaf `receiver.{main,sub}.filterWidthRadioDefault` is absent
 * from the generated `state.ts` until PR1 regenerates it, so it is read
 * through this narrow optional cast — the same defensive shape the optional
 * receiver fields establish (`digisel`, `narrow`, …). A missing key and an
 * explicit null both resolve `undefined` here, which the qualification
 * below already fails closed on.
 */
type RadioDefaultLeaf = { filterWidthRadioDefault?: boolean | null };

export function deriveFilterWidthDisplay(
  state: ServerState | null,
  caps: Capabilities | null,
  receiver: ReceiverId,
): FilterWidthDisplay {
  if (!hasCap(caps, 'filter_width')) return { state: 'unsupported' };
  const base = receiver === 'SUB' ? 'sub.' : 'main.';
  const rx = receiver === 'SUB' ? state?.sub : state?.main;
  const widthStatus = state?.fieldStatus?.[`${base}filterWidth`];

  // Row 2 — NARROW wins, but only once the width leaf is actually withheld
  // (see the header: the one-tick NARROW-over-SH window stays `known`).
  const narrowObservation = qualifyDisplayObservation({
    state, caps, receiver, path: `${base}narrow`,
    structural: hasCap(caps, 'narrow'), value: rx?.narrow ?? undefined,
  });
  if (withValue(narrowObservation) && narrowObservation.value === true
    && widthStatus !== undefined && widthStatus.availability !== 'available') {
    return { state: 'narrow' };
  }

  // Row 3 — the radio's own default (code 000). Always readable as a
  // boolean, including false, so a stale true cannot outlive the state.
  const radioDefault = (rx as RadioDefaultLeaf | null | undefined)?.filterWidthRadioDefault ?? undefined;
  const flagObservation = qualifyDisplayObservation({
    state, caps, receiver, path: `${base}filterWidthRadioDefault`,
    structural: true, value: radioDefault,
  });
  if (withValue(flagObservation) && flagObservation.value === true) {
    return { state: 'radio-default' };
  }

  // Row 4 — the Hz reading itself.
  const widthObservation = qualifyDisplayObservation({
    state, caps, receiver, path: `${base}filterWidth`,
    structural: true, value: rx?.filterWidth ?? undefined,
  });
  if (withValue(widthObservation)
    && Number.isSafeInteger(widthObservation.value) && widthObservation.value > 0) {
    return {
      state: 'known',
      hz: widthObservation.value,
      stale: widthObservation.state === 'stale',
    };
  }

  // Row 5 — the radio answered but gave no Hz for this mode.
  const widthValue = rx?.filterWidth;
  if (widthStatus?.observed === true
    && !(Number.isSafeInteger(widthValue) && (widthValue as number) > 0)) {
    return { state: 'unknown', reason: 'not-reported' };
  }
  return { state: 'unknown', reason: 'not-observed' };
}
