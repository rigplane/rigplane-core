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

async function checkUnkey(page: Page, info: TestInfo, stage: string, landscape: boolean, tab = true) {
  const unkey = page.locator('[data-testid="rx-tx-unkey"], .m-ls-unkey');
  await expect(unkey).toHaveCount(1);
  await expect(page.getByTestId('semantic-radio-surfaces')).toHaveCount(landscape ? 0 : 1);
  await expect(unkey).toBeEnabled();
  if (tab) {
    // Tab to the existing recovery control without activating any control.
    await page.locator('body').click({ position: { x: 1, y: 1 } });
    for (let index = 0; index < 100; index++) {
      await page.keyboard.press('Tab');
      if (await unkey.evaluate((el) => el === document.activeElement)) break;
    }
  } else {
    await unkey.evaluate((el) => (el as HTMLElement).blur());
    await unkey.focus();
  }
  await expect(unkey).toBeFocused();
  await page.screenshot({ path: info.outputPath(`${stage}.png`) });
  const geometry = await unkey.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const dock = document.querySelector('.m-tuning-strip')?.getBoundingClientRect();
    const content = document.querySelector('.m-content')?.getBoundingClientRect();
    const points = [[rect.x + rect.width / 2, rect.y + rect.height / 2],
      [rect.left + 10, rect.top + rect.height / 2], [rect.right - 10, rect.top + rect.height / 2]];
    return { rect: rect.toJSON(), dock: dock?.toJSON(), content: content?.toJSON(),
      viewport: { width: innerWidth, height: innerHeight },
      hits: points.map(([x, y]) => el.contains(document.elementFromPoint(x, y))) };
  });
  writeFileSync(info.outputPath(`${stage}.json`), JSON.stringify(geometry, null, 2));
  await info.attach(stage, { body: JSON.stringify(geometry), contentType: 'application/json' });
  expect.soft(geometry.hits, stage).toEqual([true, true, true]);
  expect.soft(geometry.rect.top, stage).toBeGreaterThanOrEqual(geometry.content?.top ?? 0);
  expect.soft(geometry.rect.bottom, stage).toBeLessThanOrEqual(geometry.dock?.top ?? geometry.viewport.height);
  expect.soft(geometry.rect.left, stage).toBeGreaterThanOrEqual(0);
  expect.soft(geometry.rect.right, stage).toBeLessThanOrEqual(geometry.viewport.width);
  if (geometry.content && geometry.dock) {
    expect.soft(geometry.content.bottom, stage).toBeLessThanOrEqual(geometry.dock.top);
    expect.soft(geometry.dock.bottom, stage).toBeLessThanOrEqual(geometry.viewport.height);
  }
}

for (const [width, height] of [[390, 844], [430, 932]]) {
  test(`Unkey stays reachable through scrolling and rotation at ${width}x${height}`, async ({ page }, info) => {
    const writes = await prepare(page);
    await page.setViewportSize({ width, height });
    await page.goto('/');
    await settled(page);
    await checkUnkey(page, info, 'focus-only', false, false);
    await checkUnkey(page, info, 'portrait', false);
    await page.locator('.m-content').evaluate((el) => { el.scrollTop = el.scrollHeight; });
    await checkUnkey(page, info, 'scrolled', false);
    await page.setViewportSize({ width: height, height: width });
    await expect(page.locator('.m-landscape')).toBeVisible();
    await checkUnkey(page, info, 'landscape', true);
    await page.evaluate(async () => {
      if (document.fullscreenElement) await document.exitFullscreen();
    });
    await page.setViewportSize({ width, height });
    await expect(page.locator('.m-layout')).toBeVisible();
    await checkUnkey(page, info, 'rotated-back', false);
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 44, bottom: 34 } });
    await expect(page.locator('.m-tuning-strip')).toHaveCSS('padding-bottom', '34px');
    await expect(page.locator('.m-tuning-strip')).toHaveCSS('height', '86px');
    await checkUnkey(page, info, 'safe-area', false, false);
    expect(writes).toEqual([]);
  });
}
