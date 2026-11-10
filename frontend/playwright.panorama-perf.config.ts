import { defineConfig } from '@playwright/test';

// MOR-4388 invariant 7: the recorded-fixture renderer-trace baseline. The
// fixtures vite server serves the page the production renderer modules are
// imported from (same pattern as playwright.renderer.config.ts).
export default defineConfig({
  testDir: './tests/e2e/panorama-matrix',
  testMatch: 'panorama-fixture-perf.spec.ts',
  workers: 1,
  retries: 0,
  timeout: 120_000,
  outputDir: process.env.PANORAMA_MATRIX_OUTPUT ?? '../.playwright-output/panorama-matrix',
  reporter: 'list',
  use: { browserName: 'webkit', headless: true, viewport: { width: 1920, height: 900 } },
  webServer: {
    command: 'npx vite --config vite.fixtures.config.ts --port 5613 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:5613/fixtures/spectrum-witness.html',
    timeout: 30_000,
  },
});
