/**
 * Localization visual smoke (RP-ML-006).
 *
 * Drives the built Core frontend with a stubbed backend (no live radio,
 * no live Tower) under three locales — `en-US`, `ja-JP`, `qps-ploc` — at
 * two viewports — desktop 1280×800 and mobile 390×844.
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
 * Assertion floor:
 *   - Page text AND page markup MUST NOT contain `[missing:` (lookup-miss
 *     marker) or `${` (raw template leak). The markup pass catches
 *     markers in `aria-label`/`title`/`placeholder` attributes and in
 *     hidden containers that the visibility-filtered `innerText` pass
 *     never sees.
 *   - The status-bar, disconnected and toast cases assert their own
 *     surface rather than the app shell: the desktop `.status-bar` must
 *     be visible and the mobile layout must not render one; the
 *     disconnected case (skipped on mobile) expects the StatusBar radio
 *     indicator to report `degraded`; the toast case requires the
 *     `__i18nWsDispatch` hook, dispatches through it and expects a
 *     visible toast.
 *   - Glossary tokens used by populated state — `MAIN`, `SUB`, `USB`,
 *     `LSB`, `RTTY`, `VFO` — MUST appear verbatim in EVERY locale,
 *     including `qps-ploc`, on the desktop app-shell. The radio model is
 *     not asserted: no surface this suite drives renders it. Mobile is
 *     excluded: the portrait phone renders only the active receiver.
 *   - On `qps-ploc` the body text MUST carry pseudo-locale markers
 *     (`⟦⟧` wrappers / diacritic substitutions, `pseudo.ts`), so a
 *     silent fall-back to English fails the run. Unsubstituted English
 *     glyphs elsewhere are still expected — interpolated radio values
 *     and mode tokens are passed through verbatim by design.
 *
 * Scope split with RP-ML-013A:
 *   - RP-ML-013A is the cheap unit-test floor (vitest + jsdom) that runs
 *     in <2s with no browser. It validates the runtime transform.
 *   - RP-ML-006 (this file) is the heavier full-page smoke driven in a
 *     real browser.
 */

import { test, expect, type Page, type Route } from '@playwright/test';

import {
  mockCapabilities,
  mockDisconnectedState,
  mockInfo,
  mockLocalControllerStatus,
  mockState,
} from './fixtures';

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
 * All six render on the desktop app-shell under every supported locale,
 * `qps-ploc` included. The radio model is deliberately NOT asserted:
 * no surface this suite drives renders the model string at all (the
 * fixtures publish `mockCapabilities.model` and it never reaches the
 * DOM), so there is no verbatim model text to guard. Mobile is excluded
 * because the portrait phone renders only the active receiver — its DOM
 * carries no SUB/RTTY/VFO text at all, so the desktop-wide token set
 * cannot honestly apply there.
 */
const GLOSSARY_TOKENS_DESKTOP = ['MAIN', 'SUB', 'USB', 'LSB', 'RTTY', 'VFO'];

/**
 * Characters that can only come from the `qps-ploc` transform in
 * `frontend/src/lib/i18n/pseudo.ts`: the `⟦`/`⟧` wrappers it puts around
 * every catalog string plus its DIACRITIC_MAP letter substitutions.
 * Plain English copy — the state the page falls back to when the
 * pseudo-locale never applied — contains none of them.
 */
