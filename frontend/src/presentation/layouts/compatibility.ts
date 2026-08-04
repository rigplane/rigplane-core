/**
 * The design-language x layout compatibility handshake (MOR-1066/MOR-1072).
 * Pure composition of the two manifests' own declarations — no capability
 * checks, no runtime state, nothing else consulted. Absence of a
 * declaration means compatible: today only `fieldline` (MOR-977 §4.4)
 * declares an incompatibility, against `dual-receiver-cockpit`.
 */
import type { DesignLanguageManifest } from '../languages/contract';
import type { LayoutManifest } from './contract';

export type CompatibilityResult =
  | { readonly compatible: true }
  | { readonly compatible: false; readonly reason?: string };

export function checkLayoutLanguageCompatibility(
  language: DesignLanguageManifest,
  layout: LayoutManifest,
): CompatibilityResult {
  const declaration = language.layoutCompatibility.find((d) => d.layoutId === layout.id);
  if (!declaration || declaration.compatible) return { compatible: true };
  return { compatible: false, reason: declaration.reason };
}
