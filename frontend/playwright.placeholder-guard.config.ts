import { defineConfig } from '@playwright/test';

/**
 * MOR-2677 — placeholder-guard part 2: the whole-page scan configuration.
 *
 * Drives the SAME additive fixtures vite server (`vite.fixtures.config.ts`)
 * and the SAME face × state × language matrix from `fixtures/catalog.ts`'s
 * `HARNESS_REACH` as part 1's smoke config
 * (`playwright.harness-reach.config.ts`), but READS the rendered page:
 * every text node, `<option>`/SVG `<text>`/`<title>` (all covered by the
 * text-node walk), `aria-label`, `aria-valuetext`, `title`, and the text of
 * each `aria-describedby` target, against `src/lib/placeholder-token-rule.ts`
 * and the `offenders.json` ratchet.
 *
 * No screenshots, one worker, the already-provisioned Chromium (the same
 * MOR1090_CHROMIUM escape hatch as the other configs). Wired into
 * `quick.yml`'s frontend leg as the blocking
 * `npm run test:e2e:placeholder-guard` step, after the capture step.
 */
const PORT = Number(process.env.RP_PLACEHOLDER_GUARD_PORT ?? '5601');

export default defineConfig({
  testDir: './tests/e2e/placeholder-guard',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  reporter: process.env.CI ? [['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    headless: true,
    launchOptions: process.env.MOR1090_CHROMIUM
      ? { executablePath: process.env.MOR1090_CHROMIUM } : {},
  },
  webServer: {
    command: `npx vite --config vite.fixtures.config.ts --port ${PORT} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${PORT}/fixtures/index.html?fixture=topology-2-main-sub&theme=v2`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
