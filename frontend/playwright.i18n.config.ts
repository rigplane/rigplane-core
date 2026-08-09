/// <reference types="node" />

/**
 * Playwright config for the i18n visual smoke suite (RP-ML-006).
 *
 * Distinct from `playwright.config.ts` because the v2 UI audit suite
 * needs a LIVE backend (`RIGPLANE_V2_URL`) and runs serially with a
 * long per-test timeout, while this suite:
 *
 *   - Has NO live backend; HTTP/WS are stubbed at the page boundary.
 *   - Runs against the built static frontend served by `vite preview`.
 *   - Targets a tight time budget (entire suite under ~2-3 minutes per
 *     the RP-ML-006 acceptance criteria).
 *
 * The `webServer` block starts `vite preview` on port 4173 (vite's
 * default preview port) and tears it down after the run. Playwright
 * checks the process is healthy via the `baseURL` GET.
 */

import { defineConfig, devices } from '@playwright/test';

const PREVIEW_PORT = Number(process.env.RP_I18N_PREVIEW_PORT ?? '4173');
const BASE_URL = `http://127.0.0.1:${PREVIEW_PORT}`;

// MOR-1400 Slice B uses the same calibrated comparator as the separately
// maintained fixture lane, but keeps its snapshots under the real-App suite.
// A production screenshot can never be substituted with a fixtures/ capture.
export const PRODUCTION_VISUAL_COMPARATOR = {
  threshold: 0.2,
  maxDiffPixelRatio: 0.001,
};

export default defineConfig({
  testDir: './tests/e2e/i18n',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: {
    timeout: 5_000,
    toHaveScreenshot: PRODUCTION_VISUAL_COMPARATOR,
  },
  // Do not let Playwright silently fork expected production images per host:
  // accepted images are generated/re-pinned only by the Linux Core runner.
  snapshotPathTemplate: 'tests/e2e/i18n/__screenshots__/production-design-language/{arg}{ext}',
  outputDir: './tests/e2e/i18n/.output',
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    locale: 'en-US',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      // Device descriptors are project-level overrides, so repeat every
      // production pixel input here rather than relying on root `use` merge
      // behavior. The app's selected theme supplies the light-mode cells.
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 800 },
        deviceScaleFactor: 1,
        colorScheme: 'dark',
        locale: 'en-US',
        timezoneId: 'UTC',
      },
    },
  ],
  webServer: {
    // The wrapper snapshots `dist/` to a temp directory, then launches
    // Vite preview with `--strictPort` so the suite serves one immutable
    // build and fails fast if the requested port is occupied.
    command: `node scripts/i18n-preview-server.mjs --port ${PREVIEW_PORT} --host 127.0.0.1`,
    url: BASE_URL,
    // This suite updates visual baselines and depends on serving the
    // freshly built Core frontend. Reusing an arbitrary local process on
    // this port can produce blank 404 pages that only fail later at the
    // app-shell visibility gate.
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
    timeout: 60_000,
  },
});
