import { test, expect, type Page } from '@playwright/test';

declare global {
  interface Window {
    __spectrumWitness: {
      commands: unknown[];
      scopeIntents(): string[];
      autoFloor?: { deliverFrames(count: number, noise: number, carrier: number): Promise<void> };
    };
  }
}

/**
 * MOR-4605 — the AUTO waterfall noise floor through the PRODUCT seam: the
 * real SpectrumPanel (toolbar visible, REF live from the captured IC-7300
 * state/capabilities pair), frames delivered through the real scope ingress
 * (production v2 wire header from the Python handler's encoder, parsed and
 * streamed by the production ScopeStream), colours read from the actual
 * WebGL back buffer. `waterfall-auto-floor.test.ts` keeps the renderer-level
 * pins; this spec is the one that fails when the shader stops subtracting
 * the tracked floor.
 *
 * Expected colours are hardcoded goldens of the classic palette, never a
 * re-implementation of the shader's mapping: the background at the tracked
 * floor is the palette start #001020, the carrier (40 of 80 above the
 * floor, t = 0.5) is #87FF00, and REF +5 dB (t = 52/255) lands on
 * #0043A1 — see COLOR_SCHEMES in waterfall-renderer.ts.
 */

/** The panel's waterfall canvas and renderer, retained from its own paints. */
async function retainWaterfall(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const module = await import(url);
    const original = module.WaterfallRenderer.prototype.draw;
    module.WaterfallRenderer.prototype.draw = function (this: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer) {
      (window as typeof window & { __retainedWaterfall?: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer }).__retainedWaterfall = this;
      original.call(this);
    };
  });
}

interface RowRead {
  row: number[];
  width: number;
  height: number;
  error: number;
}

/** The newest waterfall row (top in GL coordinates), repainted then read. */
async function readRow(page: Page): Promise<RowRead> {
  return page.evaluate(() => {
    const r = (window as typeof window & { __retainedWaterfall: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer }).__retainedWaterfall;
    const canvas = document.querySelector<HTMLCanvasElement>('.waterfall-history canvas')!;
    const gl = canvas.getContext('webgl2')!;
    r.draw();
    const row = new Uint8Array(canvas.width * 4);
    gl.readPixels(0, canvas.height - 1, canvas.width, 1, gl.RGBA, gl.UNSIGNED_BYTE, row);
    return { row: Array.from(row), width: canvas.width, height: canvas.height, error: gl.getError() };
  });
}

function pixel(read: RowRead, x: number): number[] {
  return read.row.slice(x * 4, x * 4 + 4);
}

/** The brightest (r+g) column of the row — the carrier. */
function carrierPixel(read: RowRead): number[] {
  let best = 0;
  for (let x = 1; x < read.width; x += 1) {
    if (read.row[x * 4] + read.row[x * 4 + 1] > read.row[best * 4] + read.row[best * 4 + 1]) best = x;
  }
  return pixel(read, best);
}

/** A column far left of the carrier (bins 120..124 of 256) — flat noise. */
function backgroundPixel(read: RowRead): number[] {
  return pixel(read, Math.floor(read.width * 0.1));
}

const PALETTE_START = [0, 16, 32, 255];
const CARRIER_ABOVE_FLOOR = [135, 255, 0, 255];
const BACKGROUND_PLUS_5DB = [0, 67, 161, 255];
const CARRIER_PLUS_5DB = [170, 255, 0, 255];

async function openWitness(page: Page): Promise<void> {
  await page.goto('/fixtures/spectrum-witness.html?autoFloor');
  await page.waitForSelector('body[data-harness-ready="true"]');
  await retainWaterfall(page);
}

function deliver(page: Page, count: number, noise: number, carrier: number): Promise<void> {
  return page.evaluate(({ count, noise, carrier }) =>
    window.__spectrumWitness.autoFloor!.deliverFrames(count, noise, carrier), { count, noise, carrier });
}

