/**
 * MOR-1422 — the shared `disabledReasonText` helper.
 *
 * Every semantic surface that renders a present-but-unusable control
 * (`TxAuxSurface`, `DspSurface`, `FilterSurface`, …) already computes the
 * MOR-977 two-level `Availability` for that field; this is the ONE place
 * that turns it into the operator-facing text those surfaces put on `title`
 * and an `aria-describedby` target. See `TxAuxSurface.test.ts`'s "disabled reason is
 * exposed on hover and to screen readers" block for the reachable
 * (`operational: false`) case wired into a real, rendered surface.
 * `structural: false` has no current shipped surface that renders it as
 * disabled-with-a-reason — every surface OMITS a structurally-absent
 * control instead (MOR-977/1256 doctrine, pinned by e.g.
 * `TxAuxSurface.test.ts`'s "renders no control at all for a structurally
 * absent" cases) — so that branch is pinned here, directly, against the
 * contract's own `Availability` shape.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { _resetLocale } from '$lib/i18n/store.svelte';
import { disabledReasonText } from '../disabled-reason';
import type { Availability } from '../radio-view-model';

describe('disabledReasonText (MOR-1422)', () => {
  // `disabledReasonText` reads the shared i18n locale singleton via `t()`, and
  // this file runs in the non-isolated "fast" vitest project where module
  // state leaks across files within a worker — pin the locale per test so a
  // preceding pseudo-locale test cannot garble the expected English strings.
  beforeEach(() => {
    _resetLocale();
  });

  afterEach(() => {
    _resetLocale();
  });

  it('returns "not supported by this radio" when the radio never declared the capability', () => {
    const availability: Availability = { structural: false, operational: false };
    expect(disabledReasonText(availability)).toBe('Not supported by this radio');
  });

  it('returns "not yet observed" when the capability exists but the field has not arrived', () => {
    const availability: Availability = { structural: true, operational: false };
    expect(disabledReasonText(availability)).toBe('Not yet observed');
  });

  it('returns undefined once both structural and operational are true — a usable control gets no reason', () => {
    const availability: Availability = { structural: true, operational: true };
    expect(disabledReasonText(availability)).toBeUndefined();
  });

  // MUTATION KILLED: swapping the branch order. `structural: false` must win
  // even if a caller (incorrectly) also carries `operational: false` — the
  // capability-missing claim is the more specific one.
  it('prefers the missing-capability reason over the unobserved one', () => {
    const availability: Availability = { structural: false, operational: false };
    expect(disabledReasonText(availability)).not.toBe('Not yet observed');
  });
});
