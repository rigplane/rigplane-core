import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e/journey', testMatch: /panorama-.*\.spec\.ts/,
  workers: 1, retries: 0, timeout: 90_000, reporter: 'list',
  outputDir: '../.playwright-output/panorama',
  use: { browserName: 'webkit', headless: true, viewport: { width: 1728, height: 1200 },
    locale: 'en-US', actionTimeout: 10_000 },
});
