import { defineConfig } from '@playwright/test';

/**
 * MOR-2676 — placeholder-guard part 1 smoke configuration.
 *
 * Drives the SAME additive fixtures vite server (`vite.fixtures.config.ts`)
 * the visual baselines use, but asserts TEXT ONLY: no screenshots, one
 * worker, the already-provisioned Chromium (the same escape hatch as
 * `playwright.visual.config.ts`'s MOR1090_CHROMIUM). What runs here is the
 * face × state × language LOAD matrix from `fixtures/catalog.ts`'s
 * `HARNESS_REACH` — every shipped face over the real adapters, with the
 * all-unread and all-unsupported states, once per design-language overlay.
 *
 * Deliberately NOT wired into `quick.yml` (that is MOR-2677, part 2, with
 * its own page-scan config); this config exists so the smoke test is
 * runnable on its own today:
 *
 *   npx playwright test -c playwright.harness-reach.config.ts
 */
const PORT = Number(process.env.RP_HARNESS_REACH_PORT ?? '5599');

export default defineConfig({
  testDir: './tests/e2e/harness-reach',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  reporter: process.env.CI ? [['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    headless: true,
    launchOptions: process.env.MOR1090_CHROMIUM
      ? { executablePath: process.env.MOR1090_CHROMIUM } : {},
  },
  webServer: {
    command: `npx vite --config vite.fixtures.config.ts --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/fixtures/index.html?fixture=topology-2-main-sub&theme=v2`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
