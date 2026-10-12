import { defineConfig } from '@playwright/test';

/**
 * Operator-journey scenario against the real WebServer with the in-memory
 * IC-7610 SerialMockRadio (tests/e2e/journey_web_server.py). It serves
 * frontend/dist, so build first:
 *
 *   npm run build
 *   npx playwright test -c ./playwright.journey.config.ts
 */
const PORT = 18766;

export default defineConfig({
  testDir: './tests/e2e/journey',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: '../.playwright-output',
  reporter: 'list',
  projects: [{ name: 'journey', use: { browserName: 'chromium' } }],
  webServer: {
    command: `uv run python tests/e2e/journey_web_server.py --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/v1/state`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: 'en-US',
    viewport: { width: 1728, height: 1200 },
    actionTimeout: 10_000,
    navigationTimeout: 10_000,
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    headless: true,
  },
});
