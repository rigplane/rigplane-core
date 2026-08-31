/**
 * Layout-mode adapter — thin façade over `normalizeLayoutMode` for
 * `skins/registry.ts` (MOR-2039, which banned skins from importing
 * `$lib/stores/*` directly, matching the panels-tier ban).
 *
 * `normalizeLayoutMode` lives in `presentation/layout-mode.ts` (MOR-2059);
 * `lib/stores/layout.svelte.ts` is now a re-export shim in front of it. It
 * takes an explicit value and maps it to its canonical/alias-resolved form,
 * reading no store state, so a mechanical re-export is a correct, lossless
 * wrapper — the same re-export shape `lcd-chrome-adapter.ts` uses to front
 * its (stateful) store pair, per the precedent in
 * docs/plans/2026-04-29-panel-adapter-migration.md ("Cluster C", option 1).
 */

export { normalizeLayoutMode, type LayoutMode } from '$lib/stores/layout.svelte';
