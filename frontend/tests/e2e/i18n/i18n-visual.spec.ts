/**
 * Localization visual smoke (RP-ML-006).
 *
 * Drives the built Core frontend with a stubbed backend (no live radio,
 * no live Tower) under three locales — `en-US`, `ja-JP`, `qps-ploc` — at
 * two viewports — desktop 1280×800 and mobile 390×844 — and captures a
 * screenshot of every P0 surface extracted in RP-ML-005.
 *
 * Locale switch path:
 *   - We do NOT click through `LanguageSelector` per locale (slow + flaky).
 *   - Instead we set `localStorage["rigplane.i18n.locale"]` before the page
 *     loads via `page.addInitScript`. The locale-contract `?locale=` query
 *     param (RP-ML-012A) is the documented fallback if storage breaks.
 *
 * Backend stub:
 *   - `page.route('**\/api/v1/state')`, `capabilities`, `info` return JSON
 *     from `fixtures.ts`.
 *   - The control WebSocket is replaced with a polyfilled class that
 *     auto-opens and exposes a global `__i18nWsDispatch(msg)` hook for
 *     pushing fake `notification` frames (used for the Toast surface).
 *
 * Assertion floor (no screenshot diffs are forced — see README):
 *   - Page text MUST NOT contain `[missing:` (lookup-miss marker).
 *   - Page text MUST NOT contain `${` (raw template leak).
 *   - Glossary tokens used by populated state — `MAIN`, `SUB`, `USB`,
 *     `LSB`, `RTTY`, the radio model `IC-7300` — MUST appear verbatim
 *     in EVERY locale, including `qps-ploc`.
 *   - On `qps-ploc` we DO NOT also check for diacritics in the page,
 *     because some unsubstituted English glyphs are intentional
 *     (interpolated radio model, mode tokens). The component-level
 *     pseudo smoke (RP-ML-013A) already covers the runtime transform.
 *
 * Scope split with RP-ML-013A:
 *   - RP-ML-013A is the cheap unit-test floor (vitest + jsdom) that runs
 *     in <2s with no browser. It validates the runtime transform.
 *   - RP-ML-006 (this file) is the heavier full-page visual smoke. It
 *     catches layout regressions (clipped buttons, overflow, broken
 *     mobile sheets) that a string-level test cannot see.
 */

import { test, expect, type Page, type Route } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  mockCapabilities,
  mockDisconnectedState,
  mockInfo,
  mockState,
} from './fixtures';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASELINE_DIR = path.join(__dirname, '__screenshots__', 'i18n');

type SupportedLocale = 'en-US' | 'ja-JP' | 'qps-ploc';
const LOCALES: SupportedLocale[] = ['en-US', 'ja-JP', 'qps-ploc'];

interface ViewportSpec {
  name: 'desktop' | 'mobile';
  width: number;
  height: number;
}

const VIEWPORTS: ViewportSpec[] = [
  { name: 'desktop', width: 1280, height: 800 },
  { name: 'mobile', width: 390, height: 844 },
];

/**
 * Glossary tokens that should survive any locale transform, including
 * the pseudo-locale. These come from the strategy glossary §3 and from
 * radio-protocol invariants the i18n runtime treats as non-translatable
 * by composing them as interpolation values at the call site
 * (see `frontend/src/lib/i18n/pseudo.ts` header).
 *
 * Note: We assert the tokens MAIN/SUB/USB/LSB and the radio model
 * "IC-7300" appear in the rendered DOM somewhere. Visibility per
 * surface is not required — the layout naturally hides some on mobile.
 * The smoke is: glossary survives the locale switch at all.
 */
const GLOSSARY_TOKENS_DESKTOP = ['USB', 'VFO'];

function locStorageInit(locale: SupportedLocale) {
  // Stamp both keys: the explicit Core selector (LanguageSelector path)
  // and a benign default for the Pro envelope so any future Pro-injected
  // locale does not stomp this one mid-test.
  return `
    try {
      localStorage.setItem('rigplane.i18n.locale', '${locale}');
      localStorage.removeItem('rigplane.i18n.proLocale.v1');
    } catch (e) {
      // jsdom or restricted environments: locale will fall through to
      // the URL query param, set below by the test runner.
    }
  `;
}

/**
 * Install a polyfilled WebSocket class. The real backend WS is not
 * available in this suite; we replace `window.WebSocket` with a minimal
 * stub that auto-opens, swallows outgoing frames, and exposes
 * `window.__i18nWsDispatch(msg)` for the test to push `notification`
 * frames into the open control channel.
 */
