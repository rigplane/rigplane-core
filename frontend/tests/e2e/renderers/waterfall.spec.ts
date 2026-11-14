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
    // MOR-4383: one row group (3 rows) per frame; 25 frames upload 75 rows.
    expect(result.error).toBe(0); expect(result.uploads).toBe(75);
    // The peak bin filters to (40 + 2*80 + 40)/4 = 60 -> t = sqrt(60/80).
    expect(result.bright).toEqual(scheme === 'classic' ? [255, 175, 0, 255]
      : scheme === 'thermal' ? [255, 197, 0, 255] : [220, 220, 220, 255]);
    expect(result.shifted).toEqual(result.bright);
    // MOR-4383 B1: no hatch — both uncovered pixels read as background.
    expect(result.gap).toEqual(result.stripe);
    // MOR-4383 B1 RED: uncovered columns must read as noise-floor
    // background, never a hatch mark (x=2 sits outside the granted
    // coverage; the hatch paints it grey (36,48,60) instead).
    expect(result.gap).toEqual([0, 16, 32, 255]);
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
  await expect(page.locator('[role="alert"][data-error="webgl-required"]')).toHaveText('WebGL required for the waterfall');
  expect(errors).toEqual([]);
});

test('retains isolated peaks after a large band change and the last valid View during silence', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
    const canvas = document.createElement('canvas'); canvas.width = 475; canvas.height = 8;
    const r = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 144_000_000, spanHz: 5000 });
    const gl = canvas.getContext('webgl2')!;
    const pixels = new Uint8Array(475); for (let i = 0; i < pixels.length; i++) pixels[i] = (i * 37) % 81;
    pixels[137] = 80; pixels[315] = 80;
    const read = () => { const row = new Uint8Array(475 * 4); gl.readPixels(0, 7, 475, 1, gl.RGBA, gl.UNSIGNED_BYTE, row); return Array.from(row); };
    r.pushRow(pixels); const before = read();
    r.updateOptions({ centerHz: 432_000_000 }); r.pushRow(pixels); const after = read();
    r.updateOptions({ centerHz: 0, spanHz: 0 }); r.draw(); const silent = read();
    // MOR-4383: the ring stores the [1 2 1]-filtered row (width equals the
    // bin count, so every column fetches its own filtered bin exactly).
    const filtered = Array.from(pixels, (_, i) =>
      Math.round((pixels[i > 0 ? i - 1 : 0] + 2 * pixels[i] + pixels[i + 1 < 475 ? i + 1 : 474]) / 4));
    return { before, after, silent, expected: filtered.map(value => Math.floor(Math.sqrt(value / 80) * 255)), error: gl.getError() };
  });
  expect(result.error).toBe(0);
  expect(result.before.every((value, index) => index % 4 === 3 || Math.abs(value - result.expected[Math.floor(index / 4)]) <= 1)).toBe(true);
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
    r.resize(48, 1298); const tall = read(6);
    r.pushRow(new Uint8Array(96).fill(80)); const newRaster = read(9);
    r.resize(48, 4); const short = read(4);
    r.resize(48, 1298);
    const steady = new Uint8Array(96).fill(80);
    for (let i = 0; i < 520; i++) r.pushRow(steady);
    const acrossBlocks = read(524);
    return { before, wide, tall, newRaster, short, retainedAcrossBlocks: acrossBlocks.slice(520), newest: acrossBlocks[0], error: gl.getError() };
  });
    expect(result.error).toBe(0);
    expect(new Set(result.before.map(row => row.join(','))).size).toBe(6);
    expect(result.wide).toEqual(result.before); expect(result.tall).toEqual(result.before);
    // The 96-bin push resets the geometry: its first group is three copies,
    // so the six retained rows sit at ages 3..8.
    expect(result.newRaster.slice(3, 9)).toEqual(result.before);
    expect(result.short).toEqual(result.newRaster.slice(0, 4));
    // 520 steady frames scroll 1560 rows through the 1298-row ring, crossing
    // every uniform-block boundary repeatedly; all surviving rows are the
    // steady filtered 80 (white).
    expect(result.retainedAcrossBlocks.every(row => row.join(',') === '255,255,255,255')).toBe(true);
    expect(result.newest).toEqual([255, 255, 255, 255]);
  });

test('recovers the surface and its cached frame after context restoration', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  await page.waitForSelector('body[data-harness-ready="true"]');
  const canvas = page.locator('.waterfall-history canvas');
  await canvas.evaluate(node => {
    const canvas = node as HTMLCanvasElement, gl = canvas.getContext('webgl2')!;
    const upload = gl.texSubImage2D.bind(gl);
    gl.texSubImage2D = ((...args: unknown[]) => {
      canvas.dataset.restoredUploads = String(Number(canvas.dataset.restoredUploads ?? 0) + 1);
      Reflect.apply(upload, gl, args);
    }) as typeof gl.texSubImage2D;
    node.dispatchEvent(new Event('webglcontextlost', { cancelable: true }));
  });
  await expect(page.locator('[role="alert"][data-error="webgl-required"]')).toBeVisible();
  await canvas.evaluate(node => node.dispatchEvent(new Event('webglcontextrestored')));
  await expect(page.locator('[role="alert"][data-error="webgl-required"]')).toHaveCount(0);
  await expect(canvas).not.toHaveAttribute('data-error', 'webgl-required');
  await expect(canvas).toHaveAttribute('data-restored-uploads', /^[1-9]/);
});


