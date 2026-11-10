// MOR-4388 — invariant 7 baseline: fps and frame time on the recorded
// IC-705 bench fixture (ic705_bench_scope_antenna_20s.rdscope converted by
// tests/e2e/panorama_matrix_fixture.py), replayed through the PRODUCTION
// SpectrumRenderer + WaterfallRenderer + ScopeRenderTrace at the recorded
// cadence, in a real WebKit page. This is a renderer-pipeline baseline:
// the Svelte panel's own overhead is not included (said so in the report).
//
// Requires the fixtures vite server (playwright.panorama-perf.config.ts)
// and PANORAMA_MATRIX_FIXTURE pointing at the converted pair's base path.

import { expect, test } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { parseFixtureSidecar } from './matrix-contract';

const fixtureBase = process.env.PANORAMA_MATRIX_FIXTURE;

interface PerfResult {
  readonly fixture: string;
  readonly frames: number;
  readonly nativeWidth: number;
  readonly replayedS: number;
  readonly cadenceFps: number;
  readonly rafCount: number;
  readonly spectrumRenderMaxMs: number;
  readonly summary: Readonly<Record<string, unknown>>;
  readonly trace: unknown;
}

test('IC-705 recorded fixture renderer-trace baseline', async ({ page }, info) => {
  test.skip(!fixtureBase, 'PANORAMA_MATRIX_FIXTURE not set (run tests/e2e/panorama_matrix_fixture.py convert first)');
  const sidecar = parseFixtureSidecar(await readFile(`${fixtureBase}.sidecar.json`, 'utf8'));
  const capture = await readFile(`${fixtureBase}.frames`);
  expect(sidecar.frames).toBeGreaterThan(20);

  await page.goto('/fixtures/spectrum-witness.html');
  const result = await page.evaluate(async ({ wires, offsets, nativeWidth }) => {
    // Module URLs as plain strings (the parity spec's pattern): the vite
    // fixtures server serves /src, and TypeScript must not resolve them.
    const traceUrl = '/src/lib/runtime/scope-render-trace';
    const spectrumUrl = '/src/lib/renderers/spectrum-renderer';
    const waterfallUrl = '/src/lib/renderers/waterfall-renderer';
    const frameUrl = '/src/lib/runtime/scope-frame';
    const traceModule = await import(/* @vite-ignore */ traceUrl) as typeof import('../../../src/lib/runtime/scope-render-trace');
    const spectrumModule = await import(/* @vite-ignore */ spectrumUrl) as typeof import('../../../src/lib/renderers/spectrum-renderer');
    const waterfallModule = await import(/* @vite-ignore */ waterfallUrl) as typeof import('../../../src/lib/renderers/waterfall-renderer');
    const frameModule = await import(/* @vite-ignore */ frameUrl) as typeof import('../../../src/lib/runtime/scope-frame');
    type Frame = import('../../../src/lib/runtime/adapters/scope-adapter').ScopeFrame;
    const trace = new traceModule.ScopeRenderTrace(() => {}, undefined, 'matrix-perf');

    const frames: Frame[] = [];
    for (const wire of wires) {
      const parsed = frameModule.parseCurrentScopeFrame(new Uint8Array(wire).buffer, {
        source: 'radio', sourceId: 'ic705-bench', epoch: 1,
      });
      if (parsed.ok && parsed.frame.pixels) frames.push(parsed.frame);
    }
    const width = 1728, spectrumHeight = 360, waterfallHeight = 320;
    const spectrumCanvas = document.createElement('canvas');
    spectrumCanvas.width = width; spectrumCanvas.height = spectrumHeight;
    spectrumCanvas.style.cssText = `display:block;width:${width}px;height:${spectrumHeight}px`;
    const waterfallCanvas = document.createElement('canvas');
    waterfallCanvas.width = width; waterfallCanvas.height = waterfallHeight;
    waterfallCanvas.style.cssText = `display:block;width:${width}px;height:${waterfallHeight}px`;
    document.body.replaceChildren(spectrumCanvas, waterfallCanvas);

    const first = frames[0];
    const span = first.viewEndHz - first.viewStartHz;
    const options = {
      ...spectrumModule.defaultSpectrumOptions,
      spanHz: span, sampleSpanHz: span, centerHz: (first.viewStartHz + first.viewEndHz) / 2,
    };
    const waterfallOptions = {
      ...waterfallModule.defaultWaterfallOptions,
      spanHz: span, sampleSpanHz: span, centerHz: (first.viewStartHz + first.viewEndHz) / 2,
    };
    const spectrum = new spectrumModule.SpectrumRenderer();
    const waterfall = new waterfallModule.WaterfallRenderer(waterfallCanvas, waterfallOptions);
    const ctx = spectrumCanvas.getContext('2d')!;
    trace.recordGeometry('spectrum', width, spectrumHeight, width, spectrumHeight, 1);
    trace.recordGeometry('waterfall', width, waterfallHeight, width, waterfallHeight, 1);

    let rafCount = 0;
    let spectrumRenderMaxMs = 0;
    const render = (frame: Frame): void => {
      const token = trace.tokenForFrame(frame);
      trace.record('waterfall_push', token);
      trace.record('spectrum_push', token);
      const start = performance.now();
      requestAnimationFrame(() => {
        rafCount++;
        trace.record('raf', token, performance.now() - start);
        const drawStart = performance.now();
        waterfall.pushRow(frame.pixels!);
        spectrum.render(ctx, frame.pixels!, width, spectrumHeight, options);
        trace.record('spectrum_render', token, performance.now() - drawStart);
        spectrumRenderMaxMs = Math.max(spectrumRenderMaxMs, performance.now() - drawStart);
      });
    };
    // Replay at the recorded cadence: offsets are seconds since capture
    // start; scale them so 20 s of bench time replays in 20 s.
    const start = performance.now();
    for (let index = 0; index < frames.length; index++) {
      const delayMs = offsets[index] * 1000;
      await new Promise<void>(resolve => setTimeout(resolve, Math.max(0, start + delayMs - performance.now())));
      render(frames[index]);
    }
    await new Promise<void>(resolve => setTimeout(resolve, 1500));
    trace.stop('operator');
    const replayedS = (performance.now() - start) / 1000;
    return {
      frames: frames.length,
      nativeWidth: frames[0].width ?? nativeWidth,
      replayedS,
      rafCount,
      spectrumRenderMaxMs,
      trace: JSON.parse(trace.export() ?? '{}'),
    };
  }, {
    wires: (() => { const out: number[][] = []; for (let offset = 0; offset < capture.length;) { const length = capture.readUInt32LE(offset); offset += 4; out.push(Array.from(capture.subarray(offset, offset + length))); offset += length; } return out; })(),
    offsets: sidecar.frameOffsetsS,
    nativeWidth: sidecar.nativeWidth,
  });

  const perf: PerfResult = {
    fixture: sidecar.source,
    frames: result.frames,
    nativeWidth: result.nativeWidth,
    replayedS: Math.round(result.replayedS * 10) / 10,
    cadenceFps: Math.round((result.frames / result.replayedS) * 100) / 100,
    rafCount: result.rafCount,
    spectrumRenderMaxMs: Math.round(result.spectrumRenderMaxMs * 100) / 100,
    summary: {
      note: 'renderer-pipeline baseline (SpectrumRenderer + WaterfallRenderer + ScopeRenderTrace) on the recorded bench stream; panel/Svelte overhead not included',
    },
    trace: result.trace,
  };
  await writeFile(info.outputPath('perf-result.json'), JSON.stringify(perf, null, 2));
  expect(result.frames).toBeGreaterThan(20);
  expect(result.rafCount).toBeGreaterThan(20);
});
