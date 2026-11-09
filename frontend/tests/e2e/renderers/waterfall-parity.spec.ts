import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import recordedWires from './reference/recorded-scope-v2.json' with { type: 'json' };

// ScopeAssembler → ScopeWireFrame → encode_scope_frame_v2, from simulator recordings
// tests/fixtures/{ic705,ic7610}/civ_pairs.jsonl. No live radio is used.
const recordings: Record<string, number[][]> = recordedWires;

test('interpolates raw amplitudes before clipping at calibration 80', async ({ page }) => {
  await page.goto('/fixtures/spectrum-witness.html');
  const results = await page.evaluate(async () => {
    const currentUrl = '/src/lib/renderers/waterfall-renderer.ts';
    const referenceUrl = '/tests/e2e/renderers/reference/waterfall-2d-rc3.ts';
    const { WaterfallRenderer, defaultWaterfallOptions } = await import(currentUrl) as typeof import('../../../src/lib/renderers/waterfall-renderer');
    const { WaterfallRenderer: Reference } = await import(referenceUrl) as typeof import('./reference/waterfall-2d-rc3');
    return [80, 160].flatMap(sampleAmplitudeMax => [0, -30].map(refLevel => {
      const canvases = [document.createElement('canvas'), document.createElement('canvas')];
      for (const c of canvases) { c.width = 4; c.height = 2; }
      const options = { ...defaultWaterfallOptions, colorScheme: 'grayscale' as const, sampleAmplitudeMax,
        refLevel, centerHz: 0, spanHz: 4, panoramaShiftHz: 0.5 };
      const reference = new Reference(canvases[0], options), current = new WaterfallRenderer(canvases[1], options);
      const pixels = new Uint8Array([0, 96, 0, 0]); reference.pushRow(pixels); current.pushRow(pixels);
      const expected = Array.from(canvases[0].getContext('2d')!.getImageData(1, 0, 2, 1).data);
      const gl = canvases[1].getContext('webgl2')!, actual = new Uint8Array(8);
      gl.readPixels(1, 1, 2, 1, gl.RGBA, gl.UNSIGNED_BYTE, actual);
      const error = gl.getError(); reference.destroy(); current.destroy();
      return { sampleAmplitudeMax, refLevel, expected, actual: Array.from(actual), error };
    }));
  });
  expect(results[0].expected.slice(0, 3)).toEqual([197, 197, 197]);
  for (const result of results) {
    expect(result.error).toBe(0);
    expect(result.actual, JSON.stringify(result)).toEqual(result.expected);
  }
});

// Only the recording's u32-LE envelope is decoded here; the browser uses the production v2 parser.
const liveCaptures = [
  { name: 'ic705_air_20m_ref-10', frames: 125, bytes: 67_500, p99: 40, max: 53 },
  { name: 'ic705_air_20m_ref+10', frames: 83, bytes: 44_820, p99: 85, max: 96 },
];
for (const recording of liveCaptures) {
  const capture = readFileSync(new URL(`../../../../tests/fixtures/scope-wire/${recording.name}.frames`, import.meta.url));
  expect(capture.length).toBe(recording.bytes);
  const wires: number[][] = [];
  for (let offset = 0; offset < capture.length;) {
    const length = capture.readUInt32LE(offset); offset += 4;
    if (length === 0 || offset + length > capture.length) throw new Error('Truncated scope recording');
    wires.push(Array.from(capture.subarray(offset, offset + length))); offset += length;
  }
  recordings[recording.name] = wires;
}

