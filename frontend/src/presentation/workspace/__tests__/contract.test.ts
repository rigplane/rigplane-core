/**
 * MOR-1077 — workspace v1 schema/validator/defaults/fallback, one describe
 * block per MOR-1076 decision plus the registry-sync pins that stand in for
 * the live imports this zone cannot make (see `../contract.ts`'s header).
 *
 * Fast pool on purpose: this file stubs no global and mocks no module, so it
 * is order-independent under `isolate: false` (MOR-1272). The registry
 * imports below DO fire `registerLayout`/`registerDesignLanguage`, which is
 * exactly why they live here and not in `purity.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  DEFAULT_WORKSPACE, WORKSPACE_DENSITY_CLAMP, WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS,
  WORKSPACE_SCHEMA_VERSION, WORKSPACE_THEME_IDS, WORKSPACE_ZONE_IDS, normalizeWorkspaceLayoutId,
  readWorkspace, readWorkspaceJson, serializeWorkspace, workspaceLayoutManifestId,
  type WorkspaceForbiddenClass, type WorkspaceV1,
} from '../contract';
// Registry-sync sources. Each is imported ONLY to prove the pinned literal
// still matches the live declaration — never used by production code here.
import * as layoutDeclarations from '../../layouts/declarations';
import type { LayoutManifest } from '../../layouts/contract';
import { fieldline, studioline } from '../../languages/declarations';
import { getAvailableThemes } from '../../../components-v2/theme/theme-switcher';

/**
 * The layout id space is synced from SOURCE TEXT, not an import: `lib/stores/
 * layout.svelte.ts` reads `localStorage` at module scope and throws in a Node
 * env without one — the same reason `presentation/layouts/__tests__/
 * loader-identity-inventory.test.ts` reads specifiers as text rather than
 * importing the modules that hold them.
 */
const LAYOUT_STORE_SOURCE = readFileSync('src/lib/stores/layout.svelte.ts', 'utf8');
function block(marker: string, open: string, close: string): string {
  const start = LAYOUT_STORE_SOURCE.indexOf(marker);
  expect(start).toBeGreaterThan(-1);
  const from = LAYOUT_STORE_SOURCE.indexOf(open, start);
  return LAYOUT_STORE_SOURCE.slice(from, LAYOUT_STORE_SOURCE.indexOf(close, from));
}
const LIVE_CANONICAL_MODES = [...block('CANONICAL_LAYOUT_MODES', '[', ']').matchAll(/'([^']+)'/g)].map((m) => m[1]);
const LIVE_ALIASES = [...block('LEGACY_LAYOUT_ALIASES', '{', '}').matchAll(/'?([\w-]+)'?:\s*'([^']+)'/g)].map((m) => [m[1], m[2]] as const);

const VALID: WorkspaceV1 = {
  version: 1,
  layout: 'lcd-cockpit',
  designLanguage: 'fieldline',
  theme: 'nord',
  density: 'compact',
  visibleSurfaces: { 'control-column': ['vfo'] },
  zoneOrder: { 'rx-tx': ['rxTx'] },
  pinnedCommands: ['set_compressor', 'set_monitor_gain'],
};

