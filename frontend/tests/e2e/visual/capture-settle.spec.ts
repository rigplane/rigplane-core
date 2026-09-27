/**
 * MOR-2713 — the fixture pages mount only after their web fonts have loaded.
 * The latin subset of Roboto Mono is held back here while the other subsets
 * load at once.
 */
import { test, expect, type Page } from '@playwright/test';

const LATIN_DELAY_MS = 1_000;

async function delayLatinSubset(page: Page): Promise<void> {
  await page.route('**/roboto-mono-latin.woff2', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, LATIN_DELAY_MS));
    await route.continue();
  });
}

function unloadedFaces(page: Page): Promise<string[]> {
  return page.evaluate(() => Array.from(document.fonts)
    .filter((face) => face.status !== 'loaded')
    .map((face) => `${face.family} ${face.weight} ${face.unicodeRange}`));
}

test('cockpit ch reservations resolve against the loaded Roboto Mono when its latin subset arrives last', async ({ page }) => {
  await delayLatinSubset(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/fixtures/index.html?fixture=tx-phase-rx&theme=v2', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  const unloadedAtReady = await unloadedFaces(page);
  // Temporary evidence line for the RED run; removed with the fix.
  console.log(`MOR-2713 latin-last at ready: ${JSON.stringify(await page.evaluate(() => ({
    agc: getComputedStyle(document.querySelector('[data-indicator-fact="agc"]')!).minInlineSize,
    loaded: Array.from(document.fonts).filter((face) => face.status === 'loaded')
      .map((face) => `${face.weight} ${face.unicodeRange.slice(0, 11)}`),
  })))}`);
  await page.evaluate(() => document.fonts.ready);
  const facts = await page.evaluate(() => {
    const context = document.createElement('canvas').getContext('2d')!;
    return [...document.querySelectorAll<HTMLElement>('[data-indicator-fact]')]
      .filter((element) => element.style.minInlineSize.endsWith('ch'))
      .map((element) => {
        const style = getComputedStyle(element);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        return {
          fact: element.dataset.indicatorFact,
          reserved: element.style.minInlineSize,
          computedPx: parseFloat(style.minInlineSize),
          expectedPx: parseFloat(element.style.minInlineSize) * context.measureText('0').width,
        };
      });
  });
  console.log(`MOR-2713 latin-last after fonts.ready: ${JSON.stringify(facts)}`);
  expect(facts.length).toBeGreaterThan(0);
  expect.soft(facts.filter((fact) => Math.abs(fact.computedPx - fact.expectedPx) > 0.01)).toEqual([]);
  expect(unloadedAtReady).toEqual([]);
});

test('mobile witness mounts after every font face has loaded', async ({ page }) => {
  await delayLatinSubset(page);
  await page.goto('/fixtures/mobile-witness.html?fixture=topology-2-main-sub', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  expect(await unloadedFaces(page)).toEqual([]);
});
