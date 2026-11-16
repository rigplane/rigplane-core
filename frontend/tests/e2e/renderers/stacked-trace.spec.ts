import { test, expect } from '@playwright/test';

// MOR-4463: the stacked-trace renderer against a real WebGL2 context. The
// vitest suite pins the call contract; shader compilation and pixels are
// only provable here.
test('real GL: stacked history in depth, retune follows frequency, dense rows keep a one-bin carrier', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const [{ StackedTraceRenderer }, { defaultWaterfallOptions }, { defaultStackedTraceSettings }] = await Promise.all([
      import('/src/lib/renderers/stacked-trace-renderer.ts' as string),
      import('/src/lib/renderers/waterfall-renderer.ts' as string),
      import('/src/lib/renderers/stacked-trace-geometry.ts' as string),
    ]);
    const canvas = document.createElement('canvas'); canvas.id = 'stacked-witness';
    canvas.width = 400; canvas.height = 200; canvas.style.cssText = 'display:block;width:400px;height:200px';
    document.body.replaceChildren(canvas);
    const options = { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 14_050_000, spanHz: 100_000 };
    const settings = { ...defaultStackedTraceSettings, rows: 8, rowPeriodMs: 100 };
    const renderer = new StackedTraceRenderer(canvas, options, settings);
    const gl = canvas.getContext('webgl2')!;
    const luma = (x: number, y: number) => {
      const value = new Uint8Array(4); gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, value);
      return (value[0] + value[1] + value[2]) / 3;
    };
    const rgb = (x: number, y: number) => {
      const value = new Uint8Array(4); gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, value);
      return Array.from(value.subarray(0, 3));
    };
    // A one-bin carrier on a dense row is a spike a pixel or two wide: read
    // the brightest of the five pixels around its column.
    const spike = (x: number, y: number) => Math.max(...[-2, -1, 0, 1, 2].map(dx => luma(x + dx, y)));
    renderer.draw(0);
    const empty = rgb(200, 190);
    // 475 points: a noise floor with a carrier at the view centre.
    const sparse = new Uint8Array(475).fill(10); sparse[236] = 80; sparse[237] = 80; sparse[238] = 80;
    for (let i = 0; i < 8; i++) renderer.pushRow(sparse, options, i * 100);
    const animating = renderer.draw(750);
    const rested = renderer.draw(5_000);
    // Front row: inset 16 px, full-scale height 120 px. Sample under the crest.
    const carrier = luma(200, 120), floorAtCarrierHeight = luma(60, 120), sky = rgb(200, 195);
    // The oldest rows sit higher than any front-row noise: history is visible in depth.
    const depth = luma(200, 135), depthAside = luma(60, 160);
    renderer.updateOptions({ centerHz: 14_060_000 });
    renderer.draw(5_000);
    const movedTo = luma(160, 120), movedFrom = luma(200, 120);
    renderer.updateOptions({ centerHz: 14_050_000 });
    // A dense source: 8192 points with a single-bin carrier a quarter in.
    const dense = new Uint8Array(8192).fill(10); dense[2048] = 80;
    renderer.pushRow(dense, options, 6_000);
    renderer.draw(20_000);
    const needle = spike(100, 100), besideNeedle = luma(110, 100);
    // Zoomed out 2x, a column spans about 82 bins: a one-bin carrier anywhere
    // in the column must still be found (bin 2110 sits between the 64-bin
    // windows the first version read). The row fills the middle half of the view.
    const gap = new Uint8Array(8192).fill(10); gap[2110] = 80;
    renderer.pushRow(gap, options, 21_000);
    renderer.updateOptions({ spanHz: 200_000 });
    renderer.draw(40_000);
    const wide = spike(151, 100), besideWide = luma(140, 100);
    return { empty, animating, rested, carrier, floorAtCarrierHeight, sky, depth, depthAside,
      movedTo, movedFrom, needle, besideNeedle, wide, besideWide, rows: renderer.historyRows, error: gl.getError() };
  });
  expect(errors).toEqual([]);
  expect(result.error).toBe(0);
  expect(result.empty).toEqual([0, 16, 32]);
  expect(result.sky).toEqual([0, 16, 32]);
  expect(result.rows).toBe(8);
  expect(result.animating).toBe(true);
  expect(result.rested).toBe(false);
  expect(result.carrier).toBeGreaterThan(120);
  expect(result.floorAtCarrierHeight).toBeLessThan(result.carrier - 60);
  expect(result.depth).toBeGreaterThan(result.depthAside + 20);
  expect(result.movedTo).toBeGreaterThan(120);
  expect(result.movedFrom).toBeLessThan(result.movedTo - 60);
  expect(result.needle).toBeGreaterThan(100);
  expect(result.besideNeedle).toBeLessThan(result.needle - 60);
  expect(result.wide).toBeGreaterThan(100);
  expect(result.besideWide).toBeLessThan(result.wide - 60);
  await page.locator('#stacked-witness').screenshot({ path: info.outputPath('stacked.png') });
});
