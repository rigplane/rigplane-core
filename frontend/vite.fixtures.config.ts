/**
 * MOR-1070 — ADDITIVE vite config for the verification-only cockpit fixture
 * harness. `vite.config.ts` is untouched: this file is passed explicitly
 * (`vite --config vite.fixtures.config.ts`) and nothing in the app build,
 * `npm run dev`, `npm run lint`, `npm run check` or `vitest` reads it.
 *
 * It differs from the app config in exactly two ways:
 *   1. no dev-server proxy — the harness is offline by construction;
 *   2. the `fixtureStubs` plugin below re-points the FOUR live seams the
 *      cockpit's tree reaches through at `fixtures/stubs/*`.
 *
 * Everything else the capture renders is the shipped code: the real
 * `radio-view-model-adapter`, the real `derivePresentationCapabilities`, the
 * real `SemanticRadioSurfaces` / `VfoSurface` / `RxTxSurface`, the real i18n
 * catalog, the real `app.css` + `components-v2/theme` token layer.
 *
 * Stubbing by RESOLVED ABSOLUTE PATH (not by alias on the import specifier) is
 * deliberate: the semantic root reaches its command callbacks through the
 * existing adapter seam, while legacy layout callers retain the relative bus
 * seam. A string alias would match unrelated same-named relative imports.
 */
import path from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

const here = import.meta.dirname;
const semanticRoot = path.resolve(here, 'src/components-v2/wiring/SemanticRadioSurfaces.svelte');
const panelAdapters = path.resolve(here, 'src/lib/runtime/adapters/panel-adapters.ts');

/** production module (repo-relative) → fixture stub (repo-relative) */
const STUBS: Readonly<Record<string, string>> = {
  'src/lib/runtime/index.ts': 'fixtures/stubs/runtime.ts',
  'src/lib/runtime/tx-controller/app-host.ts': 'fixtures/stubs/app-host.ts',
  'src/lib/runtime/adapters/mod-input-tx-guard.svelte.ts': 'fixtures/stubs/mod-input-tx-guard.ts',
  'src/lib/runtime/adapters/panel-adapters.ts': 'fixtures/stubs/panel-adapters.ts',
};

function fixtureStubs(): Plugin {
  const table = new Map(Object.entries(STUBS).map(
    ([from, to]) => [path.resolve(here, from), path.resolve(here, to)],
  ));
  const stubPaths = new Set(table.values());
  return {
    name: 'mor-1070-fixture-stubs',
    enforce: 'pre',
    async resolveId(source, importer, options) {
      if (source.startsWith('\0') || !importer) return null;
      // Never re-enter for the stubs themselves.
      if (stubPaths.has(importer.split('?')[0])) return null;
      const resolved = await this.resolve(source, importer, { ...options, skipSelf: true });
      if (!resolved) return null;
      const target = resolved.id.split('?')[0];
      // The adapter has a broad shipped export surface. Only the semantic
      // composition root uses the fixture-only binder; every other importer
      // must resolve the real adapter module unchanged.
      if (target === panelAdapters && importer.split('?')[0] !== semanticRoot) return null;
      return table.get(target) ?? null;
    },
  };
}

export default defineConfig({
  // Root stays the app root so `svelte.config.js`, `public/` and the `../src`
  // imports resolve exactly as they do in a normal build; the harness is a
  // second HTML entry at `/fixtures/index.html`, not a second project.
  plugins: [fixtureStubs(), svelte()],
  resolve: {
    alias: { $lib: path.resolve(here, 'src/lib') },
    conditions: ['svelte', 'browser'],
  },
  server: { port: 5199, strictPort: true },
  build: {
    outDir: path.resolve(here, 'fixtures-dist'),
    emptyOutDir: true,
    rollupOptions: { input: path.resolve(here, 'fixtures/index.html') },
  },
});
