import { defineConfig } from '@playwright/test';

const ritE2e = process.env.RIGPLANE_FTX1_RIT_E2E === '1';
const ritPort = Number(process.env.RIGPLANE_FTX1_RIT_PORT ?? '18765');
if (ritE2e && !process.env.RIGPLANE_V2_URL) {
  // Workers inherit this, and the spec reads it as the fake server's origin.
  process.env.RIGPLANE_V2_URL = `http://127.0.0.1:${ritPort}/?ui=v2`;
}
const liveTarget = process.env.RIGPLANE_V2_URL;
const RIT_TEST = /FTX-1 exact RIT lattice in Chromium/;

export default defineConfig({
  testDir: '../tests/e2e',
  testMatch: /v2-ui-interactive\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  outputDir: '../.playwright-output',
  reporter: process.env.CI
    ? [['list'], ['junit', { outputFile: '../.playwright-output/junit.xml' }]]
    : 'list',
  // The FTX-1 RIT scenario runs against the in-memory fake server started
  // below; every other test in the spec file stays on the live-target project.
  projects: [
    { name: 'live-audit', grepInvert: RIT_TEST },
    {
      name: 'ftx1-rit',
      grep: RIT_TEST,
      retries: 1,
      timeout: 60_000,
      use: { browserName: 'chromium' },
    },
  ],
  webServer: ritE2e
    ? {
        command: `uv run python tests/e2e/ftx1_rit_web_server.py --port ${ritPort}`,
        cwd: '..',
        url: `http://127.0.0.1:${ritPort}/api/v1/state`,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      }
    : undefined,
  use: {
    baseURL: liveTarget ? new URL(liveTarget).origin : 'http://127.0.0.1:8080',
    viewport: { width: 1728, height: 1200 },
    // Bound every individual Playwright action so a missing/disabled control
    // fails fast and localized instead of stalling the whole 180s per-test
    // budget on an un-timed wait (e.g. scrollIntoViewIfNeeded on a button that
    // never renders).
    actionTimeout: 10_000,
    navigationTimeout: 10_000,
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    headless: true,
  },
});
