import { defineConfig } from '@playwright/test';

// MOR-4388 acceptance matrix: one generated test per cell, each owning a
// loopback panorama server on a per-worker port range. The fixture perf
// spec (panorama-fixture-perf.spec.ts) runs under its own config with the
// vite fixtures webServer instead.
export default defineConfig({
  testDir: './tests/e2e/panorama-matrix',
  testMatch: 'panorama-matrix.spec.ts',
  workers: Number(process.env.PANORAMA_MATRIX_WORKERS ?? 2),
  retries: 0,
  timeout: 210_000,
  outputDir: process.env.PANORAMA_MATRIX_OUTPUT ?? '../.playwright-output/panorama-matrix',
  reporter: 'list',
  use: {
    browserName: 'webkit',
    headless: true,
    viewport: { width: 1728, height: 1200 },
    locale: 'en-US',
    actionTimeout: 10_000,
    deviceScaleFactor: 1,
  },
});
