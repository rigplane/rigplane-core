/**
 * Layout-mode vocabulary (MOR-2059) — the skin-selection preference enum and
 * its MOR-1042 alias normalization. Pure: no localStorage, no store import,
 * no module-scope side effect, so any presentation/-layer module may import
 * it directly (see `workspace/contract.ts`) instead of hand-pinning a copy.
 * Moved out of `lib/stores/layout.svelte.ts`, which now re-exports these
 * names as a compatibility shim — `getLayoutMode`/`setLayoutMode` stayed
 * behind there, since they are workspace-store-coupled, not vocabulary.
 *
 * 'auto'        = desktop-v2 (the v3 default) on every non-mobile viewport
 * 'lcd'         = legacy alias for 'lcd-cockpit'
 * 'lcd-cockpit' = force LCD cockpit (TS-990S-style dual-cockpit)
 * 'lcd-scope'   = force LCD scope (IC-7300-style scope-dominant)
 * 'standard'    = force standard layout
 * 'peer-split'  = force the segmentline peer-split skin (MOR-2151/MOR-2155;
 *                 two-column FTX-1 dual-receiver symmetry, see
 *                 `skins/segmentline/PeerSplitLayout.svelte`)
 * 'dual-receiver-cockpit' = QA-ONLY (MOR-1257): reachable solely via the
 *                 exact `?layout=dual-receiver-cockpit` query param (see
 *                 `lib/stores/qa-cockpit-override.ts`) — deliberately NOT a
 *                 CanonicalLayoutMode, so normalizeLayoutMode below falls it
 *                 through to 'auto'. It can therefore never be persisted via
 *                 setLayoutMode/the workspace and never appears in the
 *                 StatusBar skin selector.
 */
export type LayoutMode =
  | 'auto' | 'lcd' | 'lcd-cockpit' | 'lcd-scope' | 'standard' | 'sdr-test'
  | 'peer-split' | 'dual-receiver-cockpit';
export type CanonicalLayoutMode = Exclude<LayoutMode, 'lcd' | 'dual-receiver-cockpit'>;

export const CANONICAL_LAYOUT_MODES = new Set<CanonicalLayoutMode>([
  'auto',
  'lcd-cockpit',
  'lcd-scope',
  'standard',
  'sdr-test',
  'peer-split',
]);

export const LEGACY_LAYOUT_ALIASES: Record<string, CanonicalLayoutMode> = {
  lcd: 'lcd-cockpit',
  'amber-lcd': 'lcd-cockpit',
  spectrum: 'standard',
  'desktop-v2': 'standard',
};

export function normalizeLayoutMode(value: 'lcd' | 'amber-lcd'): 'lcd-cockpit';
export function normalizeLayoutMode(value: 'spectrum' | 'desktop-v2'): 'standard';
export function normalizeLayoutMode(value: unknown): CanonicalLayoutMode;
export function normalizeLayoutMode(value: unknown): CanonicalLayoutMode {
  if (typeof value !== 'string') return 'auto';
  if (Object.hasOwn(LEGACY_LAYOUT_ALIASES, value)) {
    return LEGACY_LAYOUT_ALIASES[value];
  }
  if (CANONICAL_LAYOUT_MODES.has(value as CanonicalLayoutMode)) {
    return value as CanonicalLayoutMode;
  }
  return 'auto';
}
