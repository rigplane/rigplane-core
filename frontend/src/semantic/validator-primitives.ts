/**
 * Shared validator primitives for the semantic contract layer.
 *
 * MOR-1264 export-seam narrowing: the adversarial-verification ruling on
 * slice S0's mutation report (§3/N2) found `record`/`exactKeys`/`str`
 * exported raw from `radio-view-model.ts`, justified only by that slice's
 * test harness. Moved here — a named seam, not an incidental one — before
 * the remaining MOR-1262 vocabulary families copy the shape. Every future
 * per-family group validator (MOR-1244's `validateTxAux`, …) imports these
 * the same way `radio-view-model.ts`'s own group validators
 * (`validateVfoSlot`, `validateActiveRx`, …) do. `radio-view-model.ts`'s own
 * public surface goes back to types + `validateRadioViewModel` +
 * `optionalGroup`.
 *
 * Bodies are byte-identical to their pre-move originals — only their file
 * (and therefore their exposure) changed.
 */
export function invalid(path: string, expected: string): never {
  throw new TypeError(`Invalid radio view model at ${path}: expected ${expected}`);
}
export function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) invalid(path, 'an object');
  return value as Record<string, unknown>;
}
export function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string): void {
  const extra = Object.keys(value).filter((k) => !keys.includes(k));
  if (extra.length > 0) invalid(path, `only [${keys.join(', ')}] (found extra: ${extra.join(', ')})`);
}
export function str(value: unknown, path: string): string {
  if (typeof value !== 'string') invalid(path, 'a string');
  return value;
}
