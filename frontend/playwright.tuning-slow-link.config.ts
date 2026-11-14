import { defineConfig } from '@playwright/test';

/**
 * MOR-4393 slow-link tuning repro. The spec owns its server process
 * (startJourneyServer on tests/e2e/panorama_web_server.py --response-delay),
 * so no webServer block here. Serves frontend/dist — build first:
 *
 *   npm run build
 *   npx playwright test -c ./playwright.tuning-slow-link.config.ts
 */
export default defineConfig({
  testDir: './tests/e2e/slow-link', testMatch: 'tuning-steps-slow-link.spec.ts',
  workers: 1, retries: 0, timeout: 120_000, reporter: 'list',
  outputDir: '../.playwright-output/tuning-slow-link',
  use: { browserName: 'webkit', headless: true, viewport: { width: 1728, height: 1200 },
    locale: 'en-US', actionTimeout: 10_000 },
});
