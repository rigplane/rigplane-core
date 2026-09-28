import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

const workspace = { version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'github-light' };
const { sub: _unusedSub, ...singleReceiverState } = mockState;
const state = {
  ...singleReceiverState,
  main: { ...mockState.main, freqHz: 14_035_720, mode: 'CW', filter: 3,
    filterWidth: 150, unselectedVfo: { freqHz: 14_332_000, mode: 'USB', filterNum: 1, dataMode: 0 } },
} satisfies Omit<ServerState, 'sub'>;
const capabilities = { ...mockCapabilities, model: 'IC-7300', receivers: 1,
  vfoScheme: 'ab', vfoReadback: 'selected_unselected', audioTx: true, audioTxRoute: 'usb',
  audioTxRequiredModInputSource: null } satisfies Capabilities;
const managedTransmit = {
  schemaVersion: 1, sampledAt: '2026-09-05T02:00:21.182Z',
  managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
    lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
  txObservation: { observedPtt: 'off' },
};

// Optional private acceptance capture replays the original HTTP payloads unchanged.
const capture = process.env.RP_MOBILE_OBSERVED_CAPTURE
  ? JSON.parse(readFileSync(process.env.RP_MOBILE_OBSERVED_CAPTURE, 'utf8')).before : null;

test.use({ hasTouch: true, isMobile: true });

async function prepare(page: Page) {
  const selectedState = capture?.state.body ?? state;
  const writes: string[] = [];
  await page.addInitScript((value) => {
    if (!localStorage.getItem('rigplane:workspace')) {
      localStorage.setItem('rigplane:workspace', JSON.stringify(value));
    }
    localStorage.setItem('rigplane.i18n.locale', 'en-US');
  }, workspace);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET') {
      // The local tuning-step preference is not a radio command, but is isolated too.
      if (pathname !== '/api/local/v1/rc28/tuning-step') writes.push(`${request.method()} ${pathname}`);
      await route.fulfill({ status: 404, body: '{}' });
      return;
    }
    const responses: Record<string, unknown> = {
      '/api/v1/state': selectedState,
      '/api/v1/capabilities': capture?.capabilities.body ?? capabilities,
      '/api/v1/managed-transmit': capture?.['managed-transmit'].body ?? managedTransmit,
      '/api/v1/info': mockInfo,
    };
    await route.fulfill({ status: pathname.startsWith('/api/local/') ? 404 : 200,
      contentType: 'application/json', body: JSON.stringify(responses[pathname] ?? {}) });
  });
  await page.routeWebSocket(/.*/, (socket) => {
    socket.onMessage((message) => {
      const frame = JSON.parse(String(message));
      if (frame.type !== 'subscribe') writes.push(String(message));
    });
    if (new URL(socket.url()).pathname === '/api/v1/ws') {
      socket.send(JSON.stringify({ type: 'state_update', data: {
        ...selectedState, type: 'full', data: selectedState,
      } }));
    }
  });
  return writes;
}

