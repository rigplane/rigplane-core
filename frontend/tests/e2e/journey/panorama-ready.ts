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

/** What the page-side drain needs from `window`; a fake satisfies it in unit tests. */
export interface GlDrainWindow {
  __journeyGlContexts?: Array<{ finish(): void }>;
  __journeyGlDraws?: number;
}

declare global {
  interface Window { __journeyGlContexts?: WebGL2RenderingContext[]; __journeyGlDraws?: number }
}

/**
 * Registers every WebGL2 context the page creates and counts every draw call
 * submitted on any of them; call before navigating.
 */
export async function trackWebglContexts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__journeyGlContexts = [];
    window.__journeyGlDraws = 0;
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, ...args: unknown[]) {
      const context = (original as (...a: unknown[]) => unknown).apply(this, args);
      if (typeof WebGL2RenderingContext !== 'undefined' && context instanceof WebGL2RenderingContext
        && !window.__journeyGlContexts!.includes(context)) window.__journeyGlContexts!.push(context);
      return context;
    } as typeof original;
    if (typeof WebGL2RenderingContext !== 'undefined') {
      for (const name of ['drawArrays', 'drawElements'] as const) {
        const draw = WebGL2RenderingContext.prototype[name] as (...a: unknown[]) => void;
        (WebGL2RenderingContext.prototype as unknown as Record<string, unknown>)[name] = function (
          this: WebGL2RenderingContext, ...args: unknown[]
        ) {
          window.__journeyGlDraws = (window.__journeyGlDraws ?? 0) + 1;
          return draw.apply(this, args);
        };
      }
    }
  });
}

/**
 * Runs in the page. Waits (one animation frame at a time, so there is no
 * time budget of its own) until a draw has actually been submitted, then
 * drains the GPU queue, so the first draw's shader JIT has finished before
 * the caller acts. Draining before the first draw would drain nothing: the
 * renderer paints only once its surface is visible and sized, which arrives
 * asynchronously. A final frame and drain covers a second waterfall (dual
 * receiver) that submits its first draw a frame later.
 */
export async function drainAfterFirstDraw(
  win: GlDrainWindow = window as unknown as GlDrainWindow,
  nextFrame: () => Promise<void> = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
): Promise<void> {
  while ((win.__journeyGlDraws ?? 0) === 0) await nextFrame();
  for (const context of win.__journeyGlContexts ?? []) context.finish();
  await nextFrame();
  for (const context of win.__journeyGlContexts ?? []) context.finish();
}

/** Resolves once the page has submitted its first draw and the GPU queue is empty. */
export async function awaitRenderPipeline(page: Page): Promise<void> {
  // Serialised into the page: the function must stay self-contained.
  await page.evaluate(drainAfterFirstDraw as () => Promise<void>);
}

/** `page.goto` for panorama journeys: navigate, then wait for the pipeline. */
export async function openPanorama(page: Page, url: string): Promise<void> {
  await trackWebglContexts(page);
  await page.goto(url);
  await awaitRenderPipeline(page);
}
