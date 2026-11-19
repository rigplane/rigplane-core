import type { Page } from '@playwright/test';

/**
 * MOR-4214: when a panorama journey may first touch the page.
 *
 * The waterfall is a WebGL2 canvas. On the CI runners and the bench hosts
 * Chromium rasterises it in software (SwiftShader), and the work that makes
 * the page "alive" for Playwright all runs in the GPU process: creating the
 * context, compiling and JIT-ing the waterfall fragment shader, the first
 * draw. While that runs the page produces no animation frames, so
 * `requestAnimationFrame` stalls for seconds (measured 3 to 21 s on a loaded
 * host, with no JavaScript running). Playwright's actionability check
 * ("stable": two consecutive animation frames) cannot pass in that window,
 * and a 10 s click timeout is spent on the page's start-up instead of on the
 * click. Nothing is wrong with the page; the spec simply started interacting
 * before the page had finished starting.
 *
 * The honest readiness event is "the GPU process has drained everything the
 * page queued", which is what `gl.finish()` blocks on. It has no time limit
 * of its own (only the test timeout), so it takes exactly as long as the host
 * needs and the actions after it start from a quiet pipeline.
 */

declare global {
  interface Window { __journeyGlContexts?: WebGL2RenderingContext[] }
}

/** Registers every WebGL2 context the page creates; call before navigating. */
export async function trackWebglContexts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__journeyGlContexts = [];
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, ...args: unknown[]) {
      const context = (original as (...a: unknown[]) => unknown).apply(this, args);
      if (typeof WebGL2RenderingContext !== 'undefined' && context instanceof WebGL2RenderingContext
        && !window.__journeyGlContexts!.includes(context)) window.__journeyGlContexts!.push(context);
      return context;
    } as typeof original;
  });
}

/** Resolves once the page has presented frames and the GPU queue is empty. */
export async function awaitRenderPipeline(page: Page): Promise<void> {
  // The waterfall mounts after the first state arrives, so the context may
  // not exist yet when `load` fires.
  await page.waitForFunction(() => (window.__journeyGlContexts?.length ?? 0) > 0, undefined, { timeout: 30_000 });
  await page.evaluate(async () => {
    const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
    // Two rounds: the first paint submits the first draw (shader JIT), the
    // second proves the queue is empty after it.
    for (let round = 0; round < 2; round++) {
      await frame();
      for (const context of window.__journeyGlContexts ?? []) context.finish();
    }
  });
}

/** `page.goto` for panorama journeys: navigate, then wait for the pipeline. */
export async function openPanorama(page: Page, url: string): Promise<void> {
  await trackWebglContexts(page);
  await page.goto(url);
  await awaitRenderPipeline(page);
}
