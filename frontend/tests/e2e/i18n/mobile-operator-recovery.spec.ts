import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

const workspace = { version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'github-light' };
const { sub: _unusedSub, ...singleReceiverState } = mockState;
// MOR-2852: the meta row's BW/AGC/NB/NR chips are capability-gated
// (`radio-view-model-adapter.ts` deriveReceiverIndicators) and a KNOWN
// reading additionally requires observation evidence (`seen()`), so this
// spec states both locally: the four capability tags, and fresh
// `fieldStatus` entries for the `main.*` leaves the adapter reads — the
// same pattern `mobile-unread-smeter.spec.ts` uses for its S-meter leaf.
// The shared `./fixtures` mock deliberately declares neither: other specs
// in this pack must keep seeing the chips structurally absent.
function observedLeaf(leaf: string) {
  return {
    storePath: `main.${leaf}`, observed: true,
    freshness: 'fresh' as const, availability: 'available' as const,
    lastObservedMonotonic: 0,
  };
}
const state = {
  ...singleReceiverState,
  main: { ...mockState.main, freqHz: 14_035_720, mode: 'CW', filter: 3,
    filterWidth: 150, unselectedVfo: { freqHz: 14_332_000, mode: 'USB', filterNum: 1, dataMode: 0 } },
  fieldStatus: {
    'main.filterWidth': observedLeaf('filterWidth'),
    'main.agc': observedLeaf('agc'),
    'main.nb': observedLeaf('nb'),
    'main.nr': observedLeaf('nr'),
  },
} satisfies Omit<ServerState, 'sub'>;
const capabilities = { ...mockCapabilities, model: 'IC-7300', receivers: 1,
  vfoScheme: 'ab', vfoReadback: 'selected_unselected', audioTx: true, audioTxRoute: 'usb',
  audioTxRequiredModInputSource: null,
  capabilities: ['scope', 'audio', 'tx', 'filter_width', 'agc', 'nb', 'nr'] } satisfies Capabilities;
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

// MOR-2851 (owner, 2026-09-28): the phone draws NO toolbar above the panorama
// — its controls moved to the SCOPE chip tab. The phone stays minimal: one
// scroll container, and the receiver S-meter sits directly above the
// panorama. Measured against the iPhone 13 profile's portrait viewport.
test('no scope toolbar on the phone; the S-meter sits directly above the panorama (MOR-2851)', async ({ page }, info) => {
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
    const panorama = document.querySelector('.spectrum-split-region')?.getBoundingClientRect();
    const header = document.querySelector('.m-vfo-bar')?.getBoundingClientRect();
    return {
      documentScrolls: doc.scrollHeight > doc.clientHeight + 1,
      documentScrollHeight: doc.scrollHeight,
      documentClientHeight: doc.clientHeight,
      scrollers,
      bar: bar?.toJSON(),
      toolbar: toolbar?.toJSON(),
      panorama: panorama?.toJSON(),
      header: header?.toJSON(),
    };
  });
  writeFileSync(info.outputPath('mor-2851-geometry.json'), JSON.stringify(geometry, null, 2));
  await info.attach('geometry', { body: JSON.stringify(geometry), contentType: 'application/json' });
  // One scroll container: the document does not scroll, and exactly one
  // element — main.m-content — does.
  expect(geometry.documentScrolls).toBe(false);
  expect(geometry.scrollers).toEqual(['main.m-content']);
  // MOR-2851: no toolbar renders above the panorama at all.
  expect(geometry.toolbar).toBeUndefined();
  // The S-meter sits directly above the panorama, below the frequency header.
  expect(geometry.bar).toBeDefined();
  expect(geometry.panorama).toBeDefined();
  expect(geometry.header).toBeDefined();
  expect(geometry.bar!.bottom).toBeLessThanOrEqual(geometry.panorama!.top);
  expect(geometry.header!.bottom).toBeLessThanOrEqual(geometry.bar!.top);
  expect(writes).toEqual([]);
});

