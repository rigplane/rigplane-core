/**
 * MOR-1083 — workspace forward-compatibility, rollback and legacy-read-window
 * VERIFICATION suite.
 *
 * This file adds no production behaviour. It drives the REAL stack end to end
 * — `initWorkspaceStore` → `repository` → `legacy-readers` → `contract` —
 * against a fake `Storage`, across the eight evidence classes MOR-1083 names:
 *
 *   1. fresh install
 *   2. every known legacy snapshot (the 29-key MOR-1076 inventory)
 *   3. corrupt / partial stored state
 *   4. a future version (v+1, v+2, v+3)
 *   5. v1 export → import round trip
 *   6. downgrade during the compatibility window   ← THE ROLLBACK PROBE
 *   7. fallback after a failed write               ← NEVER-OVERWRITE-BEFORE-COMMIT
 *   8. no persisted capability / runtime / module-path field
 *
 * Pool: `fast` (MOR-1272). Storage is INJECTED, so there is no `vi.mock`, no
 * `vi.stubGlobal` and no `vi.resetModules()` — none of the order-dependent
 * shapes that force the workspace purity suites into the isolated pool. The
 * `afterEach` below returns the module-level store `$state` to defaults for
 * sibling files under `isolate: false`.
 *
 * Runbook: `docs/validation/workspace-v1-verification.md`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import {
  DEFAULT_WORKSPACE, WORKSPACE_FORWARD_READ_WINDOW, WORKSPACE_SCHEMA_VERSION,
  readWorkspace, serializeWorkspace,
} from '../contract';
import {
  WORKSPACE_MIGRATION_SENTINEL_KEY, WORKSPACE_STORAGE_KEY, loadWorkspace, persistWorkspace,
} from '../repository';
import { LEGACY_KEY_ROUTING, LEGACY_MIGRATE_KEYS } from '../legacy-readers';
import {
  exportWorkspace, getWorkspace, getWorkspaceNotice, importWorkspace, initWorkspaceStore,
  setDensity, setDesignLanguage, setLayout, setPinnedCommands, setTheme,
  setZoneOrder, setZoneVisibleSurfaces,
} from '../store.svelte';

// ─────────────────────────────────────────────────────────────────────────────
// Fake storage — full byte ledger, plus first-write failure injection.
// ─────────────────────────────────────────────────────────────────────────────

class LedgerStorage {
  readonly map = new Map<string, string>();
  /** Every `setItem` key, in order — including ones a failing write rejected. */
  readonly attemptedWrites: string[] = [];
  /** Every `setItem` key that actually landed. */
  readonly writes: string[] = [];
  readonly deletes: string[] = [];
  /** > 0 fails that many leading `setItem` calls, then behaves normally. */
  failFirstWrites = 0;
  throwOnSet = false;

  getItem(key: string): string | null {
    return this.map.has(key) ? this.map.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    this.attemptedWrites.push(key);
    if (this.throwOnSet || this.failFirstWrites > 0) {
      this.failFirstWrites = Math.max(0, this.failFirstWrites - 1);
      throw new Error('QuotaExceededError');
    }
    this.writes.push(key);
    this.map.set(key, value);
  }

  removeItem(key: string): void {
    this.deletes.push(key);
    this.map.delete(key);
  }

  clear(): void {
    this.deletes.push('*');
    this.map.clear();
  }

  snapshotOf(keys: Iterable<string>): Map<string, string | null> {
    return new Map([...keys].map((key) => [key, this.getItem(key)]));
  }

  stored(): Record<string, unknown> | null {
    const raw = this.getItem(WORKSPACE_STORAGE_KEY);
    if (raw === null) return null;
    return JSON.parse(raw) as Record<string, unknown>;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Planted forbidden content. Unique sentinels, so the class-8 sweep can prove
// by SUBSTRING that nothing from a forbidden source reached the stored bytes.
// ─────────────────────────────────────────────────────────────────────────────

const SENTINELS = {
  auth: 'MOR1083-AUTH-TOKEN-9f3a1c',
  capability: 'MOR1083-CAPABILITY-BLOB-4b7e',
  runtime: 'MOR1083-RUNTIME-STATE-2d05',
  modulePath: '$lib/components-v2/panels/MOR1083Probe.svelte',
  manufacturer: 'MOR1083-ICOM-POLICY-6a92',
  txSafety: 'MOR1083-TX-INTERLOCK-1f4d',
} as const;

const SENTINEL_VALUES: readonly string[] = Object.values(SENTINELS);

/** Realistic per-key legacy values. The three migrate keys get values the
 *  frozen v1 vocabulary actually accepts; forbidden/other keys get sentinels
 *  or a marker so class 8 has something to catch if it ever leaks. */
function legacyValueFor(key: string): string {
  switch (key) {
    case 'rigplane:theme-user-choice': return 'nord';
    case 'rigplane:theme': return 'dracula';
    case 'rigplane-layout': return 'lcd-scope';
    case 'rigplane-skin': return 'amber-lcd';
    case 'rigplane-auth-token': return SENTINELS.auth;
    case 'rigplane:vfo-theme': return 'lcd-warm';
    case 'rigplane:memory-channels': return '[{"name":"MOR1083"}]';
    case 'rigplane.tuning-step-hz': return '100';
    case 'rigplane.i18n.locale': return 'ru';
    case 'icom.audio.main_gain_db': return '-6';
    case 'icom.audio.sub_gain_db': return '-12';
    case 'icom.audio.focus': return 'main';
    case 'icom.audio.split_stereo': return 'true';
    default: return `legacy:${key}`;
  }
}

const ALL_LEGACY_KEYS: readonly string[] = LEGACY_KEY_ROUTING.map((route) => route.key);

function seedFullLegacy(storage: LedgerStorage): void {
  for (const key of ALL_LEGACY_KEYS) storage.map.set(key, legacyValueFor(key));
  // Forbidden content parked OUTSIDE the inventory too — nothing in the
  // workspace path may pick these up either.
  storage.map.set('rigplane:capabilities-cache', SENTINELS.capability);
  storage.map.set('rigplane:runtime-state', SENTINELS.runtime);
  storage.map.set('rigplane:component-module', SENTINELS.modulePath);
  storage.map.set('rigplane:manufacturer-policy', SENTINELS.manufacturer);
  storage.map.set('rigplane:tx-interlock', SENTINELS.txSafety);
}

/** Every non-workspace key the fixtures ever plant — the rollback ledger. */
function retainedKeys(storage: LedgerStorage): string[] {
  return [...storage.map.keys()].filter(
    (key) => key !== WORKSPACE_STORAGE_KEY && key !== WORKSPACE_MIGRATION_SENTINEL_KEY,
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// THE ROLLBACK READERS.
//
// The v2 (pre-workspace) read path, encoded as fixture expectations. Anchored
// on `frontend/src/lib/stores/layout.svelte.ts` and
// `frontend/src/components-v2/theme/theme-switcher.ts` AS THEY READ AT COMMIT
// 727b9efe (`feat(MOR-1079): single workspace store and persistence boundary`,
// the last commit before MOR-1081 turned both modules into workspace façades).
// A rollback build is a build at or before that commit, so these four
// functions ARE what it would do with the retained bytes.
//
// They are copies on purpose: importing the live modules would import today's
// façades — which read the WORKSPACE, not the legacy keys — and the probe
// would assert nothing. Drift is intentional and expected; a v3-side change to
// `normalizeLayoutMode` must NOT silently change what the rollback build does.
// ─────────────────────────────────────────────────────────────────────────────

/** 727b9efe layout.svelte.ts: `LEGACY_LAYOUT_ALIASES`. */
const ROLLBACK_LAYOUT_ALIASES: Readonly<Record<string, string>> = {
  lcd: 'lcd-cockpit',
  'amber-lcd': 'lcd-cockpit',
  spectrum: 'standard',
  'desktop-v2': 'standard',
};
/** 727b9efe layout.svelte.ts: `CANONICAL_LAYOUT_MODES`. */
const ROLLBACK_CANONICAL_LAYOUTS: readonly string[] = [
  'auto', 'lcd-cockpit', 'lcd-scope', 'standard', 'sdr-test',
];

/** 727b9efe layout.svelte.ts: `normalizeLayoutMode`. */
function rollbackNormalizeLayout(value: unknown): string {
  if (typeof value !== 'string') return 'auto';
  if (Object.hasOwn(ROLLBACK_LAYOUT_ALIASES, value)) return ROLLBACK_LAYOUT_ALIASES[value];
  return ROLLBACK_CANONICAL_LAYOUTS.includes(value) ? value : 'auto';
}

/** 727b9efe layout.svelte.ts: `loadMode()` — the workspace key is invisible to
 *  it, and `rigplane-skin` is the pre-#889 `??` fallback. */
function rollbackReadLayout(storage: LedgerStorage): string {
  const saved = storage.getItem('rigplane-layout') ?? storage.getItem('rigplane-skin');
  return rollbackNormalizeLayout(saved);
}

/** 727b9efe theme-switcher.ts: `getTheme()` — raw string, `||` default, no
 *  allow-list validation on the read side. */
function rollbackReadTheme(storage: LedgerStorage): string {
  return storage.getItem('rigplane:theme') || 'default';
}

/** 727b9efe theme-switcher.ts: `hasExplicitTheme()` — KEY PRESENCE, any value. */
function rollbackHasExplicitTheme(storage: LedgerStorage): boolean {
  return storage.getItem('rigplane:theme-user-choice') !== null;
}

/** 727b9efe theme-switcher.ts: `getVfoTheme()`. */
function rollbackReadVfoTheme(storage: LedgerStorage): string | null {
  return storage.getItem('rigplane:vfo-theme');
}

interface RollbackView {
  readonly layout: string;
  readonly theme: string;
  readonly explicitTheme: boolean;
  readonly vfoTheme: string | null;
}

/** Everything a rollback build would resolve out of the retained bytes. */
function rollbackView(storage: LedgerStorage): RollbackView {
  return {
    layout: rollbackReadLayout(storage),
    theme: rollbackReadTheme(storage),
    explicitTheme: rollbackHasExplicitTheme(storage),
    vfoTheme: rollbackReadVfoTheme(storage),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// The class-8 sweep: no forbidden content in the stored bytes, ever.
// ─────────────────────────────────────────────────────────────────────────────

/** Mirrors `contract.ts::FORBIDDEN_KEY_MARKERS` — the six frozen classes. */
const FORBIDDEN_MARKERS: readonly (readonly [string, RegExp])[] = [
  ['capabilities', /capabilit/],
  ['transport-session', /transport|session|token|auth|credential|websocket|socket|endpoint/],
  ['tx-resource-safety', /^tx|ptt|interlock|safety|inhibit|powerlimit|resource|lockout/],
  ['runtime-state', /^freq|smeter|swr|runtimestate|radiostate|livestate|rawstate/],
  ['manufacturer-policy', /icom|yaesu|kenwood|elecraft|xiegu|alinco|vendor|manufacturer|radiomodel|firmware/],
  ['component-module-path', /^$/],
] as const;
const MODULE_PATH_VALUE = /^(\.{1,2}\/|\$lib\/|src\/|\/)|\.(svelte|ts|js)$/;

/** The frozen v1 vocabulary. A key from this set is schema, not payload, so it
 *  is exempt from the KEY marker scan (`txAux` is a SemanticSurfaceName;
 *  `rx-tx` is a declared zone id). Values are still scanned. */
const FROZEN_VOCABULARY: ReadonlySet<string> = new Set([
  'version', 'layout', 'designLanguage', 'theme', 'density',
  'visibleSurfaces', 'zoneOrder', 'pinnedCommands',
  'main', 'receiver-deck', 'rx-tx', 'primary-vfo', 'secondary-vfo', 'global',
  'portrait-deck', 'control-column',
]);

function normalizeKey(key: string): string {
  return key.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Every forbidden hit in a parsed stored object, as `field:class` strings. */
function forbiddenHits(value: unknown, key = '', path = ''): string[] {
  const hits: string[] = [];
  const here = path === '' ? key : `${path}.${key}`;
  if (key !== '' && !FROZEN_VOCABULARY.has(key)) {
    const normalized = normalizeKey(key);
    for (const [name, pattern] of FORBIDDEN_MARKERS) {
      if (pattern.source !== '^$' && pattern.test(normalized)) hits.push(`${here}:${name}`);
    }
  }
  if (typeof value === 'string') {
    if (MODULE_PATH_VALUE.test(value)) hits.push(`${here}:component-module-path`);
  } else if (Array.isArray(value)) {
    value.forEach((item, index) => hits.push(...forbiddenHits(item, String(index), here)));
  } else if (typeof value === 'object' && value !== null) {
    for (const [k, v] of Object.entries(value)) hits.push(...forbiddenHits(v, k, here));
  }
  return hits;
}

/** The class-8 assertion, run at the end of every scenario. */
function expectNoForbiddenBytes(storage: LedgerStorage, label: string): void {
  const raw = storage.getItem(WORKSPACE_STORAGE_KEY);
  if (raw === null) return;
  for (const sentinel of SENTINEL_VALUES) {
    expect(`${label}: ${raw}`).not.toContain(sentinel);
  }
  expect({ label, hits: forbiddenHits(storage.stored()) }).toEqual({ label, hits: [] });
}

afterEach(() => {
  initWorkspaceStore(null);
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 1 — fresh install
// ═══════════════════════════════════════════════════════════════════════════

describe('MOR-1083 class 1 — fresh install', () => {
  it('boots to the frozen defaults, writes one object and one sentinel', () => {
    const storage = new LedgerStorage();

    initWorkspaceStore(storage);

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()).toBeNull();
    expect(storage.writes).toEqual([WORKSPACE_STORAGE_KEY, WORKSPACE_MIGRATION_SENTINEL_KEY]);
    expect(storage.deletes).toEqual([]);
    expectNoForbiddenBytes(storage, 'fresh-install');
  });

  it('omits `theme` so a skin default stays reachable, and a reboot agrees', () => {
    const storage = new LedgerStorage();

    initWorkspaceStore(storage);
    expect(storage.stored()).not.toHaveProperty('theme');

    initWorkspaceStore(storage);
    expect(getWorkspace().theme).toBe('default');
    expect(loadWorkspace(storage).themeChosen).toBe(false);
  });

  it('a rollback build sees exactly the pristine v2 view — nothing was invented', () => {
    const storage = new LedgerStorage();

    initWorkspaceStore(storage);
    setLayout('lcd-scope');
    setTheme('nord', true);

    expect(rollbackView(storage)).toEqual({
      layout: 'auto', theme: 'default', explicitTheme: false, vfoTheme: null,
    });
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 2 — every known legacy snapshot (the 29-key MOR-1076 inventory)
// ═══════════════════════════════════════════════════════════════════════════

describe('MOR-1083 class 2 — the 29-key legacy inventory, key by key', () => {
  it('the inventory is the 29 keys MOR-1078 routed, migrate set = 3', () => {
    expect(ALL_LEGACY_KEYS).toHaveLength(29);
    expect(new Set(ALL_LEGACY_KEYS).size).toBe(29);
    expect([...LEGACY_MIGRATE_KEYS].sort()).toEqual(
      ['rigplane-layout', 'rigplane:theme', 'rigplane:theme-user-choice'].sort(),
    );
  });

  it.each(LEGACY_KEY_ROUTING.map((route) => [route.key, route.disposition] as const))(
    'seeding only %s (%s) influences the workspace iff it is a migrate key',
    (key, disposition) => {
      const storage = new LedgerStorage();
      storage.map.set(key, legacyValueFor(key));
      const before = storage.snapshotOf([key]);

      initWorkspaceStore(storage);

      if (disposition === 'migrate') {
        expect(getWorkspace()).not.toEqual(DEFAULT_WORKSPACE);
      } else {
        // Includes the pre-#889 `rigplane-skin` drop: routed `retire`, so it
        // must NOT resurrect a layout the v3 build never migrated.
        expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
      }
      // Whatever the disposition, the bytes are retained verbatim.
      expect(storage.snapshotOf([key])).toEqual(before);
      expect(storage.deletes).toEqual([]);
      expectNoForbiddenBytes(storage, `single-key:${key}`);
    },
  );

  it('a FULL modern snapshot migrates layout + theme and retains all 29 keys', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);
    const before = storage.snapshotOf(retainedKeys(storage));

    initWorkspaceStore(storage);

    // theme-user-choice wins over theme (priority index 0).
    expect(getWorkspace().theme).toBe('nord');
    expect(getWorkspace().layout).toBe('lcd-scope');
    expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);
    expectNoForbiddenBytes(storage, 'full-modern');
  });

  it('a legacy-ALIAS snapshot resolves through the MOR-1042 alias table', () => {
    const storage = new LedgerStorage();
    storage.map.set('rigplane-layout', 'amber-lcd');
    storage.map.set('rigplane:theme', 'tokyo-night');

    initWorkspaceStore(storage);

    expect(getWorkspace().layout).toBe('lcd-cockpit');
    expect(getWorkspace().theme).toBe('tokyo-night');
    expect(storage.getItem('rigplane-layout')).toBe('amber-lcd');
  });

  it('a PARTIAL snapshot fills only what it carries', () => {
    const storage = new LedgerStorage();
    storage.map.set('rigplane:theme', 'gruvbox-dark');

    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('gruvbox-dark');
    expect(getWorkspace().layout).toBe(DEFAULT_WORKSPACE.layout);
    expect(getWorkspace().density).toBe(DEFAULT_WORKSPACE.density);
  });

  it('theme-user-choice takes precedence, and its presence latches explicitness', () => {
    const storage = new LedgerStorage();
    storage.map.set('rigplane:theme', 'dracula');
    storage.map.set('rigplane:theme-user-choice', 'crt-green');

    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('crt-green');
    expect(storage.stored()).toHaveProperty('theme', 'crt-green');
    // Both legacy theme keys keep their own, DIFFERENT values for rollback.
    expect(rollbackReadTheme(storage)).toBe('dracula');
    expect(rollbackHasExplicitTheme(storage)).toBe(true);
  });

  it('a MANUFACTURER-prefixed snapshot never enters the workspace', () => {
    const storage = new LedgerStorage();
    for (const key of ALL_LEGACY_KEYS.filter((k) => k.startsWith('icom.'))) {
      storage.map.set(key, legacyValueFor(key));
    }
    storage.map.set('icom-lan:theme', 'nord');
    storage.map.set('icom-lan-layout', 'lcd-scope');

    initWorkspaceStore(storage);

    // Pre-v2 `icom-lan:*` keys are `migrate-legacy-storage.ts`'s job, not the
    // workspace's — the workspace reads only the four `rigplane*` migrate keys.
    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(storage.getItem('icom-lan:theme')).toBe('nord');
    expectNoForbiddenBytes(storage, 'manufacturer-prefixed');
  });

  it('the forbidden auth-token key is never read and never re-emitted', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);

    initWorkspaceStore(storage);
    setTheme('nord', true);
    setPinnedCommands(['set_compressor', 'tx_power', 'auth_refresh', 'icom_policy']);

    // `tx_power` / `auth_refresh` / `icom_policy` hit the forbidden markers.
    expect(getWorkspace().pinnedCommands).toEqual(['set_compressor']);
    expect(storage.getItem('rigplane-auth-token')).toBe(SENTINELS.auth);
    expectNoForbiddenBytes(storage, 'forbidden-auth');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 3 — corrupt / partial stored state
// ═══════════════════════════════════════════════════════════════════════════

describe('MOR-1083 class 3 — corrupt and partial stored state', () => {
  it.each([
    ['unparsable JSON', '{{{'],
    ['truncated JSON', '{"version":1,"theme":"no'],
    ['empty bytes', ''],
    ['a JSON scalar', '"nope"'],
    ['a JSON array', '[1,2,3]'],
    ['JSON null', 'null'],
    ['whitespace', '   '],
  ])('%s resets to defaults with a visible notice, never a throw', (_label, raw) => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);
    storage.map.set(WORKSPACE_STORAGE_KEY, raw);
    const before = storage.snapshotOf(retainedKeys(storage));

    expect(() => { initWorkspaceStore(storage); }).not.toThrow();

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()?.kind).toBe('reset');
    // A corrupt workspace object does NOT re-open the migration, and does not
    // touch the retained legacy bytes.
    expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);
    expect(storage.deletes).toEqual([]);
  });

  it('a partial object repairs field by field and reports every rejection', () => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      version: 1,
      theme: 'no-such-theme',
      layout: 'no-such-layout',
      density: 'ultra',
      visibleSurfaces: 'not-a-map',
      zoneOrder: { 'no-such-zone': ['vfo'] },
      pinnedCommands: 'not-an-array',
    }));

    initWorkspaceStore(storage);

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()?.kind).toBe('repaired');
    expect(getWorkspaceNotice()?.rejections.map((r) => r.field).sort()).toEqual(
      ['density', 'layout', 'pinnedCommands', 'theme', 'visibleSurfaces', 'zoneOrder.no-such-zone'],
    );
    expectNoForbiddenBytes(storage, 'partial-repair');
  });

  it('a cross-zone duplicate is refused rather than moved', () => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      version: 1,
      zoneOrder: { main: ['vfo', 'rxTx'], 'rx-tx': ['vfo'] },
    }));

    initWorkspaceStore(storage);

    expect(getWorkspace().zoneOrder).toEqual({ main: ['vfo', 'rxTx'], 'rx-tx': [] });
    expect(getWorkspaceNotice()?.rejections).toContainEqual({ field: 'zoneOrder.rx-tx', reason: 'cross-zone' });
  });

  it('a hostile storage whose getItem throws still boots to defaults', () => {
    const hostile = {
      getItem(): string { throw new Error('SecurityError'); },
      setItem(): void { throw new Error('SecurityError'); },
    };

    expect(() => { initWorkspaceStore(hostile); }).not.toThrow();
    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 4 — future versions and the N=2 freeze
// ═══════════════════════════════════════════════════════════════════════════

const FUTURE_FIELDS = { futureField: { nested: [1, 2] }, anotherFuture: 'plain-value' };

describe('MOR-1083 class 4 — the forward-read window (N=2)', () => {
  it('the window is exactly current+2', () => {
    expect(WORKSPACE_SCHEMA_VERSION).toBe(1);
    expect(WORKSPACE_FORWARD_READ_WINDOW).toBe(2);
  });

  it.each([2, 3])('v%i lossless: read, update and write back UN-DOWNGRADED', (version) => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      ...DEFAULT_WORKSPACE, version, theme: 'nord', ...FUTURE_FIELDS,
    }));

    initWorkspaceStore(storage);
    expect(getWorkspace().version).toBe(version);
    expect(getWorkspaceNotice()).toBeNull();

    setDensity('compact');

    const written = storage.stored()!;
    expect(written.version).toBe(version);
    expect(written.futureField).toEqual(FUTURE_FIELDS.futureField);
    expect(written.anotherFuture).toBe('plain-value');
    expect(written.density).toBe('compact');
    expect(written.theme).toBe('nord');
    expectNoForbiddenBytes(storage, `forward-lossless-v${version}`);
  });

  it.each([2, 3])('v%i LOSSY: latched read-only — no update may ever overwrite it', (version) => {
    const storage = new LedgerStorage();
    const original = JSON.stringify({
      ...DEFAULT_WORKSPACE, version, theme: `v${version}-only-theme`, ...FUTURE_FIELDS,
    });
    storage.map.set(WORKSPACE_STORAGE_KEY, original);

    initWorkspaceStore(storage);
    expect(getWorkspaceNotice()?.kind).toBe('forward-read-only');

    // The latch must survive an update that repairs the offending value away.
    setDensity('compact');
    setTheme('nord', true);
    setLayout('lcd-scope');
    setDesignLanguage('fieldline');

    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(original);
    expect(storage.writes).toEqual([]);
    expect(getWorkspaceNotice()?.kind).toBe('forward-read-only');
  });

  it('a lossy forward read can only be escaped by an explicit whole-object reset/import', () => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ version: 3, theme: 'v3-only' }));

    initWorkspaceStore(storage);
    expect(storage.writes).toEqual([]);

    const accepted = importWorkspace(JSON.stringify({ version: 1, theme: 'nord' }));

    expect(accepted.outcome).toBe('ok');
    expect(storage.stored()).toMatchObject({ version: 1, theme: 'nord' });
    expect(getWorkspaceNotice()).toBeNull();
  });

  it.each([0, 4, 99, 1.5, 'two', null, true])(
    'v%s is outside the window: discarded visibly, never silently downgraded',
    (version) => {
      const storage = new LedgerStorage();
      const original = JSON.stringify({ ...DEFAULT_WORKSPACE, version, theme: 'nord' });
      storage.map.set(WORKSPACE_STORAGE_KEY, original);

      initWorkspaceStore(storage);

      expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
      expect(getWorkspaceNotice()?.kind).toBe('version-discarded');
      expect(getWorkspaceNotice()?.discardedVersion).toBe(version);
      // A discard is NOT a write: the unreadable object is left for whichever
      // build can read it.
      expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(original);
    },
  );

  it('DOWNGRADE round trip: v3 writes, this build reads it, v3 reads it back whole', () => {
    // Stand-in for the newer build: an object with a v3 field this build has
    // no schema for, plus a v3 value in a KNOWN field it can represent.
    const storage = new LedgerStorage();
    const v3Object = { ...DEFAULT_WORKSPACE, version: 3, theme: 'nord', v3Only: { a: [1, { b: 2 }] } };
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify(v3Object));

    initWorkspaceStore(storage);
    setLayout('lcd-scope');

    const afterOlderBuild = storage.stored()!;
    expect(afterOlderBuild.version).toBe(3);
    expect(afterOlderBuild.v3Only).toEqual(v3Object.v3Only);
    expect(afterOlderBuild.layout).toBe('lcd-scope');
    // What the newer build would then see: everything it wrote, plus the one
    // field the older build legitimately changed.
    expect(Object.keys(afterOlderBuild).sort()).toEqual(
      [...Object.keys(v3Object)].sort(),
    );
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 5 — v1 export / import round trip
// ═══════════════════════════════════════════════════════════════════════════

describe('MOR-1083 class 5 — export / import round trip', () => {
  it('a v1 export re-imported into the SAME build is byte-stable', () => {
    const storage = new LedgerStorage();
    initWorkspaceStore(storage);
    setTheme('nord', true);
    setLayout('lcd-scope');
    setDesignLanguage('fieldline');
    setDensity('compact');
    setZoneVisibleSurfaces('main', ['vfo', 'rxTx']);
    setZoneOrder('main', ['rxTx', 'vfo']);
    setPinnedCommands(['set_compressor', 'set_monitor']);

    const first = JSON.stringify(exportWorkspace());
    const storedBefore = storage.getItem(WORKSPACE_STORAGE_KEY);

    const result = importWorkspace(first);

    expect(result.outcome).toBe('ok');
    expect(JSON.stringify(exportWorkspace())).toBe(first);
    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(storedBefore);
    expectNoForbiddenBytes(storage, 'export-import');
  });

  it('a forward-read export carries its unknown fields through the round trip', () => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      ...DEFAULT_WORKSPACE, version: 2, theme: 'nord', ...FUTURE_FIELDS,
    }));
    initWorkspaceStore(storage);

    const exported = JSON.stringify(exportWorkspace());
    const result = importWorkspace(exported);

    expect(result.outcome).toBe('forward-read');
    expect(JSON.stringify(exportWorkspace())).toBe(exported);
  });

  it('an import carrying forbidden content is refused whole — nothing commits', () => {
    const storage = new LedgerStorage();
    initWorkspaceStore(storage);
    setTheme('nord', true);
    const before = storage.getItem(WORKSPACE_STORAGE_KEY);

    const result = importWorkspace(JSON.stringify({
      version: 1,
      theme: 'crt-green',
      capabilities: { hasScope: true, blob: SENTINELS.capability },
      componentPath: SENTINELS.modulePath,
    }));

    expect(result.rejections.map((r) => r.reason).sort()).toEqual(
      ['capabilities', 'component-module-path'],
    );
    expect(getWorkspace().theme).toBe('nord');
    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(before);
    expectNoForbiddenBytes(storage, 'import-forbidden');
  });

  it.each([
    ['unparsable', '{{{'],
    ['a scalar', '42'],
    ['an array', '[]'],
    ['empty', ''],
  ])('a malformed import (%s) is refused and never throws', (_label, text) => {
    const storage = new LedgerStorage();
    initWorkspaceStore(storage);
    setTheme('nord', true);
    const before = storage.getItem(WORKSPACE_STORAGE_KEY);

    expect(() => importWorkspace(text)).not.toThrow();

    expect(getWorkspace().theme).toBe('nord');
    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(before);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 6 — THE ROLLBACK PROBE
//
// Acceptance heart: "the ROLLBACK build can read retained legacy state".
// After a v3 boot + migration + a full round of updates, every legacy key's
// bytes must still resolve, under the 727b9efe read semantics above, to
// exactly what they resolved to before v3 ever ran.
// ═══════════════════════════════════════════════════════════════════════════

interface RollbackCase {
  readonly name: string;
  readonly seed: (storage: LedgerStorage) => void;
  readonly expected: RollbackView;
}

const ROLLBACK_CASES: readonly RollbackCase[] = [
  {
    name: 'fresh install',
    seed: () => {},
    expected: { layout: 'auto', theme: 'default', explicitTheme: false, vfoTheme: null },
  },
  {
    name: 'full modern snapshot (all 29 keys)',
    seed: seedFullLegacy,
    expected: { layout: 'lcd-scope', theme: 'dracula', explicitTheme: true, vfoTheme: 'lcd-warm' },
  },
  {
    name: 'legacy alias (amber-lcd)',
    seed: (s) => { s.map.set('rigplane-layout', 'amber-lcd'); s.map.set('rigplane:theme', 'nord'); },
    expected: { layout: 'lcd-cockpit', theme: 'nord', explicitTheme: false, vfoTheme: null },
  },
  {
    name: 'partial (theme only)',
    seed: (s) => { s.map.set('rigplane:theme', 'gruvbox-dark'); },
    expected: { layout: 'auto', theme: 'gruvbox-dark', explicitTheme: false, vfoTheme: null },
  },
  {
    name: 'pre-#889 rigplane-skin fallback only',
    seed: (s) => { s.map.set('rigplane-skin', 'amber-lcd'); },
    // The v3 build drops it (routed `retire`), but the ROLLBACK build's `??`
    // fallback still finds it — which is precisely why it must not be deleted.
    expected: { layout: 'lcd-cockpit', theme: 'default', explicitTheme: false, vfoTheme: null },
  },
  {
    name: 'theme-user-choice precedence (two different theme keys)',
    seed: (s) => {
      s.map.set('rigplane:theme', 'dracula');
      s.map.set('rigplane:theme-user-choice', 'crt-green');
    },
    expected: { layout: 'auto', theme: 'dracula', explicitTheme: true, vfoTheme: null },
  },
  {
    name: 'manufacturer-prefixed only',
    seed: (s) => {
      for (const key of ALL_LEGACY_KEYS.filter((k) => k.startsWith('icom.'))) {
        s.map.set(key, legacyValueFor(key));
      }
    },
    expected: { layout: 'auto', theme: 'default', explicitTheme: false, vfoTheme: null },
  },
  {
    name: 'corrupt workspace object over a full legacy snapshot',
    seed: (s) => { seedFullLegacy(s); s.map.set(WORKSPACE_STORAGE_KEY, '{{{'); },
    expected: { layout: 'lcd-scope', theme: 'dracula', explicitTheme: true, vfoTheme: 'lcd-warm' },
  },
  {
    name: 'empty workspace bytes over a full legacy snapshot',
    seed: (s) => { seedFullLegacy(s); s.map.set(WORKSPACE_STORAGE_KEY, ''); },
    expected: { layout: 'lcd-scope', theme: 'dracula', explicitTheme: true, vfoTheme: 'lcd-warm' },
  },
  {
    name: 'out-of-window future object over a full legacy snapshot',
    seed: (s) => {
      seedFullLegacy(s);
      s.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ version: 42, theme: 'v42' }));
    },
    expected: { layout: 'lcd-scope', theme: 'dracula', explicitTheme: true, vfoTheme: 'lcd-warm' },
  },
];