const PSEUDO_MARKERS =
  /[⟦⟧áƀçđéƒğĥíĵķłɱñóþǫŕšťúṽŵẋýžÁƁÇĐÉƑĞĤÍĴĶŁⱮÑÓÞǪŔŠŤÚṼŴẊÝŽ]/;

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
  await page.route('**/api/v1/controller', (route) => {
    if (route.request().method() !== 'GET') return route.fulfill({ status: 405, json: {} });
    return json(route, mockLocalControllerStatus);
  });
  // Anything else under /api/v1/ — return an empty 200 so the page does
  // not surface a network error overlay we did not plan for.
  await page.route('**/api/v1/**', (route) => {
    const url = route.request().url();
    if (
      url.endsWith('/state') ||
      url.endsWith('/capabilities') ||
      url.endsWith('/info') ||
      new URL(url).pathname === '/api/v1/controller'
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

/**
 * Inspect the rendered body text and the full page markup for forbidden
 * markers.
 *
 * `[missing:` is what the runtime emits when a key is absent from the
 * active locale catalog (see `runtime.ts`). It MUST NOT appear in a
 * shipped surface. `${` is the raw template-literal leak; same story.
 *
 * The markup pass exists because `innerText` is visibility-filtered:
 * `aria-label`, `title` and `placeholder` attribute values and hidden
 * containers never reach it. Script and style blocks are stripped from
 * the markup first.
 */
async function assertNoLookupMisses(page: Page, surfaceLabel: string): Promise<void> {
  const bodyText = await page.locator('body').innerText();
  expect(bodyText, `${surfaceLabel}: lookup-miss marker present`).not.toMatch(/\[missing:/);
  expect(bodyText, `${surfaceLabel}: raw template leak present`).not.toMatch(/\$\{[a-zA-Z]/);
  const markup = (await page.content())
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '');
  expect(markup, `${surfaceLabel}: lookup-miss marker in markup`).not.toMatch(/\[missing:/);
  expect(markup, `${surfaceLabel}: raw template leak in markup`).not.toMatch(/\$\{[a-zA-Z]/);
}

/**
 * Confirm glossary tokens survive the active locale, including
 * `qps-ploc`. Restricted to desktop: the mobile portrait layout renders
 * only the active receiver, and its DOM carries no SUB/RTTY/VFO text at
 * all, so the desktop token set cannot apply there.
 */
async function assertGlossaryTokens(
  page: Page,
  viewport: ViewportSpec,
  surfaceLabel: string,
): Promise<void> {
  if (viewport.name !== 'desktop') return;
  for (const token of GLOSSARY_TOKENS_DESKTOP) {
    await expect(
      page.locator('body'),
      `${surfaceLabel}: glossary token "${token}" missing`,
    ).toContainText(token, { useInnerText: true });
  }
}

/**
 * Under `qps-ploc` the page must actually carry the pseudo-locale
 * transform. Without this, a run that silently fell back to English
 * would pass every other check: glossary tokens and mode readouts are
 * interpolation values the transform deliberately leaves verbatim.
 */
async function assertPseudoMarkers(
  page: Page,
  locale: SupportedLocale,
  surfaceLabel: string,
): Promise<void> {
  if (locale !== 'qps-ploc') return;
  await expect(
    page.locator('body'),
    `${surfaceLabel}: no pseudo-locale markers — did the locale fall back to English?`,
  ).toContainText(PSEUDO_MARKERS, { useInnerText: true });
}

async function waitForAppShell(page: Page): Promise<void> {
  // RadioLayout always renders one of these shells; wait for whichever
  // wins the responsive bracket.
  await page
    .locator('.radio-layout, .m-layout, .m-landscape, .lcd-layout, .lcd-cockpit, .error-overlay')
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 });
  // Settle layout: small idle window so transitions and Svelte 5
  // effects flush before the assertions.
  await page.waitForTimeout(400);
}

async function gotoApp(page: Page, locale: SupportedLocale): Promise<void> {
  const response = await page.goto(`/?locale=${encodeURIComponent(locale)}`);
  expect(
    response?.ok(),
    `app navigation for ${locale} returned ${response?.status() ?? 'no response'}`,
  ).toBe(true);
}

// MOR-1549: this file used to force `mode: 'serial'` here, which made the
// first failing screenshot-diff case (see the `MOR-1400 production
// design-language contract` describe below) abort every later case in the
// file as SKIPPED. Serial's SKIPPED cascade burned one CI round per
// still-unmeasured scene on past re-pins — MOR-1486 needed four rounds;
// MOR-1474 needed three, with rounds 1-2 establishing nothing about the
// FieldLine pair serial fail-fast never reached (see the production-root
// README's re-pin history) — leaving unmeasured scenes reportable as if
// they were reassuring.
//
// Investigation: no case in this file depends on another. Every test
// builds its own page/context from scratch via `preparePage`/`gotoApp`,
// and any workspace/localStorage state a case needs is stamped fresh on
// that page's own init script — there is no shared mutable fixture to
// protect with serial ordering. `playwright.i18n.config.ts` already pins
// `fullyParallel: false` and `workers: 1` for this project, so
// declaration order is preserved regardless of `mode`. Explicit
// `mode: 'default'` keeps that order without serial's fail-fast/SKIPPED
// cascade — every case now runs to completion on every run, including the
// four screenshot-diff comparisons below, which are no longer skipped
// after an earlier one fails.
test.describe.configure({ mode: 'default' });

test.describe('i18n visual smoke (RP-ML-006)', () => {
  for (const locale of LOCALES) {
    for (const viewport of VIEWPORTS) {
      test(`app-shell loaded — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
        await waitForAppShell(page);
        await assertNoLookupMisses(page, `app-shell/${locale}/${viewport.name}`);
        await assertGlossaryTokens(page, viewport, `app-shell/${locale}/${viewport.name}`);
        await assertPseudoMarkers(page, locale, `app-shell/${locale}/${viewport.name}`);
      });

      test(`status-bar populated — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
        await waitForAppShell(page);
        // StatusBar.svelte renders the `.status-bar` element and
        // RadioLayout.svelte mounts it; MobileRadioLayout.svelte does
        // not reference StatusBar, so the mobile case pins its absence.
        const statusBar = page.locator('.status-bar');
        if (viewport.name === 'desktop') {
          await expect(
            statusBar,
            `status-bar/${locale}/${viewport.name}: .status-bar not visible`,
          ).toBeVisible();
        } else {
          await expect(
            statusBar,
            `status-bar/${locale}/${viewport.name}: the mobile skin must not render a status bar`,
          ).toHaveCount(0);
        }
        await assertNoLookupMisses(page, `status-bar/${locale}/${viewport.name}`);
      });

      test(`connection-overlay disconnected — ${locale} @ ${viewport.name}`, async ({ page }) => {
        test.skip(
          viewport.name !== 'desktop',
          'the phone layout shows no radio-link state for this fixture',
        );
        await preparePage(page, locale, viewport, { state: mockDisconnectedState });
        await gotoApp(page, locale);
        await waitForAppShell(page);
        // StatusBar.svelte interpolates the raw link state
        // (`state: radioState`) into the radio indicator's title.
        await expect(
          page.locator('.status-bar .indicator[title*="degraded"]'),
          `connection-overlay/${locale}/${viewport.name}: no StatusBar indicator reports the degraded radio link`,
        ).toBeVisible();
        await assertNoLookupMisses(
          page,
          `connection-overlay/${locale}/${viewport.name}`,
        );
      });

      test(`settings modal open — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
        await waitForAppShell(page);
        // The opener depends on the responsive skin: the desktop
        // StatusBar carries `.control-btn.settings-btn`; the mobile
        // skin's header carries `.m-settings-btn` (MobileRadioLayout),
        // which opens the setup BottomSheet. Each viewport must open a
        // real dialog — a renamed selector or a dialog that never
        // appears fails here instead of degrading this case into
        // another app-shell pass.
        const desktopOpener = page.locator('.control-btn.settings-btn');
        if (viewport.name === 'desktop') {
          await expect(desktopOpener).toHaveCount(1);
          await desktopOpener.click();
        } else {
          await expect(desktopOpener).toHaveCount(0);
          const mobileOpener = page.locator('.m-settings-btn');
          await expect(mobileOpener).toHaveCount(1);
          await mobileOpener.click();
        }
        await expect(
          page.locator('[role="dialog"][aria-modal="true"]').first(),
          `settings-modal/${locale}/${viewport.name}: dialog never opened`,
        ).toBeVisible();
        await assertNoLookupMisses(
          page,
          `settings-modal/${locale}/${viewport.name}`,
        );
      });

      test(`send-report dialog open — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
        await waitForAppShell(page);
        // The report opener is desktop chrome: StatusBar renders it,
        // the mobile skin does not. Desktop must open the dialog; the
        // mobile case pins the desktop opener's absence so a selector
        // rename or a layout regression fails here loudly instead of
        // degrading this case into another app-shell pass.
        const reportBtn = page.locator('.control-btn.report-btn');
        if (viewport.name === 'desktop') {
          await expect(reportBtn).toHaveCount(1);
          await reportBtn.click();
          await expect(
            page.locator('[role="dialog"][aria-modal="true"]').first(),
            `send-report-dialog/${locale}/${viewport.name}: dialog never opened`,
          ).toBeVisible();
        } else {
          await expect(reportBtn).toHaveCount(0);
        }
        await assertNoLookupMisses(
          page,
          `send-report-dialog/${locale}/${viewport.name}`,
        );
      });

      test(`toast notification — ${locale} @ ${viewport.name}`, async ({ page }) => {
        await preparePage(page, locale, viewport);
        await gotoApp(page, locale);
        await waitForAppShell(page);
        // Dispatch a notification through the stubbed WebSocket using
        // a code that is bundled in en-US.json (RP-ML-005 emits
        // reasonCode + params from `broadcast_notification`). The hook
        // is asserted before use so a missing stub fails with its own
        // message.
        const hookType = await page.evaluate(() =>
          typeof (window as unknown as { __i18nWsDispatch?: unknown }).__i18nWsDispatch
        );
        expect(
          hookType,
          `toast/${locale}/${viewport.name}: window.__i18nWsDispatch is missing — the WS stub never installed it`,
        ).toBe('function');
        await page.evaluate(() => {
          (window as unknown as { __i18nWsDispatch: (msg: unknown) => void }).__i18nWsDispatch({
            type: 'notification',
            level: 'warning',
            code: 'licenseExpired',
            message: 'Your license has expired. Reactivate to continue.',
            params: {},
          });
        });
        // Toast.svelte renders a notification as `button.toast` inside
        // its own `.toast-container`; AppGlobalHost.svelte mounts it.
        // The toast's `in:fly` transition lasts 200 ms; the wait below
        // is 400 ms.
        await expect(
          page.locator('.toast-container .toast'),
          `toast/${locale}/${viewport.name}: dispatched notification rendered no toast`,
        ).toBeVisible();
        await page.waitForTimeout(400);
        await assertNoLookupMisses(page, `toast/${locale}/${viewport.name}`);
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
    expected: { surface: '#0e1113', text: '#eef1f2', vfoBorderTop: '0px', vfoNumeralWeight: '800' },
  },
  {
    label: 'persisted StudioLine × light',
    workspace: { version: 2, designLanguageBySkin: { 'desktop-v2': 'studioline' }, theme: 'github-light' },
    language: 'studioline',
    mode: 'light',
    expected: { surface: '#faf7f2', text: '#14181a', vfoBorderTop: '0px', vfoNumeralWeight: '800' },
  },
  {
    label: 'persisted FieldLine × dark',
    workspace: { version: 2, designLanguageBySkin: { 'desktop-v2': 'fieldline' }, theme: 'nord' },
    language: 'fieldline',
    mode: 'dark',
    expected: { surface: '#0a0a0a', text: '#f2f5f7', vfoBorderTop: '3px', vfoNumeralWeight: '700' },
  },
  {
    label: 'persisted FieldLine × light',
    workspace: { version: 2, designLanguageBySkin: { 'desktop-v2': 'fieldline' }, theme: 'github-light' },
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
        .some((href) => href !== null && /\/assets\/[^/]+-[\w-]+\.css$/.test(href)),
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
  await expect(txMark).toBeAttached();
  await expect(txLabel).toBeAttached();
  await expect(txState).toHaveAttribute('data-rf', 'unknown');
  await expect(txState).toHaveAttribute('data-session', 'idle');
  await expect(txMark).toBeEmpty();
  await expect(txLabel).toBeEmpty();
  const [stateBox, markBox, labelBox] = await Promise.all([
    txState.boundingBox(), txMark.boundingBox(), txLabel.boundingBox(),
  ]);
  expect(stateBox).not.toBeNull();
  expect(markBox).not.toBeNull();
  expect(labelBox).not.toBeNull();
  await expect(txState).not.toContainText(/ready/i);

  // Keep the keyboard-reachability probe independent of any focus-frame paint.
  await page.locator('body').focus();
  await page.keyboard.press('Tab');
  const focusedTag = await page.evaluate(() => document.activeElement?.tagName ?? null);
  expect(focusedTag).toMatch(/BUTTON|INPUT|SELECT/);

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
    };
  }, { language: item.language, surface: item.expected.surface });
  expect(contrast.text).toBeGreaterThanOrEqual(4.5);

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

async function stabilizeProductionCapture(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
  // MOR-2545 PR2: the status line is one compact indicator; its accessible
  // name carries the old three-span text (unread parts omitted).
  await expect(page.getByTestId('scope-display-surface'))
    .toHaveAttribute('aria-label', 'SRC hardware · inactive · HW off');
}

test.describe('MOR-1400 production design-language contract', () => {
  for (const item of PRODUCTION_LANGUAGE_CASES) {
    test(`${item.label} activates from production dist`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await preparePage(page, 'en-US', VIEWPORTS[0], {
        workspace: item.workspace,
        state: { ...mockState, fieldStatus: Object.fromEntries(['main.sMeter', 'sub.sMeter']
          .map(path => [path, { storePath: path, observed: false,
            freshness: 'unknown' as const, availability: 'missing' as const }])) },
      });
      await gotoApp(page, 'en-US');
      await waitForAppShell(page);
      await assertProductionLanguageCss(page, item);
      await assertProductionLanguageAccessibility(page, item);
      await stabilizeProductionCapture(page);
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

  test('v1 global designLanguage is dropped, not migrated', async ({ page }) => {
    await preparePage(page, 'en-US', VIEWPORTS[0], {
      // MOR-2218 owner ruling: a v1 global value is dropped, so a stored
      // non-default language renders the desktop-v2 default (StudioLine).
      workspace: { version: 1, designLanguage: 'fieldline', theme: 'nord' },
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
