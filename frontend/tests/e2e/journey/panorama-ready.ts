import { expect, type Page } from '@playwright/test';

interface PanoramaWork { raf: number; rafMs: number; texture: number; draw: number; static: number; labels: string[] }
declare global { interface Window { panoramaWork: PanoramaWork } }

export async function installPanoramaWorkProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const work: PanoramaWork = { raf: 0, rafMs: 0, texture: 0, draw: 0, static: 0, labels: [] };
    window.panoramaWork = work;
    const raf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => raf(time => {
      const start = performance.now(); work.raf++;
      try { callback(time); } finally { work.rafMs += performance.now() - start; }
    });
    const gl = WebGL2RenderingContext.prototype;
    const texture = gl.texImage2D, draw = gl.drawArrays;
    gl.texImage2D = function (this: WebGL2RenderingContext, ...args: unknown[]) {
      work.texture++; return Reflect.apply(texture, this, args);
    } as typeof gl.texImage2D;
    gl.drawArrays = function (...args) { work.draw++; return Reflect.apply(draw, this, args); };
    const paint = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (...args) {
      if (/^\d+\.\d{3}$/.test(args[0])) {
        if (args[1] === 20) work.labels = [];
        work.labels.push(args[0]);
      }
      work.static++; return Reflect.apply(paint, this, args);
    };
  });
}

/** What the page-side drain needs from `window`; a fake satisfies it in unit tests. */
export interface GlDrainWindow {
  document: Pick<Document, 'querySelectorAll'>;
  __journeyGlContexts?: Array<{ canvas: HTMLCanvasElement; finish(): void }>;
  __journeyGlDraws?: number;
  __journeyGlDrawnContexts?: WeakSet<object>;
}

declare global {
  interface Window {
    __journeyGlContexts?: WebGL2RenderingContext[];
    __journeyGlDraws?: number;
    __journeyGlDrawnContexts?: WeakSet<object>;
  }
}

/**
 * Registers every WebGL2 context the page creates and counts every draw call
 * submitted on any of them; call before navigating.
 */
export async function trackWebglContexts(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__journeyGlContexts = [];
    window.__journeyGlDraws = 0;
    window.__journeyGlDrawnContexts = new WeakSet();
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
          const result = draw.apply(this, args);
          window.__journeyGlDrawnContexts!.add(this);
          return result;
        };
      }
    }
  });
}

/**
 * Waits for the mounted waterfalls' own draws and unchanged bounds across
 * an animation frame, draining their GPU work before returning coordinates.
 * The unit cases in panorama-ready-drain.test.ts cover late mount, resize
 * and a draw from an unrelated canvas.
 */
export async function drainAfterFirstDraw(
  win: GlDrainWindow = window as unknown as GlDrainWindow,
  nextFrame: () => Promise<void> = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
): Promise<{ x: number; y: number; width: number; height: number }> {
  if (win.__journeyGlDraws === undefined || win.__journeyGlContexts === undefined
    || win.__journeyGlDrawnContexts === undefined) {
    throw new Error('Panorama WebGL probe is not installed; navigate with openPanorama(page, url)');
  }
  const surfaces = () => Array.from(win.document.querySelectorAll<HTMLCanvasElement>('.waterfall-content canvas'))
    .map(canvas => ({ canvas, bounds: canvas.getBoundingClientRect(),
      context: win.__journeyGlContexts!.find(context => context.canvas === canvas) }))
    .filter(({ bounds }) => bounds.width > 0 && bounds.height > 0);
  let previous: ReturnType<typeof surfaces> = [];
  for (;;) {
    const current = surfaces();
    if (current.length > 0 && current.every(({ canvas, context }) => canvas.width > 0 && canvas.height > 0
      && context !== undefined && win.__journeyGlDrawnContexts!.has(context))) {
      for (const { context } of current) context!.finish();
      if (current.length === previous.length && current.every(({ canvas, bounds }, index) => {
        const before = previous[index]!;
        return canvas === before.canvas && bounds.x === before.bounds.x && bounds.y === before.bounds.y
          && bounds.width === before.bounds.width && bounds.height === before.bounds.height;
      })) {
        const { x, y, width, height } = current[0]!.bounds;
        return { x, y, width, height };
      }
      previous = current;
    } else {
      previous = [];
    }
    await nextFrame();
  }
}

/** Returns the first waterfall's stable bounds after its render work drains. */
export async function awaitRenderPipeline(page: Page): Promise<{ x: number; y: number; width: number; height: number }> {
  // Serialised into the page: the function must stay self-contained.
  return page.evaluate(drainAfterFirstDraw as () => ReturnType<typeof drainAfterFirstDraw>);
}

/** `page.goto` for panorama journeys: navigate, then wait for the pipeline. */
export async function openPanorama(page: Page, url: string): Promise<void> {
  await trackWebglContexts(page);
  await page.goto(url);
  await awaitRenderPipeline(page);
}

/** Drain render work on both sides of an action without extending its action budget. */
export async function clickPanorama(page: Page, control: import('@playwright/test').Locator): Promise<void> {
  await awaitRenderPipeline(page);
  await expect(control).toBeVisible({ timeout: 10_000 });
  await expect(control).toBeEnabled({ timeout: 10_000 });
  await control.click({ timeout: 10_000 });
  await awaitRenderPipeline(page);
}

export async function reloadPanorama(page: Page): Promise<void> {
  await awaitRenderPipeline(page);
  await page.reload({ waitUntil: 'commit', timeout: 10_000 });
  await page.waitForLoadState('domcontentloaded');
  await awaitRenderPipeline(page);
}

/** Measurement duration, not a substitute for a readiness condition. */
export async function observePanorama(page: Page, durationMs: number): Promise<void> {
  await awaitRenderPipeline(page);
  await page.waitForFunction(({ start, duration }) => performance.now() - start >= duration,
    { start: await page.evaluate(() => performance.now()), duration: durationMs },
    { polling: 100, timeout: 0 });
  await awaitRenderPipeline(page);
}

export async function expectPanoramaFrequency(page: Page, freqHz: number): Promise<void> {
  await awaitRenderPipeline(page);
  await page.waitForFunction(expected => {
    const readout = document.querySelector<HTMLElement>('.receiver-instrument [data-vfo-freq]');
    return readout !== null && Number(readout.innerText.replace(/\D/g, '')) === expected;
  }, freqHz, { timeout: 10_000 });
  await awaitRenderPipeline(page);
}
