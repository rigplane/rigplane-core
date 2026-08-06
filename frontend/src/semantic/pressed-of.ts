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
 *
 * The parameter is `TxAuxField<boolean | AtuStatus>`, NOT `TxAuxField<unknown>`
 * (verify-MOR-1358 F2): that is the exact domain the body is correct over.
 * A numeric level field would type-check under `unknown` and then read as
 * PRESSED for every value including `0` (`0 !== false` is `true`) — the same
 * fabricated-claim class this helper exists to prevent. `RitXitScanSurface`
 * and `ScopeControlsSurface` both declared `<boolean>` locally; widening to
 * `unknown` during the extraction would have dropped that protection.
 *
 * NOTE — this module must stay dependency-free at RUNTIME. `CwKeyerSurface`
 * is safety-critical (MOR-1310) and its "imports nothing but the fact
 * contract" allow-list now names `./pressed-of`; that allow-list only scans
 * `CwKeyerSurface`'s own specifiers, so the purity of this file is what makes
 * the widened allow-list sound. It is pinned by `pressed-of.test.ts`'s
 * `'has no runtime import'` case — do not add a value import here.
 */
import type { AtuStatus, TxAuxField } from './radio-view-model';

export function pressedOf(field: TxAuxField<boolean | AtuStatus>): boolean | undefined {
  return field.reading.status === 'known'
    ? field.reading.value !== false && field.reading.value !== 'off'
    : undefined;
}
