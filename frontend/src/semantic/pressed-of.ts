/**
 * Shared `aria-pressed` derivation for semantic-surface toggle controls
 * (MOR-1358).
 *
 * Extracted from `TxAuxSurface.svelte` (MOR-1265, slice 1B), which held the
 * correct shape from the start. Three later slices (`DspSurface` 5B,
 * `RitXitScanSurface` 8B, `CwKeyerSurface` 9B) independently re-derived and
 * pinned the same rule — `RfFrontEndSurface` (6B) has the same inline shape
 * unpinned — instead of importing one answer: on an UNOBSERVED reading,
 * `aria-pressed` must be OMITTED (`undefined`), never `"false"`.
 * `aria-pressed="false"` is not the absence of a claim, it is the claim
 * "this control is OFF" about a reading the radio never reported — the same
 * fail-closed-presentation doctrine every semantic surface in this directory
 * follows for text (`textOf`/`fmt` render `?`/`—`, never a v2 default).
 *
 * `value !== false && value !== 'off'` (not a plain boolean cast) is
 * deliberate: it normalises both plain booleans (`vox`, `nrActive`, …) and
 * the `AtuStatus` three-state enum (`'off' | 'on' | 'tuning'`) to one aria
 * state without the caller pre-converting — verbatim the shape
 * `TxAuxSurface` shipped for the ATU toggle.
 */
import type { TxAuxField } from './radio-view-model';

export function pressedOf(field: TxAuxField<unknown>): boolean | undefined {
  return field.reading.status === 'known'
    ? field.reading.value !== false && field.reading.value !== 'off'
    : undefined;
}
