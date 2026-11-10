import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

// MOR-4383 before/after witness on the real bench recording. "Before" is the
// pre-fix resting behaviour (the rc3 2D reference: nearest-bin screen profile,
// one hard row per frame — main's WebGL renderer is pixel-equivalent to it,
// proven by the MOR-4348 parity suite). "After" is the production WebGL
// renderer: bin-centre interpolation, temporal IIR, [1 2 1]/4 and 3-row
// blended groups. The test fails while the production renderer still draws
// the unsmoothed image: identical images give equal row/column difference
// energy, failing the strict reduction factors.
const RECORDING = 'ic705_bench_antenna_20s';
const capture = readFileSync(new URL('../../../../tests/fixtures/scope-wire/' + RECORDING + '.frames', import.meta.url));

const wires: number[][] = [];
for (let offset = 0; offset < capture.length;) {
  const length = capture.readUInt32LE(offset); offset += 4;
  if (length === 0 || offset + length > capture.length) throw new Error('Truncated scope recording');
  wires.push(Array.from(capture.subarray(offset, offset + length))); offset += length;
}

for (const dpr of [1, 2]) {
  test.describe(`bench antenna smoothing metrics DPR ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr, viewport: { width: 1920, height: 900 } });
    test('smoothed renderer reduces speckle and preserves peaks', async ({ page }, info) => {
      await page.goto('/fixtures/spectrum-witness.html');
      await page.evaluate(async ({ wires, dpr }) => {
        const currentUrl = '/src/lib/renderers/waterfall-renderer.ts';
        const beforeUrl = '/tests/e2e/renderers/reference/waterfall-2d-rc3.ts';
        const frameUrl = '/src/lib/runtime/scope-frame.ts';
        const { WaterfallRenderer, defaultWaterfallOptions } = await import(currentUrl) as typeof import('../../../src/lib/renderers/waterfall-renderer');
        const { WaterfallRenderer: Before } = await import(beforeUrl) as typeof import('./reference/waterfall-2d-rc3');
        const { parseCurrentScopeFrame } = await import(frameUrl) as typeof import('../../../src/lib/runtime/scope-frame');
        const frames = wires.map(wire => {
          const parsed = parseCurrentScopeFrame(new Uint8Array(wire).buffer, { source: 'radio', sourceId: 'recording', epoch: 1 });
          if (!parsed.ok || !parsed.frame.pixels) throw new Error('Invalid recording');
          return parsed.frame;
        });
        const width = 1900 * dpr, height = 120 * dpr;
        const canvases = ['before', 'after'].map(id => {
          const c = document.createElement('canvas'); c.id = id; c.width = width; c.height = height;
          c.style.cssText = 'display:block;width:1900px;height:120px'; return c;
        });
        document.body.replaceChildren(...canvases);
        const frame = frames[0], span = frame.viewEndHz - frame.viewStartHz;
        const options = { ...defaultWaterfallOptions, centerHz: (frame.viewStartHz + frame.viewEndHz) / 2, spanHz: span };
        const before = new Before(canvases[0], options), after = new WaterfallRenderer(canvases[1], options);
        for (let i = 0; i < Math.max(height, frames.length); i++) {
          const pixels = frames[i % frames.length].pixels!;
          before.pushRow(pixels); after.pushRow(pixels);
        }
        return { canvases };
      }, { wires, dpr });
      const shots = await Promise.all(['before', 'after'].map(async id => Array.from(
        await page.locator(`#${id}`).screenshot({ path: info.outputPath(`${id}.png`) }))));
      const result = await page.evaluate(async ({ shots }) => {        const lum = (data: Uint8ClampedArray, i: number) => (data[i] + data[i + 1] + data[i + 2]) / 3;
        const images = await Promise.all(shots.map(async bytes => {
          const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
          const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
          const ctx = canvas.getContext('2d')!; ctx.drawImage(bitmap, 0, 0); bitmap.close();
          return ctx.getImageData(0, 0, canvas.width, canvas.height);
        }));
        const energy = ({ width, height, data }: ImageData) => {
          const background = (i: number) => data[i] === 0 && data[i + 1] === 16 && data[i + 2] === 32;
          let rowSum = 0, rowCount = 0, columnSum = 0, columnCount = 0, signalSum = 0, signalCount = 0;
          const luminance: number[] = [];
          for (let y = 0; y < height; y++) for (let x = 2; x < width - 2; x++) {
            const i = (y * width + x) * 4;
            if (!background(i)) { signalSum += lum(data, i); signalCount++; luminance.push(lum(data, i)); }
            if (y + 1 < height && (!background(i) || !background(i + width * 4))) {
              rowSum += Math.abs(lum(data, i) - lum(data, i + width * 4)); rowCount++;
            }
            if (!background(i) || !background(i + 4)) {
              columnSum += Math.abs(lum(data, i) - lum(data, i + 4)); columnCount++;
            }
          }
          luminance.sort((a, b) => a - b);
          return { rowDiff: rowSum / rowCount, columnDiff: columnSum / columnCount,
            meanSignal: signalSum / signalCount, maxLum: luminance.at(-1) ?? 0,
            p999Lum: luminance[Math.floor(luminance.length * 0.999)] ?? 0 };
        };
        const [before, after] = images.map(energy);
        return { before, after, width: images[0].width, height: images[0].height };
      }, { shots });
      console.log(JSON.stringify({ dpr, ...result }));
      await info.attach('metrics', { body: JSON.stringify(result), contentType: 'application/json' });
      expect(result.width).toBe(1900 * dpr);
      // Speckle energy per direction drops: blended groups correlate rows,
      // bin interpolation and [1 2 1] soften independent per-bin noise.
      expect(result.after.rowDiff).toBeLessThan(0.75 * result.before.rowDiff);
      expect(result.after.columnDiff).toBeLessThan(0.9 * result.before.columnDiff);
      // Signal peaks survive the smoothing.
      expect(result.after.maxLum).toBeGreaterThanOrEqual(0.7 * result.before.maxLum);
      expect(result.after.meanSignal).toBeGreaterThanOrEqual(0.8 * result.before.meanSignal);
    });
  });
}
