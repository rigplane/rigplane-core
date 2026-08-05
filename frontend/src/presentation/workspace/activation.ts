/**
 * MOR-1081 — the single route from the workspace's `designLanguage` selection
 * to the `[data-design-language]` activation attribute.
 *
 * MOR-1278 froze that attribute on the semantic-vertical root as the ONE
 * activation mechanism, and MOR-1275's `renderSlot` wiring reads the very same
 * attribute. Adoption therefore may not introduce a second switch: this module
 * only decides WHICH value the one attribute may carry, and the composition
 * root (`App.svelte`) is the only caller that writes it.
 *
 * WHY A GATE AT ALL. The workspace always carries a `designLanguage` (v1 has
 * no "unset"), but the app shell has not adopted design-language presentation
 * yet — routing a language into every shipped skin IS the cutover
 * (MOR-1048/MOR-1263), not this ticket. The gate is the language's OWN frozen
 * manifest data rather than new policy: a family declares, in
 * `layoutCompatibility`, which layouts it can serve. Today `studioline`
 * declares exactly one (`dual-receiver-cockpit`), so the selection activates
 * there and every v2 skin renders byte-identically to before.
 *
 * Pure: takes the manifest the caller already resolved, touches no DOM, no
 * storage and no registry — the registry-populating `languages/declarations`
 * barrel is a deliberate non-import here (the workspace zone's module-load
 * purity pin), so the lookup stays the caller's.
 */
import type { DesignLanguageManifest } from '../languages/contract';

/** The value `[data-design-language]` may take, or `null` for "no language active". */
export function designLanguageActivation(
  manifest: DesignLanguageManifest | undefined,
  layoutId: string,
): string | null {
  if (manifest === undefined) return null;
  const declared = manifest.layoutCompatibility.find((entry) => entry.layoutId === layoutId);
  return declared?.compatible === true ? manifest.id : null;
}
