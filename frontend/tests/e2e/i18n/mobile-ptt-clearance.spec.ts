import { test, expect, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { mockCapabilities, mockInfo, mockState } from './fixtures';
import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState } from '../../../src/lib/types/state';

/**
 * MOR-2874 — the PTT button must own its place.
 *
 * The approved portrait baseline on main drew the floating round PTT
 * button OVER the MUTE key of the ESSENTIALS grid, and because the
 * phone is ONE scroller with the FAB floating above it, some control
 * sits under the button at almost every scroll position — a thumb
 * aimed at that control can key the transmitter. The fix gives PTT a
 * reserved place in the fixed bottom chrome, outside the scroller.
 *
 * This spec measures the PTT button's box against EVERY other visible
 * interactive element at the top, the middle and the bottom of the
 * scroll, at 375×812 and at 360 px width, and finds no overlap. For
 * content of the one scroller only the VISIBLE part counts: a row
 * scrolled out of the scroller's clipped box is not a visible
 * neighbour, so candidate rects are intersected with the scroller's
 * viewport box before the PTT box is compared against them. The
 * candidate count itself is floored: a query that has gone stale and
 * measures nothing would trivially find zero offenders, so the test
 * refuses to pass on an empty measurement.
 */

const workspace = {
  version: 1,
  layout: 'standard',
  designLanguage: 'studioline',
  theme: 'github-light',
};

const state = mockState satisfies ServerState;
const capabilities = mockCapabilities satisfies Capabilities;

const managedTransmit = {
  schemaVersion: 1, sampledAt: '2026-09-28T12:00:00.000Z',
  managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
    lastError: null, lastActuation: null, abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
  txObservation: { observedPtt: 'off' },
};

test.use({ hasTouch: true, isMobile: true });

async function prepare(page: Page) {
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
      '/api/v1/state': state,
      '/api/v1/capabilities': capabilities,
      '/api/v1/managed-transmit': managedTransmit,
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
        ...state, type: 'full', data: state,
      } }));
    }
  });
  return writes;
}

async function settled(page: Page) {
  await expect(page.locator('.m-layout')).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
}

type Box = { left: number; top: number; right: number; bottom: number; width: number; height: number };

type Clearance = {
  viewport: { width: number; height: number };
  scrollTop: number;
  scrollRange: number;
  ptt: Box;
  candidates: number;
  offenders: { label: string; className: string; rect: Box }[];
};

function clearanceScript(): Clearance {
  const ptt = document.querySelector<HTMLElement>('.ptt-fab');
  if (!ptt) throw new Error('the .ptt-fab button is missing');
  const scroller = document.querySelector<HTMLElement>('.m-content');
  if (!scroller) throw new Error('the .m-content scroller is missing');
  const box = (r: DOMRect): Box => ({
    left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height,
  });
  const pttRect = ptt.getBoundingClientRect();
  const clip = scroller.getBoundingClientRect();
  const offenders: Clearance['offenders'] = [];
  let candidates = 0;
  for (const el of Array.from(document.querySelectorAll<HTMLElement>(
    'button, [role="button"], [role="slider"], a, input, select, textarea',
  ))) {
    if (el === ptt || ptt.contains(el)) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
    if (el.getClientRects().length === 0) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    // Content of the one scroller is clipped to the scroller's viewport
    // box — only the visible part can sit under the PTT button.
    let left = rect.left, right = rect.right, top = rect.top, bottom = rect.bottom;
    if (scroller.contains(el)) {
      left = Math.max(left, clip.left);
      right = Math.min(right, clip.right);
      top = Math.max(top, clip.top);
      bottom = Math.min(bottom, clip.bottom);
      if (right - left < 1 || bottom - top < 1) continue;
    }
    candidates += 1;
    const overlapLeft = Math.max(left, pttRect.left);
    const overlapRight = Math.min(right, pttRect.right);
    const overlapTop = Math.max(top, pttRect.top);
    const overlapBottom = Math.min(bottom, pttRect.bottom);
    if (overlapRight - overlapLeft > 0 && overlapBottom - overlapTop > 0) {
      const text = (el.textContent ?? '').trim().slice(0, 24);
      offenders.push({
        label: text || el.getAttribute('aria-label')?.slice(0, 24) || el.tagName,
        className: el.getAttribute('class')?.slice(0, 64) ?? '',
        rect: { left, top, right, bottom, width: right - left, height: bottom - top },
      });
    }
  }
  return {
    viewport: { width: innerWidth, height: innerHeight },
    scrollTop: Math.round(scroller.scrollTop),
    scrollRange: scroller.scrollHeight - scroller.clientHeight,
    ptt: box(pttRect),
    candidates,
    offenders,
  };
}

test('no visible interactive element ever sits under the PTT button (MOR-2874)', async ({ page }, info) => {
  const writes = await prepare(page);
  for (const viewport of [{ width: 375, height: 812 }, { width: 360, height: 812 }]) {
    const size = `${viewport.width}×${viewport.height}`;
    await page.setViewportSize(viewport);
    await page.goto('/');
    await settled(page);
    const scroller = page.locator('.m-content');
    const scrollRange = await scroller.evaluate((el) => el.scrollHeight - el.clientHeight);
    // The phone is one scroller (MOR-2816/2851): without real scroll range
    // the middle and bottom stages would silently degenerate to the top.
    expect(scrollRange, `${size}: the phone content must actually scroll`).toBeGreaterThan(0);
    const stages: [string, number][] = [
      ['top', 0],
      ['middle', Math.round(scrollRange / 2)],
      ['bottom', scrollRange],
    ];
    for (const [stage, top] of stages) {
      await scroller.evaluate((el, value) => { el.scrollTop = value; }, top);
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => resolve(null))));
      const measured = await page.evaluate(clearanceScript);
      writeFileSync(info.outputPath(`mor-2874-${viewport.width}-${stage}.json`),
        JSON.stringify(measured, null, 2));
      // An empty measurement must not pass: a stale element query would
      // find zero candidates and therefore trivially zero offenders.
      // The fixed chrome alone keeps six candidates at every stage and
      // viewport (the SETUP header button plus the five tuning-strip
      // buttons, all outside the scroller), and any visible screenful
      // of the dense single-panel content adds several more — so ten
      // is a realistic floor, while a broken query stays at zero.
      expect(measured.candidates, `${size} ${stage}: the measurement must see real interactive elements`)
        .toBeGreaterThanOrEqual(10);
      // MOR-2816 floor kept: PTT stays large and inside the viewport.
      expect.soft(measured.ptt.width, `${size} ${stage}: PTT width`).toBeGreaterThanOrEqual(44);
      expect.soft(measured.ptt.height, `${size} ${stage}: PTT height`).toBeGreaterThanOrEqual(44);
      expect(measured.ptt.left, `${size} ${stage}: PTT left edge in viewport`).toBeGreaterThanOrEqual(0);
      expect(measured.ptt.right, `${size} ${stage}: PTT right edge in viewport`)
        .toBeLessThanOrEqual(measured.viewport.width);
      expect(measured.offenders, `${size} ${stage}: nothing sits under the PTT button`).toEqual([]);
    }
  }
  expect(writes).toEqual([]);
});
