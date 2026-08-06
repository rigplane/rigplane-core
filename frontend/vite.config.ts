/// <reference types="vitest/config" />
import path from 'path'
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
// PWA disabled — Service Worker interferes with fetch on iOS Safari via Tailscale
// import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    svelte(),
  ],
  resolve: {
    alias: {
      '$lib': path.resolve(__dirname, './src/lib'),
    },
    conditions: ['svelte', 'browser'],
  },
  server: {
    proxy: {
      '/api/v1/ws': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
      '/api/v1/scope': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
      '/api/v1/meters': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
      '/api/v1/audio': {
        target: 'ws://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    target: ['es2020', 'safari14'],
  },
  test: {
    // Split the test suite into two projects to contain the cost
    // of test-file isolation.  Under ``isolate: false`` (PR #707 —
    // ~10× faster) modules are cached across test files; any file
    // that does module-scope ``vi.mock(...)``, ``vi.stubGlobal(...)``,
    // a global spy (e.g. on ``requestAnimationFrame``), or a real
    // mounted Svelte component is vulnerable to load/execution
    // ordering — a sibling file can pin the real module in the shared
    // cache, leave global state dirty, or race a live effect loop, and
    // the failure shows up as "flips red with no production change."
    //
    // POOL-MEMBERSHIP CONVENTION (MOR-1272): rather than hand-maintain
    // two mirrored enumerated file lists here (a merge-collision magnet
    // — every branch that adds a sensitive test conflicts with every
    // other branch touching this file), pool membership is decided by
    // filename: any test file named ``*.isolated.test.ts`` (anywhere
    // under src/) runs in the ``isolated`` project and is excluded from
    // ``fast``. To add a new order-dependent test, name it
    // ``<subject>.isolated.test.ts`` — no config edit required.
    // ``*.component.test.ts`` / ``*.component.svelte.test.ts`` predate
    // this convention and use the same glob mechanism for the same
    // reason (real mounted components, see #771); left as-is.
    // Full historical inventory of why each current isolated file
    // needed it lives in git blame / MOR-1262 / MOR-1272 ticket history,
    // not here.
    projects: [
      {
        extends: true,
        test: {
          name: 'fast',
          environment: 'jsdom',
          // Shadows the process-wide `--localstorage-file` store with a
          // per-environment in-memory one — see vitest.setup.ts.
          setupFiles: ['./vitest.setup.ts'],
          include: ['src/**/*.test.ts'],
          exclude: [
            'src/**/*.isolated.test.ts',
            'src/**/*.component.test.ts',
            'src/**/*.component.svelte.test.ts',
          ],
          pool: 'threads',
          isolate: false,
        },
      },
      {
        extends: true,
        test: {
          name: 'isolated',
          environment: 'jsdom',
          // Same storage shadowing as `fast`; under `isolate: true` this is
          // what makes web storage genuinely per-file — the Node store is
          // shared across worker threads and defeated the isolation.
          setupFiles: ['./vitest.setup.ts'],
          include: [
            'src/**/*.isolated.test.ts',
            'src/**/*.component.test.ts',
            'src/**/*.component.svelte.test.ts',
          ],
          pool: 'threads',
          isolate: true,
        },
      },
    ],
  },
})
