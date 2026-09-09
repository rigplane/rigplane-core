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
 */
import { test, expect } from '@playwright/test';

const VIEWPORT = { width: 1280, height: 800 };

test('spectrum-panel--managed-frame', async ({ page }) => {
  await page.setViewportSize(VIEWPORT);
  await page.goto('/fixtures/spectrum-witness.html', { waitUntil: 'load' });
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