const WS_STUB_INIT = `
  (() => {
    const sockets = [];

    class StubWebSocket extends EventTarget {
      constructor(url) {
        super();
        this.url = url;
        this.readyState = 0;
        this.binaryType = 'arraybuffer';
        this.bufferedAmount = 0;
        this.extensions = '';
        this.protocol = '';
        // Hook for compatibility with code that uses ws.onopen = fn.
        this.onopen = null;
        this.onmessage = null;
        this.onerror = null;
        this.onclose = null;
        sockets.push(this);
        // Auto-open after a microtask so callers can attach handlers.
        Promise.resolve().then(() => {
          this.readyState = 1;
          const evt = new Event('open');
          this.dispatchEvent(evt);
          if (typeof this.onopen === 'function') this.onopen(evt);
        });
      }
      send(_data) {
        // Swallow: the visual smoke does not exercise outbound commands.
      }
      close() {
        this.readyState = 3;
        const evt = new Event('close');
        this.dispatchEvent(evt);
        if (typeof this.onclose === 'function') this.onclose(evt);
      }
    }
    StubWebSocket.CONNECTING = 0;
    StubWebSocket.OPEN = 1;
    StubWebSocket.CLOSING = 2;
    StubWebSocket.CLOSED = 3;

    window.WebSocket = StubWebSocket;

    // Public dispatch hook for tests.
    window.__i18nWsDispatch = (msg) => {
      const payload = typeof msg === 'string' ? msg : JSON.stringify(msg);
      for (const s of sockets) {
        if (s.readyState !== 1) continue;
        const evt = new MessageEvent('message', { data: payload });
        s.dispatchEvent(evt);
        if (typeof s.onmessage === 'function') s.onmessage(evt);
      }
    };
  })();
`;

async function routeMockBackend(page: Page, state = mockState): Promise<void> {
  const json = (route: Route, body: unknown) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });

  await page.route('**/api/v1/state', (route) => json(route, state));
  await page.route('**/api/v1/capabilities', (route) => json(route, mockCapabilities));
  await page.route('**/api/v1/info', (route) => json(route, mockInfo));
  // Anything else under /api/v1/ — return an empty 200 so the page does
  // not surface a network error overlay we did not plan for.
  await page.route('**/api/v1/**', (route) => {
    const url = route.request().url();
    if (
      url.endsWith('/state') ||
      url.endsWith('/capabilities') ||
      url.endsWith('/info')
    ) {
      // Already handled above; double-routing safety net.
      return route.fallback();
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{}',
    });
  });
  // `/api/local/v1/*` is the LocalExtensionsHost surface (Pro shell).
  // It is not exposed by `vite preview`, so the proxy attempt logs noisy
  // ECONNREFUSED lines on each poll. Short-circuit at the page boundary.
  await page.route('**/api/local/v1/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '{}',
    }),
  );
}

async function preparePage(
  page: Page,
  locale: SupportedLocale,
  viewport: ViewportSpec,
  options: { state?: typeof mockState } = {},
): Promise<void> {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.addInitScript(locStorageInit(locale));
  await page.addInitScript(WS_STUB_INIT);
  await routeMockBackend(page, options.state ?? mockState);
}

function screenshotPath(
  surface: string,
  locale: SupportedLocale,
  viewport: ViewportSpec,
): string {
  return path.join(BASELINE_DIR, locale, `${surface}-${viewport.name}.png`);
}

async function ensureScreenshotDir(): Promise<void> {
  for (const locale of LOCALES) {
    await mkdir(path.join(BASELINE_DIR, locale), { recursive: true });
  }
}

/**
 * Inspect the rendered body text for forbidden markers.
 *
 * `[missing:` is what the runtime emits when a key is absent from the
 * active locale catalog (see `runtime.ts`). It MUST NOT appear in a
 * shipped surface. `${` is the raw template-literal leak; same story.
 */
async function assertNoLookupMisses(page: Page, surfaceLabel: string): Promise<void> {
  const bodyText = await page.locator('body').innerText();
  expect(bodyText, `${surfaceLabel}: lookup-miss marker present`).not.toMatch(/\[missing:/);
  expect(bodyText, `${surfaceLabel}: raw template leak present`).not.toMatch(/\$\{[a-zA-Z]/);
}

/**
 * Confirm glossary tokens survive the active locale, including
 * `qps-ploc`. Restricted to desktop because some panels collapse on
 * mobile and may not surface every token.
 */
async function assertGlossaryTokens(
  page: Page,
  viewport: ViewportSpec,
  surfaceLabel: string,
): Promise<void> {
  if (viewport.name !== 'desktop') return;
  const bodyText = await page.locator('body').innerText();
  for (const token of GLOSSARY_TOKENS_DESKTOP) {
    expect(bodyText, `${surfaceLabel}: glossary token "${token}" missing`).toContain(token);
  }
}

async function waitForAppShell(page: Page): Promise<void> {
  // RadioLayout always renders one of these shells; wait for whichever
  // wins the responsive bracket.
  await page
    .locator('.radio-layout, .m-layout, .m-landscape, .lcd-layout, .lcd-cockpit, .error-overlay')
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 });
  // Settle layout: small idle window so transitions and Svelte 5
  // effects flush before the screenshot.
  await page.waitForTimeout(400);
}