async function settled(page: Page) {
  await expect(page.locator('.m-layout, .m-landscape, .radio-layout')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

async function themeSnapshot(page: Page) {
  return page.evaluate(() => ({
    theme: document.documentElement.dataset.theme,
    text: getComputedStyle(document.documentElement).getPropertyValue('--v2-text-primary').trim(),
    workspace: JSON.parse(localStorage.getItem('rigplane:workspace')!),
  }));
}

test('saved mobile theme survives cold entry, reload and desktop round trip', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await settled(page);
  for (const stage of ['cold', 'reload', 'desktop', 'warm-mobile', 'cold-again']) {
    if (stage === 'reload' || stage === 'cold-again') await page.reload();
    if (stage === 'desktop') await page.setViewportSize({ width: 1440, height: 1000 });
    if (stage === 'warm-mobile') await page.setViewportSize({ width: 390, height: 844 });
    await settled(page);
    const snapshot = await themeSnapshot(page);
    writeFileSync(info.outputPath(`${stage}.json`), JSON.stringify(snapshot, null, 2));
    await info.attach(stage, { body: JSON.stringify(snapshot), contentType: 'application/json' });
    expect.soft(snapshot.theme, stage).toBe('github-light');
    expect.soft(snapshot.text, stage).toBe('#1f2328');
    expect(snapshot.workspace).toMatchObject(workspace);
  }
  expect(writes).toEqual([]);
});

// MOR-2816 (owner, 2026-09-27): the phone is minimal — one scroll container,
// and the receiver S-meter sits in the old strip's slot above the scope
// toolbar. Measured against the iPhone 13 profile's portrait viewport.
test('one scroll container and the S-meter above the scope toolbar (MOR-2816)', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 390, height: 664 });
  await page.goto('/');
  await settled(page);
  const geometry = await page.evaluate(() => {
    const scrollers: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('*'))) {
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const style = getComputedStyle(el);
      if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
        scrollers.push(`${el.tagName.toLowerCase()}${el.classList[0] ? `.${el.classList[0]}` : ''}`);
      }
    }
    const doc = document.scrollingElement as HTMLElement;
    const bar = document.querySelector('.m-smeter-bar')?.getBoundingClientRect();
    const toolbar = document.querySelector('.spectrum-toolbar')?.getBoundingClientRect();
    const header = document.querySelector('.m-vfo-bar')?.getBoundingClientRect();
    return {
      documentScrolls: doc.scrollHeight > doc.clientHeight + 1,
      documentScrollHeight: doc.scrollHeight,
      documentClientHeight: doc.clientHeight,
      scrollers,
      bar: bar?.toJSON(),
      toolbar: toolbar?.toJSON(),
      header: header?.toJSON(),
    };
  });
  writeFileSync(info.outputPath('mor-2816-geometry.json'), JSON.stringify(geometry, null, 2));
  await info.attach('geometry', { body: JSON.stringify(geometry), contentType: 'application/json' });
  // One scroll container: the document does not scroll, and exactly one
  // element — main.m-content — does.
  expect(geometry.documentScrolls).toBe(false);
  expect(geometry.scrollers).toEqual(['main.m-content']);
  // The S-meter sits between the frequency header and the scope toolbar.
  expect(geometry.bar).toBeDefined();
  expect(geometry.toolbar).toBeDefined();
  expect(geometry.header).toBeDefined();
  expect(geometry.bar!.bottom).toBeLessThanOrEqual(geometry.toolbar!.top);
  expect(geometry.header!.bottom).toBeLessThanOrEqual(geometry.bar!.top);
  expect(writes).toEqual([]);
});

// MOR-2816 (owner ruling 2026-09-28): the portrait VFO / RX-TX deck block is
// gone — no VFO or RX/TX surface, no KEY/UNKEY TRANSMITTER, no TX target
// line between the panorama and the chip row. The hoisted S-meter and the one
// scroller stay (pinned by the geometry test above).
async function checkPortraitMinimal(page: Page) {
  await expect(page.getByTestId('vfo-surface')).toHaveCount(0);
  await expect(page.getByTestId('rx-tx-surface')).toHaveCount(0);
  await expect(page.locator('[data-testid="rx-tx-key"], [data-testid="rx-tx-unkey"]')).toHaveCount(0);
  await expect(page.getByText(/TX target/)).toHaveCount(0);
  await expect(page.locator('.m-smeter-bar')).toBeVisible();
  await expect(page.getByTestId('semantic-radio-surfaces')).toHaveCount(1);
}

// The only unkey left is the landscape strip's (MOR-2816). MOR-2347's pin
// still holds: landscape hosts exactly ONE SemanticRadioSurfaces in its
// spectrum slot and renders no deck surfaces there.
async function checkLandscapeUnkey(page: Page, info: TestInfo, stage: string) {
  const unkey = page.locator('.m-ls-unkey');
  await expect(unkey).toHaveCount(1);
  const hosted = page.locator('.m-ls-spectrum [data-testid="semantic-radio-surfaces"]');
  await expect(hosted).toHaveCount(1);
  await expect(hosted.getByTestId('rx-tx-surface')).toHaveCount(0);
  await expect(unkey).toBeEnabled();
  await page.locator('body').click({ position: { x: 1, y: 1 } });
  for (let index = 0; index < 100; index++) {
    await page.keyboard.press('Tab');
    if (await unkey.evaluate((el) => el === document.activeElement)) break;
  }
  await expect(unkey).toBeFocused();
  await page.screenshot({ path: info.outputPath(`${stage}.png`) });
  const geometry = await unkey.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const viewport = { width: innerWidth, height: innerHeight };
    const points = [[rect.x + rect.width / 2, rect.y + rect.height / 2],
      [rect.left + 10, rect.top + rect.height / 2], [rect.right - 10, rect.top + rect.height / 2]];
    return { rect: rect.toJSON(), viewport,
      hits: points.map(([x, y]) => el.contains(document.elementFromPoint(x, y))) };
  });
  writeFileSync(info.outputPath(`${stage}.json`), JSON.stringify(geometry, null, 2));
  await info.attach(stage, { body: JSON.stringify(geometry), contentType: 'application/json' });
  expect.soft(geometry.hits, stage).toEqual([true, true, true]);
  expect.soft(geometry.rect.left, stage).toBeGreaterThanOrEqual(0);
  expect.soft(geometry.rect.right, stage).toBeLessThanOrEqual(geometry.viewport.width);
}

