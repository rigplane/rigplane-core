/**
 * `peer-split` was selectable and `segmentline` was registered,
 * but no operator flow could ever get from one to the other:
 * `WORKSPACE_DESIGN_LANGUAGE_IDS` (`../presentation/workspace/contract.ts`)
 * excluded `segmentline`, so a stored `designLanguage: 'segmentline'` could
 * never exist, and even where the resolved skin can't activate a stored
 * language, `App.svelte`'s activation effect used to just delete the
 * attribute — an unstyled surface.
 *
 * These tests mount the REAL `App.svelte` — the real workspace store, the
 * real `skins/registry.ts::resolveSkinId`, the real design-language and
 * layout registries — and read `document.documentElement.dataset` the way an
 * operator's browser would. Only the pieces with no bearing on design-language
 * activation (transport bootstrap, TX controller, battery, media session, the
 * App-global host) are stubbed, following the same recipe
 * `lazy-presentation.component.test.ts` already uses for a full `App.svelte`
 * mount. A test asserting `WORKSPACE_DESIGN_LANGUAGE_IDS` merely CONTAINS
 * `'segmentline'` would prove nothing about activation — this file proves the
 * attribute itself, end to end.
 *
 * Three properties:
 *
 *  A. STORABLE (change (a)). `segmentline` explicitly selected + `peer-split`
 *     resolved activates `segmentline`.
 *  B. THE LITERAL ACCEPTANCE. An operator who selects ONLY the `peer-split`
 *     layout, with no explicit design-language choice, still gets
 *     `segmentline` — `segmentline` is the only registered language whose
 *     `layoutCompatibility` names `peer-split` at all. Kill: removing the
 *     fallback loop in `App.svelte`'s activation effect leaves the attribute
 *     absent (the default `studioline` doesn't declare `peer-split`).
 *  C. THE FALLBACK (change (b)), plus the hard boundary that resolution must
 *     NOT touch the stored preference. `segmentline` stored + a skin it can't
 *     activate on (`desktop-v2`) still yields a styled surface (`studioline`,
 *     the first language in registration order that declares `desktop-v2`),
 *     `getWorkspace().designLanguage` stays `'segmentline'` throughout, and
 *     returning to `peer-split` restores `segmentline` with no re-choice.
 *     Kill: removing the same fallback loop leaves the attribute absent once
 *     `desktop-v2` is resolved.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import {
  getWorkspace, initWorkspaceStore, setDensity, setDesignLanguage, setLayout,
} from '../presentation/workspace/store.svelte';

const h = vi.hoisted(() => ({
  pending: [] as Array<{
    id: string;
    resolve: (c: unknown) => void;
    reject: (error: unknown) => void;
  }>,
  loadSkin: vi.fn(),
  plan: vi.fn(),
  bootstrap: vi.fn(),
  initBattery: vi.fn(),
  provide: vi.fn(),
  txHost: {
    refreshAuthority: vi.fn(),
    release: vi.fn(async () => {}),
    dispose: vi.fn(),
  },
  registerBarrier: vi.fn(),
}));

// The only mock in this file with production logic left in: keeps the real
// `resolveSkinId` (what actually turns a workspace layout preference into a
// SkinId), and replaces only the two members that would otherwise pull in a
// real skin's whole component tree.
vi.mock('../skins/registry', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../skins/registry')>();
  return {
    ...actual, loadSkin: h.loadSkin, presentationHostMode: () => 'self-contained',
    presentationResourcePlan: h.plan,
  };
});
vi.mock('../components-v2/wiring/SemanticRadioSurfaces.svelte', async () => ({
  default: (await import('./LayoutStub.svelte')).default,
}));

vi.mock('../lib/runtime/frontend-runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return { stateRevision: 1, freshnessRevision: 1, observationSeq: 1, ptt: false }; },
    // T198: `peer-split`'s manifest declares only the two dual-receiver
    // topology classes, and `resolveSkinId` now refuses the preference on a
    // radio outside them — so this stub has to name one. `ab_shared` is the
    // scheme `rigs/ftx1.toml` declares under `[vfo]`, and the FTX-1 is the
    // radio `peer-split` exists for.
    get caps() {
      return {
        tx: true, capabilities: ['tx', 'dual_rx'], receivers: 2, vfoScheme: 'ab_shared',
      };
    },
    bootstrap: h.bootstrap,
  },
  presentationResources: {
    snapshot: () => ({ demand: 0 }),
    acquire: () => ({}),
    release: () => true,
  },
}));
vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({ provideManagedAppTxHost: h.provide }));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));
vi.mock('$lib/stores/capabilities.svelte', () => ({ hasAnyScope: () => false }));
vi.mock('../lib/media/media-session', () => ({ initMediaSession: vi.fn(), destroyMediaSession: vi.fn() }));
vi.mock('../AppGlobalHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});

import App from '../App.svelte';
import LayoutStub from './LayoutStub.svelte';

/**
 * `App.svelte`'s own top-level `initWorkspaceStore()` call is inside its
 * `<script>` body, which Svelte re-runs on every component INSTANTIATION
 * (unlike a plain module's top level, which runs once at import) — so it
 * fires fresh on every `mount()`, reading the real `localStorage` (empty in
 * this jsdom environment) and overwriting any workspace state set BEFORE the
 * mount. Seeding therefore happens AFTER `mountApp()`, through the same real
 * `setLayout`/`setDesignLanguage` setters an operator's own selection would
 * call, followed by `flushSync()` so the mounted effects re-run.
 */