describe('registry sync — the pinned id spaces still match their live owners', () => {
  it('layout ids are exactly the store\'s CanonicalLayoutMode set', () => {
    // Kills: a new CanonicalLayoutMode landing in the store without a pin here.
    expect([...WORKSPACE_LAYOUT_IDS].sort()).toEqual([...LIVE_CANONICAL_MODES].sort());
    for (const id of WORKSPACE_LAYOUT_IDS) expect(normalizeWorkspaceLayoutId(id)).toBe(id);
    // The QA-only cockpit id must stay unpersistable (MOR-1257).
    expect(LIVE_CANONICAL_MODES).not.toContain('dual-receiver-cockpit');
    expect(normalizeWorkspaceLayoutId('dual-receiver-cockpit')).toBe('auto');
  });

  it('the MOR-1042 alias table is mirrored exactly', () => {
    expect(LIVE_ALIASES.length).toBe(4);
    for (const [alias, canonical] of LIVE_ALIASES) expect(normalizeWorkspaceLayoutId(alias)).toBe(canonical);
  });

  it('design-language ids and their density clamps match the live manifests', () => {
    expect([...WORKSPACE_DESIGN_LANGUAGE_IDS]).toEqual([studioline.id, fieldline.id]);
    for (const manifest of [studioline, fieldline]) {
      expect(manifest.density.kind).toBe('clamped');
      const supported = manifest.density.kind === 'clamped' ? manifest.density.supported : [];
      expect(WORKSPACE_DENSITY_CLAMP[manifest.id as 'studioline']).toEqual(supported);
    }
    // fieldline clamps `dense` out at 0.6 relative density (MOR-977 §4.4).
    expect(WORKSPACE_DENSITY_CLAMP.fieldline).not.toContain('dense');
  });

  it('theme ids are exactly the switcher\'s 21-id list, in order', () => {
    expect([...WORKSPACE_THEME_IDS]).toEqual(getAvailableThemes().map((t) => t.id));
  });

  it('zone ids are exactly the zones every registered layout manifest declares', () => {
    const manifests = Object.values(layoutDeclarations).filter(
      (v): v is LayoutManifest => typeof v === 'object' && v !== null && (v as LayoutManifest).schemaVersion === 1,
    );
    const live = [...new Set(manifests.flatMap((m) => m.zones.map((z) => z.id)))].sort();
    expect([...WORKSPACE_ZONE_IDS].sort()).toEqual(live);
  });
});

