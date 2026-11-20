import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { defineConfig } from '@playwright/test';
import { REQUIRED_MATRIX } from './tests/e2e/remote/no-skips-reporter';

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
// One directory per run: workers re-evaluate this config and inherit the
// runner's environment, so they reuse the runner's directory.
const LOG_DIR = (process.env.RIGPLANE_REMOTE_E2E_LOG_DIR ??= mkdtempSync(
  path.join(tmpdir(), 'rigplane-remote-e2e-logs-'),
));

export default defineConfig({
  testDir: './tests/e2e/remote',
  testMatch: /remote\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  repeatEach: REQUIRED_MATRIX.repeatEach,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: '../.playwright-output/remote',
  forbidOnly: true,
  reporter: [['list'], ['./tests/e2e/remote/no-skips-reporter.ts']],
  // The browsers of the required matrix (the reporter checks the same list).
  projects: REQUIRED_MATRIX.projects.map((name) => ({
    name,
    use: { browserName: name },
  })),
  webServer: {
    // --extra webrtc: aiortc backs the P0 answerer (WebConfig.webrtc_enabled).
    command: `uv run --extra webrtc python tests/e2e/remote_web_server.py --port ${PORT}`,
    cwd: '..',
    url: `http://127.0.0.1:${PORT}/api/v1/state`,
    reuseExistingServer: false,
    timeout: 90_000,
    // The L5 row reads the station's log back from a support bundle: the
    // station logs into a fresh directory instead of the host's Core log.
    env: { RIGPLANE_LOG_DIR: LOG_DIR },
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