test('AUTO normalizes the noise floor through the real panel, ingress and shader', async ({ page }) => {
  await openWitness(page);
  // The readiness frame (noise 10, carrier 50) plus settling frames: flat
  // levels converge to exact row bytes.
  await deliver(page, 3, 10, 50);

  // AUTO off: the fixed colour scale — the background is bright, nothing
  // like the palette start.
  const fixed = await readRow(page);
  expect(fixed.error).toBe(0);
  expect(backgroundPixel(fixed)).not.toEqual(PALETTE_START);
  expect(backgroundPixel(fixed).slice(0, 3).reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(300);

  await page.getByTestId('scope-auto-floor-toggle').click();
  await deliver(page, 3, 10, 50);

  // AUTO on: rows pushed now carry the tracked floor (10); the background
  // colour is exactly the palette start and the carrier, 40 of 80 above the
  // noise, lands exactly on t = 0.5. Deleting `- uAutoFloor * grid.w` from
  // the shader fails both assertions.
  const floored = await readRow(page);
  expect(floored.error).toBe(0);
  expect(backgroundPixel(floored)).toEqual(PALETTE_START);
  expect(carrierPixel(floored)).toEqual(CARRIER_ABOVE_FLOOR);

  // The noise floor rises 30 of 80 (at or beyond the follower's instant
  // reseed distance) with the carrier still 40 above it. The background
  // keeps the palette start and the carrier keeps its exact colour.
  await deliver(page, 9, 40, 80);
  const risen = await readRow(page);
  expect(risen.error).toBe(0);
  expect(backgroundPixel(risen)).toEqual(PALETTE_START);
  expect(carrierPixel(risen)).toEqual(CARRIER_ABOVE_FLOOR);

  // AUTO off again: the same rows, the fixed scale — the background leaves
  // the palette start immediately.
  await page.getByTestId('scope-auto-floor-toggle').click();
  await deliver(page, 1, 40, 80);
  const unmapped = await readRow(page);
  expect(unmapped.error).toBe(0);
  expect(backgroundPixel(unmapped)).not.toEqual(PALETTE_START);
  expect(backgroundPixel(unmapped).slice(0, 3).reduce((a, b) => a + b, 0)).toBeGreaterThanOrEqual(300);
});

test('with AUTO on the real REF keys step the waterfall offset, never the radio REF', async ({ page }) => {
  await openWitness(page);
  await deliver(page, 2, 10, 50);

  const intents = () => page.evaluate(() => window.__spectrumWitness.scopeIntents());
  const refGroup = page.locator('div.toolbar-group:has(span.toolbar-label:text-is("REF"))');
  const refMinus = refGroup.locator('button', { hasText: '−' });
  const refPlus = refGroup.locator('button', { hasText: /^\+$/ });

  // AUTO off: REF + steps the radio's own reference from the captured +5
  // readback — the recorded intent the toolbar used to send in every mode.
  await refPlus.click();
  expect(await intents()).toEqual(['scopeControls.onRefChange(10)']);

  // AUTO on: the REF slot carries the client-side offset (0), and stepping
  // it never reaches the scope bus.
  await page.getByTestId('scope-auto-floor-toggle').click();
  await deliver(page, 1, 10, 50);
  await expect(refGroup.locator('.toolbar-value')).toHaveText('0');
  expect(backgroundPixel(await readRow(page))).toEqual(PALETTE_START);

  await refPlus.click();
  await deliver(page, 1, 10, 50);
  await expect(refGroup.locator('.toolbar-value')).toHaveText('+5');
  expect(await intents()).toEqual(['scopeControls.onRefChange(10)']);
  const offset = await readRow(page);
  expect(offset.error).toBe(0);
  // REF +5 dB is 5 * 40/60 of the 80 scale above the tracked floor: both
  // the background and the carrier shift by exactly the offset's palette
  // parameter.
  expect(backgroundPixel(offset)).toEqual(BACKGROUND_PLUS_5DB);
  expect(carrierPixel(offset)).toEqual(CARRIER_PLUS_5DB);

  // REF back down: the offset returns to the tracked floor itself.
  await refMinus.click();
  await deliver(page, 1, 10, 50);
  await expect(refGroup.locator('.toolbar-value')).toHaveText('0');
  const back = await readRow(page);
  expect(back.error).toBe(0);
  expect(backgroundPixel(back)).toEqual(PALETTE_START);
  expect(carrierPixel(back)).toEqual(CARRIER_ABOVE_FLOOR);
});
