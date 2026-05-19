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

export default defineConfig({
  testDir: './tests/e2e/i18n',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  outputDir: './tests/e2e/i18n/.output',
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    // `--strictPort` ensures we fail fast if 4173 is occupied, instead
    // of silently rolling to 4174 and letting Playwright probe the
    // wrong URL.
    command: `npx vite preview --port ${PREVIEW_PORT} --strictPort --host 127.0.0.1`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
    timeout: 60_000,
  },
});
