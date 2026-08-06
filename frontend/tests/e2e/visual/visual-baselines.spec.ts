/**
 * MOR-1090 — approved pixel-diff baselines.
 *
 * Representative slice of the MOR-1070/1085 60-capture matrix (not the full
 * matrix — see fixtures/approved-baselines/README.md for why and for the
 * rationale table), plus the MOR-1088 mobile PTT pair. Every cockpit case
 * drives the SAME `fixtures/index.html` entry `capture.mjs` uses, so the
 * fixture ids and URL contract are shared, not re-invented. `runAssertions`
 * (the deterministic manifest/assertion layer) already gates behavior on
 * every push via `capture.mjs`'s own harness; this file is the perceptual
 * pixel layer on top, per the MOR-1087/1282/1088 ruling that raw PNG bytes
 * are not a valid identity check on this host.
 */
import { test, expect } from '@playwright/test';

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 375, height: 812 };

interface Spec {
  name: string;
  fixture: string;
  language?: 'studioline' | 'fieldline';
  viewport?: typeof DESKTOP;
}

/** Topology × {RX, TX, fault} × language, per MOR-1090's acceptance text. */
const COCKPIT: Spec[] = [
  { name: 'dual-main-sub--desktop', fixture: 'topology-2-main-sub' },
  { name: 'topology-1-single--desktop', fixture: 'topology-1-single' },
  { name: 'topology-2-ab-shared--desktop', fixture: 'topology-2-ab-shared' },
  { name: 'tx-phase-rx--desktop', fixture: 'tx-phase-rx' },
  { name: 'tx-phase-tx--desktop', fixture: 'tx-phase-tx' },
  { name: 'tx-phase-fault--desktop', fixture: 'tx-phase-fault' },
  { name: 'dual-main-sub--desktop--studioline', fixture: 'topology-2-main-sub', language: 'studioline' },
  { name: 'dual-main-sub--desktop--fieldline', fixture: 'topology-2-main-sub', language: 'fieldline' },
  { name: 'tx-phase-tx--desktop--studioline', fixture: 'tx-phase-tx', language: 'studioline' },
  { name: 'dual-main-sub--phone-portrait', fixture: 'topology-2-main-sub', viewport: PHONE },
];

for (const spec of COCKPIT) {
  test(spec.name, async ({ page }) => {
    await page.setViewportSize(spec.viewport ?? DESKTOP);
    const url = `/fixtures/index.html?fixture=${spec.fixture}&theme=v2`
      + (spec.language ? `&language=${spec.language}` : '');
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('body[data-harness-ready="true"]');
    await expect(page).toHaveScreenshot(`${spec.name}.png`, { animations: 'disabled', caret: 'hide' });
  });
}

/** The MOR-1088 mobile PTT pair: idle FAB and a real pointer-held FAB. */
test('ptt-idle--mobile', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/fixtures/ptt-harness.html', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  await expect(page).toHaveScreenshot('ptt-idle--mobile.png', { animations: 'disabled' });
});

test('ptt-held--mobile', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/fixtures/ptt-harness.html', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  // A real `page.mouse` press (not a hand-built PointerEvent) so Chromium
  // grants an actual active pointer — `PttFab`'s `setPointerCapture` call
  // throws on a synthetic pointerId with no matching live pointer. Held past
  // the 50ms guard, mirroring capture-ptt.mjs's own gesture technique.
  const box = (await page.locator('.ptt-fab').boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForSelector('.ptt-fab.ptt-fab-held', { timeout: 2_000 });
  await expect(page).toHaveScreenshot('ptt-held--mobile.png', { animations: 'disabled' });
  await page.mouse.up();
});
