/**
 * Approved pixel-diff baseline for the spectrum panorama.
 *
 * `visual-baselines.spec.ts` mounts the cockpit / reference / peer-split /
 * LCD fixtures; none of them mounts `components/spectrum/SpectrumPanel.svelte`,
 * so the spectrum renderer, the waterfall renderer and the tune-line /
 * passband DOM overlay had no approved-baseline witness. This file adds one,
 * over the `fixtures/spectrum-witness.html` entry — see that entry's header
 * for which inputs are constants and why the canvases settle.
 *
 * The captured region is the panel root (`[data-waterfall]`), not the page:
 * `maxDiffPixelRatio` is a fraction of what is captured, so bounding the
 * region to the panel keeps the comparator's absolute pixel floor tied to
 * the panel rather than to the surrounding page.
 *
 * The screenshot keeps its renderer-only pre-separator layout through the
 * fixture's `legacyBaseline` mode. The second test exercises the shipped
 * separator layout and behavior without regenerating that PNG.
 */
import { test, expect } from '@playwright/test';

const VIEWPORT = { width: 1280, height: 800 };

test('spectrum-panel--managed-frame', async ({ page }) => {
  await page.setViewportSize(VIEWPORT);
  await page.goto('/fixtures/spectrum-witness.html?legacyBaseline=1', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  await page.evaluate(() => document.fonts.ready);
  const panel = page.locator('[data-waterfall]');
  // The captured box, asserted rather than assumed: `src/app.css` declares
  // `#app { min-height: 100dvh }` too, and when that rule wins the panel is
  // as tall as the viewport (960x800 here) with nothing else in this file
  // noticing.
  const box = await panel.boundingBox();
  expect({ width: box?.width, height: box?.height })
    .toEqual({ width: 960, height: 540 });
  // The overlay elements the fixed projection is there to place. Asserted
  // before the pixel comparison so a baseline can never be approved over a
  // frame in which they are absent.
  await expect(panel.locator('.tune-line')).toBeVisible();
  await expect(panel.locator('.passband-overlay')).toBeVisible();
  // The three overlay colour roles #3388 routes from `colorRoles` into CSS
  // custom properties. Asserted rather than left to the pixel layer because
  // the comparator does not see changes of this kind: with `threshold: 0.2`,
  // recolouring `passbandFill` from `rgba(59,130,246,0.15)` to
  // `rgba(59,246,130,0.15)` left the screenshot comparison green here.
  const overlayRoles = await panel.evaluate((node) => {
    const style = getComputedStyle(node);
    return {
      tuneLine: style.getPropertyValue('--scope-tune-line').trim(),
      passbandFill: style.getPropertyValue('--scope-passband-fill').trim(),
      passbandEdge: style.getPropertyValue('--scope-passband-edge').trim(),
    };
  });
  expect(overlayRoles).toEqual({
    tuneLine: 'rgba(239,68,68,0.75)',
    passbandFill: 'rgba(59,130,246,0.15)',
    passbandEdge: 'rgba(59,130,246,0.4)',
  });
  await expect(panel).toHaveScreenshot('spectrum-panel--managed-frame.png', {
    animations: 'disabled',
    caret: 'hide',
  });
});

test('spectrum separator preserves geometry, ratio, canvas state, and command isolation', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (request) => requests.push(`${request.method()} ${request.url()}`));
  await page.setViewportSize(VIEWPORT);
  await page.goto('/fixtures/spectrum-witness.html', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(100);

  const panel = page.locator('[data-waterfall]');
  const separator = panel.locator('[role="separator"]');
  await expect(separator).toBeVisible();
  const geometry = () => panel.evaluate((node) => {
    const region = node.querySelector<HTMLElement>('.spectrum-split-region')!;
    const spectrum = node.querySelector<HTMLElement>('.spectrum-with-scales')!;
    const axis = node.querySelector<HTMLElement>('.freq-axis');
    const split = node.querySelector<HTMLElement>('[role="separator"]')!;
    const waterfall = node.querySelector<HTMLElement>('.waterfall-area')!;
    const heights = {
      region: region.getBoundingClientRect().height,
      spectrum: spectrum.getBoundingClientRect().height,
      axis: axis?.getBoundingClientRect().height ?? 0,
      separator: split.getBoundingClientRect().height,
      waterfall: waterfall.getBoundingClientRect().height,
    };
    return {
      ...heights,
      innerTotal: heights.spectrum + heights.axis + heights.separator + heights.waterfall,
      ratio: heights.spectrum / (heights.spectrum + heights.waterfall),
      ariaRatio: Number(split.getAttribute('aria-valuenow')) / 100,
    };
  });
  await panel.evaluate((node) => {
    const witness = window as typeof window & {
      __spectrumCanvasNodes?: { spectrum: Element | null; waterfall: Element | null };
    };
    witness.__spectrumCanvasNodes = {
      spectrum: node.querySelector('.spectrum-area canvas'),
      waterfall: node.querySelector('.waterfall-content canvas'),
    };
  });

  const initial = await geometry();
  const initialRequestCount = requests.length;
  const separatorBox = await separator.boundingBox();
  expect(separatorBox).not.toBeNull();
  await page.mouse.move(
    separatorBox!.x + separatorBox!.width / 2,
    separatorBox!.y + separatorBox!.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    separatorBox!.x + separatorBox!.width / 2,
    separatorBox!.y + separatorBox!.height / 2 + 90,
    { steps: 5 },
  );
  await page.mouse.up();

  const dragged = await geometry();
  expect(dragged.spectrum).toBeGreaterThan(initial.spectrum);
  expect(dragged.waterfall).toBeLessThan(initial.waterfall);
  expect(Math.abs(dragged.region - initial.region)).toBeLessThanOrEqual(1);
  expect(Math.abs(dragged.innerTotal - dragged.region)).toBeLessThanOrEqual(1);
  expect(dragged.spectrum).toBeGreaterThanOrEqual(64);
  expect(dragged.waterfall).toBeGreaterThanOrEqual(80);
  const persistedRatio = await page.evaluate(() =>
    Number(localStorage.getItem('rigplane-spectrum-split-ratio')),
  );
  expect(persistedRatio).toBeCloseTo(dragged.ariaRatio, 2);

  await separator.evaluate((node) => {
    node.addEventListener('pointerdown', (event) => {
      (window as typeof window & { __splitPointerId?: number }).__splitPointerId
        = (event as PointerEvent).pointerId;
    }, { once: true });
  });
  const cancelledBox = await separator.boundingBox();
  await page.mouse.move(cancelledBox!.x + 10, cancelledBox!.y + cancelledBox!.height / 2);
  await page.mouse.down();
  const ratioAtCancel = (await geometry()).ariaRatio;
  const pointerId = await page.evaluate(() =>
    (window as typeof window & { __splitPointerId?: number }).__splitPointerId,
  );
  expect(pointerId).toBeDefined();
  await separator.dispatchEvent('pointercancel', {
    bubbles: true,
    cancelable: true,
    pointerId,
    clientY: cancelledBox!.y + cancelledBox!.height / 2,
  });
  await page.mouse.move(cancelledBox!.x + 10, cancelledBox!.y + 60);
  await page.mouse.up();
  expect((await geometry()).ariaRatio).toBe(ratioAtCancel);

  await page.evaluate(() => {
    const app = document.getElementById('app')!;
    app.style.width = '100vw';
    app.style.height = '70vh';
  });
  await page.setViewportSize({ width: 900, height: 650 });
  const shrunk = await geometry();
  expect(shrunk.ariaRatio).toBeCloseTo(persistedRatio, 2);
  expect(shrunk.ratio).toBeCloseTo(persistedRatio, 2);
  expect(shrunk.spectrum).toBeGreaterThanOrEqual(64);
  expect(shrunk.waterfall).toBeGreaterThanOrEqual(80);

  await page.setViewportSize({ width: 1280, height: 900 });
  const grown = await geometry();
  expect(grown.ariaRatio).toBeCloseTo(persistedRatio, 2);
  expect(grown.ratio).toBeCloseTo(persistedRatio, 2);
  await panel.locator('[title="Toggle fullscreen"]').click();
  await expect(panel).toHaveClass(/fullscreen/);
  const fullscreen = await geometry();
  expect(fullscreen.ariaRatio).toBeCloseTo(persistedRatio, 2);
  expect(fullscreen.ratio).toBeCloseTo(persistedRatio, 2);

  const canvasIdentity = await panel.evaluate((node) => {
    const witness = window as typeof window & {
      __spectrumCanvasNodes?: { spectrum: Element | null; waterfall: Element | null };
    };
    return witness.__spectrumCanvasNodes?.spectrum === node.querySelector('.spectrum-area canvas')
      && witness.__spectrumCanvasNodes?.waterfall
        === node.querySelector('.waterfall-content canvas');
  });
  expect(canvasIdentity).toBe(true);
  const commands = await page.evaluate(() => window.__spectrumWitness.commands);
  expect(commands).toEqual([]);
  expect(requests).toHaveLength(initialRequestCount);
});