/** A full round of every semantic action the store exposes. */
function exerciseStore(): void {
  setLayout('standard');
  setTheme('crt-green', true);
  setDesignLanguage('fieldline');
  setDensity('compact');
  setZoneVisibleSurfaces('main', ['vfo', 'rxTx']);
  setZoneOrder('main', ['rxTx', 'vfo']);
  setPinnedCommands(['set_compressor']);
}

describe('MOR-1083 class 6 — THE ROLLBACK PROBE', () => {
  it.each(ROLLBACK_CASES.map((c) => [c.name, c] as const))(
    'after v3 boot + migration + updates, a rollback build still reads: %s',
    (_name, testCase) => {
      const storage = new LedgerStorage();
      testCase.seed(storage);
      const before = storage.snapshotOf(retainedKeys(storage));
      const viewBefore = rollbackView(storage);

      initWorkspaceStore(storage);
      exerciseStore();
      // Reboot and exercise again — a second generation must not drift either.
      initWorkspaceStore(storage);
      exerciseStore();

      // (a) the documented v2 read semantics still resolve, unchanged.
      expect(viewBefore).toEqual(testCase.expected);
      expect(rollbackView(storage)).toEqual(testCase.expected);
      // (b) byte-for-byte, not merely semantically.
      expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);
      // (c) nothing was ever deleted, and every key written is one of the two
      //     the workspace owns. (Subset, not equality: the sentinel is written
      //     only on the migrating boot — a present-but-unreadable workspace key
      //     takes the `stored` branch and never reaches the migration. See the
      //     characterization pin below.)
      expect(storage.deletes).toEqual([]);
      const owned = new Set([WORKSPACE_STORAGE_KEY, WORKSPACE_MIGRATION_SENTINEL_KEY]);
      expect(storage.attemptedWrites.filter((key) => !owned.has(key))).toEqual([]);
      expectNoForbiddenBytes(storage, `rollback:${testCase.name}`);
    },
  );

  /**
   * CHARACTERIZATION — MOR-1083 finding F1 (informational, NOT a failure).
   *
   * `loadWorkspace` branches on PRESENCE of the workspace key, before it looks
   * at readability. So a present-but-unreadable object short-circuits to
   * `source: 'stored'` + `outcome: 'reset'` and the legacy migration never runs
   * — even though the sentinel says it never ran and the legacy bytes are still
   * sitting there, fully readable.
   *
   * The MOR-1083 acceptance still HOLDS: the legacy bytes are retained
   * untouched (asserted below), so the rollback build recovers everything and
   * nothing recoverable was overwritten before a successful commit. What is
   * lost is only the v3-side convenience of re-folding them. Pinned here so a
   * future change to that ordering is a deliberate decision, not a surprise.
   */
  it('F1: a corrupt workspace object short-circuits the migration, but retains legacy bytes', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);
    storage.map.set(WORKSPACE_STORAGE_KEY, '{{{');
    const before = storage.snapshotOf(retainedKeys(storage));

    initWorkspaceStore(storage);

    // The migrated values are NOT recovered, despite the legacy keys being present...
    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()?.kind).toBe('reset');
    expect(storage.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBeNull();
    // ...but every legacy byte survives, so the rollback build is unaffected
    // and the input remains recoverable.
    expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);
    expect(rollbackView(storage)).toEqual({
      layout: 'lcd-scope', theme: 'dracula', explicitTheme: true, vfoTheme: 'lcd-warm',
    });

    // And clearing the unreadable object lets the migration run after all.
    storage.map.delete(WORKSPACE_STORAGE_KEY);
    storage.deletes.length = 0;
    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('nord');
    expect(getWorkspace().layout).toBe('lcd-scope');
  });

  it('no legacy key is ever a write target, across the whole inventory', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);

    initWorkspaceStore(storage);
    exerciseStore();
    importWorkspace(JSON.stringify({ version: 1, theme: 'nord' }));

    for (const key of ALL_LEGACY_KEYS) {
      expect(storage.attemptedWrites).not.toContain(key);
      expect(storage.deletes).not.toContain(key);
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 7 — NEVER OVERWRITE RECOVERABLE INPUT BEFORE A SUCCESSFUL COMMIT
// ═══════════════════════════════════════════════════════════════════════════

describe('MOR-1083 class 7 — never-overwrite-before-commit', () => {
  it('a failed FIRST write leaves every legacy key untouched and re-migrates next boot', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);
    const before = storage.snapshotOf(retainedKeys(storage));

    storage.failFirstWrites = 1;
    initWorkspaceStore(storage);

    // The migration produced a usable in-memory workspace...
    expect(getWorkspace().theme).toBe('nord');
    expect(getWorkspace().layout).toBe('lcd-scope');
    // ...but nothing was committed, and the sentinel was NOT set.
    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBeNull();
    expect(storage.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBeNull();
    expect(storage.writes).toEqual([]);
    expect(storage.deletes).toEqual([]);
    expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);

    // Next boot, storage healthy: the recoverable input is still there and the
    // very same workspace is produced and THEN committed.
    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('nord');
    expect(getWorkspace().layout).toBe('lcd-scope');
    expect(storage.writes).toEqual([WORKSPACE_STORAGE_KEY, WORKSPACE_MIGRATION_SENTINEL_KEY]);
    expect(storage.snapshotOf(retainedKeys(storage))).toEqual(before);
    expectNoForbiddenBytes(storage, 'failed-first-write');
  });

  it('the sentinel is never written when the workspace write failed', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);
    storage.throwOnSet = true;

    initWorkspaceStore(storage);

    expect(storage.attemptedWrites).toEqual([WORKSPACE_STORAGE_KEY]);
    expect(storage.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBeNull();
  });

  it('a failed UPDATE write leaves the previously committed object byte-identical', () => {
    const storage = new LedgerStorage();
    initWorkspaceStore(storage);
    setTheme('nord', true);
    const committed = storage.getItem(WORKSPACE_STORAGE_KEY)!;

    storage.throwOnSet = true;
    setTheme('crt-green', true);
    setLayout('sdr-test');

    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBe(committed);
    expect(getWorkspaceNotice()?.kind).toBe('persist-failed');
    // In-memory state is still usable — a failed write does not corrupt it.
    expect(getWorkspace().theme).toBe('crt-green');
    expect(getWorkspace().layout).toBe('sdr-test');
  });

  it('a write is a single atomic setItem — a partial multi-key state is impossible', () => {
    const storage = new LedgerStorage();
    initWorkspaceStore(storage);
    storage.writes.length = 0;
    storage.attemptedWrites.length = 0;

    exerciseStore();

    expect(new Set(storage.attemptedWrites)).toEqual(new Set([WORKSPACE_STORAGE_KEY]));
    expect(storage.attemptedWrites).toHaveLength(7);
  });

  it('a serialization failure writes nothing at all (repository level)', () => {
    const storage = new LedgerStorage();
    const cyclic: Record<string, unknown> = { version: 1 };
    cyclic.self = cyclic;
    const result = readWorkspace({ version: 1, theme: 'nord' });
    // Force `JSON.stringify` to throw inside `persistWorkspace` by handing it a
    // preserved field that cannot be serialized.
    const withCycle = { ...result, preserved: { cyclic } };

    expect(persistWorkspace(storage, withCycle)).toBe(false);
    expect(storage.writes).toEqual([]);
    expect(storage.getItem(WORKSPACE_STORAGE_KEY)).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// CLASS 8 — forbidden-fields sweep over the serialized bytes
// ═══════════════════════════════════════════════════════════════════════════

const FORBIDDEN_INPUTS: readonly (readonly [string, Record<string, unknown>])[] = [
  ['capabilities', { capabilities: { hasScope: true }, capabilityBlob: SENTINELS.capability }],
  ['runtime-state', { freqHz: 14074000, sMeter: 7, runtimeState: SENTINELS.runtime }],
  ['manufacturer-policy', { icomPolicy: SENTINELS.manufacturer, firmware: '1.30' }],
  ['component-module-path', { surfaceComponent: SENTINELS.modulePath, other: './Panel.svelte' }],
  ['transport-session', { authToken: SENTINELS.auth, websocketEndpoint: 'wss://x' }],
  ['tx-resource-safety', { txInterlock: SENTINELS.txSafety, pttLockout: true }],
];

describe('MOR-1083 class 8 — no forbidden content is ever persisted', () => {
  it.each(FORBIDDEN_INPUTS.map(([name, fields]) => [name, fields] as const))(
    'a stored object carrying %s content is stripped before writeback',
    (name, fields) => {
      const storage = new LedgerStorage();
      storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ version: 1, theme: 'nord', ...fields }));

      initWorkspaceStore(storage);
      setDensity('compact');

      expect(getWorkspaceNotice()?.kind).toBe('repaired');
      for (const key of Object.keys(fields)) expect(storage.stored()).not.toHaveProperty(key);
      expectNoForbiddenBytes(storage, `forbidden:${name}`);
    },
  );

  it('a BENIGN unknown field IS preserved — the sweep is not a blanket strip', () => {
    const storage = new LedgerStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      version: 1, theme: 'nord', operatorNote: 'field day', futureCounter: 3,
    }));

    initWorkspaceStore(storage);
    setDensity('compact');

    expect(storage.stored()).toMatchObject({ operatorNote: 'field day', futureCounter: 3 });
  });

  it('every scenario in this file leaves clean bytes: the frozen key set only', () => {
    const storage = new LedgerStorage();
    seedFullLegacy(storage);

    initWorkspaceStore(storage);
    exerciseStore();

    expect(Object.keys(storage.stored()!).sort()).toEqual([
      'density', 'designLanguage', 'layout', 'pinnedCommands', 'theme',
      'version', 'visibleSurfaces', 'zoneOrder',
    ]);
    expectNoForbiddenBytes(storage, 'frozen-key-set');
  });

  it('the serialized default carries no forbidden marker either', () => {
    expect(forbiddenHits(serializeWorkspace(readWorkspace(DEFAULT_WORKSPACE)))).toEqual([]);
  });
});
