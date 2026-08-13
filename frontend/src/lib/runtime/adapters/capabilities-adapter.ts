/**
 * Capabilities adapter — exposes capability-derived helpers that don't fit
 * a single panel's typed view-model.
 *
 * Tier 2 batch 2 of the panel→adapter migration (audit doc:
 * `docs/plans/2026-04-29-panel-adapter-migration.md`). The four exports
 * below cover exactly the capability surface that the in-scope helpers
 * (`meter-utils.ts`, `filter-controls.ts`) and panels
 * (`AudioRoutingControl.svelte`) currently read from
 * `$lib/stores/capabilities.svelte` directly. Per-panel capability flags
 * already live on `derive*Props()` (see `panel-adapters.ts`); this adapter
 * is intentionally thin, not a parallel hierarchy.
 *
 * Reads delegate directly to the capabilities store (the same pattern
 * other adapters use, e.g. `qsy-history-adapter.ts`, `lcd-chrome-adapter.ts`)
 * — going through the runtime singleton would pull in the full transport
 * stack and break tests that mock the store layer in isolation.
 */

import {
  getControlRange as _getControlRange,
  getMeterCalibration as _getMeterCalibration,
  getMeterRedline as _getMeterRedline,
} from '$lib/stores/capabilities.svelte';
import type { ControlRange } from '$lib/types/capabilities';

export interface MeterCalPoint {
  raw: number;
  actual: number;
  label: string;
}

/**
 * Calibration knot points for a meter (e.g. `'s_meter'`, `'power'`,
 * `'swr'`). Returns `null` when capabilities haven't loaded or when the
 * radio doesn't expose calibration for this meter — callers degrade to an
 * honest raw-scale reading (`meter-utils.ts`'s `formatRaw`), never a
 * hardcoded fallback curve borrowed from another radio (MOR-1291/MOR-1451).
 */
export function getMeterCalibration(meterType: string): MeterCalPoint[] | null {
  return _getMeterCalibration(meterType);
}

/**
 * Redline raw value for a meter (e.g. `'alc'`). Returns `null` when no
 * capability data is available — callers degrade to an honest raw-scale
 * reading, never a hardcoded fallback (MOR-1291).
 */
export function getMeterRedline(meterType: string): number | null {
  return _getMeterRedline(meterType);
}

/**
 * Control range descriptor for a named control (e.g. `'pbt_inner'`).
 * Used by filter helpers to derive raw↔display unit conversions. Returns
 * `null` when capabilities haven't loaded or when the control isn't
 * exposed.
 */
export function getControlRange(name: string): ControlRange | null {
  return _getControlRange(name);
}

/**
 * Display label for a receiver (`'MAIN'` / `'SUB'`). Currently a passthrough
 * of the requested id, but routed through the adapter so panels stay
 * agnostic to where the label comes from — future radios may want
 * localized or model-specific labels.
 */
export function getReceiverLabel(id: 'MAIN' | 'SUB'): string {
  return id;
}