describe('decision 12 — valid v1 round-trips unchanged', () => {
  it('reads a fully valid object with no rejections and re-serializes identically', () => {
    const result = readWorkspace(VALID);
    expect(result.outcome).toBe('ok');
    expect(result.rejections).toEqual([]);
    expect(result.workspace).toEqual(VALID);
    expect(serializeWorkspace(result)).toEqual(VALID);
    // Second pass over the serialized form is a fixed point.
    expect(readWorkspace(serializeWorkspace(result)).workspace).toEqual(VALID);
  });

  it('defaults are themselves a valid, rejection-free workspace', () => {
    const result = readWorkspace(DEFAULT_WORKSPACE);
    expect(result.outcome).toBe('ok');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it('JSON import/export is whole-object and carries the version field', () => {
    const text = JSON.stringify(serializeWorkspace(readWorkspace(VALID)));
    expect(JSON.parse(text).version).toBe(WORKSPACE_SCHEMA_VERSION);
    expect(readWorkspaceJson(text).workspace).toEqual(VALID);
  });
});

describe('decision 10 — unknown fields are ignored AND preserved, never erased', () => {
  it('keeps an unknown top-level field verbatim through a full round trip', () => {
    const stored = { ...VALID, futureField: { nested: [1, 'two'] }, anotherOne: 'plain' };
    const result = readWorkspace(stored);
    expect(result.outcome).toBe('ok');
    expect(result.preserved).toEqual({ futureField: { nested: [1, 'two'] }, anotherOne: 'plain' });
    // The known surface is unaffected, and serialization restores the stripped-nothing whole.
    expect(result.workspace).toEqual(VALID);
    expect(serializeWorkspace(result)).toEqual(stored);
  });

  it('an unknown field never leaks into the typed workspace object', () => {
    const result = readWorkspace({ ...VALID, futureField: 1 });
    expect(Object.keys(result.workspace).sort()).toEqual(Object.keys(VALID).sort());
  });
});

describe('decision 11 + rollback window — version policy', () => {
  it('discards a version outside the window with a surfaced, discriminated signal', () => {
    const result = readWorkspace({ ...VALID, version: 99 });
    expect(result.outcome).toBe('version-discarded');
    expect(result.outcome === 'version-discarded' && result.discardedVersion).toBe(99);
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
    // Discard is data loss: nothing from the discarded object may survive.
    expect(result.preserved).toEqual({});
    expect(result.rejections).toEqual([{ field: 'version', reason: 'malformed' }]);
  });

  it.each([undefined, null, '1', 1.5, 0])('discards a non-v1 version marker %p', (version) => {
    expect(readWorkspace({ ...VALID, version }).outcome).toBe('version-discarded');
  });

  it.each([2, 3])('N=2 forward-read: version %i is READ, not discarded', (version) => {
    const stored = { ...VALID, version, brandNewField: 'from-a-newer-app' };
    const result = readWorkspace(stored);
    expect(result.outcome).toBe('forward-read');
    expect(result.workspace.theme).toBe('nord');
    // The newer version is written back un-downgraded, with its new field intact.
    expect(result.workspace.version).toBe(version);
    expect(serializeWorkspace(result)).toEqual(stored);
  });

  it('version 4 is one past the window and discards', () => {
    expect(readWorkspace({ ...VALID, version: 4 }).outcome).toBe('version-discarded');
  });
});

describe('forbidden classes — one refusal per class, fail-closed', () => {
  const CASES: readonly (readonly [WorkspaceForbiddenClass, Record<string, unknown>])[] = [
    ['capabilities', { capabilities: ['audio', 'tx'] }],
    ['runtime-state', { freqHz: 14195000 }],
    ['manufacturer-policy', { icomModPolicy: 'lan' }],
    ['component-module-path', { favouritePanel: '$lib/../components-v2/panels/TxPanel.svelte' }],
    ['transport-session', { authToken: 'deadbeef' }],
    ['tx-resource-safety', { txPowerOverride: 100 }],
  ];

  it.each(CASES)('refuses a %s field with a typed reason and never persists it', (reason, field) => {
    const key = Object.keys(field)[0];
    const result = readWorkspace({ ...VALID, ...field });
    expect(result.rejections).toContainEqual({ field: key, reason });
    expect(result.preserved).toEqual({});
    expect(serializeWorkspace(result)).not.toHaveProperty(key);
    // Refusing one field must not destroy the rest of a valid workspace.
    expect(result.outcome).toBe('repaired');
    expect(result.workspace).toEqual(VALID);
  });

  it('catches a forbidden payload nested inside an otherwise innocuous unknown field', () => {
    const result = readWorkspace({ ...VALID, uiExtras: { panel: { capabilities: ['tx'] } } });
    expect(result.rejections).toContainEqual({ field: 'uiExtras', reason: 'capabilities' });
    expect(result.preserved).toEqual({});
  });

  it('catches a module path smuggled as a value deep in an unknown field', () => {
    const result = readWorkspace({ ...VALID, uiExtras: { slots: ['./panels/TxPanel.svelte'] } });
    expect(result.rejections).toContainEqual({ field: 'uiExtras', reason: 'component-module-path' });
  });

  it('rejects a pinned command whose intent name is TX-safety shaped', () => {
    const result = readWorkspace({ ...VALID, pinnedCommands: ['set_compressor', 'tx_inhibit'] });
    expect(result.workspace.pinnedCommands).toEqual(['set_compressor']);
    expect(result.rejections).toContainEqual({ field: 'pinnedCommands', reason: 'tx-resource-safety' });
  });

  it('rejects a pinned command that is a module path rather than an intent name', () => {
    const result = readWorkspace({ ...VALID, pinnedCommands: ['$lib/runtime/commands/tx'] });
    expect(result.workspace.pinnedCommands).toEqual([]);
    expect(result.rejections).toContainEqual({ field: 'pinnedCommands', reason: 'malformed' });
  });
});

describe('decision 4 — density is clamped by the ACTIVE design language', () => {
  it('honours an in-clamp override', () => {
    expect(readWorkspace({ ...VALID, designLanguage: 'studioline', density: 'dense' }).workspace.density).toBe('dense');
  });

  it('clamps `dense` out under fieldline and says why', () => {
    const result = readWorkspace({ ...VALID, designLanguage: 'fieldline', density: 'dense' });
    expect(result.workspace.density).toBe('comfortable');
    expect(result.rejections).toContainEqual({ field: 'density', reason: 'out-of-clamp' });
  });

  it('an unknown density is `unknown-id`, not `out-of-clamp`', () => {
    expect(readWorkspace({ ...VALID, density: 'spacious' }).rejections)
      .toContainEqual({ field: 'density', reason: 'unknown-id' });
  });

  it('the clamp follows the language that was actually accepted, not the one requested', () => {
    // An invalid language falls back to studioline, whose clamp then admits `dense`.
    const result = readWorkspace({ ...VALID, designLanguage: 'nope', density: 'dense' });
    expect(result.workspace.designLanguage).toBe('studioline');
    expect(result.workspace.density).toBe('dense');
  });
});

describe('decision 1 — `auto` is first class and resolution is deferred', () => {
  it('round-trips `auto` and defers its manifest resolution to resolveSkinId', () => {
    expect(readWorkspace({ ...VALID, layout: 'auto' }).workspace.layout).toBe('auto');
    expect(workspaceLayoutManifestId('auto')).toBeNull();
  });

  it('bridges the two id spaces — `standard` is the `desktop-v2` manifest', () => {
    expect(workspaceLayoutManifestId('standard')).toBe('desktop-v2');
    for (const id of WORKSPACE_LAYOUT_IDS) {
      const manifestId = workspaceLayoutManifestId(id);
      if (manifestId !== null) expect(typeof manifestId).toBe('string');
    }
  });

  it('normalizes a legacy alias on read instead of rejecting it', () => {
    const result = readWorkspace({ ...VALID, layout: 'amber-lcd' });
    expect(result.workspace.layout).toBe('lcd-cockpit');
    expect(result.rejections).toEqual([]);
  });

  it('falls an unknown layout id back to `auto` with a rejection', () => {
    const result = readWorkspace({ ...VALID, layout: 'dual-receiver-cockpit' });
    expect(result.workspace.layout).toBe('auto');
    expect(result.rejections).toContainEqual({ field: 'layout', reason: 'unknown-id' });
  });
});

describe('decisions 5 and 6 — zone constraints', () => {
  it('drops an unknown zone id and an unknown surface id', () => {
    const result = readWorkspace({ ...VALID, visibleSurfaces: { 'no-such-zone': ['vfo'], main: ['vfo', 'nope'] } });
    expect(result.workspace.visibleSurfaces).toEqual({ main: ['vfo'] });
    expect(result.rejections).toContainEqual({ field: 'visibleSurfaces.no-such-zone', reason: 'unknown-id' });
    expect(result.rejections).toContainEqual({ field: 'visibleSurfaces.main', reason: 'unknown-id' });
  });

  it('rejects the same surface claimed by two zones — the cross-zone move shape', () => {
    const result = readWorkspace({ ...VALID, zoneOrder: { 'primary-vfo': ['vfo'], 'secondary-vfo': ['vfo'] } });
    expect(result.workspace.zoneOrder).toEqual({ 'primary-vfo': ['vfo'], 'secondary-vfo': [] });
    expect(result.rejections).toContainEqual({ field: 'zoneOrder.secondary-vfo', reason: 'cross-zone' });
  });

  it('preserves within-zone order — reordering is the whole point of the field', () => {
    const result = readWorkspace({ ...VALID, zoneOrder: { main: ['rxTx', 'vfo'] } });
    expect(result.workspace.zoneOrder.main).toEqual(['rxTx', 'vfo']);
  });

  it('a malformed zone map degrades to empty rather than throwing', () => {
    expect(readWorkspace({ ...VALID, visibleSurfaces: ['vfo'] }).workspace.visibleSurfaces).toEqual({});
    expect(readWorkspace({ ...VALID, zoneOrder: 42 }).workspace.zoneOrder).toEqual({});
  });
});

describe('decision 12 — invalid state resets, never throws, never blocks boot', () => {
  it('invalid-everything (but a readable version) falls back field by field', () => {
    const result = readWorkspace({
      version: 1, layout: 7, designLanguage: {}, theme: 'no-such-theme', density: null,
      visibleSurfaces: 'nope', zoneOrder: null, pinnedCommands: 'set_compressor',
    });
    expect(result.outcome).toBe('repaired');
    expect(result.workspace).toEqual({ ...DEFAULT_WORKSPACE, version: 1 });
  });

  it.each([null, undefined, 42, 'text', [], true, NaN])('never throws on %p', (input) => {
    expect(() => readWorkspace(input)).not.toThrow();
    expect(readWorkspace(input).workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it('unparseable JSON resets instead of propagating a SyntaxError', () => {
    expect(() => readWorkspaceJson('{not json')).not.toThrow();
    expect(readWorkspaceJson('{not json').outcome).toBe('reset');
    expect(readWorkspaceJson('null').outcome).toBe('reset');
  });

  it('a prototype-polluting key is treated as an ordinary unknown field, not applied', () => {
    const result = readWorkspace(JSON.parse('{"version":1,"__proto__":{"polluted":true}}') as unknown);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(result.workspace).toEqual({ ...DEFAULT_WORKSPACE, version: 1 });
  });
});
