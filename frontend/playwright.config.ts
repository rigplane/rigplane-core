import { defineConfig } from '@playwright/test';

const liveTarget = process.env.RIGPLANE_V2_URL ?? process.env.ICOM_LAN_V2_URL;

export default defineConfig({
  testDir: '../tests/e2e',
  testMatch: /v2-ui-interactive\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  outputDir: '../.playwright-output',
  reporter: 'list',
  use: {
    baseURL: liveTarget ? new URL(liveTarget).origin : 'http://127.0.0.1:8080',
    viewport: { width: 1728, height: 1200 },
    trace: 'retain-on-failure',
    screenshot: 'off',
    video: 'off',
    headless: true,
  },
});
