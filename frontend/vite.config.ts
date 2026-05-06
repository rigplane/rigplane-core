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
    // that does module-scope ``vi.mock(...)`` is vulnerable to load
    // ordering — a sibling file that imports the real module first
    // pins it in the cache and the hoisted mock becomes a no-op.
    // Rather than fixing each affected file (treadmill, and any
    // new test file that uses ``vi.mock`` becomes a future flake),
    // route the known-sensitive files through a small isolated pool
    // and keep the rest fast.  Issue #771.
    projects: [
      {
        extends: true,
        test: {
          name: 'fast',
          environment: 'jsdom',
          include: ['src/**/*.test.ts'],
          exclude: [
            'src/components-v2/wiring/__tests__/keyboard-wiring.test.ts',
            'src/components-v2/wiring/__tests__/vfo-wiring.test.ts',
            'src/components-v2/wiring/__tests__/focus-mode-race.test.ts',
            'src/components-v2/vfo/__tests__/VfoOps.test.ts',
            'src/components-v2/panels/__tests__/AudioRoutingControl.test.ts',
            'src/lib/stores/radio.svelte.test.ts',
            'src/lib/runtime/__tests__/frontend-runtime.test.ts',
            'src/lib/radio/pending-focus.test.ts',
            // *.component(.svelte).test.ts mount real Svelte components and
            // depend on store mocks that vary across the suite. Under
            // ``isolate: false`` sibling tests' inconsistent ``vi.mock(...)``
            // definitions (e.g. ``capabilities.svelte`` returning
            // ``hasCapability: false`` in some files vs ``true`` here) leak
            // via the shared module cache and the component renders with the
            // wrong capability flags. Failure mode: "component renders
            // fallback markup" rather than "test asserts wrong" — extremely
            // hard to diagnose without isolation. Reproduces only on
            // low-parallelism CI (Ubuntu 2-core), passes locally on macOS
            // with 16-core worker spread. See #771 for the original symptom;
            // this expands the sensitive set after the post-#1385 marathon
            // exposed SpectrumToolbar / CwPanel / DspPanel / SpectrumPanel /
            // MobileRadioLayout / BandPlanOverlay component tests.
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
          include: [
            'src/components-v2/wiring/__tests__/keyboard-wiring.test.ts',
            'src/components-v2/wiring/__tests__/vfo-wiring.test.ts',
            'src/components-v2/wiring/__tests__/focus-mode-race.test.ts',
            'src/components-v2/vfo/__tests__/VfoOps.test.ts',
            'src/components-v2/panels/__tests__/AudioRoutingControl.test.ts',
            'src/lib/stores/radio.svelte.test.ts',
            'src/lib/runtime/__tests__/frontend-runtime.test.ts',
            'src/lib/radio/pending-focus.test.ts',
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
