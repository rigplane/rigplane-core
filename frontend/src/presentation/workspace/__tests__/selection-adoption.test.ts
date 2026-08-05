/**
 * MOR-1081 — the adoption integration test: layout, design-language and theme
 * selection now come from ONE workspace field set.
 *
 * It drives the real façades the app uses (`lib/stores/layout.svelte`,
 * `components-v2/theme/theme-switcher`, `skins/registry`) against a recording
 * fake storage, so every assertion about "who writes what" is about the actual
 * call the shipped code makes, not about a mock of it.
 *
 * Pool: `fast`. Deliberately no `vi.resetModules()` / `vi.stubGlobal` — the
 * store takes its storage as an argument, so the order-dependent shapes that
 * force the workspace purity suites into the isolated pool (MOR-1272) are not
 * needed here. `afterEach` re-inits the shared module state back to defaults
 * so nothing leaks to a sibling file under `isolate: false`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { DEFAULT_WORKSPACE } from '../contract';
import { designLanguageActivation } from '../activation';
import { WORKSPACE_MIGRATION_SENTINEL_KEY, WORKSPACE_STORAGE_KEY } from '../repository';
import { getWorkspace, initWorkspaceStore, setDesignLanguage } from '../store.svelte';
import {
  cycleLayoutMode, getLayoutMode, setLayoutMode,
} from '../../../lib/stores/layout.svelte';
import { readQaCockpitLayoutOverride } from '../../../lib/stores/qa-cockpit-override';
import {
  getTheme, hasExplicitTheme, setTheme, setThemeUserChoice,
} from '../../../components-v2/theme/theme-switcher';
import { resolveSkinId } from '../../../skins/registry';
import { fieldline, studioline } from '../../languages/declarations';

const LEGACY_LAYOUT_KEY = 'rigplane-layout';
const LEGACY_SKIN_KEY = 'rigplane-skin';
const LEGACY_THEME_KEY = 'rigplane:theme';
const LEGACY_THEME_USER_CHOICE_KEY = `${LEGACY_THEME_KEY}-user-choice`;

interface Recorder {
  readonly data: Map<string, string>;
  readonly writes: string[];
  readonly storage: Storage;
}

/** A full `Storage` whose `removeItem`/`clear` throw: deleting a legacy key in
 *  the rollback window is a failure, not a tolerated write. */
function recorder(seed: Record<string, string> = {}): Recorder {
  const data = new Map(Object.entries(seed));
  const writes: string[] = [];
  const storage = {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => { writes.push(k); data.set(k, v); },
    removeItem: (k: string) => { throw new Error(`legacy-window violation: removeItem(${k})`); },
    clear: () => { throw new Error('legacy-window violation: clear()'); },
    key: (i: number) => [...data.keys()][i] ?? null,
    get length() { return data.size; },
  } as unknown as Storage;
  return { data, writes, storage };
}

function workspaceJson(fields: Record<string, unknown>): string {
  return JSON.stringify({ version: 1, ...fields });
}

/** Every key written that is not the one workspace key or its migration sentinel. */
function foreignWrites(rec: Recorder): string[] {
  return rec.writes.filter(
    (k) => k !== WORKSPACE_STORAGE_KEY && k !== WORKSPACE_MIGRATION_SENTINEL_KEY,
  );
}

beforeEach(() => {
  document.documentElement.removeAttribute('data-theme');
});

afterEach(() => {
  // Return the shared module-level `$state` to defaults for sibling files.
  initWorkspaceStore(null);
  document.documentElement.removeAttribute('data-theme');
});

