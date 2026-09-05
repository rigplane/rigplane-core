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
const PTT_SERVER = {
  held: {
    phase: 'active', intent: 'momentary', radioTx: 'on', txRisk: 'confirmed-on',
    fault: null, faultDetail: null, fresh: true, releaseRequired: true,
    remainingMs: 12_000, lastOperation: 'ptt_on',
  },
  rx: {
    phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none',
    fault: null, faultDetail: null, fresh: true, releaseRequired: false,
    remainingMs: null, lastOperation: 'force_receive',
  },
} as const;
/**
 * MOR-2243 — narrower than the peer-split glass's own 1280px native canvas,
 * so `computeStageScale` resolves below 1 (1100/1280 ≈ 0.859; the group's
 * 0.5 floor stays inert). At DESKTOP the same fixture resolves
 * `min(1280/1280, 800/540, MAX_STAGE_SCALE)` = exactly 1 — the identity
 * transform — so a baseline taken only there pins the one configuration in
 * which `ScaledStage`'s scaling path does not run.
 */
const NARROW = { width: 1100, height: 800 };
/** Fixed instant for `freezeClock`; only its constancy matters, not its value. */
const FROZEN_CLOCK = new Date('2026-01-01T14:32:00Z');

interface Spec {
  name: string;
  fixture: string;
  language?: 'studioline' | 'fieldline';
  mode?: 'light';
  viewport?: typeof DESKTOP;
  /**
   * Pin `Date` before the page loads. `PeerSplitLayout.svelte` renders a live
   * wall clock inside the glass (`setInterval(…, 30_000)`, a UTC and a local
   * `HH:MM` span), so without this its digits differ between the run that
   * captured the baseline and every later run — legitimate variation that
   * would spend part of the `maxDiffPixelRatio` budget every run and shrink
   * what remains for catching a real regression. Opt-in per spec: every
   * capture approved before MOR-2243 was taken without it.
   */
  freezeClock?: boolean;
}

/**
 * Topology × {RX, TX, fault} × language, per MOR-1090's acceptance text,
 * plus two additions from the independent verification's adequacy ranking
 * (`verify-mor-1090.md` §4/§10.6): a light-mode capture (light is a separate
 * token resolution from dark — see MOR-1073/1074 — and none of the original
 * 12 exercised it) and a fault × non-default-language capture (fault only
 * ever appeared in the default language before).
 *
 * MOR-2243 appends the two `peer-split-chassis` rows, which sit outside that
 * grid entirely: a different layout (`PeerSplitLayout`, not the cockpit),
 * captured at two frame sizes because at 1280x800 the stage transform
 * resolves to the identity.
 */
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
  {
    name: 'dual-main-sub--desktop--studioline--light',
    fixture: 'topology-2-main-sub',
    language: 'studioline',
    mode: 'light',
  },
  { name: 'tx-phase-fault--desktop--fieldline', fixture: 'tx-phase-fault', language: 'fieldline' },
  { name: 'peer-split-chassis--desktop', fixture: 'peer-split-chassis', freezeClock: true },
  { name: 'unified-instrument--desktop', fixture: 'lcd-unified-instrument' },
  { name: 'panadapter-first--desktop', fixture: 'lcd-panadapter-first' },
  {
    name: 'peer-split-chassis--1100x800',
    fixture: 'peer-split-chassis',
    viewport: NARROW,
    freezeClock: true,
  },
  { name: 'unified-instrument--1100x800', fixture: 'lcd-unified-instrument', viewport: NARROW },
  { name: 'panadapter-first--1100x800', fixture: 'lcd-panadapter-first', viewport: NARROW },
];

for (const spec of COCKPIT) {
  test(spec.name, async ({ page }) => {
    // Before `goto`: the clock is installed into the page's init scripts, so
    // it has to be in place before any of the page's own script reads `Date`.
    if (spec.freezeClock) await page.clock.setFixedTime(FROZEN_CLOCK);
    await page.setViewportSize(spec.viewport ?? DESKTOP);
    const url = `/fixtures/index.html?fixture=${spec.fixture}&theme=v2`
      + (spec.language ? `&language=${spec.language}` : '')
      + (spec.mode ? `&mode=${spec.mode}` : '');
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForSelector('body[data-harness-ready="true"]');
    await expect(page).toHaveScreenshot(`${spec.name}.png`, { animations: 'disabled', caret: 'hide' });
  });
}