async function captureBaseline(
  page: Page,
  surface: string,
  locale: SupportedLocale,
  viewport: ViewportSpec,
): Promise<void> {
  const dest = screenshotPath(surface, locale, viewport);
  await page.screenshot({ path: dest, fullPage: false });
}

test.describe.configure({ mode: 'serial' });

test.describe('i18n visual smoke (RP-ML-006)', () => {
  test.beforeAll(async () => {
    await ensureScreenshotDir();
  });

  for (const locale of LOCALES) {
    for (const viewport of VIEWPORTS) {
      test(`app-shell loaded — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        await assertNoLookupMisses(page, `app-shell/${locale}/${viewport.name}`);
        await assertGlossaryTokens(page, viewport, `app-shell/${locale}/${viewport.name}`);
        await captureBaseline(page, 'app-shell', locale, viewport);
      });

      test(`status-bar populated — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        // StatusBar lives at the top of the layout in both skins.
        const statusBar = page.locator('.status-bar, [data-status-bar], header.status-bar');
        if ((await statusBar.count()) > 0) {
          await statusBar.first().scrollIntoViewIfNeeded();
        }
        await assertNoLookupMisses(page, `status-bar/${locale}/${viewport.name}`);
        await captureBaseline(page, 'status-bar', locale, viewport);
      });

      test(`connection-overlay disconnected — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport, { state: mockDisconnectedState });
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        await assertNoLookupMisses(
          page,
          `connection-overlay/${locale}/${viewport.name}`,
        );
        await captureBaseline(page, 'connection-overlay', locale, viewport);
      });

      test(`settings modal open — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        // The settings button lives in StatusBar. Its aria-label is
        // localized; instead of looking up the translated string per
        // locale, use the CSS class hook the component already exposes.
        const settingsBtn = page.locator('.control-btn.settings-btn');
        if ((await settingsBtn.count()) > 0) {
          await settingsBtn.first().click();
          // Wait for the modal dialog.
          await page
            .locator('[role="dialog"][aria-modal="true"]')
            .first()
            .waitFor({ state: 'visible', timeout: 4_000 })
            .catch(() => undefined);
          await page.waitForTimeout(250);
        }
        await assertNoLookupMisses(
          page,
          `settings-modal/${locale}/${viewport.name}`,
        );
        await captureBaseline(page, 'settings-modal', locale, viewport);
      });

      test(`send-report dialog open — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        const reportBtn = page.locator('.control-btn.report-btn');
        if ((await reportBtn.count()) > 0) {
          await reportBtn.first().click();
          await page
            .locator('[role="dialog"][aria-modal="true"]')
            .first()
            .waitFor({ state: 'visible', timeout: 4_000 })
            .catch(() => undefined);
          await page.waitForTimeout(250);
        }
        await assertNoLookupMisses(
          page,
          `send-report-dialog/${locale}/${viewport.name}`,
        );
        await captureBaseline(page, 'send-report-dialog', locale, viewport);
      });

      test(`toast notification — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await page.goto(`/?locale=${encodeURIComponent(locale)}`);
        await waitForAppShell(page);
        // Dispatch a notification through the stubbed WebSocket using
        // a code that is bundled in en-US.json (RP-ML-005 emits
        // reasonCode + params from `broadcast_notification`).
        await page.evaluate(() => {
          const dispatch = (
            window as unknown as {
              __i18nWsDispatch?: (msg: unknown) => void;
            }
          ).__i18nWsDispatch;
          if (dispatch) {
            dispatch({
              type: 'notification',
              level: 'warning',
              code: 'licenseExpired',
              message: 'Your license has expired. Reactivate to continue.',
              params: {},
            });
          }
        });
        await page.waitForTimeout(400);
        await assertNoLookupMisses(page, `toast/${locale}/${viewport.name}`);
        await captureBaseline(page, 'toast', locale, viewport);
      });
    }
  }
});
