/**
 * MOR-1070 — ADDITIVE vite config for the verification-only cockpit fixture
 * harness. `vite.config.ts` is untouched: this file is passed explicitly
 * (`vite --config vite.fixtures.config.ts`) and nothing in the app build,
 * `npm run dev`, `npm run lint`, `npm run check` or `vitest` reads it.
 *
 * It differs from the app config in exactly three ways:
 *   1. no dev-server proxy — the harness is offline by construction;
 *   2. the `fixtureStubs` plugin below re-points the FOUR live seams the
 *      cockpit's tree reaches through at `fixtures/stubs/*`.
 *   3. the `fixtureCompanionProbeStubs` plugin (MOR-1430) answers the two
 *      companion-app probe endpoints deterministically instead of letting
 *      them fall through to the dev server's 404 — see that plugin's header
 *      comment for why.
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

/**
 * MOR-1430 — deterministic answers for the companion-app probe endpoints.
 *
 * The real app fetches two same-origin routes that only a companion process
 * (RC-28 / the local-extensions host) ever actually serves:
 *   - `GET  /api/local/v1/ui/manifest`      (`$lib/local-extensions/manifest.ts`)
 *   - `PUT  /api/local/v1/rc28/tuning-step` (`$lib/stores/tuning.svelte.ts`)
 *
 * Both call sites already treat "no companion" as a normal, silent condition
 * — the manifest loader returns `null` on any non-ok response or unparseable
 * body, and the tuning-step sync only `.catch()`s to swallow network-level
 * failures. Neither checks `response.ok`, so an HTTP error status never
 * throws in application code. But the browser itself does not know that: any
 * fetch/XHR that resolves with a >=400 status gets its own
 * "Failed to load resource: the server responded with a status of ###"
 * console entry, regardless of how the page-level JS handles the response.
 * On the fixture harness's offline dev server (no proxy, see file header),
 * both routes are unhandled and fall through to Vite's 404 — which the
 * capture harness's clean-console gate (`capture.mjs`) then fails on, for a
 * *reference* fixture that has nothing to do with either endpoint. Because
 * which fixture happens to still be mid-navigation when the probe's response
 * lands is a race, the failing fixture roams from run to run (MOR-1430).
 *
 * The fix is to make the companion's ABSENCE a deterministic, silent 2xx
 * here — not to special-case 404s in the console-error gate. A blanket
 * allowance would hide a real regression (e.g. a typo'd fetch URL) behind
 * "oh, that's just the companion". Answering with 204 No Content: `.ok` is
 * true so both call sites take their normal no-op path, and there is no body
 * to keep in sync with `LocalExtensionManifest`'s shape as it evolves. Every
 * OTHER route keeps the strict default 404 — this is a two-route allowlist,
 * not a tolerance policy.
 */
function fixtureCompanionProbeStubs(): Plugin {
  const STUBBED_COMPANION_ROUTES: ReadonlySet<string> = new Set([
    'GET /api/local/v1/ui/manifest',
    'PUT /api/local/v1/rc28/tuning-step',
  ]);
  return {
    name: 'mor-1430-fixture-companion-probe-stubs',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const [pathname] = (req.url ?? '').split('?');
        const key = `${req.method} ${pathname}`;
        if (!STUBBED_COMPANION_ROUTES.has(key)) {
          next();
          return;
        }
        res.statusCode = 204;
        res.end();
      });
    },
  };
}

export default defineConfig({
  // Root stays the app root so `svelte.config.js`, `public/` and the `../src`
  // imports resolve exactly as they do in a normal build; the harness is a
  // second HTML entry at `/fixtures/index.html`, not a second project.
  plugins: [fixtureStubs(), fixtureCompanionProbeStubs(), svelte()],
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