/** MOR-2364: real line boxes catch phone wrapping even if a PNG is re-pinned. */
test('VFO phone cue preserves one-line fit and freshness geometry', async ({ page }) => {
  await page.setViewportSize(PHONE);
  await page.goto('/fixtures/index.html?fixture=topology-2-main-sub&theme=v2');
  await page.waitForSelector('body[data-harness-ready="true"]');
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const tiles = [...document.querySelectorAll<HTMLElement>('[data-vfo-tile]')];
    const cues = tiles.map((tile) => tile.querySelector<HTMLElement>('[data-vfo-stale-cue]')!);
    const rect = (el: Element) => {
      const { x, y, width, height } = el.getBoundingClientRect();
      return { x, y, width, height };
    };
    const geometry = () => tiles.map((tile) => ({ tile: rect(tile),
      content: [...tile.children].filter((el) => !el.matches('[data-vfo-stale-cue]')).map(rect),
    }));
    const lineCounts = () => tiles.flatMap((tile) =>
      [...tile.querySelectorAll('.vfo-role, .vfo-mode, .vfo-select')].map((el) => {
        const range = document.createRange(); range.selectNodeContents(el);
        return new Set([...range.getClientRects()].map((box) => box.top)).size;
      }));
    // Removing the cue models the pre-feature footprint, independent of host fonts.
    cues.forEach((cue) => { cue.style.display = 'none'; });
    const withoutCue = geometry();
    cues.forEach((cue) => { cue.style.display = ''; });
    const current = geometry(); const lines = lineCounts();
    // This is a CSS layout probe; component tests drive real observation transitions.
    cues.forEach((cue) => cue.classList.add('stale'));
    const stale = geometry();
    const clear = cues.map((cue, index) => {
      const marker = cue.firstElementChild!.getBoundingClientRect();
      const tile = tiles[index].getBoundingClientRect();
      if (marker.width <= 0 || marker.height <= 0 || marker.left < tile.left
        || marker.right > tile.right || marker.top < tile.top || marker.bottom > tile.bottom) return false;
      return [...tiles[index].children].filter((el) => el !== cue).every((el) => {
        const box = el.getBoundingClientRect();
        return marker.right <= box.left || marker.left >= box.right
          || marker.bottom <= box.top || marker.top >= box.bottom;
      });
    });
    const visible = cues.map((cue) => getComputedStyle(cue).visibility);
    cues.forEach((cue) => cue.classList.remove('stale'));
    return { withoutCue, current, stale, restored: geometry(), lines, clear, visible };
  });
  expect(result.lines.every((count) => count === 1)).toBe(true);
  expect(result.current).toEqual(result.withoutCue);
  expect(result.stale).toEqual(result.current);
  expect(result.restored).toEqual(result.current);
  expect(result.clear).toEqual([true, true, true, true]);
  expect(result.visible).toEqual(['visible', 'visible', 'visible', 'visible']);
});

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
  await expect.poll(() => page.evaluate(() => window.__ptt.deliveryTrace()))
    .toEqual(['ws.ptt_on']);
  await page.evaluate((snapshot) => window.__ptt.emitServerSnapshot(snapshot), PTT_SERVER.held);
  await page.waitForSelector('.ptt-fab.ptt-fab-held', { timeout: 2_000 });
  await expect(page).toHaveScreenshot('ptt-held--mobile.png', { animations: 'disabled' });
  await page.mouse.up();
  await expect.poll(() => page.evaluate(() => window.__ptt.deliveryTrace()))
    .toEqual(['ws.ptt_on', 'ws.ptt_off']);
  await page.evaluate((snapshot) => window.__ptt.emitServerSnapshot(snapshot), PTT_SERVER.rx);
});
