import { test, expect } from '@playwright/test';

// MOR-4463: the stacked-trace renderer against a real WebGL2 context. The
// vitest suite pins the call contract; shader compilation and pixels are
// only provable here.
test('real GL: stacked history in depth, retune follows frequency', async ({ page }, info) => {
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
    renderer.draw(0);
    const empty = rgb(200, 190);
    // 475 points: a noise floor with a carrier at the view centre. The seven
    // older rows also carry one at three quarters of the view that the
    // newest row — the live front trace — does not have.
    const sparse = new Uint8Array(475).fill(10); sparse[236] = 80; sparse[237] = 80; sparse[238] = 80;
    const older = sparse.slice(); older[355] = 80; older[356] = 80; older[357] = 80;
    for (let i = 0; i < 8; i++) renderer.pushRow(i < 7 ? older : sparse, options, i * 100);
    const animating = renderer.draw(750);
    const rested = renderer.draw(5_000);
    // Front row: inset 16 px, full-scale height 120 px. Sample under the crest.
    const carrier = luma(200, 120), floorAtCarrierHeight = luma(60, 120), sky = rgb(200, 195);
    // Above the front row's noise at x=300 only the older rows' carrier can
    // paint: history is visible in depth.
    const depth = luma(300, 100), depthAside = luma(60, 100);
    renderer.updateOptions({ centerHz: 14_060_000 });
    renderer.draw(5_000);
    const movedTo = luma(160, 120), movedFrom = luma(200, 120);
    renderer.updateOptions({ centerHz: 14_050_000 });
    return { empty, animating, rested, carrier, floorAtCarrierHeight, sky, depth, depthAside,
      movedTo, movedFrom, rows: renderer.historyRows, error: gl.getError() };
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
  await page.locator('#stacked-witness').screenshot({ path: info.outputPath('stacked.png') });
});

// A one-bin carrier on a dense row must be found wherever it sits inside a
// screen column, at any zoom: sweep it across two adjacent column windows
// and scan the whole pixel row each time. `limit` forces a small device
// texture width, so the same sweep also runs through the pre-reduced path.
for (const limit of [0, 4096]) {
  test(`real GL: no bin is skipped — carrier sweep at 1x and 2x${limit ? `, texture width forced to ${limit}` : ''}`, async ({ page }) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto('/fixtures/spectrum-witness.html');
    const result = await page.evaluate(async (limit) => {
      const [{ StackedTraceRenderer }, { defaultWaterfallOptions }, { defaultStackedTraceSettings }] = await Promise.all([
        import('/src/lib/renderers/stacked-trace-renderer.ts' as string),
        import('/src/lib/renderers/waterfall-renderer.ts' as string),
        import('/src/lib/renderers/stacked-trace-geometry.ts' as string),
      ]);
      const WIDTH = 400, HEIGHT = 200, BINS = 8192, ROW_Y = 100;
      const canvas = document.createElement('canvas');
      canvas.width = WIDTH; canvas.height = HEIGHT; canvas.style.cssText = 'display:block;width:400px;height:200px';
      document.body.replaceChildren(canvas);
      if (limit) {
        const getContext = canvas.getContext.bind(canvas);
        canvas.getContext = ((kind: string, attributes?: unknown) => {
          const gl = getContext(kind as 'webgl2', attributes as WebGLContextAttributes) as WebGL2RenderingContext;
          const getParameter = gl.getParameter.bind(gl);
          gl.getParameter = (name: number) => name === gl.MAX_TEXTURE_SIZE ? limit : getParameter(name);
          return gl;
        }) as typeof canvas.getContext;
      }
      const options = { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 14_050_000, spanHz: 100_000 };
      const renderer = new StackedTraceRenderer(canvas, options, { ...defaultStackedTraceSettings, rows: 8, rowPeriodMs: 100 });
      const gl = canvas.getContext('webgl2')!;
      const pixels = new Uint8Array(WIDTH * 4);
      /** Runs of pixels brighter than 100 in the pixel row ROW_Y: [first, last] inclusive. */
      const runs = (): [number, number][] => {
        gl.readPixels(0, ROW_Y, WIDTH, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        const found: [number, number][] = [];
        for (let x = 0; x < WIDTH; x++) {
          if ((pixels[x * 4] + pixels[x * 4 + 1] + pixels[x * 4 + 2]) / 3 <= 100) continue;
          if (found.length && found[found.length - 1][1] === x - 1) found[found.length - 1][1] = x;
          else found.push([x, x]);
        }
        return found;
      };
      // Frames two seconds apart: each row has aged out of the eight-row
      // stack before the next is read, so only the live front trace paints.
      let now = 0;
      const data = new Uint8Array(BINS).fill(10);
      renderer.pushRow(data, options, now);
      const failures: string[] = [];
      let checked = 0;
      for (const zoom of [1, 2]) {
        renderer.updateOptions({ spanHz: 100_000 * zoom });
        // One screen column spans BINS * zoom / 199 bins: sweep a little over two of them.
        const sweep = Math.ceil(BINS * zoom / 199 * 2.2);
        for (let bin = 2040; bin < 2040 + sweep; bin++) {
          data.fill(10); data[bin] = 80;
          now += 2_000;
          renderer.pushRow(data, options, now);
          renderer.draw(now + 10_000);
          const found = runs();
          // The row fills 1/zoom of the view, centred.
          const expected = WIDTH * (0.5 - 0.5 / zoom) + (bin + 0.5) / BINS * WIDTH / zoom;
          const column = WIDTH / 199;
          checked++;
          if (found.length !== 1) { failures.push(`zoom ${zoom} bin ${bin}: ${found.length} runs ${JSON.stringify(found)}`); continue; }
          const centre = (found[0][0] + found[0][1] + 1) / 2, width = found[0][1] - found[0][0] + 1;
          if (Math.abs(centre - expected) > column) failures.push(`zoom ${zoom} bin ${bin}: centre ${centre}, expected ${expected.toFixed(2)}`);
          if (width > 4) failures.push(`zoom ${zoom} bin ${bin}: ${width} px wide`);
        }
      }
      return { failures, checked, error: gl.getError() };
    }, limit);
    expect(errors).toEqual([]);
    expect(result.error).toBe(0);
    expect(result.checked).toBeGreaterThan(250);
    expect(result.failures).toEqual([]);
  });
}
