/**
 * Shared "why is this disabled" text for semantic-surface controls
 * (MOR-1422).
 *
 * Several surfaces (`DspSurface`, `FilterSurface`, `TxAuxSurface`, …) already
 * compute a `DisabledReasonCode` for a present-but-unusable control (the
 * per-file `reasonOf` helpers) and stamp it onto `data-disabled-reason` — a
 * DOM attribute that is invisible on hover and to assistive tech. This
 * module turns the SAME MOR-977 two-level `Availability` a fact already
 * carries into operator-facing text, for the hover channel (`title`) and a
 * screen-reader channel (`aria-describedby`, since a plain `aria-description`
 * string is not yet in Svelte's own attribute typings) a disabled control
 * needs so a fail-closed control does not look like a broken one.
 *
 * `structural: false` — the radio model does not declare this capability at
 * all ("not supported by this radio"). `operational: false` — the control
 * exists but its reading has not arrived yet, whether because it was simply
 * never read or because the same connection trouble
 * `sendCommand`'s refusal notice (`$lib/transport/ws-client`) reports has
 * degraded it — this module's `core.disabledReason.*` keys are the shared
 * wording family both surfaces draw from, so the operator reads one
 * vocabulary for "this is fail-closed," not two.
 *
 * `undefined` for a usable field: a usable control gets no reason text at
 * all, matching every surface's existing `usable()` gate.
 */
import { t } from '$lib/i18n';
import type { Availability } from './radio-view-model';

export function disabledReasonText(availability: Availability): string | undefined {
  if (!availability.structural) return t('core.disabledReason.missing');
  if (!availability.operational) return t('core.disabledReason.unobserved');
  return undefined;
}