describe('MOR-1081 — one workspace field set owns the selection', () => {
  it('reconciles a disagreeing legacy key by letting the migrated workspace win', () => {
    // The 1079 migration already ran (sentinel present) and the workspace says
    // lcd-scope; `rigplane-layout` still holds the pre-migration value, and an
    // older build during the rollback window could even have moved it on.
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'lcd-scope', theme: 'nord' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
      [LEGACY_LAYOUT_KEY]: 'standard',
      [LEGACY_SKIN_KEY]: 'amber-lcd',
      [LEGACY_THEME_KEY]: 'dracula',
    });

    initWorkspaceStore(rec.storage);

    expect(getLayoutMode()).toBe('lcd-scope');
    expect(getTheme()).toBe('nord');
    // …and the legacy values are still there, untouched, for an older build.
    expect(rec.data.get(LEGACY_LAYOUT_KEY)).toBe('standard');
    expect(rec.data.get(LEGACY_THEME_KEY)).toBe('dracula');
  });

  it('migrates the legacy keys once when no workspace object exists yet', () => {
    const rec = recorder({ [LEGACY_LAYOUT_KEY]: 'lcd', [LEGACY_THEME_USER_CHOICE_KEY]: 'nord' });

    initWorkspaceStore(rec.storage);

    // MOR-1042 alias canonicalization survives the migration read path.
    expect(getLayoutMode()).toBe('lcd-cockpit');
    expect(getTheme()).toBe('nord');
  });

  it('canonicalizes MOR-1042 aliases stored in the workspace object itself', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'amber-lcd' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });

    initWorkspaceStore(rec.storage);

    expect(getLayoutMode()).toBe('lcd-cockpit');
  });

  it('writes ONLY the workspace key when layout or theme is selected', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({}),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
      [LEGACY_LAYOUT_KEY]: 'auto',
      [LEGACY_THEME_KEY]: 'default',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    setLayoutMode('lcd-scope');
    setThemeUserChoice('nord');
    cycleLayoutMode(true);

    expect(foreignWrites(rec)).toEqual([]);
    expect(rec.writes.every((k) => k === WORKSPACE_STORAGE_KEY)).toBe(true);
    // The legacy keys keep their pre-adoption bytes: readable, never rewritten,
    // never deleted (the fake throws on removeItem/clear).
    expect(rec.data.get(LEGACY_LAYOUT_KEY)).toBe('auto');
    expect(rec.data.get(LEGACY_THEME_KEY)).toBe('default');
    expect(rec.data.get(LEGACY_THEME_USER_CHOICE_KEY)).toBeUndefined();
  });

  it('preserves the chosen presentation across a reload', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({}),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);

    setLayoutMode('lcd-scope');
    setThemeUserChoice('gruvbox-dark');
    setDesignLanguage('fieldline');

    // Reload: a fresh init against the same bytes.
    initWorkspaceStore(rec.storage);

    expect(getLayoutMode()).toBe('lcd-scope');
    expect(getTheme()).toBe('gruvbox-dark');
    expect(getWorkspace().designLanguage).toBe('fieldline');
  });

  it('keeps selection working in-memory when writes are blocked (forward-read-only)', () => {
    // A newer object this build cannot fully represent: the store latches
    // "not writable", but the operator can still change presentation now.
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: JSON.stringify({ version: 2, layout: 'lcd-scope', theme: 'v2-only-theme' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    setLayoutMode('standard');

    expect(getLayoutMode()).toBe('standard');
    expect(rec.writes).toEqual([]);
  });
});

describe('MOR-1081 — capabilities cannot mutate the persisted preference', () => {
  it('resolves `auto` against the rig at resolution time and leaves `auto` persisted', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'auto' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    const withScope = resolveSkinId({
      capabilities: null, layoutPreference: getLayoutMode(), isMobile: false, hasAnyScope: true,
    });
    const withoutScope = resolveSkinId({
      capabilities: null, layoutPreference: getLayoutMode(), isMobile: false, hasAnyScope: false,
    });

    expect(withScope).toBe('desktop-v2');
    expect(withoutScope).toBe('lcd-cockpit');
    // The resolved skin is never written back over the preference.
    expect(getWorkspace().layout).toBe('auto');
    expect(rec.writes).toEqual([]);
    expect(JSON.parse(rec.data.get(WORKSPACE_STORAGE_KEY)!).layout).toBe('auto');
  });

  it('does not let a mobile viewport overwrite the persisted preference', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'standard' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    expect(resolveSkinId({
      capabilities: null, layoutPreference: getLayoutMode(), isMobile: true, hasAnyScope: true,
    })).toBe('mobile');
    expect(getWorkspace().layout).toBe('standard');
    expect(rec.writes).toEqual([]);
  });
});

describe('MOR-1257 — the ?layout= QA override survives adoption byte-exactly', () => {
  it('is desktop-only, non-persistable, and never writes the workspace', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'standard' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    const override = readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit');
    expect(override).toBe('dual-receiver-cockpit');

    // App's own expression: the override wins for RESOLUTION only.
    expect(resolveSkinId({
      capabilities: null,
      layoutPreference: override ?? getLayoutMode(),
      isMobile: false,
      hasAnyScope: true,
    })).toBe('dual-receiver-cockpit');

    // …and mobile still wins under 640px.
    expect(resolveSkinId({
      capabilities: null,
      layoutPreference: override ?? getLayoutMode(),
      isMobile: true,
      hasAnyScope: true,
    })).toBe('mobile');

    // Nothing was persisted, and the stored preference is untouched.
    expect(rec.writes).toEqual([]);
    expect(getWorkspace().layout).toBe('standard');
  });

  it('cannot enter the workspace even if something tries to select it', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ layout: 'standard' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);

    setLayoutMode('dual-receiver-cockpit');

    expect(getWorkspace().layout).toBe('auto');
    expect(JSON.parse(rec.data.get(WORKSPACE_STORAGE_KEY)!).layout).toBe('auto');
  });
});

