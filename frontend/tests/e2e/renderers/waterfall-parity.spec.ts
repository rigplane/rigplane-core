import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import recordedWires from './reference/recorded-scope-v2.json' with { type: 'json' };

// ScopeAssembler → ScopeWireFrame → encode_scope_frame_v2, from simulator recordings
// tests/fixtures/{ic705,ic7610}/civ_pairs.jsonl. No live radio is used.
const recordings: Record<string, number[][]> = recordedWires;

// Only the recording's u32-LE envelope is decoded here; the browser uses the production v2 parser.
const capture = readFileSync(new URL('../../../../tests/fixtures/scope-wire/ic705_dummy_load_100k.frames', import.meta.url));
const sharedWires: number[][] = [];
for (let offset = 0; offset < capture.length;) {
  const length = capture.readUInt32LE(offset); offset += 4;
  if (length === 0 || offset + length > capture.length) throw new Error('Truncated scope recording');
  sharedWires.push(Array.from(capture.subarray(offset, offset + length))); offset += length;
}
recordings.ic705_shared = sharedWires;

for (const profile of ['ic705', 'ic7610', 'ic705_shared']) for (const dpr of [1, 2]) {
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
        for (let i = 0; i < height; i++) {
          const pixels = frames[i % frames.length].pixels!;
          reference.pushRow(pixels); current.pushRow(pixels);
        }
        const pixels = frames.flatMap(f => Array.from(f.pixels!)).sort((a, b) => a - b);
        return { bins: [...new Set(frames.map(f => f.width))], frames: frames.length, width, height, dpr,
          pixelCount: pixels.length, zeroCount: pixels.filter(p => p === 0).length,
          p90: pixels[Math.floor(pixels.length * 0.9)], p99Amplitude: pixels[Math.floor(pixels.length * 0.99)],
          maxAmplitude: pixels.at(-1),
          css: canvases.map(c => [c.getBoundingClientRect().width, c.getBoundingClientRect().height]),
          glError: canvases[1].getContext('webgl2')!.getError() };
      }, { wires: recordings[profile], shift, dpr });
      const screenshots = await Promise.all(['rc3', 'webgl'].map(async id => Array.from(
        await page.locator(`#${id}`).screenshot({ path: info.outputPath(`${id}.png`) }))));
      const metric = await page.evaluate(async (screenshots) => {
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
        return { mean: sum / differences.length, p99: differences[Math.floor(differences.length * 0.99)],
          changed, active, tolerantMean: active ? tolerant / active : 0, signalMean: active ? sum / active : 0,
          screenshotWidth: width, screenshotHeight: height };
      }, screenshots);
      const result = { ...geometry, ...metric };
      console.log(JSON.stringify({ profile, shift, ...result }));
      await info.attach('metrics', { body: JSON.stringify(result), contentType: 'application/json' });
      expect(result.screenshotWidth).toBe(result.width); expect(result.screenshotHeight).toBe(result.height);
      expect(result.glError).toBe(0);
      // RGB with a one-backing-pixel horizontal tolerance for nearest-bin boundary rounding.
      if (profile.startsWith('ic705')) expect(result.active).toBeGreaterThan(0);
      if (profile === 'ic705_shared') {
        expect(capture.length).toBe(45_360); expect(result.frames).toBe(84);
        expect(result.bins).toEqual([475]); expect(result.pixelCount).toBe(39_900);
        expect(result.p90).toBe(2); expect(result.p99Amplitude).toBe(34); expect(result.maxAmplitude).toBe(51);
      }
      expect(result.tolerantMean).toBeLessThan(0.25);
      expect(result.mean).toBeLessThan(0.25);
      expect(result.p99).toBeLessThanOrEqual(1);
    });
  });
}