function mountApp() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(App, { target });
  flushSync();
  return instance;
}

beforeEach(() => {
  vi.clearAllMocks();
  h.pending.length = 0;
  document.body.innerHTML = '';
  localStorage.clear();
  document.documentElement.removeAttribute('data-design-language');
  document.documentElement.removeAttribute('data-language-mode');
  document.documentElement.removeAttribute('data-density');
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  h.provide.mockImplementation(() => h.txHost);
  h.bootstrap.mockResolvedValue(vi.fn());
  h.loadSkin.mockImplementation(
    (id: string) => new Promise((resolve, reject) => { h.pending.push({ id, resolve, reject }); }),
  );
  h.plan.mockReturnValue([]);
});

afterEach(() => {
  initWorkspaceStore(null);
  localStorage.clear();
  document.documentElement.removeAttribute('data-design-language');
  document.documentElement.removeAttribute('data-language-mode');
  document.documentElement.removeAttribute('data-density');
});

describe('segmentline activation, in a rendered App', () => {
  async function settle(): Promise<void> {
    for (let index = 0; index < 4; index++) await Promise.resolve();
    await tick();
    flushSync();
  }
  function pendingIndex(id: string): number {
    for (let index = h.pending.length - 1; index >= 0; index -= 1) {
      if (h.pending[index]?.id === id) return index;
    }
    return -1;
  }

  async function complete(id: string): Promise<void> {
    const index = pendingIndex(id);
    if (index < 0) throw new Error(`no pending load for ${id}`);
    h.pending.splice(index, 1)[0].resolve(LayoutStub);
    await settle();
  }
  async function fail(id: string): Promise<void> {
    const index = pendingIndex(id);
    if (index < 0) throw new Error(`no pending load for ${id}`);
    h.pending.splice(index, 1)[0].reject(new Error(`failed ${id}`));
    await settle();
  }
  function setWidth(width: number): void {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
    window.dispatchEvent(new Event('resize'));
    flushSync();
  }

  it('A: activates metadata only after the selected presentation commits', async () => {
    const instance = mountApp();

    setLayout('peer-split');
    setDesignLanguage('segmentline');
    flushSync();

    expect(document.documentElement.dataset.designLanguage).toBeUndefined();
    expect(document.documentElement.dataset.languageMode).toBeUndefined();
    expect(document.documentElement.dataset.density).toBeUndefined();

    await complete('peer-split');
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');

    unmount(instance);
  });

  it('B: selecting only the peer-split LAYOUT (no explicit language) still activates segmentline', async () => {
    const instance = mountApp();
    expect(getWorkspace().designLanguage).toBe('studioline'); // the DEFAULT_WORKSPACE value

    setLayout('peer-split');
    flushSync();

    expect(document.documentElement.dataset.designLanguage).toBeUndefined();
    await complete('peer-split');
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');

    unmount(instance);
  });

  it('C: pending and failed replacement keeps committed language/density while workspace updates', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
    const instance = mountApp();

    setLayout('peer-split');
    setDensity('dense');
    flushSync();
    await complete('peer-split');
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');
    expect(document.documentElement.dataset.density).toBe('comfortable');
    const committedLanguageMode = document.documentElement.dataset.languageMode;
    expect(committedLanguageMode).toBeDefined();

    setWidth(390); // requests mobile, which has no compatible design language

    // The requested mobile presentation is still pending. Its absent language
    // must not clear metadata from the committed peer-split face.
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');
    expect(document.documentElement.dataset.languageMode).toBe(committedLanguageMode);
    expect(document.documentElement.dataset.density).toBe('comfortable');
    expect(getWorkspace().designLanguage).toBe('studioline');

    // A workspace update while B is pending still resolves against committed A.
    setDensity('comfortable');
    flushSync();
    expect(document.documentElement.dataset.density).toBe('comfortable');
    setDensity('dense');
    flushSync();
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');
    expect(document.documentElement.dataset.density).toBe('comfortable');

    await fail('mobile');
    expect(consoleError).toHaveBeenCalledWith(
      '[rigplane] presentation "mobile" failed to load:', expect.any(Error),
    );
    expect(document.documentElement.dataset.designLanguage).toBe('segmentline');
    expect(document.documentElement.dataset.languageMode).toBe(committedLanguageMode);
    expect(document.documentElement.dataset.density).toBe('comfortable');

    // A later successful desktop request commits component metadata together.
    setLayout('standard');
    setWidth(1200);
    await complete('desktop-v2');
    expect(document.documentElement.dataset.designLanguage).toBe('studioline');
    expect(document.documentElement.dataset.density).toBe('dense');
    expect(getWorkspace().designLanguage).toBe('studioline');

    unmount(instance);
    consoleError.mockRestore();
  });
});
