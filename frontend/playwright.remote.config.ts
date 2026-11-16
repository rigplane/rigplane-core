import { defineConfig } from '@playwright/test';

/**
 * Remote/browser E2E matrix (C5b PR-0, MOR-4493): the rows run in BOTH
 * Chromium and WebKit, every row twice (repeatEach: 2), no retries, and
 * the no-skips reporter fails the whole run when any row is skipped or
 * missing. Serves the real frontend/dist build through
 * tests/e2e/remote_web_server.py (IC-7610 LAN fake with stateful PTT,
 * managed TX composed, WebRTC answerer enabled); build the frontend first
 * (on the Mac mini, never the laptop):
 *
 *   npm ci && npm run build
 *   npx playwright test -c ./playwright.remote.config.ts
 */
const PORT = 18780;

export default defineConfig({
  testDir: './tests/e2e/remote',
  testMatch: /remote\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  repeatEach: 2,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: '../.playwright-output/remote',
  forbidOnly: true,
  reporter: [['list'], ['./tests/e2e/remote/no-skips-reporter.ts']],
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  webServer: {
    // --extra webrtc: aiortc backs the P0 answerer (WebConfig.webrtc_enabled).
    command: `uv run --extra webrtc python tests/e2e/remote_web_server.py --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/v1/state`,
    reuseExistingServer: false,
    timeout: 90_000,
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
    // No fake-device flags: the microphone stub in station.ts replaces
    // getUserMedia with an oscillator stream in BOTH browsers; everything
    // after getUserMedia is the production path.
  },
});
