import { expect, test } from '@playwright/test';
import { startJourneyServer } from './server-process';
import { settlePassband, watchScopeFrames } from './panorama-passband-probe';
import { expectPanoramaClean, watchPanoramaErrors } from './panorama-guards';

/**
 * MOR-4386: the panorama zoom cluster ([-] [+] over [S] [B], bottom-right of
 * the spectrum). IC-705 panorama simulator tuned to 7.200 MHz LSB: S opens
 * the 40 m phone segment of the ARRL band plan, B the whole 40 m band, and
 * the keys halve/double the width. Every wait is on the frames the radio
 * simulator streams.
 */
const PORT = Number(process.env.PANORAMA_ZOOM_OVERLAY_PORT ?? 22340);
const PHONE_40M = [7_125_000, 7_300_000];
const BAND_40M = [7_000_000, 7_300_000];

async function boot(page: import('@playwright/test').Page, info: import('@playwright/test').TestInfo) {
  const server = await startJourneyServer(PORT, 7_200_000,
    ['--model', 'IC-705', '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
  const frames = watchScopeFrames(page);
  await page.goto(`http://127.0.0.1:${PORT}/?ui=v2`);
  await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
  await settlePassband(page, PORT);
  return { server, frames };
}
const width = (frames: Array<{ view: number[] }>) => { const v = frames.at(-1)?.view; return v ? v[1] - v[0] : 0; };

test('IC-705 at 7.200 LSB: S, B and the zoom keys drive the view; the cluster never blocks drag-pan (MOR-4386)', async ({ page }, info) => {
  const watch = watchPanoramaErrors(page);
  const { server, frames } = await boot(page, info);
  try {
    const cluster = page.getByTestId('scope-zoom-overlay').first();
    await expect(cluster.getByRole('button')).toHaveText(['−', '+', 'S', 'B']);
    await expect(page.getByTestId('scope-zoom-segment').first()).toHaveAttribute('data-segment-kind', 'phone');

    // The cluster sits in the spectrum area's bottom-right and clear of the axis labels.
    const area = (await page.locator('.spectrum-area').first().boundingBox())!;
    const box = (await cluster.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(area.x + area.width);
    expect(box.y + box.height).toBeLessThanOrEqual(area.y + area.height);
    expect(box.x + box.width / 2).toBeGreaterThan(area.x + area.width / 2);
    expect(box.y + box.height / 2).toBeGreaterThan(area.y + area.height / 2);

    // [S]: the 40 m phone segment, edge to edge.
    await page.getByTestId('scope-zoom-segment').first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual(PHONE_40M);
    // [B]: the whole band.
    await page.getByTestId('scope-zoom-band').first().click();
    await expect.poll(() => frames.at(-1)?.view).toEqual(BAND_40M);
    // A lit key returns to the VFO-following view.
    await page.getByTestId('scope-zoom-band').first().click();
    // CENTER window around the VFO: the 7.200 MHz centre, no longer the band edges.
    await expect.poll(() => { const v = frames.at(-1)?.view; return v ? (v[0]! + v[1]!) / 2 : 0; }).toBe(7_200_000);

    // [+] halves and [-] doubles the visible width.
    await expect.poll(() => width(frames)).toBeGreaterThan(0);
    const before = width(frames);
    await page.getByTestId('scope-zoom-in').first().click();
    await expect.poll(() => width(frames)).toBe(before / 2);
    await page.getByTestId('scope-zoom-out').first().click();
    await expect.poll(() => width(frames)).toBe(before);

    // Drag-pan through the cluster's own gap still tunes (the gap is not a button).
    const gapX = box.x + 28 + 2; const gapY = box.y + 28 + 2;
    const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x!, y!)?.closest('button') !== null, [gapX, gapY]);
    expect(hit).toBe(false);
    const freqBefore = ((await (await page.request.get(`http://127.0.0.1:${PORT}/api/v1/state`)).json()) as { main: { freqHz: number } }).main.freqHz;
    await page.mouse.move(gapX, gapY);
    await page.mouse.down();
    await page.mouse.move(gapX - area.width * 0.2, gapY, { steps: 8 });
    await page.mouse.up();
    await expect.poll(async () => ((await (await page.request.get(`http://127.0.0.1:${PORT}/api/v1/state`)).json()) as
      { main: { freqHz: number } }).main.freqHz).not.toBe(freqBefore);
    expectPanoramaClean(watch);
  } finally {
    await server.kill();
  }
});