for (const profile of Object.keys(recordings)) for (const dpr of [1, 2]) {
  test.describe(`${profile} DPR ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr, viewport: { width: 1920, height: 900 } });
    for (const shift of [0, 0.5]) test(`rc3 image parity, ${shift} backing pixel offset`, async ({ page }, info) => {
      await page.goto('/fixtures/spectrum-witness.html');
      const geometry = await page.evaluate(async ({ wires, shift, dpr }) => {
        const currentUrl = '/src/lib/renderers/waterfall-renderer.ts';
        const referenceUrl = '/tests/e2e/renderers/reference/waterfall-2d-rc3.ts';
        const frameUrl = '/src/lib/runtime/scope-frame.ts';
        const { WaterfallRenderer, defaultWaterfallOptions } = await import(currentUrl) as typeof import('../../../src/lib/renderers/waterfall-renderer');
        const { WaterfallRenderer: Reference } = await import(referenceUrl) as typeof import('./reference/waterfall-2d-rc3');
        const { parseCurrentScopeFrame } = await import(frameUrl) as typeof import('../../../src/lib/runtime/scope-frame');
        const frames = wires.map(wire => {
          const parsed = parseCurrentScopeFrame(new Uint8Array(wire).buffer, { source: 'radio', sourceId: 'recording', epoch: 1 });
          if (!parsed.ok || !parsed.frame.pixels) throw new Error('Invalid recording');
          return parsed.frame;
        });
        const width = 1900 * dpr, height = 120 * dpr;
        const canvases = ['rc3', 'webgl'].map(id => {
          const c = document.createElement('canvas'); c.id = id; c.width = width; c.height = height;
          c.style.cssText = 'display:block;width:1900px;height:120px'; return c;
        });
        document.body.replaceChildren(...canvases);
        const frame = frames[0], span = frame.viewEndHz - frame.viewStartHz;
        const options = { ...defaultWaterfallOptions, centerHz: (frame.viewStartHz + frame.viewEndHz) / 2,
          spanHz: span, panoramaShiftHz: shift * span / width };
        const reference = new Reference(canvases[0], options), current = new WaterfallRenderer(canvases[1], options);
        for (let i = 0; i < Math.max(height, frames.length); i++) {
          const pixels = frames[i % frames.length].pixels!;
          reference.pushRow(pixels); current.pushRow(pixels);
        }
        const interiorPixels: { x: number; y: number; left: number; right: number }[] = [];
        for (let y = 0; y < height; y++) {
          const pixels = frames[(Math.max(height, frames.length) - 1 - y) % frames.length].pixels!;
          for (let x = 2; x < width - 2; x++) {
            const leftColumn = Math.floor(x + shift), rightColumn = leftColumn + 1;
            if (leftColumn < 0 || rightColumn >= width) continue;
            const left = pixels[Math.floor(leftColumn / width * pixels.length)];
            const right = pixels[Math.floor(rightColumn / width * pixels.length)];
            if ((left > 80 && right < 80) || (left < 80 && right > 80)) interiorPixels.push({ x, y, left, right });
          }
        }
        const pixels = frames.flatMap(f => Array.from(f.pixels!)).sort((a, b) => a - b);
        return { bins: [...new Set(frames.map(f => f.width))], frames: frames.length, width, height, dpr,
          pixelCount: pixels.length, zeroCount: pixels.filter(p => p === 0).length,
          p10: pixels[Math.floor(pixels.length * 0.1)], median: pixels[Math.floor(pixels.length * 0.5)],
          p90: pixels[Math.floor(pixels.length * 0.9)], p99Amplitude: pixels[Math.floor(pixels.length * 0.99)],
          maxAmplitude: pixels.at(-1), interiorPixels,
          css: canvases.map(c => [c.getBoundingClientRect().width, c.getBoundingClientRect().height]),
          glError: canvases[1].getContext('webgl2')!.getError() };
      }, { wires: recordings[profile], shift, dpr });
      const screenshots = await Promise.all(['rc3', 'webgl'].map(async id => Array.from(
        await page.locator(`#${id}`).screenshot({ path: info.outputPath(`${id}.png`) }))));
      const metric = await page.evaluate(async ({ screenshots, interiorPixels }) => {
        const images = await Promise.all(screenshots.map(async bytes => {
          const bitmap = await createImageBitmap(new Blob([new Uint8Array(bytes)], { type: 'image/png' }));
          const canvas = document.createElement('canvas'); canvas.width = bitmap.width; canvas.height = bitmap.height;
          const ctx = canvas.getContext('2d')!; ctx.drawImage(bitmap, 0, 0); bitmap.close();
          return ctx.getImageData(0, 0, canvas.width, canvas.height);
        }));
        const [{ width, height, data: a }, { width: otherWidth, height: otherHeight, data: b }] = images;
        if (width !== otherWidth || height !== otherHeight) throw new Error('Screenshot geometry differs');
        let sum = 0, changed = 0, active = 0, tolerant = 0; const differences = [];
        for (let y = 0; y < height; y++) for (let x = 2; x < width - 2; x++) {
          let error = 0;
          for (let channel = 0; channel < 3; channel++) {
            const i = (y * width + x) * 4 + channel, j = (y * width + x) * 4 + channel;
            error += Math.abs(a[i] - b[j]);
          }
          let nearest = 255, reverse = 255;
          for (let dx = -1; dx <= 1; dx++) {
            let difference = 0, reverseDifference = 0;
            for (let channel = 0; channel < 3; channel++) {
              difference += Math.abs(a[(y * width + x) * 4 + channel] - b[(y * width + x + dx) * 4 + channel]);
              reverseDifference += Math.abs(b[(y * width + x) * 4 + channel] - a[(y * width + x + dx) * 4 + channel]);
            }
            nearest = Math.min(nearest, difference / 3); reverse = Math.min(reverse, reverseDifference / 3);
          }
          tolerant += (nearest + reverse) / 2;
          error /= 3; if (a[(y * width + x) * 4] !== 0 || a[(y * width + x) * 4 + 1] !== 16 || a[(y * width + x) * 4 + 2] !== 32 || error > 0) active++; sum += error; differences.push(error); if (error > 1) changed++;
        }
        differences.sort((a, b) => a - b);
        let interiorMaxError = 0;
        const interiorMismatches = [];
        for (const pixel of interiorPixels) {
          const i = (pixel.y * width + pixel.x) * 4;
          const expected = Array.from(a.slice(i, i + 3)), actual = Array.from(b.slice(i, i + 3));
          const error = Math.max(...expected.map((value, channel) => Math.abs(value - actual[channel])));
          interiorMaxError = Math.max(interiorMaxError, error);
          if (error && interiorMismatches.length < 8) interiorMismatches.push({ ...pixel, expected, actual });
        }
        return { mean: sum / differences.length, p99: differences[Math.floor(differences.length * 0.99)],
          changed, active, tolerantMean: active ? tolerant / active : 0, signalMean: active ? sum / active : 0,
          screenshotWidth: width, screenshotHeight: height, interiorCount: interiorPixels.length, interiorMaxError, interiorMismatches };
      }, { screenshots, interiorPixels: geometry.interiorPixels });
      const { interiorPixels: _interiorPixels, ...dimensions } = geometry;
      const result = { ...dimensions, ...metric };
      console.log(JSON.stringify({ profile, shift, ...result }));
      await info.attach('metrics', { body: JSON.stringify(result), contentType: 'application/json' });
      expect(result.screenshotWidth).toBe(result.width); expect(result.screenshotHeight).toBe(result.height);
      expect(result.glError).toBe(0);
      // RGB with a one-backing-pixel horizontal tolerance for nearest-bin boundary rounding.
      if (profile.startsWith('ic705')) expect(result.active).toBeGreaterThan(0);
      const live = liveCaptures.find(c => c.name === profile);
      if (live) {
        expect(result.frames).toBe(live.frames); expect(result.bins).toEqual([475]);
        expect(result.pixelCount).toBe(live.frames * 475); expect(result.maxAmplitude).toBe(live.max);
        expect(result.p99Amplitude).toBe(live.p99);
        if (live.name.endsWith('ref+10')) {
          expect([result.p10, result.median, result.p90]).toEqual([22, 34, 45]);
          expect(result.interiorCount).toBeGreaterThan(0);
          expect(result.interiorMaxError, JSON.stringify(result.interiorMismatches)).toBe(0);
        }
      }
      expect(result.tolerantMean).toBeLessThan(0.25);
      expect(result.mean).toBeLessThan(0.25);
      expect(result.p99).toBeLessThanOrEqual(1);
    });
  });
}
