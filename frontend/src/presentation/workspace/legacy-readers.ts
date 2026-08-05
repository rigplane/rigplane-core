/**
 * MOR-1078 — deterministic legacy workspace readers. Turns the MOR-1076
 * evidence inventory (27 keys audited, resolved below to 29 distinct storage
 * keys once every row is counted) into code: a full key routing table, plus
 * a pure reader that folds the keys with a genuine home in `WorkspaceV1`
 * (MOR-1077's `contract.ts`) into that schema's input shape. `readWorkspace`
 * is the ONLY construction path — this module never bypasses it.
 *
 * Read-only by construction, not just by convention: `snapshotLegacyStorage`
 * is the sole point of storage contact and its parameter type is
 * `Pick<Storage, 'getItem'>` — a `setItem`/`removeItem`/`clear` call would
 * not type-check. Actual persistence (writing the migrated object back) is
 * MOR-1079's boundary.
 *
 * Scope note — several evidence-file keys were tentatively marked MIGRATE
 * before the v1 schema was frozen (`panel-collapsed`, `panel-order`,
 * `right-panel-order`, `lcd-display-mode`, `lcd-contrast`, `vfo-theme`).
 * None has a matching field in the frozen schema: `zoneOrder`/
 * `visibleSurfaces` are keyed by the 4-id `SemanticSurfaceName` enum
 * (`vfo`/`rxTx`/`txAux`/`meters`), not the ~15-20 fine-grained sidebar panel
 * ids those keys actually store, and there is no `contrast`/`lcdMode`/
 * `vfoTheme` field at all. Routing them through the validator would either
 * invent zone assignments no legacy data provides, or produce 100%
 * `unknown-id` rejections on every real value — never an actual migration.
 * Per this ticket's own acceptance language ("maps ... to workspace v1 OR
 * ITS CORRECT NON-WORKSPACE OWNER"), they route to `retain-outside`: their
 * current store stays the owner. See the MOR-1078 build report for the full
 * per-key reasoning and the resulting MIGRATE-set size mismatch.
 */
import { readWorkspace, type WorkspaceReadResult } from './contract';

/** Terminal routing outcome for one legacy key, per the MOR-1076 evidence table. */
export type LegacyKeyDisposition = 'migrate' | 'retain-outside' | 'retire' | 'forbidden';

export interface LegacyKeyRoute {
  readonly key: string;
  readonly disposition: LegacyKeyDisposition;
  readonly note: string;
}

/** Mirrors `contract.ts`'s own `manufacturer-policy` marker class — used only to
 *  prove, structurally, that the migrate set below carries no such key. */
const MANUFACTURER_KEY_PATTERN = /^(icom|yaesu|kenwood|elecraft|xiegu|alinco)[.:-]/i;

/** The full MOR-1076 evidence inventory, one disposition per key. */
export const LEGACY_KEY_ROUTING: readonly LegacyKeyRoute[] = [
  { key: 'rigplane:theme', disposition: 'migrate', note: 'canonical theme field (fallback source)' },
  { key: 'rigplane:theme-user-choice', disposition: 'migrate', note: 'canonical theme field (priority source: explicit user choice)' },
  { key: 'rigplane:vfo-theme', disposition: 'retain-outside', note: 'per-VFO theme override; no v1 field, owner-gate unresolved' },
  { key: 'rigplane-layout', disposition: 'migrate', note: 'canonical layout field; MOR-1042 aliases resolved by the validator' },
  { key: 'rigplane-skin', disposition: 'retire', note: 'dead write path, legacy read-only fallback' },
  { key: 'rigplane-lcd-display-mode', disposition: 'retain-outside', note: 'clean/vintage/crt/flicker is a distinct axis, no v1 field' },
  { key: 'rigplane:lcd-contrast', disposition: 'retain-outside', note: 'ambient-light setting, no v1 field' },
  { key: 'rigplane:panel-collapsed', disposition: 'retain-outside', note: 'panel-id vocabulary, not the 4-id SemanticSurfaceName enum' },
  { key: 'rigplane:panel-order', disposition: 'retain-outside', note: 'same vocabulary mismatch as panel-collapsed' },
  { key: 'rigplane:panel-order:known-defaults', disposition: 'retain-outside', note: 'internal bookkeeping, not a user preference' },
  { key: 'rigplane:right-panel-order', disposition: 'retain-outside', note: 'same vocabulary mismatch as panel-collapsed' },
  { key: 'rigplane:right-panel-order:known-defaults', disposition: 'retain-outside', note: 'internal bookkeeping, not a user preference' },
  { key: 'rigplane:memory-channels', disposition: 'retain-outside', note: 'radio data, not a UI/presentation preference' },
  { key: 'rigplane:install-dismissed', disposition: 'retire', note: 'PWA install nag, unrelated to workspace' },
  { key: 'rigplane.tuning-step-hz', disposition: 'retain-outside', note: 'operating preference, mirrors to a companion device' },
  { key: 'rigplane.tuning-step-auto', disposition: 'retain-outside', note: 'same reasoning as tuning-step-hz' },
  { key: 'rigplane.i18n.locale', disposition: 'retain-outside', note: 'stable cross-app locale contract' },
  { key: 'rigplane.i18n.proLocale.v1', disposition: 'retain-outside', note: 'cross-app contract jointly owned with Pro' },
  { key: 'rigplane:local-extension-dock-layout:v1', disposition: 'retain-outside', note: 'local-extension dock surface, not workspace' },
  { key: 'rigplane:auto-lan-mod-input', disposition: 'retain-outside', note: 'TX-safety-adjacent opt-in, must not enter workspace' },
  { key: 'rigplane:mod-input-tx-restore:v1', disposition: 'retire', note: 'dead key retained only for migration cleanup' },
  { key: 'rigplane-auth-token', disposition: 'forbidden', note: 'transport/session ownership' },
  { key: 'icom.audio.focus', disposition: 'retain-outside', note: 'manufacturer-prefixed but live; global flat key stays with the audio layer' },
  { key: 'icom.audio.split_stereo', disposition: 'retain-outside', note: 'same as icom.audio.focus' },
  { key: 'icom.audio.main_gain_db', disposition: 'retain-outside', note: 'same as icom.audio.focus' },
  { key: 'icom.audio.sub_gain_db', disposition: 'retain-outside', note: 'same as icom.audio.focus' },
  { key: 'eibi-favourites', disposition: 'retain-outside', note: 'station-data favourite, legacy component tree' },
  { key: 'rigplane-hidden-layers', disposition: 'retain-outside', note: 'legacy component tree, retire candidate' },
  { key: 'rigplane:storage-migrated-from-icom-lan', disposition: 'retire', note: 'internal migration sentinel, never workspace-visible' },
];

