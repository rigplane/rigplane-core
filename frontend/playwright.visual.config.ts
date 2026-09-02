/// <reference types="node" />

/**
 * MOR-1090 — deterministic pixel-diff comparison for the fixtures/ harness
 * (MOR-1070/1085/1087/1088). Distinct from `playwright.config.ts` (live-backend
 * v2 UI audit) and `playwright.i18n.config.ts` (built-dist locale smoke,
 * string-only floor today): this config drives the SAME additive fixtures
 * vite server `fixtures/capture.mjs` uses, but through Playwright Test's own
 * `expect(page).toHaveScreenshot()` comparator instead of hand-rolled PNG
 * output — reusing the bundled pixelmatch comparator already shipped inside
 * `@playwright/test` rather than adding a new npm dependency.
 *
 * Threshold calibration — see `fixtures/approved-baselines/README.md` for the
 * full 3x-clean-run + sabotage data. Per-pixel `threshold: 0.2` is
 * pixelmatch's own default (perceptual YIQ distance). At that per-pixel
 * setting, three same-host reruns measured a same-tree noise ceiling of
 * ≤123 differing pixels (≤0.012% of a 1280×800 frame) from antialiasing —
 * the MOR-1088/MOR-1282 finding that raw PNG bytes aren't a valid identity
 * check on this host. `maxDiffPixelRatio: 0.001` sits ~8x above that ceiling
 * while still catching a real localized change (a 60×60px, 0.35%-of-frame
 * sabotage went red — see the README).
 *
 * `snapshotPathTemplate` drops Playwright's default per-platform suffix
 * deliberately: this program keeps ONE approved baseline set, re-pinned as a
 * reviewed act (not auto-forked per OS) — see the "Platform" section of the
 * README for why, and MOR-1090's acceptance criteria for the re-pin cadence.
 */
import { defineConfig } from '@playwright/test';

const PORT = Number(process.env.RP_VISUAL_PORT ?? '5399');
// MOR-2219 — second webServer for the "looks gallery" baselines
// (gallery-baselines.spec.ts). The fixtures server above CAN technically
// reach the real App.svelte demo route too — vite.fixtures.config.ts's
// `root` stays the app root ("not a second project"), so `GET /` there
// serves the real index.html, not a 404. It is deliberately not reused
// here: App.svelte calls `provideAppTxControllerHost` from
// `$lib/runtime/tx-controller/app-host` unconditionally, at script top
// level, on EVERY load including the demo route — every
// `demoMode === 'control-buttons'` bailout in that file runs inside an
// `$effect`/`onMount` callback, which fires after the script's
// synchronous body, so none of them run in time to skip that call.
// `fixtureStubs()` in vite.fixtures.config.ts re-points
// `tx-controller/app-host.ts` (plus three other runtime modules) to
// `fixtures/stubs/app-host.ts`, whose only export is
// `getAppTxController` — not `provideAppTxControllerHost`. On the
// fixtures server this import fails to resolve. A dedicated, unstubbed `vite` (no --config
// override) runs the actual production entry instead, on a port that
// doesn't collide with this fixtures port, the fixtures dev port (5199),
// the app's own default dev port (5173), or capture-ptt.mjs's port
// (5299).
const GALLERY_PORT = Number(process.env.RP_GALLERY_PORT ?? '5499');
// Exported so global-teardown.ts's manifest reports the SAME numbers this
// config actually runs with — never a hand-copied, driftable duplicate.
export const COMPARATOR = { threshold: 0.2, maxDiffPixelRatio: 0.001 };

export default defineConfig({
  testDir: './tests/e2e/visual',
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  snapshotPathTemplate: 'fixtures/approved-baselines/{arg}{ext}',
  expect: {
    timeout: 5_000,
    toHaveScreenshot: COMPARATOR,
  },
  reporter: process.env.CI ? [['list'], ['html', { open: false }]] : 'list',
  globalTeardown: './tests/e2e/visual/global-teardown.ts',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    headless: true,
    // MOR1090_CHROMIUM: same escape hatch as capture.mjs's MOR1070_CHROMIUM,
    // for hosts where the exact pinned revision isn't installed. Unset in CI.
    launchOptions: process.env.MOR1090_CHROMIUM
      ? { executablePath: process.env.MOR1090_CHROMIUM } : {},
  },
  webServer: [
    {
      command: `npx vite --config vite.fixtures.config.ts --port ${PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${PORT}/fixtures/index.html?fixture=topology-2-main-sub&theme=v2`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
    {
      command: `npx vite --port ${GALLERY_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${GALLERY_PORT}/?demo=control-buttons`,
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
    },
  ],
});
