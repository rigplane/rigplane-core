import { defineConfig } from '@playwright/test';

/**
 * Operator-journey scenarios against the real WebServer with the in-memory
 * IC-7610 SerialMockRadio (tests/e2e/journey_web_server.py). Two servers are
 * started: a writable one, and a --read-only one on READ_ONLY_PORT, which the
 * spec addresses as READ_ONLY_URL. They serve frontend/dist, so build first:
 *
 *   npm run build
 *   npx playwright test -c ./playwright.journey.config.ts
 */
const PORT = 18766;
const READ_ONLY_PORT = 18767;

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
  webServer: [
    {
      command: `uv run python tests/e2e/journey_web_server.py --port ${PORT}`,
      cwd: '..',
      url: `http://127.0.0.1:${PORT}/api/v1/state`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `uv run python tests/e2e/journey_web_server.py --port ${READ_ONLY_PORT} --read-only`,
      cwd: '..',
      url: `http://127.0.0.1:${READ_ONLY_PORT}/api/v1/state`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
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
    // The PTT scenario starts the browser microphone before it sends ptt_on
    // (ManagedTxController.#sendPttOn); these flags give it a fake one.
    launchOptions: {
      args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
    },
  },
});
