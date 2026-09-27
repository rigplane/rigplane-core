/**
 * MOR-2713 — the fixture pages mount only after every declared font face has
 * loaded (`fixtures/main.ts`, `fixtures/mobile-witness.ts`). The latin subset
 * of Roboto Mono is held back here so that the other subsets arrive first.
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

test('cockpit: at harness-ready every font face is loaded and each ch fact reserves the loaded font width', async ({ page }) => {
  await delayLatinSubset(page);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/fixtures/index.html?fixture=tx-phase-rx&theme=v2', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  const unloadedAtReady = await unloadedFaces(page);
  const atReady = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('[data-indicator-fact]')]
    .filter((element) => element.style.minInlineSize.endsWith('ch'))
    .map((element) => ({
      fact: element.dataset.indicatorFact,
      reserved: element.style.minInlineSize,
      computedPx: parseFloat(getComputedStyle(element).minInlineSize),
    })));
  await page.evaluate(() => document.fonts.ready);
  // The advance of '0' in each fact's own font, measured once every face has loaded.
  const loadedZeroPx = await page.evaluate(() => {
    const context = document.createElement('canvas').getContext('2d')!;
    return [...document.querySelectorAll<HTMLElement>('[data-indicator-fact]')]
      .filter((element) => element.style.minInlineSize.endsWith('ch'))
      .map((element) => {
        const style = getComputedStyle(element);
        context.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
        return context.measureText('0').width;
      });
  });
  expect(atReady.length).toBeGreaterThan(0);
  expect(loadedZeroPx).toHaveLength(atReady.length);
  expect.soft(atReady
    .map((fact, index) => ({ ...fact, expectedPx: parseFloat(fact.reserved) * loadedZeroPx[index] }))
    .filter((fact) => Math.abs(fact.computedPx - fact.expectedPx) > 0.01)).toEqual([]);
  expect(unloadedAtReady).toEqual([]);
});

test('mobile witness: at harness-ready every font face is loaded', async ({ page }) => {
  await delayLatinSubset(page);
  await page.goto('/fixtures/mobile-witness.html?fixture=topology-2-main-sub', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  expect(await unloadedFaces(page)).toEqual([]);
});
