import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

// ScopeAssembler → ScopeWireFrame → encode_scope_frame_v2, from simulator recordings
// tests/fixtures/{ic705,ic7610}/civ_pairs.jsonl. No live radio is used.
const recordings: Record<string, number[][]> = JSON.parse(readFileSync(
  new URL('./reference/recorded-scope-v2.json', import.meta.url), 'utf8'));

for (const profile of ['ic705', 'ic7610']) for (const dpr of [1, 2]) {
  test.describe(`${profile} DPR ${dpr}`, () => {
    test.use({ deviceScaleFactor: dpr, viewport: { width: 1920, height: 900 } });
    for (const shift of [0, 0.5]) test(`rc3 image parity, ${shift} backing pixel offset`, async ({ page }, info) => {
      await page.goto('/fixtures/spectrum-witness.html');
      const result = await page.evaluate(async ({ wires, shift, dpr }) => {
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
        const a = canvases[0].getContext('2d')!.getImageData(0, 0, width, height).data;
        const gl = canvases[1].getContext('webgl2')!, b = new Uint8Array(width * height * 4);
        gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, b);
        let sum = 0, changed = 0, active = 0, tolerant = 0; const differences = [];
        for (let y = 0; y < height; y++) for (let x = 2; x < width - 2; x++) {
          let error = 0;
          for (let channel = 0; channel < 3; channel++) {
            const i = (y * width + x) * 4 + channel, j = ((height - 1 - y) * width + x) * 4 + channel;
            error += Math.abs(a[i] - b[j]);
          }
          let nearest = 255;
          for (let dx = -1; dx <= 1; dx++) {
            let difference = 0;
            for (let channel = 0; channel < 3; channel++) difference += Math.abs(
              a[(y * width + x) * 4 + channel] - b[((height - 1 - y) * width + x + dx) * 4 + channel]);
            nearest = Math.min(nearest, difference / 3);
          }
          tolerant += nearest;
          error /= 3; if (a[(y * width + x) * 4] !== 0 || a[(y * width + x) * 4 + 1] !== 16 || a[(y * width + x) * 4 + 2] !== 32 || error > 0) active++; sum += error; differences.push(error); if (error > 1) changed++;
        }
        differences.sort((a, b) => a - b);
        return { mean: sum / differences.length, p99: differences[Math.floor(differences.length * 0.99)],
          changed, active, tolerantMean: active ? tolerant / active : 0, signalMean: active ? sum / active : 0, bins: frames.map(f => f.width), frames: frames.length, width, height, dpr,
          css: canvases.map(c => [c.getBoundingClientRect().width, c.getBoundingClientRect().height]), glError: gl.getError() };
      }, { wires: recordings[profile], shift, dpr });
      console.log(JSON.stringify({ profile, shift, ...result }));
      await info.attach('metrics', { body: JSON.stringify(result), contentType: 'application/json' });
      await page.locator('#rc3').screenshot({ path: info.outputPath('rc3.png') });
      await page.locator('#webgl').screenshot({ path: info.outputPath('webgl.png') });
      expect(result.glError).toBe(0);
      // RGB with a one-backing-pixel horizontal tolerance for nearest-bin boundary rounding.
      if (profile === 'ic705') expect(result.active).toBeGreaterThan(0);
      expect(result.tolerantMean).toBeLessThan(0.25);
      expect(result.mean).toBeLessThan(0.25);
      expect(result.p99).toBeLessThanOrEqual(1);
    });
  });
}