describe('MOR-1278 — the workspace is the only source [data-design-language] is written from', () => {
  it('activates the selected language where its own manifest declares the layout', () => {
    expect(designLanguageActivation(studioline, 'dual-receiver-cockpit')).toBe('studioline');
  });

  it('stays inert for a layout the language has not declared (no cutover here)', () => {
    for (const layoutId of ['desktop-v2', 'lcd-cockpit', 'lcd-scope', 'mobile', 'sdr-test']) {
      expect(designLanguageActivation(studioline, layoutId)).toBeNull();
    }
  });

  it('honours an explicit incompatible declaration', () => {
    expect(designLanguageActivation(fieldline, 'dual-receiver-cockpit')).toBeNull();
  });

  it('is inert when the selected id resolves to no registered manifest', () => {
    expect(designLanguageActivation(undefined, 'dual-receiver-cockpit')).toBeNull();
  });
});

describe('MOR-1081 — theme selection reads and writes the workspace only', () => {
  it('applies the selection to the DOM and reports explicitness off the workspace', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({}),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);

    expect(hasExplicitTheme()).toBe(false);
    setThemeUserChoice('tokyo-night');

    expect(getTheme()).toBe('tokyo-night');
    expect(hasExplicitTheme()).toBe(true);
    expect(document.documentElement.dataset.theme).toBe('tokyo-night');
    expect(foreignWrites(rec)).toEqual([]);
  });

  it('does not persist a re-apply of the theme already selected', () => {
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ theme: 'nord' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    // What a skin mount does: re-apply the stored theme to the DOM.
    setTheme(getTheme());

    expect(document.documentElement.dataset.theme).toBe('nord');
    expect(rec.writes).toEqual([]);
  });

  it('rejects an unknown theme id without touching the workspace', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const rec = recorder({
      [WORKSPACE_STORAGE_KEY]: workspaceJson({ theme: 'nord' }),
      [WORKSPACE_MIGRATION_SENTINEL_KEY]: '1',
    });
    initWorkspaceStore(rec.storage);
    rec.writes.length = 0;

    setTheme('not-a-theme');

    expect(getTheme()).toBe('nord');
    expect(rec.writes).toEqual([]);
    warn.mockRestore();
  });

  it('falls back to the schema default with no storage at all', () => {
    initWorkspaceStore(null);
    expect(getTheme()).toBe(DEFAULT_WORKSPACE.theme);
    expect(getLayoutMode()).toBe(DEFAULT_WORKSPACE.layout);
  });
});

/**
 * MOR-1081 F1 — the explicitness latch, pinned at the consumer.
 *
 * `hasExplicitTheme()` has exactly one consumer, `LcdLayout.svelte:6-14`, and
 * the regression it guards is invisible at the store: an operator who picks
 * *Default Dark* (a real, first-in-list, selectable theme) must keep it, and
 * must not be silently given `lcd-warm` instead. `lcdLayoutBoot()` below is
 * that component's own two lines, so these probes fail if the semantics drift
 * even when every store-level assertion still passes.
 */
function lcdLayoutBoot(): string {
  if (hasExplicitTheme()) setTheme(getTheme());
  else document.documentElement.dataset.theme = 'lcd-warm';
  // `setTheme('default')` deletes the attribute — absence IS Default Dark.
  return document.documentElement.dataset.theme ?? 'default';
}

