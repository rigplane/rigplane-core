/**
 * Structural `LayoutManifest` guard for filtering a namespace import of
 * `../declarations` down to real manifests. Imported by every
 * `*-declarability.test.ts` file in this directory and by
 * `forward-declaration-inventory.test.ts`, each deriving its own inventory
 * from the barrel instead of hand-listing ids. Not itself a test file.
 *
 * Extracted (MOR-2061) from the identical copy `forward-declaration-
 * inventory.test.ts` carried (MOR-2060).
 */
import type { LayoutManifest } from '../contract';

export function isLayoutManifest(value: unknown): value is LayoutManifest {
  return (
    typeof value === 'object' && value !== null &&
    (value as { schemaVersion?: unknown }).schemaVersion === 1 &&
    typeof (value as { id?: unknown }).id === 'string' &&
    Array.isArray((value as { zones?: unknown }).zones)
  );
}
