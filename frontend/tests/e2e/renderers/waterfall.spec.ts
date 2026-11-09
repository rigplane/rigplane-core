import { test, expect } from '@playwright/test';

for (const scheme of ['classic', 'thermal', 'grayscale'] as const) {
  test(`${scheme}: real GL ring, View reprojection, coverage and history recolouring`, async ({ page }, info) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto('/fixtures/spectrum-witness.html');
    const result = await page.evaluate(async (scheme) => {
      const url = '/src/lib/renderers/waterfall-renderer.ts';
      const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
      const canvas = document.createElement('canvas'); canvas.id = 'ring-witness';
      canvas.width = 100; canvas.height = 20; canvas.style.cssText = 'width:1000px;height:200px;image-rendering:pixelated';
      document.body.replaceChildren(canvas);
      const renderer = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, colorScheme: scheme,
        centerHz: 14_050_000, spanHz: 100_000, coverage: { startHz: 14_025_000, endHz: 14_075_000 } });
      const gl = canvas.getContext('webgl2')!;
      const pixels = new Uint8Array(100).fill(40); pixels[50] = 80;
      let uploads = 0;
      const upload = gl.texSubImage2D.bind(gl);
      gl.texSubImage2D = ((...args: unknown[]) => { uploads++; Reflect.apply(upload, gl, args); }) as typeof gl.texSubImage2D;
      const pixel = (x: number, age = 0) => {
        const value = new Uint8Array(4); gl.readPixels(x, 19 - age, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, value);
        return Array.from(value);
      };
      for (let i = 0; i < 25; i++) renderer.pushRow(pixels);
      const bright = pixel(50, 10), gap = pixel(2, 10), stripe = pixel(6, 10);
      renderer.updateOptions({ centerHz: 14_051_000 }); renderer.draw();
      const shifted = pixel(49, 10);
      renderer.updateOptions({ colorScheme: scheme === 'grayscale' ? 'thermal' : 'grayscale' }); renderer.draw();
      const recoloured = pixel(49, 10);
      renderer.updateOptions({ refLevel: -30 }); renderer.draw();
      const dimmed = pixel(49, 10);
      renderer.updateOptions({ colorScheme: scheme, refLevel: 0, centerHz: 14_050_000 }); renderer.draw();
      return { bright, gap, stripe, shifted, recoloured, dimmed, uploads, error: gl.getError() };
    }, scheme);
    expect(result.error).toBe(0); expect(result.uploads).toBe(25);
    expect(result.bright).toEqual(scheme === 'classic' ? [255, 0, 0, 255]
      : scheme === 'thermal' ? [255, 255, 0, 255] : [255, 255, 255, 255]);
    expect(result.shifted).toEqual(result.bright);
    expect(result.gap).not.toEqual(result.stripe);
    expect(result.recoloured).not.toEqual(result.bright);
    expect(result.dimmed).not.toEqual(result.recoloured);
    expect(errors).toEqual([]);
    await page.locator('#ring-witness').screenshot({ path: info.outputPath(`${scheme}.png`) });
  });
}

test('WebGL unavailable renders an explicit surface error', async ({ page }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const get = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, kind: string, ...args: unknown[]) {
      return kind === 'webgl2' ? null : Reflect.apply(get, this, [kind, ...args]);
    } as typeof get;
  });
  await page.goto('/fixtures/spectrum-witness.html');
  await expect(page.locator('[data-error="webgl-required"] [role="alert"]')).toHaveText('WebGL required for the waterfall');
  expect(errors).toEqual([]);
});

test('retains isolated peaks after a large band change and the last valid View during silence', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
    const canvas = document.createElement('canvas'); canvas.width = 475; canvas.height = 8;
    const r = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, centerHz: 144_000_000, spanHz: 5000 });
    const gl = canvas.getContext('webgl2')!;
    const pixels = new Uint8Array(475); pixels[137] = 80; pixels[315] = 80;
    const read = () => { const row = new Uint8Array(475 * 4); gl.readPixels(0, 7, 475, 1, gl.RGBA, gl.UNSIGNED_BYTE, row); return Array.from(row); };
    r.pushRow(pixels); const before = read();
    r.updateOptions({ centerHz: 432_000_000 }); r.pushRow(pixels); const after = read();
    r.updateOptions({ centerHz: 0, spanHz: 0 }); r.draw(); const silent = read();
    return { before, after, silent, error: gl.getError() };
  });
  expect(result.error).toBe(0);
  expect(result.after).toEqual(result.before);
  expect(result.silent).toEqual(result.after);
});

test('preserves distinct historical rows across width, height and incoming raster changes', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
    const canvas = document.createElement('canvas'); canvas.width = 24; canvas.height = 6;
    const r = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 14_050_000, spanHz: 100_000 });
    const gl = canvas.getContext('webgl2')!;
    const read = (ages: number) => { const colors: number[][] = []; r.draw(); for (let age = 0; age < ages; age++) { const p = new Uint8Array(4); gl.readPixels(Math.floor(canvas.width / 2), canvas.height - 1 - age, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); colors.push(Array.from(p)); } return colors; };
    for (let i = 1; i <= 6; i++) r.pushRow(new Uint8Array(24).fill(i * 10));
    const before = read(6);
    r.resize(48, 6); const wide = read(6);
    r.resize(48, 10); const tall = read(6);
    r.pushRow(new Uint8Array(96).fill(80)); const newRaster = read(7);
    r.resize(48, 4); const short = read(4);
    return { before, wide, tall, newRaster, short, error: gl.getError() };
  });
  expect(result.error).toBe(0);
  expect(new Set(result.before.map(row => row.join(','))).size).toBe(6);
  expect(result.wide).toEqual(result.before); expect(result.tall).toEqual(result.before);
  expect(result.newRaster.slice(1)).toEqual(result.before);
  expect(result.short).toEqual(result.newRaster.slice(0, 4));
});

test('recovers the surface and its cached frame after context restoration', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const canvas = page.locator('.waterfall-history canvas');
  await canvas.evaluate(node => node.dispatchEvent(new Event('webglcontextlost', { cancelable: true })));
  await expect(page.locator('[role="alert"][data-error="webgl-required"]')).toBeVisible();
  await canvas.evaluate(node => node.dispatchEvent(new Event('webglcontextrestored')));
  await expect(page.locator('[role="alert"][data-error="webgl-required"]')).toHaveCount(0);
  await expect(canvas).not.toHaveAttribute('data-error', 'webgl-required');
});