const ROUTING_BY_KEY: ReadonlyMap<string, LegacyKeyRoute> = new Map(LEGACY_KEY_ROUTING.map((r) => [r.key, r]));

/** `undefined` = not in the MOR-1076 inventory at all (out of MOR-1078's scope). */
export function classifyLegacyKey(key: string): LegacyKeyDisposition | undefined {
  return ROUTING_BY_KEY.get(key)?.disposition;
}

/** Legacy keys that fold into `WorkspaceV1.theme`, in priority order (index 0 wins). */
const THEME_SOURCE_KEYS = ['rigplane:theme-user-choice', 'rigplane:theme'] as const;
const LAYOUT_SOURCE_KEY = 'rigplane-layout';

/** Every key `snapshotLegacyStorage` will ever call `getItem` for — the read-only
 *  pin's enumerated closure (MOR-1078 constraint: "enumerated-closure pin if feasible"). */
export const LEGACY_MIGRATE_KEYS: readonly string[] = [...THEME_SOURCE_KEYS, LAYOUT_SOURCE_KEY];

// Structural guarantee, also asserted by a test: the migrate set carries no
// manufacturer-prefixed key today, so there is nothing to translate at read
// time — a future addition that violated this would fail loudly at import.
if (LEGACY_MIGRATE_KEYS.some((k) => MANUFACTURER_KEY_PATTERN.test(k))) {
  throw new Error('MOR-1078 invariant violated: a manufacturer-prefixed key entered the migrate set');
}

export type LegacyStorageSnapshot = Readonly<Record<string, string | null>>;

/** The ONLY place this module touches storage. `getItem`-only by type — a write
 *  call would not compile. Per-key try/catch: a throwing `getItem` (sandboxed
 *  contexts) degrades to "absent," matching the codebase's `migrate-legacy-
 *  storage.ts` resilience doctrine rather than blocking boot. */
export function snapshotLegacyStorage(storage: Pick<Storage, 'getItem'>): LegacyStorageSnapshot {
  const snapshot: Record<string, string | null> = {};
  for (const key of LEGACY_MIGRATE_KEYS) {
    try {
      snapshot[key] = storage.getItem(key);
    } catch {
      snapshot[key] = null;
    }
  }
  return snapshot;
}

/** Pure: snapshot in, a `WorkspaceV1`-shaped candidate input out. Never touches storage. */
export function buildWorkspaceInput(snapshot: LegacyStorageSnapshot): Record<string, unknown> {
  const input: Record<string, unknown> = { version: 1 };
  const theme = THEME_SOURCE_KEYS.map((k) => snapshot[k]).find((v) => v !== null && v !== undefined);
  if (theme !== undefined) input.theme = theme;
  const layout = snapshot[LAYOUT_SOURCE_KEY];
  if (layout !== null && layout !== undefined) input.layout = layout;
  return input;
}

/** Pure: snapshot in, a fully validated `WorkspaceReadResult` out. `readWorkspace`
 *  (MOR-1077) is the only construction path — never bypassed. */
export function readLegacyWorkspace(snapshot: LegacyStorageSnapshot): WorkspaceReadResult {
  return readWorkspace(buildWorkspaceInput(snapshot));
}

/** Convenience for real callers (MOR-1079): read + validate in one pass, read-only
 *  by construction (see `snapshotLegacyStorage`). */
export function readLegacyWorkspaceFromStorage(storage: Pick<Storage, 'getItem'>): WorkspaceReadResult {
  return readLegacyWorkspace(snapshotLegacyStorage(storage));
}