test('spectrum separator moves and reports rendered bounds in the 220px portrait slot', async ({ page }) => {
  await page.setViewportSize(VIEWPORT);
  await page.goto('/fixtures/spectrum-witness.html?portrait=1', { waitUntil: 'load' });
  await page.waitForSelector('body[data-harness-ready="true"]');
  const panel = page.locator('[data-waterfall]');
  const separator = panel.locator('[role="separator"]');
  const read = () => panel.evaluate((node) => {
    const region = node.querySelector<HTMLElement>('.spectrum-split-region')!;
    const spectrum = node.querySelector<HTMLElement>('.spectrum-with-scales')!;
    const axis = node.querySelector<HTMLElement>('.freq-axis')!;
    const split = node.querySelector<HTMLElement>('[role="separator"]')!;
    const waterfall = node.querySelector<HTMLElement>('.waterfall-area')!;
    const paneTotal = spectrum.offsetHeight + waterfall.offsetHeight;
    return {
      panel: node.getBoundingClientRect().height,
      region: region.offsetHeight,
      spectrum: spectrum.offsetHeight,
      axis: axis.offsetHeight,
      separator: split.offsetHeight,
      waterfall: waterfall.offsetHeight,
      innerTotal: spectrum.offsetHeight + axis.offsetHeight
        + split.offsetHeight + waterfall.offsetHeight,
      renderedRatio: spectrum.offsetHeight / paneTotal,
      ariaRatio: Number(split.getAttribute('aria-valuenow')) / 100,
      storedRatio: Number(localStorage.getItem('rigplane-spectrum-split-ratio')),
    };
  });
  await panel.evaluate((node) => {
    (window as typeof window & { __portraitCanvases?: Element[] }).__portraitCanvases = [
      node.querySelector('.spectrum-area canvas')!,
      node.querySelector('.waterfall-content canvas')!,
    ];
  });

  const initial = await read();
  expect(initial.panel).toBe(220);
  expect(Math.abs(initial.innerTotal - initial.region)).toBeLessThanOrEqual(1);
  expect(initial.spectrum).toBeGreaterThanOrEqual(64);
  expect(initial.waterfall).toBeGreaterThanOrEqual(80);
  expect(initial.ariaRatio).toBeCloseTo(initial.renderedRatio, 2);

  const box = await separator.boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height / 2 + 12);
  await page.mouse.up();
  const pointerMoved = await read();
  expect(pointerMoved.spectrum).toBeGreaterThan(initial.spectrum);
  expect(pointerMoved.ariaRatio).toBeCloseTo(pointerMoved.renderedRatio, 2);
  expect(pointerMoved.storedRatio).toBeCloseTo(pointerMoved.renderedRatio, 2);

  await separator.press('Home');
  const home = await read();
  await separator.press('ArrowDown');
  const keyboardMoved = await read();
  expect(keyboardMoved.spectrum).toBeGreaterThan(home.spectrum);
  expect(keyboardMoved.ariaRatio).toBeCloseTo(keyboardMoved.renderedRatio, 2);
  expect(keyboardMoved.storedRatio).toBeCloseTo(keyboardMoved.renderedRatio, 2);

  await panel.evaluate(() => {
    document.getElementById('app')!.style.height = '150px';
  });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  const constrained = await read();
  expect(Math.abs(constrained.innerTotal - constrained.region)).toBeLessThanOrEqual(1);
  expect(constrained.renderedRatio).toBeCloseTo(64 / (64 + 80), 2);
  expect(constrained.ariaRatio).toBeCloseTo(constrained.renderedRatio, 2);
  await panel.evaluate(() => {
    document.getElementById('app')!.style.height = '220px';
  });
  await page.evaluate(() => new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  }));
  const restored = await read();
  expect(restored.ariaRatio).toBeCloseTo(keyboardMoved.storedRatio, 2);
  expect(restored.renderedRatio).toBeCloseTo(keyboardMoved.storedRatio, 2);
  expect(await panel.evaluate((node) => {
    const canvases = (window as typeof window & { __portraitCanvases?: Element[] })
      .__portraitCanvases;
    return canvases?.[0] === node.querySelector('.spectrum-area canvas')
      && canvases?.[1] === node.querySelector('.waterfall-content canvas');
  })).toBe(true);
  expect(await page.evaluate(() => window.__spectrumWitness.commands)).toEqual([]);
});