// MOR-2816 (owner ruling 2026-09-28 00:27 EDT): every visible button in the
// PORTRAIT phone layout — chip tabs, chip panels, the spectrum toolbar, the
// tuning strip — carries a label of at least 16px, a touch height of at least
// 44px, and is not clipped. Glyph-only buttons keep a 44px hit area and a
// glyph of at least 16px. Landscape is measured but deliberately unchanged.
type ButtonAudit = {
  label: string;
  className: string;
  font: number;
  width: number;
  height: number;
  clipped: boolean;
  glyphs: { width: number; height: number }[];
};

function auditScript(): ButtonAudit[] {
  const root = document.querySelector('.m-layout, .m-landscape');
  if (!root) throw new Error('phone root not found');
  const audited: ButtonAudit[] = [];
  for (const el of Array.from(root.querySelectorAll<HTMLElement>('button, [role="button"]'))) {
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    const rects = el.getClientRects();
    if (rects.length === 0) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    const text = (el.textContent ?? '').trim();
    const glyphs = Array.from(el.querySelectorAll('svg')).map((svg) => {
      const r = svg.getBoundingClientRect();
      return { width: r.width, height: r.height };
    });
    audited.push({
      label: text.slice(0, 24) || (glyphs.length ? '(glyph)' : '(empty)'),
      className: el.getAttribute('class')?.slice(0, 48) ?? '',
      font: parseFloat(style.fontSize),
      width: rect.width,
      height: rect.height,
      clipped: el.scrollWidth > el.clientWidth + 1,
      glyphs,
    });
  }
  return audited;
}

function fontHistogram(buttons: ButtonAudit[]) {
  const histogram: Record<string, number> = {};
  for (const b of buttons) histogram[b.font] = (histogram[b.font] ?? 0) + 1;
  return histogram;
}

async function auditPortrait(page: Page, info: TestInfo, stage: string) {
  const buttons = await page.evaluate(auditScript);
  console.log(`MOR-2816 portrait ${stage} histogram: ${JSON.stringify(fontHistogram(buttons))}`);
  await info.attach(`portrait-${stage}-audit`, {
    body: JSON.stringify(buttons, null, 2), contentType: 'application/json',
  });
  expect(buttons.length, `${stage}: buttons found`).toBeGreaterThan(0);
  for (const b of buttons) {
    const name = `${stage} "${b.label}" (${b.className})`;
    expect.soft(b.font, `${name} font-size`).toBeGreaterThanOrEqual(16);
    expect.soft(b.height, `${name} height`).toBeGreaterThanOrEqual(44);
    expect.soft(b.clipped, `${name} clipped`).toBe(false);
    // Glyph-only buttons: the glyph itself keeps the 16px floor.
    if (!b.label || b.label === '(glyph)') {
      for (const g of b.glyphs) {
        expect.soft(g.width, `${name} glyph width`).toBeGreaterThanOrEqual(16);
        expect.soft(g.height, `${name} glyph height`).toBeGreaterThanOrEqual(16);
      }
    }
  }
}

test('portrait buttons carry 16px labels and 44px touch targets (MOR-2816)', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await settled(page);
  await expect(page.locator('.m-layout')).toBeVisible();
  // ESSENTIALS is the default active chip; audit it, then the RF chip panel.
  await auditPortrait(page, info, 'essentials');
  await page.getByRole('tab', { name: 'RF', exact: true }).click();
  await expect(page.locator('#m-chip-panel-rf')).toBeVisible();
  await auditPortrait(page, info, 'rf');
  expect(writes).toEqual([]);
});