test('divider resize preserves the real component history', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  await page.waitForSelector('body[data-harness-ready="true"]');
  await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const module = await import(url);
    const original = module.WaterfallRenderer.prototype.draw;
    module.WaterfallRenderer.prototype.draw = function (this: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer) {
      (window as typeof window & { __retainedWaterfall?: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer }).__retainedWaterfall = this;
      original.call(this);
    };
    document.getElementById('app')!.style.width = '961px';
  });
  await expect.poll(() => page.evaluate(() => Boolean((window as typeof window & { __retainedWaterfall?: unknown }).__retainedWaterfall))).toBe(true);
  const read = () => page.evaluate(() => {
    const r = (window as typeof window & { __retainedWaterfall: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer }).__retainedWaterfall;
    const canvas = document.querySelector<HTMLCanvasElement>('.waterfall-history canvas')!;
    const gl = canvas.getContext('webgl2')!;
    r.draw();
    const colors = [];
    for (let age = 0; age < 20; age++) { const p = new Uint8Array(4); gl.readPixels(Math.floor(canvas.width / 2), canvas.height - 1 - age, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); colors.push(Array.from(p)); }
    return { colors, height: canvas.height, error: gl.getError() };
  });
  await page.evaluate(() => {
    const r = (window as typeof window & { __retainedWaterfall: import('../../../src/lib/renderers/waterfall-renderer').WaterfallRenderer }).__retainedWaterfall;
    for (let i = 1; i <= 20; i++) r.pushRow(new Uint8Array(256).fill(i * 4));
  });
  const before = await read();
  expect(new Set(before.colors.map(row => row.join(','))).size).toBeGreaterThan(10);
  const divider = page.locator('[role="separator"]'), box = await divider.boundingBox();
  await page.mouse.move(box!.x + 20, box!.y + box!.height / 2); await page.mouse.down();
  await page.mouse.move(box!.x + 20, box!.y + box!.height / 2 + 70, { steps: 4 }); await page.mouse.up();
  await expect.poll(async () => (await read()).height).not.toBe(before.height);
  const after = await read();
  expect(after.error).toBe(0); expect(after.colors).toEqual(before.colors);
});

test('frame silence preserves the painted RF View', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
    const canvas = document.createElement('canvas'); canvas.width = 100; canvas.height = 8;
    const r = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 14_050_000, spanHz: 100_000 });
    const gl = canvas.getContext('webgl2')!, color = new Uint8Array(4);
    r.pushRow(new Uint8Array(100).fill(80)); gl.readPixels(50, 7, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, color);
    const before = Array.from(color);
    r.updateOptions({ centerHz: 0, spanHz: 0 }); r.draw();
    gl.readPixels(50, 7, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, color);
    return { before, silent: Array.from(color), error: gl.getError() };
  });
  expect(result.error).toBe(0); expect(result.before).toEqual([255, 255, 255, 255]);
  expect(result.silent).toEqual(result.before);
});

test('retains each row calibration and the reversed AF sample window', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async () => {
    const url = '/src/lib/renderers/waterfall-renderer.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(url);
    const canvas = document.createElement('canvas'); canvas.width = 100; canvas.height = 4;
    const r = new WaterfallRenderer(canvas, { ...defaultWaterfallOptions, colorScheme: 'grayscale', centerHz: 14_050_000,
      spanHz: 100_000, sampleOffset: 2, sampleAmplitudeMax: 160 });
    const gl = canvas.getContext('webgl2')!;
    const read = (age: number) => { const a = new Uint8Array(4), b = new Uint8Array(4);
      gl.readPixels(25, 3 - age, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, a); gl.readPixels(75, 3 - age, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, b);
      return [Array.from(a), Array.from(b)]; };
    r.pushRow(new Uint8Array([255, 255, 40, 160])); const before = read(0);
    r.updateOptions({ sampleOffset: 0, sampleAmplitudeMax: 80 }); r.pushRow(new Uint8Array([20, 80])); const old = read(1), converted = read(0);
    r.updateOptions({ sampleWindow: { start: 1, end: 0 } }); r.pushRow(new Uint8Array([80, 20]));
    return { before, old, converted, reversed: read(0), error: gl.getError() };
  });
  expect(result.error).toBe(0);
  // Push 1 stores [(40+40+160)/4, (40+160+160)/4] = [70,130] at calibration
  // 160. Push 2 keeps the geometry: the IIR runs on RAW bins,
  // [(40+20)/2, (160+80)/2] = [30,120], filtering to [53,98], blended 1/3
  // and 2/3 towards it from [70,130] at calibration 80. Push 3 reverses the
  // sample window (geometry reset, raw + [1 2 1] of [80,20] = [65,35]).
  // Column centres x=25/75 sample bin positions 0.01 and 1.0 (0.245 ->
  // clamped 0 through the reversed window).
  const near = (channels: number[], expected: number[]) =>
    channels.every((value, i) => Math.abs(value - expected[i]) <= 1);
  expect(near(result.before[0], [169, 169, 169, 255])).toBe(true);
  expect(near(result.before[1], [229, 229, 229, 255])).toBe(true);
  expect(near(result.old[0], [219, 219, 219, 255])).toBe(true);
  expect(near(result.old[1], [255, 255, 255, 255])).toBe(true);
  expect(near(result.converted[0], [208, 208, 208, 255])).toBe(true);
  expect(result.converted[1]).toEqual([255, 255, 255, 255]);
  expect(near(result.reversed[0], [169, 169, 169, 255])).toBe(true);
  expect(near(result.reversed[1], [229, 229, 229, 255])).toBe(true);
});
