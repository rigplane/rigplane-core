/**
 * MOR-2713 — evidence probe (temporary). Records, per fresh browser context,
 * how the cockpit's `ch` reservations resolve relative to the Roboto Mono
 * web-font load, and whether the phone S-meter bar is still moving after the
 * harness signals ready. Logs JSON only; the assertions come in the fix.
 */
import { test, type Browser, type Page } from '@playwright/test';

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 375, height: 812 };

async function freshPage(browser: Browser, baseURL: string, options: {
  viewport: { width: number; height: number };
  reducedMotion: 'reduce' | 'no-preference';
  fontDelayMs: number;
}): Promise<Page> {
  const context = await browser.newContext({
    baseURL, viewport: options.viewport, deviceScaleFactor: 1, colorScheme: 'dark',
    reducedMotion: options.reducedMotion,
  });
  const page = await context.newPage();
  if (options.fontDelayMs > 0) {
    await page.route('**/*.woff2', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, options.fontDelayMs));
      await route.continue();
    });
  }
  return page;
}

async function sampleFacts(page: Page, label: string) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('DOM.enable');
  await cdp.send('CSS.enable');
  const { root } = await cdp.send('DOM.getDocument', { depth: -1 });
  const { nodeId } = await cdp.send('DOM.querySelector', {
    nodeId: root.nodeId, selector: '[data-indicator-fact="agc"]',
  });
  const platform = nodeId
    ? (await cdp.send('CSS.getPlatformFontsForNode', { nodeId })).fonts
    : [];
  await cdp.detach();
  const inPage = await page.evaluate(() => {
    const facts = [...document.querySelectorAll<HTMLElement>('[data-indicator-fact]')]
      .slice(0, 3).map((el) => ({
        fact: el.dataset.indicatorFact,
        inline: el.style.minInlineSize,
        computed: getComputedStyle(el).minInlineSize,
        width: el.getBoundingClientRect().width,
      }));
    const ctx = document.createElement('canvas').getContext('2d')!;
    const zero = (font: string) => { ctx.font = font; return ctx.measureText('0').width; };
    return {
      t: Math.round(performance.now()),
      facts,
      zero: {
        roboto: zero('10px "Roboto Mono", monospace'), mono: zero('10px monospace'),
        serif: zero('10px serif'), sans: zero('10px sans-serif'),
      },
      robotoLoaded: document.fonts.check('10px "Roboto Mono"'),
      setStatus: document.fonts.status,
      faces: [...document.fonts].filter((face) => face.status !== 'unloaded')
        .map((face) => `${face.family}/${face.weight}/${face.unicodeRange.slice(0, 11)}/${face.status}`),
      fontResponses: performance.getEntriesByType('resource')
        .filter((entry) => entry.name.endsWith('.woff2'))
        .map((entry) => `${entry.name.split('/').pop()}@${Math.round((entry as PerformanceResourceTiming).responseEnd)}`),
    };
  });
  return { label, ...inPage, platform };
}

for (const [variant, fontDelayMs, runs] of [['natural', 0, 3], ['font-delay-1000', 1000, 2]] as const) {
  test(`MOR-2713 probe: cockpit ch reservations vs font load (${variant})`, async ({ browser }, testInfo) => {
    test.setTimeout(90_000);
    const baseURL = testInfo.project.use.baseURL!;
    for (let run = 0; run < runs; run += 1) {
      const page = await freshPage(browser, baseURL, { viewport: DESKTOP, reducedMotion: 'reduce', fontDelayMs });
      await page.goto('/fixtures/index.html?fixture=tx-phase-rx&theme=v2', { waitUntil: 'load' });
      await page.waitForSelector('body[data-harness-ready="true"]');
      const ready = await sampleFacts(page, 'ready');
      await page.evaluate(() => document.fonts.ready);
      const fontsReady = await sampleFacts(page, 'fonts-ready');
      await page.waitForTimeout(500);
      const settled = await sampleFacts(page, 'settled+500ms');
      console.log(`MOR-2713 probe ${variant} #${run}: ${JSON.stringify({ ready, fontsReady, settled })}`);
      await page.context().close();
    }
  });
}

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`MOR-2713 probe: phone S-meter bar after ready (${reducedMotion})`, async ({ browser }, testInfo) => {
    test.setTimeout(60_000);
    const baseURL = testInfo.project.use.baseURL!;
    for (let run = 0; run < 2; run += 1) {
      const page = await freshPage(browser, baseURL, { viewport: PHONE, reducedMotion, fontDelayMs: 0 });
      await page.goto('/fixtures/mobile-witness.html?fixture=topology-2-main-sub', { waitUntil: 'load' });
      await page.waitForSelector('body[data-harness-ready="true"]');
      const trace = await page.evaluate(async () => {
        const out: string[] = [];
        const start = performance.now();
        for (let step = 0; step < 24; step += 1) {
          const lit = [...document.querySelectorAll<SVGRectElement>('.m-smeter-bar [data-meter-fill]')]
            .filter((rect) => rect.getAttribute('visibility') === 'visible');
          const last = lit[lit.length - 1];
          const svg = document.querySelector<SVGSVGElement>('.m-smeter-bar svg');
          out.push(`${Math.round(performance.now() - start)}ms:${lit.length}:${last?.getAttribute('width')}`
            + `:${last?.getBoundingClientRect().right.toFixed(3)}:${svg?.clientWidth}`);
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        return out;
      });
      console.log(`MOR-2713 probe phone ${reducedMotion} #${run}: ${JSON.stringify(trace)}`);
      await page.context().close();
    }
  });
}