// MOR-2816 (owner ruling 2026-09-28): the 16px/44px floors made the scope
// toolbar wrap into 4-5 rows and eat the panorama box. On the PORTRAIT phone
// the toolbar is ONE row that scrolls horizontally, and the panorama
// (spectrum + waterfall split region) keeps at least 190 CSS px at 375x812.
test('portrait scope toolbar is one scrolling row over a 190px panorama (MOR-2816)', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await settled(page);
  await expect(page.locator('.m-layout')).toBeVisible();
  const measured = await page.evaluate(() => {
    const root = document.querySelector<HTMLElement>('.m-layout');
    const toolbar = root?.querySelector<HTMLElement>('.spectrum-toolbar');
    const panorama = root?.querySelector<HTMLElement>('.spectrum-split-region');
    if (!root || !toolbar || !panorama) throw new Error('portrait scope surfaces not found');
    const toolbarStyle = getComputedStyle(toolbar);
    const buttons = Array.from(toolbar.querySelectorAll<HTMLElement>('button')).map((el) => {
      const rect = el.getBoundingClientRect();
      const fullyVisible = rect.left >= -1 && rect.right <= innerWidth + 1;
      const reachableByScroll = toolbar.scrollWidth > toolbar.clientWidth + 1;
      return { label: (el.textContent ?? '').trim().slice(0, 24), fullyVisible, reachableByScroll };
    });
    // The FAB mounts outside the .m-layout scroll root — query it directly.
    const ptt = document.querySelector<HTMLElement>('.ptt-fab-label');
    return {
      toolbarHeight: toolbar.getBoundingClientRect().height,
      toolbarOverflowX: toolbarStyle.overflowX,
      toolbarWrap: toolbarStyle.flexWrap,
      panoramaHeight: panorama.getBoundingClientRect().height,
      buttons,
      docScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      pttFont: ptt ? parseFloat(getComputedStyle(ptt).fontSize) : null,
    };
  });
  writeFileSync(info.outputPath('mor-2816-toolbar.json'), JSON.stringify(measured, null, 2));
  await info.attach('toolbar', { body: JSON.stringify(measured), contentType: 'application/json' });
  // One button row: the 44px floor, at most 64px including padding.
  expect(measured.toolbarHeight).toBeGreaterThanOrEqual(44);
  expect(measured.toolbarHeight).toBeLessThanOrEqual(64);
  expect(measured.toolbarWrap).toBe('nowrap');
  expect(measured.toolbarOverflowX).toBe('auto');
  // The panorama keeps its box: at least 190 CSS px of spectrum + waterfall.
  expect(measured.panoramaHeight).toBeGreaterThanOrEqual(190);
  // Every toolbar button is fully visible, or the toolbar scrolls to it.
  for (const b of measured.buttons) {
    expect.soft(b.fullyVisible || b.reachableByScroll, `toolbar button "${b.label}" reachable`).toBe(true);
  }
  // No page-level horizontal overflow.
  expect(measured.docScrollWidth).toBeLessThanOrEqual(measured.innerWidth);
  // The FAB's PTT label carries the 16px floor too.
  expect(measured.pttFont).not.toBeNull();
  expect(measured.pttFont!).toBeGreaterThanOrEqual(16);
  expect(writes).toEqual([]);
});

// Landscape is MEASURED only (owner ruling: portrait-only change) — the
// numbers are reported, nothing is asserted and nothing is changed there.
test('landscape button label sizes measured, report only (MOR-2816)', async ({ page }, info) => {
  await prepare(page);
  await page.setViewportSize({ width: 812, height: 375 });
  await page.goto('/');
  await settled(page);
  await expect(page.locator('.m-landscape')).toBeVisible();
  const buttons = await page.evaluate(auditScript);
  const measured = {
    count: buttons.length,
    histogram: fontHistogram(buttons),
    heights: buttons.reduce<Record<string, number>>((acc, b) => {
      acc[b.height] = (acc[b.height] ?? 0) + 1;
      return acc;
    }, {}),
  };
  console.log(`MOR-2816 landscape measured: ${JSON.stringify(measured)}`);
  await info.attach('landscape-measured', {
    body: JSON.stringify({ measured, buttons }, null, 2), contentType: 'application/json',
  });
  expect(measured.count).toBeGreaterThan(0);
});

for (const [width, height] of [[390, 844], [430, 932]]) {
  test(`portrait shows no deck; landscape unkey stays reachable at ${width}x${height}`, async ({ page }, info) => {
    const writes = await prepare(page);
    await page.setViewportSize({ width, height });
    await page.goto('/');
    await settled(page);
    await checkPortraitMinimal(page);
    await page.setViewportSize({ width: height, height: width });
    await expect(page.locator('.m-landscape')).toBeVisible();
    await checkLandscapeUnkey(page, info, 'landscape');
    await page.evaluate(async () => {
      if (document.fullscreenElement) await document.exitFullscreen();
    });
    await page.setViewportSize({ width, height });
    await expect(page.locator('.m-layout')).toBeVisible();
    await checkPortraitMinimal(page);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 44, bottom: 34 } });
    await expect(page.locator('.m-tuning-strip')).toHaveCSS('padding-bottom', '34px');
    await expect(page.locator('.m-tuning-strip')).toHaveCSS('height', '86px');
    expect(writes).toEqual([]);
  });
}