// MOR-2852 — the meta row shows mode, filter, then BW/AGC/NB/NR chips, and at
// a 360 px wide phone the whole row must stay on ONE line with no fact clipped.
test('360 px portrait keeps the meta facts on one line, nothing clipped (MOR-2852)', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 360, height: 844 });
  await page.goto('/');
  await settled(page);
  const measured = await page.evaluate(() => {
    const row = document.querySelector<HTMLElement>('.m-vfo-meta');
    if (!row) throw new Error('the .m-vfo-meta row is missing');
    const facts = Array.from(row.querySelectorAll<HTMLElement>('[data-indicator-fact]'))
      .map((node) => ({
        fact: node.getAttribute('data-indicator-fact'),
        // MOR-2873: a real browser applies `text-transform` to `innerText`,
        // so this string pins each fact's rendered case (jsdom cannot).
        text: node.innerText.trim(),
        right: node.getBoundingClientRect().right,
        scrollW: node.scrollWidth,
        clientW: node.clientWidth,
      }));
    return {
      scrollW: row.scrollWidth,
      clientW: row.clientWidth,
      lineHeight: row.getBoundingClientRect().height,
      facts,
    };
  });
  writeFileSync(info.outputPath('mor-2852-meta.json'), JSON.stringify(measured, null, 2));
  await info.attach('meta', { body: JSON.stringify(measured), contentType: 'application/json' });
  expect(measured.scrollW).toBeLessThanOrEqual(measured.clientW + 1);
  expect(measured.facts.map((f) => f.fact)).toEqual(['bandwidth', 'agc', 'nb', 'nr']);
  // MOR-2873: the meta row is styled `text-transform: uppercase`, but the
  // filter-width unit must keep its source case: `… Hz`, not `… HZ`.
  // The width itself is scenario data (the local mock and the private
  // capture replay stage different values), so pin the case, not the
  // number. Only a real browser sees this — jsdom applies no CSS.
  expect(measured.facts.find((f) => f.fact === 'bandwidth')?.text).toMatch(/^BW \d+ Hz$/);
  for (const fact of measured.facts) {
    expect.soft(fact.right, `${fact.fact} in viewport`).toBeLessThanOrEqual(360);
    expect.soft(fact.scrollW, `${fact.fact} not clipped`).toBeLessThanOrEqual(fact.clientW + 1);
  }
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
  // MOR-2816: the audit walks EVERY phone root, not only the arrangement
  // roots — the floating MOD-input warning banner (.m-mod-input-warning)
  // and the PTT FAB button sit outside .m-layout/.m-landscape, so a single
  // root query never saw their buttons. The FAB root IS the button, so a
  // root that matches the selector set is audited itself.
  const roots = Array.from(document.querySelectorAll<HTMLElement>(
    '.m-layout, .m-landscape, .m-mod-input-warning, .ptt-fab',
  ));
  if (roots.length === 0) throw new Error('phone root not found');
  const audited: ButtonAudit[] = [];
  for (const root of roots) {
    const buttons = root.matches('button, [role="button"]')
      ? [root, ...Array.from(root.querySelectorAll<HTMLElement>('button, [role="button"]'))]
      : Array.from(root.querySelectorAll<HTMLElement>('button, [role="button"]'));
    for (const el of buttons) {
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
  // ESSENTIALS is the default active chip; audit it, then the RF chip panel,
  // then the MOR-2851 SCOPE chip panel (view keys + semantic scope controls).
  await auditPortrait(page, info, 'essentials');
  await page.getByRole('tab', { name: 'RF', exact: true }).click();
  await expect(page.locator('#m-chip-panel-rf')).toBeVisible();
  await auditPortrait(page, info, 'rf');
  await page.getByRole('tab', { name: 'SCOPE', exact: true }).click();
  await expect(page.locator('#m-chip-panel-scope')).toBeVisible();
  await auditPortrait(page, info, 'scope');
  expect(writes).toEqual([]);
});

// MOR-2851 (owner, 2026-09-28): the scope toolbar is GONE from the portrait
// phone — its controls live in the SCOPE chip tab. Nothing renders above the
// panorama, which keeps at least 190 CSS px at 375x812.
test('portrait panorama keeps its box with no toolbar above it (MOR-2851)', async ({ page }, info) => {
  const writes = await prepare(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await settled(page);
  await expect(page.locator('.m-layout')).toBeVisible();
  await expect(page.locator('.m-layout .spectrum-toolbar')).toHaveCount(0);
  const measured = await page.evaluate(() => {
    const root = document.querySelector<HTMLElement>('.m-layout');
    const panorama = root?.querySelector<HTMLElement>('.spectrum-split-region');
    if (!root || !panorama) throw new Error('portrait scope surfaces not found');
    // The FAB mounts outside the .m-layout scroll root — query it directly.
    const ptt = document.querySelector<HTMLElement>('.ptt-fab-label');
    return {
      panoramaHeight: panorama.getBoundingClientRect().height,
      docScrollWidth: document.documentElement.scrollWidth,
      innerWidth,
      pttFont: ptt ? parseFloat(getComputedStyle(ptt).fontSize) : null,
    };
  });
  writeFileSync(info.outputPath('mor-2851-panorama.json'), JSON.stringify(measured, null, 2));
  await info.attach('panorama', { body: JSON.stringify(measured), contentType: 'application/json' });
  // The panorama keeps its box: at least 190 CSS px of spectrum + waterfall.
  expect(measured.panoramaHeight).toBeGreaterThanOrEqual(190);
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
    // MOR-2874: the strip grew from 52px to 76px to host the PTT FAB
    // (72px + 2px clearance) as a fixed sibling outside the scroller.
    await expect(page.locator('.m-tuning-strip')).toHaveCSS('height', '110px');
    expect(writes).toEqual([]);
  });
}