describe('MOR-1081 F1 — an explicit Default Dark survives reload and the amber-lcd default', () => {
  it('fresh install: picking Default Dark in the UI keeps it across a reload', () => {
    const rec = recorder();
    initWorkspaceStore(rec.storage); // first boot, nothing chosen

    expect(hasExplicitTheme()).toBe(false);
    setThemeUserChoice('default');
    expect(hasExplicitTheme()).toBe(true);

    initWorkspaceStore(rec.storage); // reload

    expect(getTheme()).toBe('default');
    expect(hasExplicitTheme()).toBe(true);
    expect(lcdLayoutBoot()).toBe('default');
  });

  it('migrated operator: legacy theme-user-choice=default keeps Default Dark', () => {
    const rec = recorder({ [LEGACY_THEME_USER_CHOICE_KEY]: 'default' });
    initWorkspaceStore(rec.storage); // one-time migration

    expect(getTheme()).toBe('default');
    expect(hasExplicitTheme()).toBe(true);
    expect(lcdLayoutBoot()).toBe('default');

    initWorkspaceStore(rec.storage); // reload

    expect(hasExplicitTheme()).toBe(true);
    expect(lcdLayoutBoot()).toBe('default');
  });

  it('control: Nord is unchanged, before and after a reload', () => {
    const rec = recorder();
    initWorkspaceStore(rec.storage);

    setThemeUserChoice('nord');
    expect(lcdLayoutBoot()).toBe('nord');

    initWorkspaceStore(rec.storage);

    expect(getTheme()).toBe('nord');
    expect(hasExplicitTheme()).toBe(true);
    expect(lcdLayoutBoot()).toBe('nord');
  });

  // The other half of the same invariant, and the reason the latch alone is not
  // enough: once the first boot has written the workspace object, a never-chosen
  // theme must STILL read as "never chosen", or every fresh install would lose
  // the amber-lcd warm default on its second boot.
  it('never-chose: the amber-lcd default still applies after the first boot writes', () => {
    const rec = recorder();
    initWorkspaceStore(rec.storage);
    expect(lcdLayoutBoot()).toBe('lcd-warm');

    // The migration write happened; the theme field must not be in it.
    expect(rec.data.has(WORKSPACE_STORAGE_KEY)).toBe(true);
    expect('theme' in JSON.parse(rec.data.get(WORKSPACE_STORAGE_KEY)!)).toBe(false);

    initWorkspaceStore(rec.storage); // reload

    expect(hasExplicitTheme()).toBe(false);
    expect(lcdLayoutBoot()).toBe('lcd-warm');
  });

  it('a host re-applying the current theme does not turn "never chose" into "chose"', () => {
    const rec = recorder();
    initWorkspaceStore(rec.storage);

    setTheme(getTheme()); // what RadioLayout/LcdLayout do on mount

    expect(hasExplicitTheme()).toBe(false);
  });

  it('keeps a non-default value persisted even when it was never explicitly chosen', () => {
    const rec = recorder();
    initWorkspaceStore(rec.storage);

    setTheme('nord'); // non-explicit, but carries real information

    initWorkspaceStore(rec.storage);
    expect(getTheme()).toBe('nord');
  });
});

/**
 * MOR-1081 F2 — "no duplicate writes" as a static pin.
 *
 * The behavioural pins above only see writes routed through the injected fake
 * storage, so a rogue direct `localStorage.setItem('rigplane-layout', …)`
 * escapes them entirely. This scan closes that hole: no production module may
 * so much as NAME a legacy selection key, which also kills the const-indirection
 * variant of the same mutation.
 */
describe('MOR-1081 F2 — no production module names a legacy selection key', () => {
  /** `rigplane:theme` is a prefix of `rigplane:theme-user-choice`, so both are covered. */
  const LEGACY_SELECTION_KEYS = ['rigplane-layout', 'rigplane-skin', 'rigplane:theme'];

  /**
   * `rigplane:vfo-theme` is deliberately NOT in the list: MOR-1078 routed it
   * `retain-outside` (a per-VFO override with no field in the frozen v1
   * schema), so `theme-switcher.ts` is still its legitimate owner and writer.
   * It also does not contain any banned substring, so it needs no exemption
   * beyond this comment.
   */
  const ALLOWED: readonly string[] = [
    // The `getItem`-only routing table + migration reader (MOR-1078). Reads only.
    'src/presentation/workspace/legacy-readers.ts',
    // The v1.x → v2.x key RENAME (`icom-lan-*` → `rigplane-*`), which runs
    // before the workspace exists and is what makes the legacy keys readable
    // for the one-time migration. Not a selection write.
    'src/lib/migrate-legacy-storage.ts',
  ];

  function productionSources(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = `${dir}/${entry.name}`;
      if (entry.isDirectory()) {
        if (entry.name !== '__tests__') productionSources(path, out);
      } else if (/\.(ts|svelte)$/.test(entry.name) && !entry.name.endsWith('.test.ts')) {
        out.push(path);
      }
    }
    return out;
  }

  /** Comments are prose about the retired keys — only real code counts. */
  function stripComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
  }

  it.each(LEGACY_SELECTION_KEYS)('no production file references %s', (key) => {
    const offenders = productionSources('src')
      .filter((path) => !ALLOWED.includes(path))
      .filter((path) => stripComments(readFileSync(path, 'utf8')).includes(key));

    expect(offenders).toEqual([]);
  });

  it('the allow-list itself still exists, so a rename cannot silently empty the scan', () => {
    for (const path of ALLOWED) expect(readFileSync(path, 'utf8').length).toBeGreaterThan(0);
    expect(productionSources('src').length).toBeGreaterThan(200);
  });
});
