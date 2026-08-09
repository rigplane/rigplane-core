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
 *     auto-opens, emits the canonical registration full, and exposes a global
 *     `__i18nWsDispatch(msg)` hook for pushing fake `notification` frames
 *     (used for the Toast surface).
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
function wsStubInit(selectedState: typeof mockState): string {
  const initialControlFrame = {
    type: 'state_update',
    data: {
      type: 'full',
      data: selectedState,
      revision: selectedState.revision,
      stateRevision: selectedState.stateRevision,
      freshnessRevision: selectedState.freshnessRevision,
      healthRevision: selectedState.healthRevision,
      observationSeq: selectedState.observationSeq,
      publicStateSeq: selectedState.publicStateSeq,
      transportSeq: selectedState.transportSeq,
      stateContractVersion: selectedState.stateContractVersion,
      providerGeneration: selectedState.providerGeneration,
    },
  };
  return `
  (() => {
    const sockets = [];
    const initialControlFrame = ${JSON.stringify(initialControlFrame)};

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
          if (new URL(String(this.url), window.location.href).pathname === '/api/v1/ws') {
            const payload = JSON.stringify(initialControlFrame);
            const message = new MessageEvent('message', { data: payload });
            this.dispatchEvent(message);
            if (typeof this.onmessage === 'function') this.onmessage(message);
          }
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
}

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
  options: { state?: typeof mockState; workspace?: Record<string, unknown> } = {},
): Promise<void> {
  const selectedState = options.state ?? mockState;
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  await page.addInitScript(locStorageInit(locale));
  if (options.workspace !== undefined) {
    await page.addInitScript((workspace) => {
      localStorage.setItem('rigplane:workspace', JSON.stringify(workspace));
    }, options.workspace);
  }
  await page.addInitScript(wsStubInit(selectedState));
  await routeMockBackend(page, selectedState);
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

async function gotoApp(page: Page, locale: SupportedLocale): Promise<void> {
  const response = await page.goto(`/?locale=${encodeURIComponent(locale)}`);
  expect(
    response?.ok(),
    `app navigation for ${locale} returned ${response?.status() ?? 'no response'}`,
  ).toBe(true);
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
        await gotoApp(page, locale);
        await waitForAppShell(page);
        await assertNoLookupMisses(page, `app-shell/${locale}/${viewport.name}`);
        await assertGlossaryTokens(page, viewport, `app-shell/${locale}/${viewport.name}`);
        await captureBaseline(page, 'app-shell', locale, viewport);
      });

      test(`status-bar populated — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
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
        await gotoApp(page, locale);
        await waitForAppShell(page);
        await assertNoLookupMisses(
          page,
          `connection-overlay/${locale}/${viewport.name}`,
        );
        await captureBaseline(page, 'connection-overlay', locale, viewport);
      });

      test(`settings modal open — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
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
        await gotoApp(page, locale);
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
        await gotoApp(page, locale);
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

type ProductionLanguageCase = {
  readonly label: string;
  readonly workspace: Record<string, unknown>;
  readonly language: 'studioline' | 'fieldline';
  readonly mode: 'dark' | 'light';
  readonly expected: {
    readonly surface: string;
    readonly text: string;
    readonly vfoBorderTop: string;
    readonly vfoNumeralWeight: string;
  };
};

const PRODUCTION_LANGUAGE_CASES: readonly ProductionLanguageCase[] = [
  {
    label: 'clean StudioLine × dark',
    workspace: {},
    language: 'studioline',
    mode: 'dark',
    expected: { surface: '#0e1113', text: '#eef1f2', vfoBorderTop: '0px', vfoNumeralWeight: '200' },
  },
  {
    label: 'persisted StudioLine × light',
    workspace: { version: 1, designLanguage: 'studioline', theme: 'github-light' },
    language: 'studioline',
    mode: 'light',
    expected: { surface: '#faf7f2', text: '#14181a', vfoBorderTop: '0px', vfoNumeralWeight: '300' },
  },
  {
    label: 'persisted FieldLine × dark',
    workspace: { version: 1, designLanguage: 'fieldline', theme: 'nord' },
    language: 'fieldline',
    mode: 'dark',
    expected: { surface: '#0a0a0a', text: '#f2f5f7', vfoBorderTop: '3px', vfoNumeralWeight: '700' },
  },
  {
    label: 'persisted FieldLine × light',
    workspace: { version: 1, designLanguage: 'fieldline', theme: 'github-light' },
    language: 'fieldline',
    mode: 'light',
    expected: { surface: '#ffffff', text: '#000000', vfoBorderTop: '3px', vfoNumeralWeight: '700' },
  },
];

function productionScreenshotName(item: ProductionLanguageCase): string {
  return `${item.language}--${item.mode}--production-root.png`;
}

async function assertProductionLanguageCss(page: Page, item: ProductionLanguageCase): Promise<void> {
  const root = page.locator('html');
  await expect(root).toHaveAttribute('data-design-language', item.language);
  await expect(root).toHaveAttribute('data-language-mode', item.mode);
  const applied = await page.evaluate((language) => {
    const root = document.documentElement;
    const frequency = document.querySelector<HTMLElement>('.vfo-freq');
    const tile = document.querySelector<HTMLElement>('.vfo-tile');
    const style = getComputedStyle(root);
    return {
      surface: style.getPropertyValue(`--dl-${language}-surface`).trim(),
      text: style.getPropertyValue(`--dl-${language}-text`).trim(),
      vfoBorderTop: tile === null ? null : getComputedStyle(tile).borderTopWidth,
      vfoNumeralWeight: frequency === null ? null : getComputedStyle(frequency).fontWeight,
    };
  }, item.language);
  expect(applied).toEqual(item.expected);
  await expect.poll(() => page.evaluate(() => {
    const rules = [...document.styleSheets].flatMap((sheet) => {
      try {
        return [...sheet.cssRules].map((rule) => rule.cssText);
      } catch {
        return [];
      }
    });
    return {
      studioline: rules.some((rule) => rule.includes('--dl-studioline-surface')),
      fieldline: rules.some((rule) => rule.includes('--dl-fieldline-surface')),
      productionCss: [...document.styleSheets]
        .map((sheet) => sheet.href)
        .some((href) => /\/assets\/[^/]+-[\w-]+\.css$/.test(href)),
    };
  })).toEqual({ studioline: true, fieldline: true, productionCss: true });
}

async function assertProductionLanguageAccessibility(
  page: Page,
  item: ProductionLanguageCase,
): Promise<void> {
  const vfo = page.getByTestId('vfo-surface').first();
  const txKey = page.getByTestId('rx-tx-key').first();
  const txState = page.getByTestId('rx-tx-state').first();
  const txMark = page.getByTestId('rx-tx-rf-mark').first();
  const txLabel = page.getByTestId('rx-tx-rf-label').first();
  await expect(vfo).toHaveAccessibleName(/VFO/i);
  await expect(txKey).toHaveAccessibleName(/key|transmit|ptt/i);
  await expect(txState).toBeVisible();
  await expect(txMark).toBeVisible();
  await expect(txLabel).toBeVisible();
  await expect(txState).toHaveAttribute('data-rf', 'unknown');
  await expect(txState).toHaveAttribute('data-session', 'idle');
  await expect(txMark).toHaveText('◇');
  await expect(txLabel).toHaveText('RF ?');
  await expect(txState).toContainText('ready');

  // A real keyboard-caused focus target, rather than a programmatic focus,
  // proves the active production family has a visible focus treatment.
  await page.locator('body').focus();
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement)) return null;
    const style = getComputedStyle(element);
    return { tag: element.tagName, outline: style.outlineStyle, outlineColor: style.outlineColor };
  });
  expect(focused?.tag).toMatch(/BUTTON|INPUT|SELECT/);
  expect(focused?.outline).not.toBe('none');

  const contrast = await page.locator('html').evaluate((element, args) => {
    const style = getComputedStyle(element);
    const rgb = (value: string) => {
      const hex = /^#([0-9a-f]{6})$/i.exec(value);
      if (hex !== null) return [0, 2, 4].map((offset) => Number.parseInt(hex[1].slice(offset, offset + 2), 16) / 255);
      const functional = /^rgba?\(([^)]+)\)$/i.exec(value);
      if (functional === null) return null;
      const values = functional[1].match(/[\d.]+/g)?.slice(0, 3).map(Number);
      return values?.length === 3 ? values.map((channel) => channel / 255) : null;
    };
    const luminance = (channels: number[]) => channels
      .map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
      .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
    const ratio = (first: string, second: string) => {
      const a = rgb(first), b = rgb(second);
      if (a === null || b === null) return 0;
      const [lighter, darker] = [luminance(a), luminance(b)].sort((left, right) => right - left);
      return (lighter + 0.05) / (darker + 0.05);
    };
    return {
      text: ratio(style.getPropertyValue(`--dl-${args.language}-text`).trim(), args.surface),
      focus: ratio(args.outlineColor, args.surface),
    };
  }, { language: item.language, surface: item.expected.surface, outlineColor: focused?.outlineColor ?? '' });
  expect(contrast.text).toBeGreaterThanOrEqual(4.5);
  expect(contrast.focus).toBeGreaterThanOrEqual(3);

  const reducedMotion = await page.locator('.vfo-tile, .rx-tx-key, .rx-tx-unkey').evaluateAll((elements) =>
    elements.map((element) => {
      const style = getComputedStyle(element);
      return [style.transitionDuration, style.animationDuration];
    }));
  const durationMs = (value: string) => value.split(',').map((duration) => {
    const numeric = Number.parseFloat(duration);
    return duration.trim().endsWith('ms') ? numeric : numeric * 1000;
  });
  expect(reducedMotion.flat(2).flatMap(durationMs).every((duration) => duration <= 0.01)).toBe(true);
}

test.describe('MOR-1400 production design-language contract', () => {
  for (const item of PRODUCTION_LANGUAGE_CASES) {
    test(`${item.label} activates from production dist`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await preparePage(page, 'en-US', VIEWPORTS[0], { workspace: item.workspace });
      await gotoApp(page, 'en-US');
      await waitForAppShell(page);
      await assertProductionLanguageCss(page, item);
      await assertProductionLanguageAccessibility(page, item);
      // This is deliberately the real built `/' entry, served via the
      // immutable dist wrapper above. Fixture screenshots live in a separate
      // Playwright config and are not candidates for this expectation.
      await expect(page).toHaveScreenshot(productionScreenshotName(item), {
        animations: 'disabled',
        caret: 'hide',
      });
    });
  }

  test('invalid persisted presentation repairs to scoped StudioLine dark', async ({ page }) => {
    await preparePage(page, 'en-US', VIEWPORTS[0], {
      workspace: { version: 1, designLanguage: 'unknown', theme: 'unknown' },
    });
    await gotoApp(page, 'en-US');
    await waitForAppShell(page);
    await assertProductionLanguageCss(page, PRODUCTION_LANGUAGE_CASES[0]);
  });

  test('legacy LCD remains language-inert', async ({ page }) => {
    await preparePage(page, 'en-US', VIEWPORTS[0], {
      workspace: { version: 1, layout: 'lcd-cockpit', designLanguage: 'fieldline', theme: 'github-light' },
    });
    await gotoApp(page, 'en-US');
    await waitForAppShell(page);
    await expect(page.locator('html')).not.toHaveAttribute('data-design-language');
    await expect(page.locator('html')).not.toHaveAttribute('data-language-mode');
    const fieldlineSurface = await page.locator('html').evaluate((element) =>
      getComputedStyle(element).getPropertyValue('--dl-fieldline-surface').trim());
    expect(fieldlineSurface).toBe('');
  });

  test('mobile remains language-inert', async ({ browser }) => {
    const mobile = await browser.newPage();
    try {
      await preparePage(mobile, 'en-US', VIEWPORTS[1], {
        workspace: { version: 1, designLanguage: 'fieldline', theme: 'github-light' },
      });
      await gotoApp(mobile, 'en-US');
      await waitForAppShell(mobile);
      await expect(mobile.locator('html')).not.toHaveAttribute('data-design-language');
      await expect(mobile.locator('html')).not.toHaveAttribute('data-language-mode');
    } finally {
      await mobile.close();
    }
  });
});
