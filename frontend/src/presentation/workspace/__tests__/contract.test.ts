/**
 * MOR-1077 — workspace v1 schema/validator/defaults/fallback, one describe
 * block per MOR-1076 decision plus the registry-sync pins that stand in for
 * the live imports this zone cannot make (see `../contract.ts`'s header).
 *
 * Fast pool on purpose: this file stubs no global and mocks no module, so it
 * is order-independent under `isolate: false` (MOR-1272). The registry
 * imports below DO fire `registerLayout`/`registerDesignLanguage`, which is
 * exactly why they live here and not in `purity.isolated.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import {
  DEFAULT_WORKSPACE, WORKSPACE_DENSITY_CLAMP, WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS,
  WORKSPACE_SCHEMA_VERSION, WORKSPACE_SKIN_DESIGN_LANGUAGES, WORKSPACE_THEME_IDS, WORKSPACE_ZONE_IDS,
  normalizeWorkspaceLayoutId,
  readWorkspace, readWorkspaceJson, serializeWorkspace, workspaceLayoutManifestId,
  type WorkspaceForbiddenClass, type WorkspaceV1,
} from '../contract';
// Registry-sync sources. Each is imported ONLY to prove the pinned literal
// still matches the live declaration — never used by production code here.
import * as layoutDeclarations from '../../layouts/declarations';
import type { LayoutManifest } from '../../layouts/contract';
import { fieldline, segmentline, studioline } from '../../languages/declarations';
import { getAvailableThemes } from '../../../components-v2/theme/theme-switcher';
// MOR-2059: the layout id space is a LIVE import now — `../contract` derives
// `WORKSPACE_LAYOUT_IDS`/its alias table from this same module, so there is
// no separate copy left to scrape `lib/stores/layout.svelte.ts`'s source
// text for. `LEGACY_LAYOUT_ALIASES` is imported here only for the one count
// that isn't already covered by `../contract`'s own re-derivation.
import { LEGACY_LAYOUT_ALIASES } from '../../layout-mode';

const VALID: WorkspaceV1 = {
  version: WORKSPACE_SCHEMA_VERSION,
  layout: 'lcd-cockpit',
  designLanguageBySkin: { 'desktop-v2': 'fieldline' },
  theme: 'nord',
  density: 'compact',
  visibleSurfaces: { 'control-column': ['vfo'] },
  zoneOrder: { 'rx-tx': ['rxTx'] },
  pinnedCommands: ['set_compressor', 'set_monitor_gain'],
};

describe('registry sync — the pinned id spaces still match their live owners', () => {
  it('layout ids are exactly the canonical layout-mode set', () => {
    // Kills: normalizeWorkspaceLayoutId failing to round-trip a canonical id unchanged.
    for (const id of WORKSPACE_LAYOUT_IDS) expect(normalizeWorkspaceLayoutId(id)).toBe(id);
    // The QA-only cockpit id must stay unpersistable (MOR-1257).
    expect(normalizeWorkspaceLayoutId('dual-receiver-cockpit')).toBe('auto');
  });

  it('the MOR-1042 alias table has exactly the 4 known aliases', () => {
    expect(Object.keys(LEGACY_LAYOUT_ALIASES).length).toBe(4);
  });

  it('design-language ids and their density clamps match the live manifests', () => {
    expect([...WORKSPACE_DESIGN_LANGUAGE_IDS]).toEqual([studioline.id, fieldline.id, segmentline.id]);
    for (const manifest of [studioline, fieldline, segmentline]) {
      expect(manifest.density.kind).toBe('clamped');
      if (manifest.density.kind === 'clamped') {
        expect(WORKSPACE_DENSITY_CLAMP[manifest.id as 'studioline']).toEqual({
          supported: manifest.density.supported,
          default: manifest.density.default,
        });
      }
    }
    // fieldline and segmentline both clamp `dense` out (0.6 relative density
    // for fieldline, MOR-977 §4.4; segmentline's 7px meter pitch collides
    // with the dense cell outline, `../../languages/declarations.ts`).
    expect(WORKSPACE_DENSITY_CLAMP.fieldline.supported).not.toContain('dense');
    expect(WORKSPACE_DENSITY_CLAMP.segmentline.supported).not.toContain('dense');
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

  it('per-skin design-language declarations match the live layoutCompatibility manifests', () => {
    // Derived from the LIVE declarations: `supported` is every language
    // whose `layoutCompatibility` names the skin `compatible: true`, and
    // `default` is the first of those (MOR-2218: derive, never hand-write).
    const live: Record<string, { supported: string[]; default: string }> = {};
    for (const manifest of [studioline, fieldline, segmentline]) {
      for (const entry of manifest.layoutCompatibility) {
        if (entry.compatible !== true) continue;
        (live[entry.layoutId] ??= { supported: [], default: manifest.id }).supported.push(manifest.id);
      }
    }
    expect(Object.keys(WORKSPACE_SKIN_DESIGN_LANGUAGES).sort()).toEqual(Object.keys(live).sort());
    for (const [skin, declared] of Object.entries(WORKSPACE_SKIN_DESIGN_LANGUAGES)) {
      expect(declared.supported).toEqual(live[skin]?.supported);
      expect(declared.default).toBe(live[skin]?.default);
    }
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

  it.each([undefined, null, '1', 1.5, 0, 1])('discards a non-current version marker %p', (version) => {
    expect(readWorkspace({ ...VALID, version }).outcome).toBe('version-discarded');
  });

  it('MOR-2218: a stored v1 object is discarded, not migrated — the version bump rides the existing rule', () => {
    const result = readWorkspace({ ...VALID, version: 1, designLanguage: 'fieldline', designLanguageBySkin: undefined });
    expect(result.outcome).toBe('version-discarded');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it.each([3, 4])('N=2 forward-read: version %i is READ, not discarded', (version) => {
    const stored = { ...VALID, version, brandNewField: 'from-a-newer-app' };
    const result = readWorkspace(stored);
    expect(result.outcome).toBe('forward-read');
    expect(result.workspace.theme).toBe('nord');
    // The newer version is written back un-downgraded, with its new field intact.
    expect(result.workspace.version).toBe(version);
    expect(serializeWorkspace(result)).toEqual(stored);
  });

  it('version 5 is one past the window and discards', () => {
    expect(readWorkspace({ ...VALID, version: 5 }).outcome).toBe('version-discarded');
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

describe('decision 4 + MOR-2218 — density ids and the per-skin language map', () => {
  it('honours every density the union clamp admits; an unknown id repairs', () => {
    expect(readWorkspace({ ...VALID, density: 'dense' }).workspace.density).toBe('dense');
    const unknown = readWorkspace({ ...VALID, density: 'spacious' });
    expect(unknown.workspace.density).toBe('comfortable');
    expect(unknown.rejections).toContainEqual({ field: 'density', reason: 'unknown-id' });
  });

  it('keeps a declared per-skin value, drops an undeclared skin key, degrades a malformed map', () => {
    const stored = { ...VALID, designLanguageBySkin: { 'desktop-v2': 'fieldline', 'peer-split': 'segmentline' } };
    expect(readWorkspace(stored).workspace.designLanguageBySkin)
      .toEqual({ 'desktop-v2': 'fieldline', 'peer-split': 'segmentline' });
    expect(serializeWorkspace(readWorkspace(stored))).toEqual(stored);
    const unknown = readWorkspace({ ...VALID, designLanguageBySkin: { 'lcd-cockpit': 'studioline' } });
    expect(unknown.workspace.designLanguageBySkin).toEqual({});
    expect(unknown.rejections).toContainEqual({ field: 'designLanguageBySkin.lcd-cockpit', reason: 'unknown-id' });
    expect(readWorkspace({ ...VALID, designLanguageBySkin: 'studioline' }).workspace.designLanguageBySkin).toEqual({});
  });

  it('MOR-2218: an undeclared stored value clamps to THAT skin\'s default', () => {
    // `studioline` is known but no `peer-split` declaration accepts it.
    const undeclared = readWorkspace({ ...VALID, designLanguageBySkin: { 'peer-split': 'studioline' } });
    expect(undeclared.workspace.designLanguageBySkin).toEqual({ 'peer-split': 'segmentline' });
    expect(undeclared.rejections).toContainEqual({ field: 'designLanguageBySkin.peer-split', reason: 'out-of-clamp' });
    // An unknown language id is `unknown-id` and still clamps to the default.
    const unknown = readWorkspace({ ...VALID, designLanguageBySkin: { 'desktop-v2': 'nope' } });
    expect(unknown.workspace.designLanguageBySkin).toEqual({ 'desktop-v2': 'studioline' });
    expect(unknown.rejections).toContainEqual({ field: 'designLanguageBySkin.desktop-v2', reason: 'unknown-id' });
  });

  it('MOR-2218: the retired global `designLanguage` is dropped, not migrated, not preserved', () => {
    const result = readWorkspace({ ...VALID, designLanguage: 'fieldline' });
    expect(result.workspace.designLanguageBySkin).toEqual(VALID.designLanguageBySkin);
    expect(serializeWorkspace(result)).not.toHaveProperty('designLanguage');
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

  it.each(['peer-split', 'unified-instrument', 'panadapter-first'] as const)(
    'bridges production segmentline layout %s to its own manifest id',
    (id) => expect(workspaceLayoutManifestId(id)).toBe(id),
  );

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
    const result = readWorkspace({ ...VALID, visibleSurfaces: { 'no-such-zone': ['vfo'], 'receiver-deck': ['vfo', 'nope'] } });
    expect(result.workspace.visibleSurfaces).toEqual({ 'receiver-deck': ['vfo'] });
    expect(result.rejections).toContainEqual({ field: 'visibleSurfaces.no-such-zone', reason: 'unknown-id' });
    expect(result.rejections).toContainEqual({ field: 'visibleSurfaces.receiver-deck', reason: 'unknown-id' });
  });

  it('rejects the same surface claimed by two zones — the cross-zone move shape', () => {
    const result = readWorkspace({ ...VALID, zoneOrder: { 'primary-vfo': ['vfo'], 'secondary-vfo': ['vfo'] } });
    expect(result.workspace.zoneOrder).toEqual({ 'primary-vfo': ['vfo'], 'secondary-vfo': [] });
    expect(result.rejections).toContainEqual({ field: 'zoneOrder.secondary-vfo', reason: 'cross-zone' });
  });

  it('preserves within-zone order — reordering is the whole point of the field', () => {
    const result = readWorkspace({ ...VALID, zoneOrder: { 'receiver-deck': ['rxTx', 'vfo'] } });
    expect(result.workspace.zoneOrder['receiver-deck']).toEqual(['rxTx', 'vfo']);
  });

  it('a malformed zone map degrades to empty rather than throwing', () => {
    expect(readWorkspace({ ...VALID, visibleSurfaces: ['vfo'] }).workspace.visibleSurfaces).toEqual({});
    expect(readWorkspace({ ...VALID, zoneOrder: 42 }).workspace.zoneOrder).toEqual({});
  });
});

describe('decision 12 — invalid state resets, never throws, never blocks boot', () => {
  it('invalid-everything (but a readable version) falls back field by field', () => {
    const result = readWorkspace({
      version: WORKSPACE_SCHEMA_VERSION, layout: 7, designLanguageBySkin: 'nope', theme: 'no-such-theme', density: null,
      visibleSurfaces: 'nope', zoneOrder: null, pinnedCommands: 'set_compressor',
    });
    expect(result.outcome).toBe('repaired');
    expect(result.workspace).toEqual({ ...DEFAULT_WORKSPACE, version: WORKSPACE_SCHEMA_VERSION });
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
    const result = readWorkspace(JSON.parse('{"version":2,"__proto__":{"polluted":true}}') as unknown);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
    expect(result.workspace).toEqual({ ...DEFAULT_WORKSPACE, version: WORKSPACE_SCHEMA_VERSION });
  });
});
