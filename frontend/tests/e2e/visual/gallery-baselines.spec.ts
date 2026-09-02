/**
 * MOR-2219 — per-section visual baselines for the "looks gallery" (PR A: the
 * Button family). Distinct from `visual-baselines.spec.ts`, which drives the
 * isolated `fixtures/index.html` harness — these captures load the real
 * `App.svelte` demo route (`?demo=control-buttons` → `ControlButtonDemo.svelte`)
 * via the second `webServer` entry in `playwright.visual.config.ts`, so the
 * navigation below uses that server's full absolute URL rather than the
 * shared `baseURL` (which still points at the fixtures server's port).
 *
 * Each capture is element-scoped (`data-testid="gallery-*"` on the target
 * `<section class="demo-card">`), not full-page — a full-page screenshot
 * would dilute the comparator's 0.1%-of-frame floor (see
 * `fixtures/approved-baselines/README.md`) below what a single small
 * instrument needs.
 */
import { test, expect } from '@playwright/test';

const GALLERY_PORT = Number(process.env.RP_GALLERY_PORT ?? '5299');
const GALLERY_URL = `http://127.0.0.1:${GALLERY_PORT}/?demo=control-buttons`;

const SECTIONS = [
  'gallery-dotbutton',
  'gallery-fillbutton',
  'gallery-hardwarebutton',
  'gallery-hardwareplainbutton',
  'gallery-statusindicator',
];

for (const testid of SECTIONS) {
  test(testid, async ({ page }) => {
    await page.goto(GALLERY_URL, { waitUntil: 'load' });
    const el = page.locator(`[data-testid="${testid}"]`);
    await el.scrollIntoViewIfNeeded();
    await expect(el).toHaveScreenshot(`${testid}.png`, { animations: 'disabled' });
  });
}
