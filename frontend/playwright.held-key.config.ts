import { defineConfig } from '@playwright/test';

/**
 * MOR-4451 held-key acceptance. The spec owns its server processes
 * (startJourneyServer on tests/e2e/panorama_web_server.py --response-delay),
 * so no webServer block here. Serves frontend/dist — build first:
 *
 *   npm run build
 *   npx playwright test -c ./playwright.held-key.config.ts
 */
export default defineConfig({
  testDir: './tests/e2e/held-key', testMatch: 'panorama-held-key.spec.ts',
  workers: 1, retries: 0, timeout: 120_000, reporter: 'list',
  outputDir: '../.playwright-output/held-key',
  use: { browserName: 'webkit', headless: true, viewport: { width: 1728, height: 1200 },
    locale: 'en-US', actionTimeout: 10_000 },
});
